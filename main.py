from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

from .game_state.session_manager import SessionManager
from .ai_layers.rule_ai import RuleAI
from .ai_layers.rhythm_ai import RhythmAI
from .ai_layers.narrative_ai import NarrativeAI

import json


@register("aitrpg", "TheEyeoftheUniverse", "AI驱动TRPG跑团系统", "1.1.0")
class AITRPGPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.session_manager = None
        self.rule_ai = None
        self.rhythm_ai = None
        self.narrative_ai = None

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
        self.session_manager = SessionManager()
        self.session_manager.module_data = self.session_manager._load_module(module_name)

        # 初始化三层AI（传入提供商名称、配置和模组数据）
        self.rule_ai = RuleAI(self.context, rule_ai_provider, config)
        self.rhythm_ai = RhythmAI(self.context, rhythm_ai_provider, config, self.session_manager.module_data)
        self.narrative_ai = NarrativeAI(self.context, narrative_ai_provider, config, self.session_manager.module_data)

        logger.info("[AITRPG] 插件初始化完成！")

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
            self.session_manager.create_session(session_id)
            self.session_manager.load_module_for_session(session_id, selected["filename"])

            # 同步各AI层的模组数据
            self.rhythm_ai.module_data = self.session_manager.module_data
            self.narrative_ai.module_data = self.session_manager.module_data

            # 新建独立对话，避免污染之前的上下文
            conv_mgr = self.context.conversation_manager
            conv_id = await conv_mgr.new_conversation(session_id)
            await conv_mgr.switch_conversation(session_id, conv_id)
            logger.info(f"[AITRPG] 为会话 {session_id} 新建对话 {conv_id}")

            # 将开场白作为第一组固定对话写入history
            opening = selected["opening"]
            await conv_mgr.add_message_pair(
                cid=conv_id,
                user_message={"role": "user", "content": "缓缓苏醒"},
                assistant_message={"role": "assistant", "content": opening}
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

    async def _process_player_action(self, session_id: str, player_input: str):
        """三层AI处理流程"""
        logger.info(f"[AITRPG] 开始处理玩家行动: {player_input}")

        # 获取当前游戏状态
        state = self.session_manager.get_session(session_id)

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

        # === 第一步：规则AI - 意图解析 ===
        logger.info("[AITRPG] 调用规则AI进行意图解析...")
        intent = await self.rule_ai.parse_intent(player_input)
        logger.info(f"[AITRPG] 意图解析结果: {intent}")

        # === 第二步：节奏AI - 对比模组 + 控制节奏 ===
        logger.info("[AITRPG] 调用节奏AI进行剧情控制...")
        rhythm_result = await self.rhythm_ai.process(
            intent=intent,
            player_input=player_input,
            game_state=state,
            history=history
        )
        logger.info(f"[AITRPG] 节奏AI结果: {rhythm_result}")

        # === 第三步：规则AI - 执行判定 ===
        logger.info("[AITRPG] 调用规则AI进行规则判定...")
        rule_result = await self.rule_ai.judge(
            rhythm_result=rhythm_result,
            player_state=state["player"]
        )
        logger.info(f"[AITRPG] 规则判定结果: {rule_result}")

        # 根据判定结果从物品字段中提取SAN损失并更新状态
        if rule_result.get("success"):
            object_context = rhythm_result.get("object_context") or {}
            san_cost = object_context.get("san_cost", 0)
            if san_cost:
                state["player"]["san"] += san_cost
            clue_name = object_context.get("name") or (list(object_context.keys())[0] if object_context else None)
            if clue_name:
                rhythm_result["world_changes"] = rhythm_result.get("world_changes") or {}
                rhythm_result["world_changes"].setdefault("clues", [])
                if clue_name not in rhythm_result["world_changes"]["clues"]:
                    rhythm_result["world_changes"]["clues"].append(clue_name)

        # 更新游戏状态
        self.session_manager.update_state(session_id, rhythm_result)

        # === 第四步：文案AI - 生成叙述 ===
        logger.info("[AITRPG] 调用文案AI生成叙述...")
        narrative_result = await self.narrative_ai.generate(
            rule_result=rule_result,
            rhythm_result=rhythm_result,
            narrative_history=state.get("narrative_history", []),
            history=history
        )
        logger.info(f"[AITRPG] 文案生成完成")

        # 将用户输入和完整文案写入AstrBot对话历史
        await conv_mgr.add_message_pair(
            cid=conv_id,
            user_message={"role": "user", "content": player_input},
            assistant_message={"role": "assistant", "content": narrative_result["narrative"]}
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

    async def _compress_history_if_needed(self, conv_mgr, session_id: str, conv_id: str):
        """超过10轮后，将最老的一轮assistant内容替换为对应的小总结"""
        conversation = await conv_mgr.get_conversation(session_id, conv_id)
        if not conversation or not conversation.history:
            return

        history = json.loads(conversation.history)

        # 统计assistant消息索引
        assistant_idxs = [i for i, m in enumerate(history) if m.get("role") == "assistant"]
        if len(assistant_idxs) <= 10:
            return

        # 找到最老的尚未压缩的轮次
        oldest_idx = assistant_idxs[0]
        current_content = history[oldest_idx].get("content", "")
        if current_content.startswith("[摘要]"):
            return

        # 从session_manager的narrative_history中取对应轮次的summary
        state = self.session_manager.get_session(session_id)
        narrative_history = list(state.get("narrative_history", []))
        # assistant_idxs[0]对应第1轮，narrative_history[0]对应第1轮
        round_index = 0  # 第一条未压缩的assistant消息对应第0个记录
        if round_index < len(narrative_history):
            entry = narrative_history[round_index]
            summary = entry.get("summary", "") if isinstance(entry, dict) else str(entry)
        else:
            summary = current_content[:30]

        history[oldest_idx]["content"] = f"[摘要] {summary}"
        conversation.history = json.dumps(history, ensure_ascii=False)
        await conv_mgr.update_conversation(conv_id, conversation)
        logger.info(f"[AITRPG] 已压缩第{oldest_idx}条历史记录为摘要")

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

    async def terminate(self):
        """插件销毁"""
        logger.info("[AITRPG] 插件正在卸载...")
