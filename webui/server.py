import asyncio
import uuid
import json
from collections import deque
from quart import Quart, render_template, request, jsonify, make_response

from astrbot.api import logger


def create_trpg_app(plugin):
    """创建 Quart 应用，绑定 AITRPG 插件实例"""
    import os
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    app = Quart(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
        static_url_path="/trpg/static"
    )
    app.secret_key = "aitrpg-webui-secret"
    app.plugin = plugin

    # Web 会话存储: cookie_id -> session_data
    _web_sessions = {}
    # 每个会话的并发锁
    _session_locks = {}

    def _get_or_create_web_session(cookie_id: str) -> dict:
        """获取或创建 Web 会话"""
        if cookie_id not in _web_sessions:
            session_id = f"web_{cookie_id[:8]}"
            _web_sessions[cookie_id] = {
                "session_id": session_id,
                "game_started": False,
                "history": [],           # LLM 对话历史
                "chat_messages": [],     # 前端聊天消息
                "module_index": None,
            }
        if cookie_id not in _session_locks:
            _session_locks[cookie_id] = asyncio.Lock()
        return _web_sessions[cookie_id]

    def _get_cookie_id() -> str:
        """从请求中获取或生成 cookie ID"""
        return request.cookies.get("trpg_session", "")

    def _serialize_state(state: dict) -> dict:
        """序列化游戏状态（处理 deque 等不可序列化类型）"""
        if state is None:
            return None
        result = {}
        for key, value in state.items():
            if isinstance(value, deque):
                result[key] = list(value)
            elif isinstance(value, dict):
                result[key] = _serialize_state(value)
            else:
                result[key] = value
        return result

    # ─── 路由 ───

    @app.route("/trpg/")
    async def index():
        """渲染主页面"""
        resp = await make_response(await render_template("index.html"))
        if not request.cookies.get("trpg_session"):
            resp.set_cookie("trpg_session", str(uuid.uuid4()), max_age=86400 * 7)
        return resp

    @app.route("/trpg/api/modules", methods=["GET"])
    async def api_modules():
        """列出可用模组"""
        modules = plugin.session_manager.list_modules()
        return jsonify({"modules": modules})

    @app.route("/trpg/api/start", methods=["POST"])
    async def api_start():
        """选择模组并开始游戏"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _get_or_create_web_session(cookie_id)
        lock = _session_locks[cookie_id]

        async with lock:
            data = await request.get_json()
            module_index = data.get("module_index", 0)

            modules = plugin.session_manager.list_modules()
            if module_index < 0 or module_index >= len(modules):
                return jsonify({"error": "无效的模组序号"}), 400

            selected = modules[module_index]
            session_id = web_session["session_id"]

            # 如果已有游戏，先清除
            if plugin.session_manager.has_session(session_id):
                plugin.session_manager.delete_session(session_id)

            # 创建会话并加载模组
            plugin.session_manager.create_session(session_id)
            plugin.session_manager.load_module_for_session(session_id, selected["filename"])

            # 同步 AI 层模组数据
            plugin.rhythm_ai.module_data = plugin.session_manager.module_data
            plugin.narrative_ai.module_data = plugin.session_manager.module_data

            opening = selected["opening"]

            # 初始化 web 会话
            web_session["game_started"] = True
            web_session["module_index"] = module_index
            web_session["history"] = [
                {"role": "user", "content": "缓缓苏醒"},
                {"role": "assistant", "content": opening}
            ]
            web_session["chat_messages"] = [
                {"role": "assistant", "content": opening}
            ]

            state = plugin.session_manager.get_session(session_id)

            return jsonify({
                "success": True,
                "opening": opening,
                "module_name": selected["name"],
                "game_state": _serialize_state(state)
            })

    @app.route("/trpg/api/action", methods=["POST"])
    async def api_action():
        """发送玩家行动，返回三层 AI 结果"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _get_or_create_web_session(cookie_id)
        if not web_session["game_started"]:
            return jsonify({"error": "游戏尚未开始"}), 400

        lock = _session_locks[cookie_id]

        async with lock:
            data = await request.get_json()
            player_input = data.get("input", "").strip()
            if not player_input:
                return jsonify({"error": "输入不能为空"}), 400

            session_id = web_session["session_id"]

            try:
                # 调用核心处理流程
                result = await plugin._process_action_core(
                    session_id=session_id,
                    player_input=player_input,
                    history=web_session["history"]
                )

                narrative = result["narrative_result"]["narrative"]
                summary = result["narrative_result"]["summary"]

                # 更新 web 会话历史
                web_session["history"].append({"role": "user", "content": player_input})
                web_session["history"].append({"role": "assistant", "content": narrative})

                # 限制历史长度（保留最近 20 条对话）
                if len(web_session["history"]) > 40:
                    web_session["history"] = web_session["history"][-40:]

                # 更新前端聊天消息
                web_session["chat_messages"].append({"role": "user", "content": player_input})
                web_session["chat_messages"].append({"role": "assistant", "content": narrative})

                # 同步 narrative_history
                plugin.session_manager.add_narrative_summary(session_id, narrative, summary)

                state = plugin.session_manager.get_session(session_id)

                return jsonify({
                    "success": True,
                    "narrative": narrative,
                    "rule_result": result["rule_result"],
                    "rhythm_result": {
                        "feasible": result["rhythm_result"].get("feasible"),
                        "hint": result["rhythm_result"].get("hint"),
                        "stage_assessment": result["rhythm_result"].get("stage_assessment"),
                    },
                    "game_state": _serialize_state(state)
                })

            except Exception as e:
                logger.error(f"[AITRPG WebUI] 处理行动出错: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return jsonify({"error": f"处理出错: {str(e)}"}), 500

    @app.route("/trpg/api/state", methods=["GET"])
    async def api_state():
        """获取当前游戏状态"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _get_or_create_web_session(cookie_id)
        session_id = web_session["session_id"]

        if not plugin.session_manager.has_session(session_id):
            return jsonify({
                "game_started": False,
                "game_state": None,
                "chat_messages": []
            })

        state = plugin.session_manager.get_session(session_id)
        return jsonify({
            "game_started": web_session["game_started"],
            "game_state": _serialize_state(state),
            "chat_messages": web_session["chat_messages"]
        })

    @app.route("/trpg/api/reset", methods=["POST"])
    async def api_reset():
        """重置游戏"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _get_or_create_web_session(cookie_id)
        lock = _session_locks[cookie_id]

        async with lock:
            session_id = web_session["session_id"]
            plugin.session_manager.delete_session(session_id)
            web_session["game_started"] = False
            web_session["history"] = []
            web_session["chat_messages"] = []
            web_session["module_index"] = None

        return jsonify({"success": True})

    return app


async def start_webui_server(app, port: int, shutdown_event: asyncio.Event = None):
    """使用 Hypercorn 启动 WebUI 服务器"""
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    config = Config()
    config.bind = [f"0.0.0.0:{port}"]
    config.accesslog = None
    config.graceful_timeout = 3

    try:
        logger.info(f"[AITRPG] WebUI starting on http://0.0.0.0:{port}/trpg/")
        await serve(app, config, shutdown_trigger=shutdown_event.wait())
        logger.info("[AITRPG] WebUI server stopped cleanly")
    except Exception as e:
        logger.error(f"[AITRPG] WebUI server error: {e}", exc_info=True)
