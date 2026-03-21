from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .game_state.session_manager import SessionManager
from .ai_layers.rule_ai import RuleAI
from .ai_layers.rhythm_ai import RhythmAI
from .ai_layers.narrative_ai import NarrativeAI
from .webui.server import create_trpg_app, start_webui_server

import json
import asyncio
import copy
import time


@register("aitrpg", "TheEyeoftheUniverse", "AI驱动TRPG跑团系统", "1.2.0")
class AITRPGPlugin(Star):
    OPENING_USER_TEXT = "缓缓苏醒"
    OPENING_KIND = "opening"
    SUMMARY_PREFIX = "[摘要] "
    NARRATIVE_FULL_TURNS = 10
    PROGRESS_STEPS = [
        ("rule_intent", "规则AI · 意图解析", True),
        ("rule_adjudication", "规则AI · 动作裁定", True),
        ("rule_check", "规则层 · 执行判定", False),
        ("rhythm", "节奏AI · 节奏评估", True),
        ("narrative", "文案AI · 生成叙述", True),
    ]

    def __init__(self, context: Context):
        super().__init__(context)
        self.session_manager = None
        self.rule_ai = None
        self.rhythm_ai = None
        self.narrative_ai = None
        self._webui_task = None
        self._webui_shutdown_event = None
        self._action_progress = {}

    async def initialize(self):
        """插件初始化"""
        logger.info("[AITRPG] 正在初始化插件...")

        # 读取配置
        config = self.context.get_config()
        module_name = config.get("module_name", "default_module") or "default_module"
        rule_ai_provider = config.get("rule_ai_provider", "") or None
        rhythm_ai_provider = config.get("rhythm_ai_provider", "") or None
        narrative_ai_provider = config.get("narrative_ai_provider", "") or None

        logger.info(f"[AITRPG] 配置: 模组={module_name}, 规则AI={rule_ai_provider or '默认'}, 节奏AI={rhythm_ai_provider or '默认'}, 文案AI={narrative_ai_provider or '默认'}")

        # 初始化会话管理器（传入模组名称）
        self.session_manager = SessionManager(module_name)

        # 初始化三层AI（传入提供商名称和配置）
        self.rule_ai = RuleAI(self.context, rule_ai_provider, config)
        self.rhythm_ai = RhythmAI(self.context, rhythm_ai_provider, config)
        self.narrative_ai = NarrativeAI(self.context, narrative_ai_provider, config)

        logger.info("[AITRPG] 插件初始化完成！")

        # 启动 WebUI
        webui_port = config.get("webui_port", 9999)
        try:
            webui_port = int(webui_port)
        except (TypeError, ValueError):
            webui_port = 9999
        app = create_trpg_app(self)
        self._webui_shutdown_event = asyncio.Event()
        self._webui_task = asyncio.create_task(
            start_webui_server(app, webui_port, self._webui_shutdown_event)
        )

    def _build_history_message(self, role: str, content: str, kind: str = None) -> dict:
        message = {"role": role, "content": content}
        if kind:
            message["metadata"] = {"aitrpg_kind": kind}
            message["aitrpg_kind"] = kind
        return message

    def _build_opening_history_pair(self, opening: str):
        return (
            self._build_history_message("user", self.OPENING_USER_TEXT, kind=self.OPENING_KIND),
            self._build_history_message("assistant", opening, kind=self.OPENING_KIND),
        )

    def _get_history_message_kind(self, message: dict) -> str:
        if not isinstance(message, dict):
            return ""
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            kind = str(metadata.get("aitrpg_kind") or "").strip()
            if kind:
                return kind
        return str(message.get("aitrpg_kind") or "").strip()

    def _get_history_message_content(self, message: dict) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return str(content or "")

    def _is_opening_history_turn(self, user_message: dict, assistant_message: dict) -> bool:
        if self._get_history_message_kind(user_message) == self.OPENING_KIND:
            return True
        if self._get_history_message_kind(assistant_message) == self.OPENING_KIND:
            return True
        return self._get_history_message_content(user_message).strip() == self.OPENING_USER_TEXT

    def _build_play_history_turns(self, history: list) -> list:
        turns = []
        if not isinstance(history, list):
            return turns

        idx = 0
        formal_turn_index = 0
        while idx + 1 < len(history):
            user_message = history[idx]
            assistant_message = history[idx + 1]
            if not isinstance(user_message, dict) or not isinstance(assistant_message, dict):
                idx += 1
                continue
            if user_message.get("role") != "user" or assistant_message.get("role") != "assistant":
                idx += 1
                continue

            is_opening = self._is_opening_history_turn(user_message, assistant_message)
            if not is_opening:
                formal_turn_index += 1

            turns.append({
                "user_index": idx,
                "assistant_index": idx + 1,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "is_opening": is_opening,
                "formal_turn_index": formal_turn_index if not is_opening else None,
            })
            idx += 2

        return turns

    @filter.command("trpg")
    async def start_game(self, event: AstrMessageEvent):
        """列出模组或开始游戏"""
        session_id = event.session_id
        args = event.message_str.strip().split()

        # /trpg [序号] 选择并开始模组
        if len(args) >= 2 and args[1].isdigit():
            index = int(args[1]) - 1
            modules = self.session_manager.list_modules()

            if index < 0 or index >= len(modules):
                yield event.plain_result(f"序号无效，请输入 1~{len(modules)} 之间的数字。")
                return

            if self.session_manager.has_session(session_id):
                yield event.plain_result("游戏已在进行中！发送 /trpg_reset 可以重新开始。")
                return

            selected = modules[index]

            # 创建会话并加载模组
            self.session_manager.create_session(session_id, selected["filename"])

            # 新建独立对话，避免污染之前的上下文
            conv_mgr = self.context.conversation_manager
            conv_id = await conv_mgr.new_conversation(session_id)
            await conv_mgr.switch_conversation(session_id, conv_id)
            logger.info(f"[AITRPG] 为会话 {session_id} 新建对话 {conv_id}")

            # 将开场白作为第一组固定对话写入history
            opening = selected["opening"]
            opening_user_message, opening_assistant_message = self._build_opening_history_pair(opening)
            await conv_mgr.add_message_pair(
                cid=conv_id,
                user_message=opening_user_message,
                assistant_message=opening_assistant_message
            )

            yield event.plain_result(f"🎲 {selected['name']}\n\n{opening}\n\n请输入你的行动...")
            return

        # /trpg 列出所有可用模组
        if self.session_manager.has_session(session_id):
            yield event.plain_result("游戏已在进行中！发送 /trpg_reset 可以重新开始。")
            return

        modules = self.session_manager.list_modules()
        if not modules:
            yield event.plain_result("未找到任何模组文件。")
            return

        lines = ["🎲 AI驱动TRPG跑团系统", "", "请选择模组："]
        for i, m in enumerate(modules, 1):
            type_tag = f"（{m['module_type']}）" if m['module_type'] else ""
            lines.append(f"{i}. {m['name']} - {m['description']}{type_tag}")
        lines.append("")
        lines.append("使用 /trpg [序号] 开始游戏")

        yield event.plain_result("\n".join(lines))

    @filter.command("trpg_reset")
    async def reset_game(self, event: AstrMessageEvent):
        """重置游戏"""
        session_id = event.session_id
        self.session_manager.delete_session(session_id)
        yield event.plain_result("游戏已重置！发送 /trpg 开始新游戏。")

    @filter.command("trpg_status")
    async def show_status(self, event: AstrMessageEvent):
        """显示当前游戏状态"""
        session_id = event.session_id

        if not self.session_manager.has_session(session_id):
            yield event.plain_result("当前没有进行中的游戏。发送 /trpg 开始游戏。")
            return

        state = self.session_manager.get_session(session_id)
        status_text = self._format_status(state)
        yield event.plain_result(status_text)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_message(self, event: AstrMessageEvent):
        """处理玩家输入"""
        session_id = event.session_id

        logger.info(f"[AITRPG] on_message被调用: {event.message_str}")

        # 检查是否有游戏进行中
        if not self.session_manager.has_session(session_id):
            logger.info(f"[AITRPG] 会话{session_id}不存在，跳过")
            return

        # 检查是否是命令（命令由其他handler处理）
        if event.message_str.startswith("/"):
            logger.info(f"[AITRPG] 是命令消息，跳过")
            return

        player_input = event.message_str
        logger.info(f"[AITRPG] 开始处理玩家输入: {player_input}")

        try:
            # 显示处理中提示
            yield event.plain_result("🎲 AI正在处理你的行动...")

            # 执行三层AI处理流程
            result = await self._process_player_action(session_id, player_input)

            # 返回结果
            yield event.plain_result(result)

        except Exception as e:
            logger.error(f"[AITRPG] 处理玩家行动时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            yield event.plain_result(f"❌ 处理出错: {str(e)}")

    async def _process_action_core(self, session_id: str, player_input: str, history: list, move_to: str = None) -> dict:
        """核心三层AI处理，返回结构化结果。可被 AstrBot 消息处理和 WebUI API 共同调用。"""
        logger.info(f"[AITRPG] 开始处理玩家行动: {player_input}, move_to: {move_to}")
        self._begin_action_progress(session_id, player_input, move_to)

        try:
            # 获取当前游戏状态
            state = self.session_manager.get_session(session_id)
            module_data = self.session_manager.get_module_data(session_id)

            # === 移动处理 ===
            move_result = None
            if move_to:
                move_result = self.session_manager.move_player(session_id, move_to)
                if not move_result["success"]:
                    for step_key, _, _ in self.PROGRESS_STEPS:
                        self._skip_progress_step(session_id, step_key, "本轮未执行")
                    return self._finalize_action_result(
                        session_id,
                        {
                            "rule_plan": {},
                            "rule_result": {"check_type": None},
                            "hard_changes": {},
                            "rhythm_result": {"feasible": False, "hint": move_result["message"], "stage_assessment": "", "world_changes": {}, "soft_world_changes": {}},
                            "narrative_result": {"narrative": move_result["message"], "summary": move_result["message"]},
                            "game_state": state
                        },
                        status="completed",
                        message="移动失败",
                    )
                # 刷新状态（位置已更新）
                state = self.session_manager.get_session(session_id)

            # === 纯移动无行动 ===
            if move_to and not player_input:
                target_loc = self.session_manager.get_location_context(session_id, move_to)
                loc_name = target_loc.get("name", move_to)
                description = str(target_loc.get("runtime_description") or target_loc.get("description") or "").strip()

                if target_loc.get("entity_present"):
                    self._skip_progress_step(session_id, "rule_intent", "纯移动，不解析意图")
                    self._skip_progress_step(session_id, "rule_adjudication", "纯移动，不做动作裁定")
                    self._skip_progress_step(session_id, "rule_check", "纯移动，不触发判定")
                    self._skip_progress_step(session_id, "rhythm", "使用到场默认节奏")
                    result = await self._build_move_arrival_result(
                        session_id=session_id,
                        move_to=move_to,
                        state=state,
                        module_data=module_data,
                        history=history,
                    )
                    return self._finalize_action_result(session_id, result, message="到场叙述完成")

                # 纯移动不应被伪造成“我前往某处”再交给三层AI，否则会被误判成
                # 主动和场景内NPC搭话，导致NPC在玩家尚未开口时就开始回应。
                if description:
                    narrative = f"你来到了{loc_name}。\n\n{description}"
                else:
                    narrative = f"你来到了{loc_name}。"

                for step_key, _, _ in self.PROGRESS_STEPS:
                    self._skip_progress_step(session_id, step_key, "纯移动，无需调用AI")
                return self._finalize_action_result(
                    session_id,
                    {
                        "rule_plan": {},
                        "rule_result": {"check_type": None},
                        "hard_changes": {},
                        "rhythm_result": {"feasible": True, "hint": None, "stage_assessment": "", "world_changes": {}, "soft_world_changes": {}},
                        "narrative_result": {"narrative": narrative, "summary": f"移动到{loc_name}"},
                        "game_state": state
                    },
                    message="场景移动完成",
                )

            # === 第一步：规则AI - 意图解析 ===
            logger.info("[AITRPG] 调用规则AI进行意图解析...")
            self._start_progress_step(session_id, "rule_intent", "规则AI 正在解析意图")
            intent_trace_id = f"{session_id}:rule_intent"
            intent = await self.rule_ai.parse_intent(player_input, trace_id=intent_trace_id)
            self._finish_progress_step(session_id, "rule_intent", self.rule_ai.pop_call_metric(intent_trace_id), "意图解析完成")
            logger.info(f"[AITRPG] 意图解析结果: {intent}")

            # === 第二步：规则AI - 动作裁定与硬变化规划 ===
            logger.info("[AITRPG] 调用规则AI进行动作裁定...")
            self._start_progress_step(session_id, "rule_adjudication", "规则AI 正在裁定动作")
            adjudication_trace_id = f"{session_id}:rule_adjudication"
            rule_plan = await self.rule_ai.adjudicate_action(
                player_input=player_input,
                intent=intent,
                game_state=state,
                module_data=module_data,
                trace_id=adjudication_trace_id,
            )
            self._finish_progress_step(session_id, "rule_adjudication", self.rule_ai.pop_call_metric(adjudication_trace_id), "动作裁定完成")
            logger.info(f"[AITRPG] 动作裁定结果: {rule_plan}")

            # === 第三步：规则AI - 执行检定 ===
            logger.info("[AITRPG] 调用规则AI进行规则判定...")
            self._start_progress_step(session_id, "rule_check", "正在执行规则判定")
            rule_result = await self.rule_ai.resolve_check(
                adjudication_result=rule_plan,
                player_state=state["player"]
            )
            self._finish_progress_step(session_id, "rule_check", {}, "规则判定完成")
            logger.info(f"[AITRPG] 规则判定结果: {rule_result}")

            hard_changes = self.rule_ai.build_hard_changes(
                player_input=player_input,
                adjudication_result=rule_plan,
                rule_result=rule_result,
                game_state=state,
            )
            logger.info(f"[AITRPG] 规则层硬变化: {hard_changes}")

            # === 第四步：节奏AI - 节奏评估与软变化补充 ===
            logger.info("[AITRPG] 调用节奏AI进行节奏评估...")
            self._start_progress_step(session_id, "rhythm", "节奏AI 正在评估剧情节奏")
            preview_state = self._preview_state_with_world_changes(state, hard_changes)
            rhythm_trace_id = f"{session_id}:rhythm"
            rhythm_result = await self.rhythm_ai.process(
                intent=intent,
                player_input=player_input,
                rule_plan=rule_plan,
                rule_result=rule_result,
                game_state=preview_state,
                module_data=module_data,
                history=history,
                trace_id=rhythm_trace_id,
            )
            self._finish_progress_step(session_id, "rhythm", self.rhythm_ai.pop_call_metric(rhythm_trace_id), "节奏评估完成")
            logger.info(f"[AITRPG] 节奏AI结果: {rhythm_result}")

            soft_changes = rhythm_result.get("world_changes", {})
            soft_changes = soft_changes if isinstance(soft_changes, dict) else {}
            merged_changes = self._merge_world_changes(hard_changes, soft_changes)

            rhythm_result["feasible"] = bool(rule_plan.get("feasibility", {}).get("ok", True))
            if not rhythm_result.get("hint"):
                rhythm_result["hint"] = rule_plan.get("feasibility", {}).get("reason")
            rhythm_result["location_context"] = rule_plan.get("location_context", {})
            rhythm_result["object_context"] = rule_plan.get("object_context")
            rhythm_result["npc_context"] = rhythm_result.get("npc_context") or rule_plan.get("npc_context", {})
            rhythm_result["soft_world_changes"] = soft_changes
            rhythm_result["world_changes"] = merged_changes

            # 更新游戏状态
            self.session_manager.update_state(session_id, rhythm_result)
            state = self.session_manager.get_session(session_id)

            # === 第五步：文案AI - 生成叙述 ===
            logger.info("[AITRPG] 调用文案AI生成叙述...")
            self._start_progress_step(session_id, "narrative", "文案AI 正在生成叙述")
            narrative_trace_id = f"{session_id}:narrative"
            narrative_result = await self.narrative_ai.generate(
                player_input=player_input,
                rule_plan=rule_plan,
                rule_result=rule_result,
                rhythm_result=rhythm_result,
                narrative_history=state.get("narrative_history", []),
                history=history,
                trace_id=narrative_trace_id,
            )
            self._finish_progress_step(session_id, "narrative", self.narrative_ai.pop_call_metric(narrative_trace_id), "叙述生成完成")
            logger.info(f"[AITRPG] 文案生成完成")

            return self._finalize_action_result(
                session_id,
                {
                    "rule_plan": rule_plan,
                    "rule_result": rule_result,
                    "hard_changes": hard_changes,
                    "rhythm_result": rhythm_result,
                    "narrative_result": narrative_result,
                    "game_state": state
                },
                message="三层 AI 处理完成",
            )
        except Exception as e:
            self._complete_action_progress(session_id, status="error", message=str(e))
            raise

    async def _process_player_action(self, session_id: str, player_input: str):
        """AstrBot 消息管道的三层AI处理流程（包装器）"""

        # 获取或创建对话ID
        conv_mgr = self.context.conversation_manager
        conv_id = await conv_mgr.get_curr_conversation_id(session_id)
        if not conv_id:
            conv_id = await conv_mgr.new_conversation(session_id)
            logger.info(f"[AITRPG] 创建新对话: {conv_id}")

        # 获取对话历史
        conversation = await conv_mgr.get_conversation(session_id, conv_id)
        history = json.loads(conversation.history) if conversation and conversation.history else []
        logger.info(f"[AITRPG] 当前对话历史长度: {len(history)}")

        # 调用核心处理
        result = await self._process_action_core(session_id, player_input, history)

        narrative_result = result["narrative_result"]
        rule_result = result["rule_result"]
        rhythm_result = result["rhythm_result"]
        state = result["game_state"]

        # 将用户输入和完整文案写入AstrBot对话历史
        await conv_mgr.add_message_pair(
            cid=conv_id,
            user_message=self._build_history_message("user", player_input),
            assistant_message=self._build_history_message("assistant", narrative_result["narrative"])
        )

        # 超过10轮后，将最老的一轮assistant内容替换为对应的小总结
        await self._compress_history_if_needed(
            conv_mgr=conv_mgr,
            session_id=session_id,
            conv_id=conv_id
        )

        # 同步更新session_manager的文案历史记录
        self.session_manager.add_narrative_summary(
            session_id,
            player_input,
            narrative_result["narrative"],
            narrative_result["summary"]
        )

        logger.info(f"[AITRPG] 已更新对话历史")

        # 格式化输出
        output = self._format_output(
            narrative=narrative_result["narrative"],
            rule_result=rule_result,
            rhythm_result=rhythm_result,
            state=state
        )

        return output

    async def _build_move_arrival_result(
        self,
        session_id: str,
        move_to: str,
        state: dict,
        module_data: dict,
        history: list,
    ) -> dict:
        target_loc = self.session_manager.get_location_context(session_id, move_to)
        loc_name = target_loc.get("name", move_to)

        rule_plan = {
            "normalized_action": {
                "verb": "move",
                "target_kind": "location",
                "target_key": move_to,
                "raw_target_text": loc_name,
            },
            "feasibility": {"ok": True, "reason": None},
            "location_context": target_loc,
            "object_context": None,
            "npc_context": {},
            "check": {"required": False, "skill": None, "difficulty": "无需判定"},
            "on_success": {
                "discover_clues": [],
                "add_inventory": [],
                "remove_inventory": [],
                "set_flags": {},
                "npc_updates": {},
                "san_effect": 0,
            },
            "on_failure": {
                "discover_clues": [],
                "add_inventory": [],
                "remove_inventory": [],
                "set_flags": {},
                "npc_updates": {},
                "san_effect": 0,
            },
            "san_effect": 0,
        }
        rule_result = {
            "check_type": None,
            "success": True,
            "result_description": "移动到新场景",
        }
        use_rhythm_arrival_judgement = self.session_manager.should_use_butler_arrival_judgement(session_id, move_to)
        if use_rhythm_arrival_judgement:
            self._start_progress_step(session_id, "rhythm", "鑺傚AI 姝ｅ湪鍒ゆ柇濞佽儊瀹炰綋鐨勫埌鍦哄弽搴?")
            rhythm_trace_id = f"{session_id}:rhythm"
            rhythm_result = await self.rhythm_ai.process(
                intent={
                    "intent": "move",
                    "target_kind": "location",
                    "target_key": move_to,
                },
                player_input="",
                rule_plan=rule_plan,
                rule_result=rule_result,
                game_state=state,
                module_data=module_data,
                history=history,
                trace_id=rhythm_trace_id,
            )
            self._finish_progress_step(
                session_id,
                "rhythm",
                self.rhythm_ai.pop_call_metric(rhythm_trace_id),
                "濞佽儊瀹炰綋鍒板満鍒ゆ柇瀹屾垚",
            )
            rhythm_result = rhythm_result if isinstance(rhythm_result, dict) else {}
            soft_changes = rhythm_result.get("world_changes", {})
            soft_changes = soft_changes if isinstance(soft_changes, dict) else {}
            rhythm_result["soft_world_changes"] = soft_changes
            if soft_changes:
                self.session_manager.update_state(session_id, rhythm_result)
                state = self.session_manager.get_session(session_id)
            self._refresh_rhythm_runtime_context(session_id, rhythm_result, module_data)
        else:
            rhythm_result = self.rhythm_ai._build_base_result("", rule_plan, state, module_data)
            rhythm_result["location_context"] = target_loc
            rhythm_result["world_changes"] = {}
            rhythm_result["soft_world_changes"] = {}

        rhythm_result["arrival_mode"] = True

        self._start_progress_step(session_id, "narrative", "文案AI 正在生成到场叙述")
        narrative_trace_id = f"{session_id}:narrative"
        narrative_result = await self.narrative_ai.generate(
            player_input="",
            rule_plan=rule_plan,
            rule_result=rule_result,
            rhythm_result=rhythm_result,
            narrative_history=state.get("narrative_history", []),
            history=history,
            trace_id=narrative_trace_id,
        )
        self._finish_progress_step(session_id, "narrative", self.narrative_ai.pop_call_metric(narrative_trace_id), "到场叙述生成完成")
        if not narrative_result.get("summary"):
            narrative_result["summary"] = f"移动到{loc_name}"

        return {
            "rule_plan": rule_plan,
            "rule_result": rule_result,
            "hard_changes": {},
            "rhythm_result": rhythm_result,
            "narrative_result": narrative_result,
            "game_state": state,
        }

    async def _compress_history_if_needed(self, conv_mgr, session_id: str, conv_id: str):
        """超过10轮后，将最老的一轮assistant内容替换为对应的小总结"""
        conversation = await conv_mgr.get_conversation(session_id, conv_id)
        if not conversation or not conversation.history:
            return

        history = json.loads(conversation.history)

        # 统计assistant消息索引
        turns = [turn for turn in self._build_play_history_turns(history) if not turn.get("is_opening")]
        if len(turns) <= self.NARRATIVE_FULL_TURNS:
            return

        compressible_turns = turns[:-self.NARRATIVE_FULL_TURNS]
        target_turn = None
        for turn in compressible_turns:
            assistant_message = turn.get("assistant_message") or {}
            current_content = self._get_history_message_content(assistant_message).strip()
            if not current_content.startswith(self.SUMMARY_PREFIX):
                target_turn = turn
                break

        if not target_turn:
            return

        # 找到最老的尚未压缩的轮次
        assistant_index = target_turn["assistant_index"]
        current_content = self._get_history_message_content(history[assistant_index]).strip()
        round_index = int(target_turn["formal_turn_index"] or 0) - 1

        # 从session_manager的narrative_history中取对应轮次的summary
        state = self.session_manager.get_session(session_id)
        narrative_history = list(state.get("narrative_history", []))
        if round_index < len(narrative_history):
            entry = narrative_history[round_index]
            summary = entry.get("summary", "") if isinstance(entry, dict) else str(entry)
        else:
            summary = current_content[:30]

        summary = str(summary or "").strip() or current_content[:30]
        history[assistant_index]["content"] = f"{self.SUMMARY_PREFIX}{summary}"
        conversation.history = json.dumps(history, ensure_ascii=False)
        await conv_mgr.update_conversation(conv_id, conversation)
        logger.info(
            f"[AITRPG] compressed assistant turn={target_turn['formal_turn_index']} to summary, history_index={assistant_index}"
        )

    def _begin_action_progress(self, session_id: str, player_input: str, move_to: str = None):
        started_at = time.perf_counter()
        started_wall = time.time()
        self._action_progress[session_id] = {
            "status": "running",
            "message": "准备处理中",
            "current_step_key": None,
            "current_step_label": "准备中",
            "player_input": player_input or "",
            "move_to": move_to,
            "started_at": started_at,
            "started_at_unix": started_wall,
            "updated_at": started_at,
            "finished_at": None,
            "total_duration_ms": None,
            "steps": [
                {
                    "key": key,
                    "label": label,
                    "llm": llm,
                    "status": "pending",
                    "message": "",
                    "started_at": None,
                    "finished_at": None,
                    "duration_ms": None,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "token_source": None,
                    "call_count": 0,
                }
                for key, label, llm in self.PROGRESS_STEPS
            ],
        }

    def _get_progress_step(self, session_id: str, step_key: str):
        progress = self._action_progress.get(session_id)
        if not progress:
            return None
        for step in progress.get("steps", []):
            if step.get("key") == step_key:
                return step
        return None

    def _start_progress_step(self, session_id: str, step_key: str, message: str = ""):
        progress = self._action_progress.get(session_id)
        step = self._get_progress_step(session_id, step_key)
        if not progress or not step:
            return

        now = time.perf_counter()
        step["status"] = "running"
        step["message"] = message or step.get("message") or ""
        step["started_at"] = now
        step["finished_at"] = None
        step["duration_ms"] = None

        progress["current_step_key"] = step_key
        progress["current_step_label"] = step.get("label")
        progress["message"] = message or step.get("label")
        progress["updated_at"] = now

    def _finish_progress_step(self, session_id: str, step_key: str, metrics: dict = None, message: str = ""):
        progress = self._action_progress.get(session_id)
        step = self._get_progress_step(session_id, step_key)
        if not progress or not step:
            return

        now = time.perf_counter()
        if step.get("started_at") is None:
            step["started_at"] = now
        step["finished_at"] = now
        step["duration_ms"] = int((now - step["started_at"]) * 1000)
        step["status"] = "completed"
        if message:
            step["message"] = message

        metrics = metrics if isinstance(metrics, dict) else {}
        step["prompt_tokens"] = int(metrics.get("prompt_tokens", 0) or 0)
        step["completion_tokens"] = int(metrics.get("completion_tokens", 0) or 0)
        step["total_tokens"] = int(metrics.get("total_tokens", 0) or 0)
        step["token_source"] = metrics.get("token_source")
        step["call_count"] = int(metrics.get("call_count", 0) or 0)

        progress["updated_at"] = now

    def _skip_progress_step(self, session_id: str, step_key: str, message: str = ""):
        progress = self._action_progress.get(session_id)
        step = self._get_progress_step(session_id, step_key)
        if not progress or not step:
            return

        step["status"] = "skipped"
        step["message"] = message or step.get("message") or ""
        step["started_at"] = None
        step["finished_at"] = None
        step["duration_ms"] = 0
        progress["updated_at"] = time.perf_counter()

    def _complete_action_progress(self, session_id: str, status: str = "completed", message: str = ""):
        progress = self._action_progress.get(session_id)
        if not progress:
            return

        now = time.perf_counter()
        progress["status"] = status
        progress["message"] = message or progress.get("message") or ""
        progress["current_step_key"] = None
        progress["current_step_label"] = "已完成" if status == "completed" else "处理失败"
        progress["finished_at"] = now
        progress["updated_at"] = now
        progress["total_duration_ms"] = int((now - progress["started_at"]) * 1000)

    def get_action_progress(self, session_id: str) -> dict:
        progress = self._action_progress.get(session_id)
        if not progress:
            return {}

        snapshot = copy.deepcopy(progress)
        now = time.perf_counter()
        if snapshot.get("status") == "running":
            snapshot["total_duration_ms"] = int((now - snapshot["started_at"]) * 1000)
        for step in snapshot.get("steps", []):
            if step.get("status") == "running" and step.get("started_at") is not None:
                step["duration_ms"] = int((now - step["started_at"]) * 1000)

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        token_sources = set()
        for step in snapshot.get("steps", []):
            total_prompt_tokens += int(step.get("prompt_tokens", 0) or 0)
            total_completion_tokens += int(step.get("completion_tokens", 0) or 0)
            total_tokens += int(step.get("total_tokens", 0) or 0)
            if step.get("token_source"):
                token_sources.add(step["token_source"])

        snapshot["summary"] = {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "token_source": (
                "actual"
                if token_sources == {"actual"}
                else "mixed"
                if token_sources
                else None
            ),
        }
        return snapshot

    def _finalize_action_result(self, session_id: str, result: dict, status: str = "completed", message: str = "") -> dict:
        self._complete_action_progress(session_id, status=status, message=message)
        finalized = dict(result or {})
        finalized["telemetry"] = self.get_action_progress(session_id)
        return finalized

    def _merge_world_changes(self, hard_changes: dict, soft_changes: dict) -> dict:
        """合并规则层硬变化与节奏层软变化，避免状态更新分散在多处。"""
        hard_changes = hard_changes if isinstance(hard_changes, dict) else {}
        soft_changes = soft_changes if isinstance(soft_changes, dict) else {}

        merged = dict(hard_changes)

        for list_key in ("clues", "inventory_add", "inventory_remove"):
            merged_values = []
            for source in (hard_changes, soft_changes):
                value = source.get(list_key, [])
                if not isinstance(value, list):
                    continue
                for item in value:
                    if item and item not in merged_values:
                        merged_values.append(item)
            if merged_values:
                merged[list_key] = merged_values

        for dict_key in ("flags", "npc_locations", "npc_updates"):
            merged_dict = {}
            for source in (hard_changes, soft_changes):
                value = source.get(dict_key, {})
                if isinstance(value, dict):
                    merged_dict = self._deep_merge_dict(merged_dict, value)
            if merged_dict:
                merged[dict_key] = merged_dict

        san_delta = int(hard_changes.get("san_delta", 0) or 0) + int(soft_changes.get("san_delta", 0) or 0)
        if san_delta:
            merged["san_delta"] = san_delta

        return merged

    def _deep_merge_dict(self, base: dict, incoming: dict) -> dict:
        merged = dict(base or {})
        for key, value in (incoming or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge_dict(merged[key], value)
            elif isinstance(value, list) and isinstance(merged.get(key), list):
                if key in {"pending_questions"}:
                    merged[key] = [item for item in value if item]
                else:
                    merged[key] = list(merged[key])
                    for item in value:
                        if item not in merged[key]:
                            merged[key].append(item)
            else:
                merged[key] = value
        return merged

    def _preview_state_with_world_changes(self, state: dict, changes: dict) -> dict:
        preview_state = copy.deepcopy(state or {})
        self._apply_world_changes_to_state(preview_state, changes)
        return preview_state

    def _apply_world_changes_to_state(self, state: dict, changes: dict):
        if not isinstance(state, dict) or not isinstance(changes, dict):
            return

        world_state = state.setdefault("world_state", {})
        player_state = state.setdefault("player", {})

        if "clues" in changes:
            clues_found = world_state.setdefault("clues_found", [])
            for clue in changes["clues"]:
                if clue and clue not in clues_found:
                    clues_found.append(clue)

        if "san_delta" in changes:
            san_delta = int(changes.get("san_delta", 0) or 0)
            player_state["san"] = max(0, player_state.get("san", 0) + san_delta)

        if "inventory_add" in changes:
            inventory = player_state.setdefault("inventory", [])
            for item in changes["inventory_add"]:
                if item and item not in inventory:
                    inventory.append(item)

        if "inventory_remove" in changes:
            inventory = player_state.setdefault("inventory", [])
            for item in changes["inventory_remove"]:
                if item in inventory:
                    inventory.remove(item)

        if "flags" in changes and isinstance(changes["flags"], dict):
            world_state.setdefault("flags", {})
            world_state["flags"].update(changes["flags"])

        if "npc_locations" in changes and isinstance(changes["npc_locations"], dict):
            npc_state = world_state.setdefault("npcs", {})
            for npc_name, location in changes["npc_locations"].items():
                if npc_name in npc_state:
                    npc_state[npc_name]["location"] = location

        if "npc_updates" in changes and isinstance(changes["npc_updates"], dict):
            npc_state = world_state.setdefault("npcs", {})
            for npc_name, update in changes["npc_updates"].items():
                if npc_name not in npc_state:
                    npc_state[npc_name] = {}
                if isinstance(update, dict):
                    npc_state[npc_name] = self._deep_merge_dict(npc_state[npc_name], update)
                elif isinstance(update, str):
                    npc_state[npc_name]["location"] = update

    def _format_output(self, narrative, rule_result, rhythm_result, state):
        """格式化输出（包含AI工作流展示）"""
        output = []

        # 主要叙述
        output.append("📖 " + narrative)
        output.append("")

        # AI工作流展示（右侧面板内容，暂时在文本中展示）
        output.append("━━━━━━━━━━━━━━━━")
        output.append("🤖 AI工作流")
        output.append("")

        # 规则AI判定
        if rule_result.get("check_type"):
            output.append(f"⚙️ 规则判定:")
            output.append(f"  技能: {rule_result.get('skill', 'N/A')}")
            output.append(f"  难度: {rule_result.get('difficulty', 'N/A')}")
            output.append(f"  投骰: {rule_result.get('roll', 'N/A')}/{rule_result.get('player_skill', 'N/A')}")
            output.append(f"  结果: {'✅ 成功' if rule_result.get('success') else '❌ 失败'}")
            output.append("")

        # 节奏AI进展
        output.append(f"🎬 剧情进展:")
        output.append(f"  阶段: {rhythm_result.get('stage_assessment', 'N/A')}")
        output.append(f"  场景: {state.get('current_location', 'N/A')}")
        if not rhythm_result.get('feasible') and rhythm_result.get('hint'):
            output.append(f"  ⚠️ 限制: {rhythm_result['hint']}")
        output.append("")

        # 玩家状态
        player = state.get("player", {})
        output.append(f"👤 玩家状态:")
        output.append(f"  理智: {player.get('san', 0)}")
        output.append(f"  生命: {player.get('hp', 0)}")

        return "\n".join(output)

    def _format_status(self, state):
        """格式化状态显示"""
        player = state.get("player", {})
        world = state.get("world_state", {})

        lines = []
        lines.append("📊 游戏状态")
        lines.append("")
        lines.append(f"👤 {player.get('name', '调查员')}")
        lines.append(f"  理智: {player.get('san', 0)}")
        lines.append(f"  生命: {player.get('hp', 0)}")
        lines.append("")
        lines.append(f"📍 当前位置: {state.get('current_location', 'N/A')}")
        lines.append("")
        lines.append(f"🔍 已发现线索: {len(world.get('clues_found', []))}")
        if world.get('clues_found'):
            for clue in world['clues_found']:
                lines.append(f"  • {clue}")

        return "\n".join(lines)

    def _refresh_rhythm_runtime_context(self, session_id: str, rhythm_result: dict, module_data: dict):
        state = self.session_manager.get_session(session_id) or {}
        if not isinstance(rhythm_result, dict):
            return state
        rhythm_result["location_context"] = self.session_manager.get_location_context(session_id)
        rhythm_result["npc_context"] = self.rhythm_ai._build_scene_npc_context(state, module_data)
        return state

    async def terminate(self):
        """插件销毁"""
        logger.info("[AITRPG] 插件正在卸载...")
        # 通知 Hypercorn 优雅关闭（释放端口），而不是强制 cancel
        if self._webui_shutdown_event:
            self._webui_shutdown_event.set()
        if self._webui_task and not self._webui_task.done():
            try:
                await asyncio.wait_for(self._webui_task, timeout=5)
            except asyncio.TimeoutError:
                self._webui_task.cancel()
                try:
                    await self._webui_task
                except asyncio.CancelledError:
                    pass
            logger.info("[AITRPG] WebUI 已停止")
