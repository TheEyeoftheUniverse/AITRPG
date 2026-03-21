import asyncio
import uuid
import json
import socket
import os
import platform
import re
from collections import deque
from quart import Quart, render_template, request, jsonify, make_response

from astrbot.api import logger
from ..game_state.save_store import JsonSaveStore


async def _is_port_available(port: int) -> bool:
    """检查端口是否可用"""
    def check_sync():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, check_sync)


async def _get_pids_on_port(port: int) -> list:
    """获取占用指定端口的进程 PID 列表（仅 Linux）"""
    pids = set()
    try:
        methods = [
            ("ss", ["-ltnp", f"sport = {port}"]),
            ("lsof", ["-i", f":{port}", "-sTCP:LISTEN", "-t"]),
            ("netstat", ["-tlnp"]),
        ]
        for i, (cmd, args) in enumerate(methods):
            try:
                proc = await asyncio.create_subprocess_exec(
                    cmd, *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                result = stdout.decode(errors="ignore")
                if i == 0:  # ss
                    for line in result.splitlines():
                        if f":{port} " in line or line.strip().endswith(f":{port}"):
                            m = re.search(r'pid=(\d+)', line)
                            if m:
                                pids.add(int(m.group(1)))
                    if pids:
                        break
                elif i == 1:  # lsof
                    for line in result.splitlines():
                        if line.strip().isdigit():
                            pids.add(int(line.strip()))
                    if pids:
                        break
                elif i == 2:  # netstat
                    for line in result.splitlines():
                        if f":{port} " in line and "LISTEN" in line:
                            parts = line.split()
                            if parts and "/" in parts[-1]:
                                pid_str = parts[-1].split("/")[0]
                                if pid_str.isdigit():
                                    pids.add(int(pid_str))
            except FileNotFoundError:
                continue
    except Exception as e:
        logger.warning(f"[AITRPG] 获取端口 {port} 占用进程时出错: {e}")
    current_pid = os.getpid()
    pids.discard(current_pid)
    return list(pids)


async def _free_port(port: int) -> bool:
    """尝试杀死占用端口的进程，返回是否成功释放"""
    pids = await _get_pids_on_port(port)
    if not pids:
        return True
    for pid in pids:
        try:
            proc = await asyncio.create_subprocess_exec(
                "kill", "-9", str(pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            logger.warning(f"[AITRPG] 已终止占用端口 {port} 的进程 PID={pid}")
        except Exception as e:
            logger.warning(f"[AITRPG] 终止进程 {pid} 失败: {e}")
    await asyncio.sleep(1)
    return await _is_port_available(port)


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
    save_store = JsonSaveStore(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "web_sessions")
    )

    def _build_empty_web_session(cookie_id: str) -> dict:
        """创建空白 Web 会话结构"""
        return {
            "session_id": f"web_{cookie_id[:8]}",
            "game_started": False,
            "history": [],
            "chat_messages": [],
            "last_workflow": None,
            "module_index": None,
            "conv_id": None,
        }

    def _get_or_create_web_session(cookie_id: str) -> dict:
        """获取或创建 Web 会话"""
        if cookie_id not in _web_sessions:
            _web_sessions[cookie_id] = _build_empty_web_session(cookie_id)
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

    def _get_static_version() -> str:
        """基于静态资源修改时间生成版本号，避免浏览器缓存旧文件。"""
        static_files = [
            os.path.join(static_dir, "css", "style.css"),
            os.path.join(static_dir, "css", "workflow_override.css"),
            os.path.join(static_dir, "js", "app.js"),
            os.path.join(static_dir, "js", "workflow_override.js"),
        ]
        mtimes = []
        for file_path in static_files:
            try:
                mtimes.append(int(os.path.getmtime(file_path)))
            except OSError:
                continue
        return str(max(mtimes) if mtimes else 1)

    def _persist_web_session(cookie_id: str):
        """将 Web 会话和游戏状态持久化到 JSON"""
        web_session = _web_sessions.get(cookie_id)
        if not web_session:
            return

        session_id = web_session["session_id"]
        game_state = plugin.session_manager.export_session(session_id)
        if not web_session.get("game_started") or not game_state:
            return

        save_store.save(cookie_id, {
            "web_session": {
                "session_id": session_id,
                "game_started": web_session.get("game_started", False),
                "history": web_session.get("history", []),
                "chat_messages": web_session.get("chat_messages", []),
                "last_workflow": web_session.get("last_workflow"),
                "module_index": web_session.get("module_index"),
                "conv_id": web_session.get("conv_id"),
            },
            "game_state": game_state,
        })

    def _restore_web_session(cookie_id: str) -> dict:
        """如果存在 JSON 存档，则恢复 Web 会话和游戏状态"""
        web_session = _get_or_create_web_session(cookie_id)
        session_id = web_session["session_id"]

        if web_session.get("game_started") and plugin.session_manager.has_session(session_id):
            return web_session

        saved_data = save_store.load(cookie_id)
        if not saved_data:
            return web_session

        saved_web = saved_data.get("web_session") or {}
        restored_web = _build_empty_web_session(cookie_id)
        restored_web["session_id"] = saved_web.get("session_id") or restored_web["session_id"]
        restored_web["game_started"] = bool(saved_web.get("game_started", False))
        restored_web["history"] = list(saved_web.get("history") or [])
        restored_web["chat_messages"] = list(saved_web.get("chat_messages") or [])
        restored_web["last_workflow"] = saved_web.get("last_workflow")
        restored_web["module_index"] = saved_web.get("module_index")
        restored_web["conv_id"] = saved_web.get("conv_id")
        _web_sessions[cookie_id] = restored_web

        if restored_web["game_started"] and not plugin.session_manager.has_session(restored_web["session_id"]):
            saved_state = saved_data.get("game_state")
            plugin.session_manager.restore_session(restored_web["session_id"], saved_state)

        return restored_web

    # ─── 路由 ───

    @app.route("/")
    async def root_redirect():
        """根路径重定向到 /trpg/"""
        from quart import redirect
        return redirect("/trpg/")

    @app.route("/trpg/")
    async def index():
        """渲染主页面"""
        resp = await make_response(await render_template(
            "index.html",
            static_version=_get_static_version()
        ))
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
            try:
                logger.info(f"[AITRPG WebUI] 收到启动游戏请求，cookie={cookie_id[:8]}")
                data = await request.get_json()
                if not isinstance(data, dict):
                    return jsonify({"error": "请求格式错误"}), 400

                module_index = data.get("module_index", 0)

                modules = plugin.session_manager.list_modules()
                if module_index < 0 or module_index >= len(modules):
                    return jsonify({"error": "无效的模组序号"}), 400

                selected = modules[module_index]
                session_id = web_session["session_id"]
                logger.info(f"[AITRPG WebUI] 准备启动模组: session_id={session_id}, module={selected['filename']}")

                # 如果已有游戏，先清除
                if plugin.session_manager.has_session(session_id):
                    plugin.session_manager.delete_session(session_id)

                # 创建会话并加载模组
                plugin.session_manager.create_session(session_id, selected["filename"])

                opening = selected["opening"]

                # 尝试在 AstrBot 中创建新对话并写入开场白；失败时降级为仅Web会话
                conv_id = None
                try:
                    conv_mgr = plugin.context.conversation_manager
                    conv_id = await conv_mgr.new_conversation(session_id)
                    await conv_mgr.switch_conversation(session_id, conv_id)
                    await conv_mgr.add_message_pair(
                        cid=conv_id,
                        user_message={"role": "user", "content": "缓缓苏醒"},
                        assistant_message={"role": "assistant", "content": opening}
                    )
                except Exception as e:
                    logger.warning(f"[AITRPG WebUI] 初始化 AstrBot 对话失败，将继续使用仅 Web 会话模式: {e}")

                # 初始化 web 会话
                web_session["game_started"] = True
                web_session["module_index"] = module_index
                web_session["conv_id"] = conv_id
                web_session["history"] = [
                    {"role": "user", "content": "缓缓苏醒"},
                    {"role": "assistant", "content": opening}
                ]
                web_session["chat_messages"] = [
                    {"role": "assistant", "content": opening}
                ]

                state = plugin.session_manager.get_session(session_id)
                map_data = plugin.session_manager.get_map_data(session_id)
                _persist_web_session(cookie_id)
                logger.info(f"[AITRPG WebUI] 启动游戏成功: session_id={session_id}, module={selected['filename']}")

                return jsonify({
                    "success": True,
                    "opening": opening,
                    "module_name": selected["name"],
                    "game_state": _serialize_state(state),
                    "map_data": map_data
                })
            except Exception as e:
                logger.error(f"[AITRPG WebUI] 启动游戏失败: {e}", exc_info=True)
                return jsonify({"error": f"启动游戏失败: {str(e)}"}), 500

    @app.route("/trpg/api/action", methods=["POST"])
    async def api_action():
        """发送玩家行动，返回三层 AI 结果"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _restore_web_session(cookie_id)
        if not web_session["game_started"]:
            return jsonify({"error": "游戏尚未开始"}), 400

        lock = _session_locks[cookie_id]

        async with lock:
            data = await request.get_json()
            player_input = data.get("input", "").strip()
            move_to = data.get("move_to", "").strip() or None

            if not player_input and not move_to:
                return jsonify({"error": "输入不能为空"}), 400

            session_id = web_session["session_id"]

            try:
                # 调用核心处理流程
                result = await plugin._process_action_core(
                    session_id=session_id,
                    player_input=player_input,
                    history=web_session["history"],
                    move_to=move_to
                )

                narrative = result["narrative_result"]["narrative"]
                summary = result["narrative_result"]["summary"]

                # 同步到 AstrBot 对话记录
                conv_id = web_session.get("conv_id")
                display_input = player_input or (f"[移动到{move_to}]" if move_to else "")
                if conv_id:
                    try:
                        conv_mgr = plugin.context.conversation_manager
                        await conv_mgr.add_message_pair(
                            cid=conv_id,
                            user_message={"role": "user", "content": display_input},
                            assistant_message={"role": "assistant", "content": narrative}
                        )
                    except Exception as e:
                        logger.warning(f"[AITRPG WebUI] 同步对话记录失败: {e}")

                # 更新 web 会话历史
                web_session["history"].append({"role": "user", "content": display_input})
                web_session["history"].append({"role": "assistant", "content": narrative})

                # 限制历史长度（保留最近 20 条对话）
                if len(web_session["history"]) > 40:
                    web_session["history"] = web_session["history"][-40:]

                # 更新前端聊天消息
                if display_input:
                    web_session["chat_messages"].append({"role": "user", "content": display_input})
                web_session["chat_messages"].append({"role": "assistant", "content": narrative})

                # 同步 narrative_history
                plugin.session_manager.add_narrative_summary(session_id, display_input, narrative, summary)

                state = plugin.session_manager.get_session(session_id)
                map_data = plugin.session_manager.get_map_data(session_id)
                web_session["last_workflow"] = {
                    "rule_plan": result.get("rule_plan", {}),
                    "rule_result": result.get("rule_result", {}),
                    "hard_changes": result.get("hard_changes", {}),
                    "rhythm_result": result.get("rhythm_result", {}),
                }
                _persist_web_session(cookie_id)

                return jsonify({
                    "success": True,
                    "narrative": narrative,
                    "rule_plan": result.get("rule_plan", {}),
                    "rule_result": result["rule_result"],
                    "hard_changes": result.get("hard_changes", {}),
                    "rhythm_result": result["rhythm_result"],
                    "game_state": _serialize_state(state),
                    "map_data": map_data
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

        web_session = _restore_web_session(cookie_id)
        session_id = web_session["session_id"]

        if not plugin.session_manager.has_session(session_id):
            return jsonify({
                "game_started": False,
                "game_state": None,
                "chat_messages": []
            })

        state = plugin.session_manager.get_session(session_id)
        map_data = plugin.session_manager.get_map_data(session_id)
        return jsonify({
            "game_started": web_session["game_started"],
            "game_state": _serialize_state(state),
            "chat_messages": web_session["chat_messages"],
            "last_workflow": web_session.get("last_workflow"),
            "map_data": map_data
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
            web_session["last_workflow"] = None
            web_session["module_index"] = None
            web_session["conv_id"] = None
            save_store.delete(cookie_id)

        return jsonify({"success": True})

    return app


async def start_webui_server(app, port: int, shutdown_event: asyncio.Event = None):
    """使用 Hypercorn 启动 WebUI 服务器"""
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    # 检查端口占用，尝试释放
    if not await _is_port_available(port):
        logger.warning(f"[AITRPG] 端口 {port} 被占用，尝试自动释放...")
        freed = await _free_port(port)
        if not freed:
            # 等待最多 10 秒
            for _ in range(10):
                await asyncio.sleep(1)
                if await _is_port_available(port):
                    freed = True
                    break
        if not freed:
            logger.error(f"[AITRPG] 端口 {port} 无法释放，WebUI 启动失败")
            return

    config = Config()
    config.bind = [f"0.0.0.0:{port}"]
    config.use_reloader = False
    config.accesslog = None

    try:
        logger.info(f"[AITRPG] WebUI starting on http://0.0.0.0:{port}/trpg/")
        await serve(app, config)
        logger.info("[AITRPG] WebUI server stopped cleanly")
    except asyncio.CancelledError:
        logger.info("[AITRPG] WebUI 已停止")
        raise
    except Exception as e:
        logger.error(f"[AITRPG] WebUI server error: {e}", exc_info=True)
