import asyncio
import time
import uuid
import json
import socket
import os
import platform
import re
from datetime import datetime, timezone
from collections import deque
from quart import Quart, render_template, request, jsonify, make_response, send_from_directory

from astrbot.api import logger
from ..game_state.save_store import JsonSaveStore
from ..game_state import character_card as character_card_module
from ..game_state.placeholder_resolver import (
    resolve_in,
    resolve_hard_in,
    resolve_hard_placeholders,
    get_and_clear_pending_checks,
)
from ..theatrical_parser import parse_theatrical_tags


def _extract_location_name_from_state(game_state: dict) -> str:
    if not isinstance(game_state, dict):
        return ""
    current_location = str(game_state.get("current_location") or "").strip()
    module_data = game_state.get("module_data", {}) if isinstance(game_state.get("module_data"), dict) else {}
    locations = module_data.get("locations", {}) if isinstance(module_data.get("locations"), dict) else {}
    location_data = locations.get(current_location, {}) if isinstance(locations.get(current_location), dict) else {}
    return str(location_data.get("name") or current_location).strip()


def _build_save_summary(saved_data: dict) -> dict:
    if not isinstance(saved_data, dict):
        return {"has_save": False, "save": None}

    web_session = saved_data.get("web_session", {}) if isinstance(saved_data.get("web_session"), dict) else {}
    game_state = saved_data.get("game_state", {}) if isinstance(saved_data.get("game_state"), dict) else {}
    if not web_session or not game_state or not bool(web_session.get("game_started")):
        return {"has_save": False, "save": None}

    game_over = bool(saved_data.get("game_over", False))
    if game_over:
        return {"has_save": False, "save": None}

    module_data = game_state.get("module_data", {}) if isinstance(game_state.get("module_data"), dict) else {}
    module_info = module_data.get("module_info", {}) if isinstance(module_data.get("module_info"), dict) else {}
    current_location = str(game_state.get("current_location") or "").strip()

    save_payload = {
        "module_index": web_session.get("module_index"),
        "module_name": str(module_info.get("name") or game_state.get("module_filename") or "AI驱动TRPG").strip(),
        "round_count": int(game_state.get("round_count", 0) or 0),
        "current_location": current_location or None,
        "current_location_name": _extract_location_name_from_state(game_state) or current_location or None,
        "saved_at": saved_data.get("saved_at"),
        "game_over": game_over,
    }
    return {"has_save": True, "save": save_payload}


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
    # 模组编辑器目录: 位于插件根目录下的 tools/module-editor/, 作为纯静态资源对外暴露,
    # 不调 LLM、不开 session, 主页通过新窗口跳转到 /trpg/module-editor/ 即可使用
    module_editor_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "tools", "module-editor"
    )

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
    # 每个会话的最后活跃时间（用于 TTL 清理）
    _session_last_active: dict[str, float] = {}
    # 异步 action 结果存储: cookie_id -> result payload
    _action_results: dict = {}
    save_store = JsonSaveStore(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "web_sessions")
    )

    _SESSION_TTL = 86400      # 24小时不活跃后清理
    _CLEANUP_INTERVAL = 1800  # 每30分钟检查一次
    _SAVE_CLEANUP_COUNTER = 0
    _SAVE_CLEANUP_EVERY = 336  # 每336次内存清理时执行一次存档清理（336 * 30min ≈ 7天）

    async def _cleanup_stale_sessions():
        """后台任务：定期清理长时间不活跃的会话，防止内存泄漏。"""
        nonlocal _SAVE_CLEANUP_COUNTER
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            try:
                now = time.time()
                expired = [
                    cid for cid, ts in list(_session_last_active.items())
                    if now - ts > _SESSION_TTL
                ]
                for cid in expired:
                    _web_sessions.pop(cid, None)
                    _session_locks.pop(cid, None)
                    _session_last_active.pop(cid, None)
                    _action_results.pop(cid, None)
                if expired:
                    logger.info(f"[AITRPG] 已清理 {len(expired)} 个过期 Web 会话")
                # 每约7天清理一次磁盘存档
                _SAVE_CLEANUP_COUNTER += 1
                if _SAVE_CLEANUP_COUNTER >= _SAVE_CLEANUP_EVERY:
                    _SAVE_CLEANUP_COUNTER = 0
                    active_keys = set(_web_sessions.keys())
                    save_store.cleanup_stale(max_age_seconds=86400 * 7, active_keys=active_keys)
            except Exception as e:
                logger.warning(f"[AITRPG] 会话清理任务出错: {e}")

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
        _session_last_active[cookie_id] = time.time()
        return _web_sessions[cookie_id]

    def _get_cookie_id() -> str:
        """从请求中获取或生成 cookie ID"""
        return request.cookies.get("trpg_session", "")

    def _serialize_state(state: dict) -> dict:
        """序列化游戏状态（处理 deque 等不可序列化类型，剔除前端不需要的大字段）"""
        if state is None:
            return None
        result = {}
        for key, value in state.items():
            if key == "module_data":
                continue
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
            # v3.1.1: 把地图视图也纳入缓存破坏，否则单独改 map_view.js 不会换 ?v= 值
            os.path.join(static_dir, "js", "map_view.js"),
            os.path.join(static_dir, "js", "character_card.js"),
        ]
        mtimes = []
        for file_path in static_files:
            try:
                mtimes.append(int(os.path.getmtime(file_path)))
            except OSError:
                continue
        return str(max(mtimes) if mtimes else 1)

    def _normalize_resolved_state(web_session: dict) -> dict:
        """确保 resolved 三类集合存在；存档恢复时 list 会在这里转回 set。"""
        raw = web_session.get("resolved")
        if not isinstance(raw, dict):
            raw = {}
        resolved = {}
        for key in ("locations", "objects", "npcs"):
            value = raw.get(key)
            if isinstance(value, set):
                resolved[key] = value
            elif isinstance(value, (list, tuple)):
                resolved[key] = set(str(item) for item in value if str(item or "").strip())
            else:
                resolved[key] = set()
        web_session["resolved"] = resolved
        return resolved

    def _serialize_resolved_state(web_session: dict) -> dict:
        resolved = _normalize_resolved_state(web_session)
        return {
            key: sorted(str(item) for item in value)
            for key, value in resolved.items()
        }

    def _lazy_resolve_location(loc_key, module_data, card, player, web_session):
        """解析指定 location 的硬 placeholder；返回本次产生的骰点。"""
        loc_key = str(loc_key or "").strip()
        if not loc_key:
            return []
        resolved = _normalize_resolved_state(web_session)
        if loc_key in resolved["locations"]:
            return []
        get_and_clear_pending_checks()
        loc_data = module_data.get("locations", {}).get(loc_key, {})
        if isinstance(loc_data, dict):
            for field in ("description", "npc_present_description"):
                if loc_data.get(field):
                    loc_data[field] = resolve_hard_in(loc_data[field], card, player)
        resolved["locations"].add(loc_key)
        return get_and_clear_pending_checks()

    def _lazy_resolve_object(obj_key, module_data, card, player, web_session):
        """解析指定 object 的硬 placeholder；返回本次产生的骰点。"""
        obj_key = str(obj_key or "").strip()
        if not obj_key:
            return []
        resolved = _normalize_resolved_state(web_session)
        if obj_key in resolved["objects"]:
            return []
        get_and_clear_pending_checks()
        obj_data = module_data.get("objects", {}).get(obj_key, {})
        if isinstance(obj_data, dict):
            for field in ("description", "examine_text", "success_result", "failure_result"):
                if obj_data.get(field):
                    obj_data[field] = resolve_hard_in(obj_data[field], card, player)
        resolved["objects"].add(obj_key)
        return get_and_clear_pending_checks()

    def _lazy_resolve_npc(npc_key, module_data, card, player, web_session):
        """解析指定 NPC 的硬 placeholder；返回本次产生的骰点。"""
        npc_key = str(npc_key or "").strip()
        if not npc_key:
            return []
        resolved = _normalize_resolved_state(web_session)
        if npc_key in resolved["npcs"]:
            return []
        get_and_clear_pending_checks()
        npc_data = module_data.get("npcs", {}).get(npc_key, {})
        if isinstance(npc_data, dict):
            for field in ("description", "dialogue"):
                if npc_data.get(field):
                    npc_data[field] = resolve_hard_in(npc_data[field], card, player)
        resolved["npcs"].add(npc_key)
        return get_and_clear_pending_checks()

    def _persist_web_session(cookie_id: str):
        """将 Web 会话和游戏状态持久化到 JSON"""
        web_session = _web_sessions.get(cookie_id)
        if not web_session:
            return

        session_id = web_session["session_id"]
        game_state = plugin.session_manager.export_session(session_id)
        if not web_session.get("game_started") or not game_state:
            return

        saved_at = datetime.now(timezone.utc).astimezone().isoformat()
        save_store.save(cookie_id, {
            "saved_at": saved_at,
            "game_over": plugin.session_manager.is_game_over(session_id),
            "ending_phase": plugin.session_manager.get_ending_phase(session_id),
            "web_session": {
                "session_id": session_id,
                "game_started": web_session.get("game_started", False),
                "history": web_session.get("history", []),
                "chat_messages": web_session.get("chat_messages", []),
                "last_workflow": web_session.get("last_workflow"),
                "module_index": web_session.get("module_index"),
                "conv_id": web_session.get("conv_id"),
                "lazy_dice": web_session.get("lazy_dice", True),
                "resolved": _serialize_resolved_state(web_session),
                "object_dice": web_session.get("object_dice", {}),
                "npc_dice": web_session.get("npc_dice", {}),
                "location_theatrical": web_session.get("location_theatrical", {}),
                "theatrical_played_locations": web_session.get("theatrical_played_locations", []),
            },
            "game_state": game_state,
        })

    def _get_saved_data(cookie_id: str) -> dict:
        """读取持久化存档，不触发恢复"""
        saved_data = save_store.load(cookie_id)
        return saved_data if isinstance(saved_data, dict) else {}

    def _build_live_save_snapshot(cookie_id: str) -> dict:
        """从当前内存态构建存档摘要源数据，不做恢复。"""
        web_session = _web_sessions.get(cookie_id)
        if not isinstance(web_session, dict) or not web_session.get("game_started"):
            return {}
        session_id = str(web_session.get("session_id") or "").strip()
        if not session_id or not plugin.session_manager.has_session(session_id):
            return {}
        game_state = plugin.session_manager.export_session(session_id)
        if not isinstance(game_state, dict):
            return {}
        saved_data = _get_saved_data(cookie_id)
        return {
            "saved_at": saved_data.get("saved_at"),
            "game_over": plugin.session_manager.is_game_over(session_id),
            "ending_phase": plugin.session_manager.get_ending_phase(session_id),
            "web_session": {
                "session_id": session_id,
                "game_started": True,
                "history": list(web_session.get("history") or []),
                "chat_messages": list(web_session.get("chat_messages") or []),
                "last_workflow": web_session.get("last_workflow"),
                "module_index": web_session.get("module_index"),
                "conv_id": web_session.get("conv_id"),
                "lazy_dice": web_session.get("lazy_dice", True),
                "resolved": _serialize_resolved_state(web_session),
                "object_dice": web_session.get("object_dice", {}),
                "npc_dice": web_session.get("npc_dice", {}),
                "location_theatrical": web_session.get("location_theatrical", {}),
                "theatrical_played_locations": web_session.get("theatrical_played_locations", []),
            },
            "game_state": game_state,
        }

    def _get_save_summary_payload(cookie_id: str) -> dict:
        """返回当前浏览器的中断存档摘要，不触发恢复。"""
        return _build_save_summary(_build_live_save_snapshot(cookie_id) or _get_saved_data(cookie_id))

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
        restored_web["lazy_dice"] = saved_web.get("lazy_dice", True)
        restored_web["resolved"] = saved_web.get("resolved") or {}
        _normalize_resolved_state(restored_web)
        restored_web["object_dice"] = saved_web.get("object_dice") if isinstance(saved_web.get("object_dice"), dict) else {}
        restored_web["npc_dice"] = saved_web.get("npc_dice") if isinstance(saved_web.get("npc_dice"), dict) else {}
        restored_web["location_theatrical"] = saved_web.get("location_theatrical") if isinstance(saved_web.get("location_theatrical"), dict) else {}
        restored_web["theatrical_played_locations"] = list(saved_web.get("theatrical_played_locations") or [])
        _web_sessions[cookie_id] = restored_web

        if restored_web["game_started"] and not plugin.session_manager.has_session(restored_web["session_id"]):
            saved_state = saved_data.get("game_state")
            plugin.session_manager.restore_session(restored_web["session_id"], saved_state)

        return restored_web

    def _build_runtime_state_payload(web_session: dict) -> dict:
        """构建前端进入游戏界面所需的完整运行态载荷。"""
        session_id = web_session["session_id"]
        if not plugin.session_manager.has_session(session_id):
            return {
                "game_started": False,
                "game_state": None,
                "chat_messages": [],
            }

        state = plugin.session_manager.get_session(session_id)
        map_data = plugin.session_manager.get_map_data(session_id)
        return {
            "success": True,
            "game_started": bool(web_session.get("game_started")),
            "game_state": _serialize_state(state),
            "chat_messages": web_session.get("chat_messages", []),
            "last_workflow": web_session.get("last_workflow"),
            "map_data": map_data,
            "ending_phase": plugin.session_manager.get_ending_phase(session_id),
            "ending_id": plugin.session_manager.get_ending_id(session_id),
            "ending_display": plugin.session_manager.get_ending_display(session_id),
            "game_over": plugin.session_manager.is_game_over(session_id),
        }

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

    # ─── 模组编辑器（纯静态）───
    # 把插件根的 tools/module-editor/ 作为静态文件托管, 主页有图标入口跳转过来。
    # 不走任何 API、不调 LLM、不持久化, 仅提供本地工具页面所需的 HTML/CSS/JS/lib 文件。

    @app.route("/trpg/module-editor/")
    async def module_editor_index():
        return await send_from_directory(module_editor_dir, "index.html")

    @app.route("/trpg/module-editor/<path:filename>")
    async def module_editor_static(filename):
        return await send_from_directory(module_editor_dir, filename)

    @app.route("/trpg/api/modules", methods=["GET"])
    async def api_modules():
        """列出可用模组"""
        modules = plugin.session_manager.list_modules()
        return jsonify({"modules": modules})

    @app.route("/trpg/api/save-summary", methods=["GET"])
    async def api_save_summary():
        """返回当前浏览器的中断存档摘要，不恢复运行态。"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"has_save": False, "save": None})
        return jsonify(_get_save_summary_payload(cookie_id))

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
                force_new = bool(data.get("force_new"))

                character_card_payload = data.get("character_card")
                custom_profile = None
                if character_card_payload is not None:
                    try:
                        profs = character_card_module.load_professions()
                        skills_base = character_card_module.load_skills_base()
                    except Exception as e:
                        logger.warning(f"[AITRPG WebUI] character_card 数据加载失败: {e}")
                        return jsonify({"error": "character_card data unavailable"}), 500
                    ok, errs, normalized = character_card_module.validate_card(
                        character_card_payload, profs, skills_base
                    )
                    if not ok:
                        logger.info(f"[AITRPG WebUI] character_card 校验失败: {errs}")
                        return jsonify({
                            "error": "character_card validation failed",
                            "errors": errs,
                        }), 400
                    custom_profile = character_card_module.to_player_profile(normalized)
                    web_session["character_card"] = normalized
                else:
                    web_session.pop("character_card", None)

                modules = plugin.session_manager.list_modules()
                if module_index < 0 or module_index >= len(modules):
                    return jsonify({"error": "无效的模组序号"}), 400

                existing_save = _get_save_summary_payload(cookie_id)
                if existing_save.get("has_save") and not force_new:
                    return jsonify({
                        "error": "存在可继续的存档，请先继续存档或确认覆盖后再开始新游戏。",
                        "requires_confirm": True,
                        "save": existing_save.get("save"),
                    }), 409

                selected = modules[module_index]
                session_id = web_session["session_id"]
                logger.info(f"[AITRPG WebUI] 准备启动模组: session_id={session_id}, module={selected['filename']}")

                # 如果已有游戏，先清除
                if plugin.session_manager.has_session(session_id):
                    plugin.session_manager.delete_session(session_id)

                # 创建会话并加载模组
                plugin.session_manager.create_session(
                    session_id, selected["filename"],
                    custom_profile=custom_profile,
                    character_card=web_session.get("character_card"),
                )

                # 预清洗所有地点描述中的演出标签，防止AI看到原始标签
                module_data = plugin.session_manager.get_module_data(session_id)
                location_theatrical = {}
                for loc_key, loc_data in module_data.get("locations", {}).items():
                    desc = loc_data.get("description", "")
                    if desc:
                        parsed_desc = parse_theatrical_tags(desc)
                        if parsed_desc["effects"]:
                            location_theatrical[loc_key] = parsed_desc["effects"]
                            loc_data["description"] = parsed_desc["clean_text"]

                # Phase 3: 开局时解析所有地点/物品/NPC 描述中的软 placeholder,
                # 让后续 AI prompt + 前端展示都用已解析文本。模块数据在 session 内是
                # 单例, 就地解析一次全局生效。
                session_state = plugin.session_manager.get_session(session_id)
                card = None
                player = None
                if session_state:
                    card = session_state.get("character_card")
                    player = session_state.get("player")
                    for loc_key, loc_data in module_data.get("locations", {}).items():
                        if isinstance(loc_data, dict) and loc_data.get("description"):
                            loc_data["description"] = resolve_in(loc_data["description"], card, player)
                        if isinstance(loc_data, dict) and loc_data.get("npc_present_description"):
                            loc_data["npc_present_description"] = resolve_in(loc_data["npc_present_description"], card, player)
                    for obj_key, obj_data in module_data.get("objects", {}).items():
                        if isinstance(obj_data, dict):
                            for field in ("description", "examine_text", "success_result", "failure_result"):
                                if obj_data.get(field):
                                    obj_data[field] = resolve_in(obj_data[field], card, player)
                    for npc_key, npc_data in module_data.get("npcs", {}).items():
                        if isinstance(npc_data, dict):
                            for field in ("description", "dialogue"):
                                if npc_data.get(field):
                                    npc_data[field] = resolve_in(npc_data[field], card, player)

                # Phase 5 W1: 硬 placeholder 默认按需解析。开局只解析起始 location
                # 与 opening；object/NPC 等首次互动时再解析并注入骰点动画。
                opening_checks = []
                object_dice = {}
                npc_dice = {}
                lazy_dice = bool(web_session.get("lazy_dice", True))
                web_session["lazy_dice"] = lazy_dice
                web_session["resolved"] = {"locations": set(), "objects": set(), "npcs": set()}
                if lazy_dice:
                    start_loc_key = ""
                    if session_state:
                        start_loc_key = str(session_state.get("current_location") or "").strip()
                    if not start_loc_key:
                        start_loc_key = str(module_data.get("module_info", {}).get("start_location") or "").strip()
                    opening_checks.extend(_lazy_resolve_location(start_loc_key, module_data, card, player, web_session))
                else:
                    resolved = _normalize_resolved_state(web_session)
                    for loc_key, loc_data in module_data.get("locations", {}).items():
                        if isinstance(loc_data, dict) and loc_data.get("description"):
                            loc_data["description"] = resolve_hard_in(loc_data["description"], card, player)
                        if isinstance(loc_data, dict) and loc_data.get("npc_present_description"):
                            loc_data["npc_present_description"] = resolve_hard_in(loc_data["npc_present_description"], card, player)
                        opening_checks.extend(get_and_clear_pending_checks())
                        resolved["locations"].add(loc_key)
                    for obj_key, obj_data in module_data.get("objects", {}).items():
                        if isinstance(obj_data, dict):
                            for field in ("description", "examine_text"):
                                if obj_data.get(field):
                                    obj_data[field] = resolve_hard_in(obj_data[field], card, player)
                            checks = get_and_clear_pending_checks()
                            if checks:
                                object_dice[obj_key] = checks
                            resolved["objects"].add(obj_key)
                    for npc_key, npc_data in module_data.get("npcs", {}).items():
                        if isinstance(npc_data, dict):
                            for field in ("description", "dialogue"):
                                if npc_data.get(field):
                                    npc_data[field] = resolve_hard_in(npc_data[field], card, player)
                        get_and_clear_pending_checks()  # v3.2 回退行为: NPC checks 不触发动画
                        resolved["npcs"].add(npc_key)

                opening = selected["opening"]
                parsed_opening = parse_theatrical_tags(opening)
                opening = parsed_opening["clean_text"]
                # 硬 placeholder 也解析 opening 文本 (可能含 {检定:X} 等)
                opening = resolve_hard_placeholders(opening, card, player)
                opening_effects = parsed_opening["effects"]
                opening_checks.extend(get_and_clear_pending_checks())
                opening_user_message, opening_assistant_message = plugin._build_opening_history_pair(opening)

                # 尝试在 AstrBot 中创建新对话并写入开场白；失败时降级为仅Web会话
                conv_id = None
                try:
                    conv_mgr = plugin.context.conversation_manager
                    conv_id = await conv_mgr.new_conversation(session_id)
                    await conv_mgr.switch_conversation(session_id, conv_id)
                    await conv_mgr.add_message_pair(
                        cid=conv_id,
                        user_message=opening_user_message,
                        assistant_message=opening_assistant_message
                    )
                except Exception as e:
                    logger.warning(f"[AITRPG WebUI] 初始化 AstrBot 对话失败，将继续使用仅 Web 会话模式: {e}")

                # 初始化 web 会话
                web_session["game_started"] = True
                web_session["module_index"] = module_index
                web_session["conv_id"] = conv_id
                web_session["location_theatrical"] = location_theatrical
                web_session["theatrical_played_locations"] = []
                web_session["object_dice"] = object_dice  # Phase 5: object→骰子缓存
                web_session["npc_dice"] = npc_dice
                web_session["history"] = [opening_user_message, opening_assistant_message]
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
                    "theatrical_effects": opening_effects,
                    "dice_rolls": opening_checks,
                    "module_name": selected["name"],
                    "game_state": _serialize_state(state),
                    "map_data": map_data
                })
            except Exception as e:
                logger.error(f"[AITRPG WebUI] 启动游戏失败: {e}", exc_info=True)
                return jsonify({"error": f"启动游戏失败: {str(e)}"}), 500

    @app.route("/trpg/api/action", methods=["POST"])
    async def api_action():
        """发送玩家行动，立即返回202，后台异步执行三层 AI"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _restore_web_session(cookie_id)
        if not web_session["game_started"]:
            return jsonify({"error": "游戏尚未开始"}), 400

        lock = _session_locks[cookie_id]
        if lock.locked():
            return jsonify({"error": "正在处理上一个行动，请稍候"}), 429

        data = await request.get_json()
        player_input = data.get("input", "").strip()
        move_to = data.get("move_to", "").strip() or None
        custom_api = data.get("custom_api") or None
        merge_mode = bool(data.get("merge_mode", False))
        if isinstance(custom_api, dict) and not any(
            isinstance(v, dict) and v.get("base_url") and v.get("api_key") and v.get("model")
            for v in custom_api.values()
        ):
            custom_api = None

        if not player_input and not move_to:
            return jsonify({"error": "输入不能为空"}), 400

        session_id = web_session["session_id"]
        _action_results.pop(cookie_id, None)

        async def _run_action():
            async with lock:
                try:
                    result = await plugin._process_action_core(
                        session_id=session_id,
                        player_input=player_input,
                        history=web_session["history"],
                        move_to=move_to,
                        custom_api=custom_api,
                        merge_mode=merge_mode,
                    )

                    narrative = result["narrative_result"]["narrative"]
                    summary = result["narrative_result"]["summary"]

                    # 解析演出效果标签
                    parsed = parse_theatrical_tags(narrative)
                    narrative = parsed["clean_text"]
                    theatrical_effects = parsed["effects"]

                    # 同步到 AstrBot 对话记录
                    conv_id = web_session.get("conv_id")
                    display_input = player_input or (f"[移动到{move_to}]" if move_to else "")
                    if conv_id:
                        try:
                            conv_mgr = plugin.context.conversation_manager
                            await conv_mgr.add_message_pair(
                                cid=conv_id,
                                user_message=plugin._build_history_message("user", display_input),
                                assistant_message=plugin._build_history_message("assistant", narrative)
                            )
                            await plugin._compress_history_if_needed(
                                conv_mgr=conv_mgr,
                                session_id=session_id,
                                conv_id=conv_id,
                            )
                        except Exception as e:
                            logger.warning(f"[AITRPG WebUI] 同步对话记录失败: {e}")

                    # 更新 web 会话历史
                    web_session["history"].append(plugin._build_history_message("user", display_input))
                    web_session["history"].append(plugin._build_history_message("assistant", narrative))

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
                    rule_plan = result.get("rule_plan", {})
                    normalized_action = (rule_plan.get("normalized_action") or {}) if isinstance(rule_plan, dict) else {}
                    target_kind = normalized_action.get("target_kind")
                    target_key = normalized_action.get("target_key")
                    location_dice = []

                    if web_session.get("lazy_dice", True) and state:
                        module_data = state.get("module_data", {}) if isinstance(state.get("module_data"), dict) else {}
                        card = state.get("character_card")
                        player = state.get("player")
                        if move_to and state.get("current_location") == move_to:
                            location_dice.extend(_lazy_resolve_location(move_to, module_data, card, player, web_session))
                        if target_kind == "object" and target_key:
                            checks = _lazy_resolve_object(target_key, module_data, card, player, web_session)
                            if checks:
                                obj_dice = web_session.setdefault("object_dice", {})
                                obj_dice.setdefault(target_key, []).extend(checks)
                        if target_kind == "npc" and target_key:
                            checks = _lazy_resolve_npc(target_key, module_data, card, player, web_session)
                            if checks:
                                npc_dice = web_session.setdefault("npc_dice", {})
                                npc_dice.setdefault(target_key, []).extend(checks)

                    # 持久化 map_corrupt 效果到会话状态
                    corrupt_entries = {e["target"]: e["content"] for e in theatrical_effects if e.get("type") == "map_corrupt"}
                    if corrupt_entries:
                        ws = state.setdefault("world_state", {})
                        ws.setdefault("corrupt_map", {}).update(corrupt_entries)

                    map_data = plugin.session_manager.get_map_data(session_id)

                    # 地点描述中的演出效果（首次访问时触发，效果已在游戏启动时预提取）
                    if move_to and state and state.get("current_location") == move_to:
                        played = web_session.setdefault("theatrical_played_locations", [])
                        if move_to not in played:
                            loc_effects = web_session.get("location_theatrical", {}).get(move_to, [])
                            if loc_effects:
                                theatrical_effects.extend(loc_effects)
                            played.append(move_to)

                    web_session["last_workflow"] = {
                        "rule_plan": result.get("rule_plan", {}),
                        "rule_result": result.get("rule_result", {}),
                        "hard_changes": result.get("hard_changes", {}),
                        "rhythm_result": result.get("rhythm_result", {}),
                        "telemetry": result.get("telemetry", {}),
                    }
                    _persist_web_session(cookie_id)

                    # 构建骰子演出数据
                    dice_rolls = []

                    move_check = result.get("move_check_result")
                    if isinstance(move_check, dict) and move_check.get("check_type") == "skill_check":
                        dice_rolls.append({
                            "type": "skill_check",
                            "label": f"{move_check['skill']}检定（{move_check['difficulty']}）",
                            "roll": move_check["roll"],
                            "threshold": move_check["threshold"],
                            "success": move_check["success"],
                            "critical_success": move_check.get("critical_success", False),
                            "critical_failure": move_check.get("critical_failure", False),
                            "description": move_check.get("result_description", ""),
                        })
                    if location_dice:
                        dice_rolls.extend(location_dice)

                    rule_result_data = result.get("rule_result", {})
                    if rule_result_data.get("check_type") == "skill_check":
                        dice_rolls.append({
                            "type": "skill_check",
                            "label": f"{rule_result_data['skill']}检定（{rule_result_data['difficulty']}）",
                            "roll": rule_result_data["roll"],
                            "threshold": rule_result_data["threshold"],
                            "success": rule_result_data["success"],
                            "critical_success": rule_result_data.get("critical_success", False),
                            "critical_failure": rule_result_data.get("critical_failure", False),
                            "description": rule_result_data.get("result_description", ""),
                        })
                    sancheck_data = result.get("sancheck_result")
                    if sancheck_data:
                        dice_rolls.append({
                            "type": "sancheck",
                            "label": f"SAN检定（{sancheck_data['entity_name']}）",
                            "roll": sancheck_data["roll"],
                            "threshold": sancheck_data["threshold"],
                            "success": sancheck_data["success"],
                            "san_loss": sancheck_data["san_loss"],
                            "description": f"{'成功' if sancheck_data['success'] else '失败'}, SAN {'不变' if sancheck_data['san_loss'] == 0 else str(sancheck_data['san_loss'])}",
                        })

                    # Phase 5: 注入交互对象/NPC 的硬 placeholder 骰子。
                    if target_kind == "object" and target_key:
                        obj_dice = web_session.get("object_dice", {})
                        cached = obj_dice.pop(target_key, [])
                        if cached:
                            dice_rolls.extend(cached)
                            web_session["object_dice"] = obj_dice
                            _persist_web_session(cookie_id)
                    if target_kind == "npc" and target_key:
                        npc_dice = web_session.get("npc_dice", {})
                        cached = npc_dice.pop(target_key, [])
                        if cached:
                            dice_rolls.extend(cached)
                            web_session["npc_dice"] = npc_dice
                            _persist_web_session(cookie_id)

                    _action_results[cookie_id] = {
                        "success": True,
                        "narrative": narrative,
                        "theatrical_effects": theatrical_effects,
                        "dice_rolls": dice_rolls,
                        "rule_plan": result.get("rule_plan", {}),
                        "rule_result": result["rule_result"],
                        "hard_changes": result.get("hard_changes", {}),
                        "rhythm_result": result["rhythm_result"],
                        "telemetry": result.get("telemetry", {}),
                        "game_state": _serialize_state(state),
                        "map_data": map_data,
                        "ending_phase": plugin.session_manager.get_ending_phase(session_id),
                        "ending_id": plugin.session_manager.get_ending_id(session_id),
                        "ending_display": plugin.session_manager.get_ending_display(session_id),
                        "game_over": plugin.session_manager.is_game_over(session_id),
                    }

                except Exception as e:
                    logger.error(f"[AITRPG WebUI] 处理行动出错: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    progress_payload = plugin.get_action_progress_payload(session_id)
                    _action_results[cookie_id] = {
                        "error": str(e),
                        "telemetry": progress_payload.get("progress", {}),
                        "partial_results": progress_payload.get("partial_results", {}),
                        "retry_from_hint": progress_payload.get("retry_from_hint"),
                        "can_retry": progress_payload.get("can_retry", False),
                    }

        asyncio.ensure_future(_run_action())
        return jsonify({"status": "accepted", "job_id": cookie_id}), 202

    @app.route("/trpg/api/progress", methods=["GET"])
    async def api_progress():
        """获取当前动作处理进度"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"progress": {}})

        web_session = _restore_web_session(cookie_id)
        session_id = web_session["session_id"]
        return jsonify(plugin.get_action_progress_payload(session_id))

    @app.route("/trpg/api/action/result", methods=["GET"])
    async def api_action_result():
        """获取后台异步 action/retry 的结果"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        result = _action_results.get(cookie_id)
        if result is None:
            return jsonify({"status": "pending"}), 202
        return jsonify(result)

    @app.route("/trpg/api/retry", methods=["POST"])
    async def api_retry():
        """重试失败的AI处理步骤，立即返回202，结果从 /api/action/result 取"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _restore_web_session(cookie_id)
        if not web_session["game_started"]:
            return jsonify({"error": "游戏尚未开始"}), 400

        lock = _session_locks[cookie_id]
        if lock.locked():
            return jsonify({"error": "正在处理上一个行动，请稍候"}), 429

        data = await request.get_json() or {}
        retry_from = data.get("retry_from")
        if not retry_from:
            retry_from = plugin.get_action_progress_payload(web_session["session_id"]).get("retry_from_hint") or "rule"

        if retry_from not in ("rule", "rhythm", "narrative", "story"):
            return jsonify({"error": f"无效的重试起点: {retry_from}"}), 400

        custom_api = data.get("custom_api") or None
        merge_mode = bool(data.get("merge_mode", False))

        session_id = web_session["session_id"]
        _action_results.pop(cookie_id, None)

        async def _run_retry():
            async with lock:
                try:
                    # 调用核心处理流程（带重试参数）
                    result = await plugin._process_action_core(
                        session_id=session_id,
                        player_input="",  # Will be overridden from cache
                        history=[],       # Will be overridden from cache
                        move_to=None,
                        retry_from=retry_from,
                        custom_api=custom_api,
                        merge_mode=merge_mode,
                    )

                    narrative = result["narrative_result"]["narrative"]
                    summary = result["narrative_result"]["summary"]

                    # 解析演出效果标签
                    parsed = parse_theatrical_tags(narrative)
                    narrative = parsed["clean_text"]
                    theatrical_effects = parsed["effects"]

                    # 同步到 AstrBot 对话记录
                    cache = plugin._last_action_cache.get(session_id, {})
                    player_input = cache.get("player_input", "")
                    move_to = cache.get("move_to")
                    conv_id = web_session.get("conv_id")
                    display_input = player_input or (f"[移动到{move_to}]" if move_to else "")
                    if conv_id:
                        try:
                            conv_mgr = plugin.context.conversation_manager
                            await conv_mgr.add_message_pair(
                                cid=conv_id,
                                user_message=plugin._build_history_message("user", display_input),
                                assistant_message=plugin._build_history_message("assistant", narrative)
                            )
                            await plugin._compress_history_if_needed(
                                conv_mgr=conv_mgr,
                                session_id=session_id,
                                conv_id=conv_id,
                            )
                        except Exception as e:
                            logger.warning(f"[AITRPG WebUI] 重试时同步对话记录失败: {e}")

                    # 更新 web 会话历史（重试时替换最后一组消息而非追加）
                    if web_session["chat_messages"] and web_session["chat_messages"][-1].get("role") == "assistant":
                        # 移除上次失败的错误消息
                        last_msg = web_session["chat_messages"][-1]
                        if last_msg.get("content", "").startswith("处理出错:") or last_msg.get("content") == "网络错误，请重试。":
                            web_session["chat_messages"].pop()

                    web_session["history"].append(plugin._build_history_message("user", display_input))
                    web_session["history"].append(plugin._build_history_message("assistant", narrative))

                    if len(web_session["history"]) > 40:
                        web_session["history"] = web_session["history"][-40:]

                    if display_input:
                        web_session["chat_messages"].append({"role": "user", "content": display_input})
                    web_session["chat_messages"].append({"role": "assistant", "content": narrative})

                    # 同步 narrative_history
                    plugin.session_manager.add_narrative_summary(session_id, display_input, narrative, summary)

                    state = plugin.session_manager.get_session(session_id)

                    # 持久化 map_corrupt 效果
                    corrupt_entries = {e["target"]: e["content"] for e in theatrical_effects if e.get("type") == "map_corrupt"}
                    if corrupt_entries:
                        ws = state.setdefault("world_state", {})
                        ws.setdefault("corrupt_map", {}).update(corrupt_entries)

                    map_data = plugin.session_manager.get_map_data(session_id)

                    web_session["last_workflow"] = {
                        "rule_plan": result.get("rule_plan", {}),
                        "rule_result": result.get("rule_result", {}),
                        "hard_changes": result.get("hard_changes", {}),
                        "rhythm_result": result.get("rhythm_result", {}),
                        "telemetry": result.get("telemetry", {}),
                    }
                    _persist_web_session(cookie_id)

                    # 构建骰子演出数据
                    dice_rolls = []
                    rule_result_data = result.get("rule_result", {})
                    if rule_result_data.get("check_type") == "skill_check":
                        dice_rolls.append({
                            "type": "skill_check",
                            "label": f"{rule_result_data['skill']}检定（{rule_result_data['difficulty']}）",
                            "roll": rule_result_data["roll"],
                            "threshold": rule_result_data["threshold"],
                            "success": rule_result_data["success"],
                            "critical_success": rule_result_data.get("critical_success", False),
                            "critical_failure": rule_result_data.get("critical_failure", False),
                            "description": rule_result_data.get("result_description", ""),
                        })
                    sancheck_data = result.get("sancheck_result")
                    if sancheck_data:
                        dice_rolls.append({
                            "type": "sancheck",
                            "label": f"SAN检定（{sancheck_data['entity_name']}）",
                            "roll": sancheck_data["roll"],
                            "threshold": sancheck_data["threshold"],
                            "success": sancheck_data["success"],
                            "san_loss": sancheck_data["san_loss"],
                            "description": f"{'成功' if sancheck_data['success'] else '失败'}, SAN {'不变' if sancheck_data['san_loss'] == 0 else str(sancheck_data['san_loss'])}",
                        })

                    _action_results[cookie_id] = {
                        "success": True,
                        "narrative": narrative,
                        "theatrical_effects": theatrical_effects,
                        "dice_rolls": dice_rolls,
                        "rule_plan": result.get("rule_plan", {}),
                        "rule_result": result["rule_result"],
                        "hard_changes": result.get("hard_changes", {}),
                        "rhythm_result": result["rhythm_result"],
                        "telemetry": result.get("telemetry", {}),
                        "game_state": _serialize_state(state),
                        "map_data": map_data,
                        "ending_phase": plugin.session_manager.get_ending_phase(session_id),
                        "ending_id": plugin.session_manager.get_ending_id(session_id),
                        "ending_display": plugin.session_manager.get_ending_display(session_id),
                        "game_over": plugin.session_manager.is_game_over(session_id),
                    }

                except Exception as e:
                    logger.error(f"[AITRPG WebUI] 重试处理出错: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    progress_payload = plugin.get_action_progress_payload(session_id)
                    _action_results[cookie_id] = {
                        "error": str(e),
                        "telemetry": progress_payload.get("progress", {}),
                        "partial_results": progress_payload.get("partial_results", {}),
                        "retry_from_hint": progress_payload.get("retry_from_hint"),
                        "can_retry": progress_payload.get("can_retry", False),
                    }

        asyncio.ensure_future(_run_retry())
        return jsonify({"status": "accepted", "job_id": cookie_id}), 202

    @app.route("/trpg/api/resume", methods=["POST"])
    async def api_resume():
        """显式恢复已持久化的中断存档。"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        summary = _get_save_summary_payload(cookie_id)
        if not summary.get("has_save"):
            return jsonify({"error": "没有可恢复的存档"}), 404

        web_session = _restore_web_session(cookie_id)
        if not web_session.get("game_started") or not plugin.session_manager.has_session(web_session["session_id"]):
            return jsonify({"error": "恢复存档失败"}), 500

        return jsonify(_build_runtime_state_payload(web_session))

    @app.route("/trpg/api/state", methods=["GET"])
    async def api_state():
        """获取当前游戏状态"""
        cookie_id = _get_cookie_id()
        if not cookie_id:
            return jsonify({"error": "无有效会话"}), 400

        web_session = _get_or_create_web_session(cookie_id)
        return jsonify(_build_runtime_state_payload(web_session))

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
            if session_id in plugin._action_progress:
                del plugin._action_progress[session_id]
            _action_results.pop(cookie_id, None)

        return jsonify({"success": True})

    @app.route("/trpg/api/character-card/professions", methods=["GET"])
    async def api_character_card_professions():
        """返回 COC7 职业列表，前端用于渲染职业 select 与公式预览。"""
        try:
            data = character_card_module.load_professions()
            return jsonify({"professions": list(data.values())})
        except Exception as e:
            logger.warning(f"[AITRPG] 加载职业失败: {e}")
            return jsonify({"error": "professions data unavailable"}), 500

    @app.route("/trpg/api/character-card/skills-base", methods=["GET"])
    async def api_character_card_skills_base():
        """返回 COC7 技能基础值表（部分值是公式串，前端按 attributes 派生）。"""
        try:
            data = character_card_module.load_skills_base()
            return jsonify({"skills_base": data})
        except Exception as e:
            logger.warning(f"[AITRPG] 加载技能基础值失败: {e}")
            return jsonify({"error": "skills base unavailable"}), 500

    @app.route("/trpg/api/character-card/roll-attributes", methods=["GET"])
    async def api_character_card_roll_attributes():
        """按 COC7 公式 roll 一组 8 属性 + LUCK。每次返回不同结果。"""
        attrs = character_card_module.roll_attributes()
        return jsonify({"attributes": attrs})

    @app.route("/trpg/api/character-card/random", methods=["GET"])
    async def api_character_card_random():
        """生成一张完整、合法、随机的 COC7 角色卡。不持久化。

        可选 query 参数: era ∈ {modern, 1920s, custom}, 决定 names/locations/backgrounds 池。
        缺省 / 非法值 → 'modern' (默认现代风, 避免现代卡出现 '上海公共租界' 这种 1920s 地名)。
        """
        try:
            era_raw = (request.args.get("era") or "").strip().lower()
            era = era_raw if era_raw in ("modern", "1920s", "custom") else "modern"
            profs = character_card_module.load_professions()
            skills_base = character_card_module.load_skills_base()
            random_pool = character_card_module.load_random_pool()
            card = character_card_module.roll_random_card(profs, skills_base, random_pool, era=era)
            ok, errs, normalized = character_card_module.validate_card(card, profs, skills_base)
            if not ok:
                logger.warning(f"[AITRPG] 随机卡未通过 validate_card 兜底: {errs}")
                return jsonify({"error": "random card failed validation", "errors": errs}), 500
            return jsonify({"card": normalized})
        except Exception as e:
            logger.warning(f"[AITRPG] 生成随机卡失败: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/trpg/api/character-card/template", methods=["GET"])
    async def api_character_card_template():
        """返回带 _hint 注释的空白卡模板，供玩家拿到 AI 离线填写后再导入。"""
        try:
            profs = character_card_module.load_professions()
            skills_base = character_card_module.load_skills_base()
            template = character_card_module.make_blank_template_with_hints(profs, skills_base)
            return jsonify({"template": template})
        except Exception as e:
            logger.warning(f"[AITRPG] 生成模板失败: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/trpg/api/character-card/validate", methods=["POST"])
    async def api_character_card_validate():
        """校验一张 COC7 卡。返回 {ok, errors, normalized} —— derived 总是按公式重算覆盖。"""
        data = await request.get_json()
        if not isinstance(data, dict):
            return jsonify({"ok": False, "errors": ["body must be JSON object"], "normalized": {}}), 400
        try:
            profs = character_card_module.load_professions()
            skills_base = character_card_module.load_skills_base()
            ok, errs, normalized = character_card_module.validate_card(data, profs, skills_base)
            return jsonify({"ok": ok, "errors": errs, "normalized": normalized})
        except Exception as e:
            logger.warning(f"[AITRPG] 校验卡失败: {e}")
            return jsonify({"ok": False, "errors": [str(e)], "normalized": {}}), 500

    # 启动后台会话清理任务
    asyncio.ensure_future(_cleanup_stale_sessions())

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
    config.keep_alive_timeout = 120

    try:
        logger.info(f"[AITRPG] WebUI starting on http://0.0.0.0:{port}/trpg/")
        await serve(app, config)
        logger.info("[AITRPG] WebUI server stopped cleanly")
    except asyncio.CancelledError:
        logger.info("[AITRPG] WebUI 已停止")
        raise
    except Exception as e:
        logger.error(f"[AITRPG] WebUI server error: {e}", exc_info=True)
