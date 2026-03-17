from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

from .game_state.session_manager import SessionManager
from .ai_layers.rule_ai import RuleAI
from .ai_layers.rhythm_ai import RhythmAI
from .ai_layers.narrative_ai import NarrativeAI

import json


@register("aitrpg", "TheEyeoftheUniverse", "AI驱动TRPG跑团系统", "1.0.0")
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

        # 初始化三层AI（传入提供商名称）
        self.rule_ai = RuleAI(self.context, rule_ai_provider)
        self.rhythm_ai = RhythmAI(self.context, rhythm_ai_provider)
        self.narrative_ai = NarrativeAI(self.context, narrative_ai_provider)

        logger.info("[AITRPG] 插件初始化完成！")

    @filter.command("trpg")
    async def start_game(self, event: AstrMessageEvent):
        """开始TRPG游戏"""
        session_id = event.session_id

        # 检查是否已有游戏进行中
        if self.session_manager.has_session(session_id):
            yield event.plain_result("游戏已在进行中！发送 /trpg_reset 可以重新开始。")
            return

        # 创建新游戏会话
        self.session_manager.create_session(session_id)

        # 获取开场白
        opening = self.session_manager.get_opening()

        yield event.plain_result(f"🎲 AI驱动TRPG跑团系统\n\n{opening}\n\n请输入你的行动...")

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

        # === 第一步：规则AI - 意图解析 ===
        logger.info("[AITRPG] 调用规则AI进行意图解析...")
        intent = await self.rule_ai.parse_intent(player_input)
        logger.info(f"[AITRPG] 意图解析结果: {intent}")

        # === 第二步：节奏AI - 对比模组 + 控制节奏 ===
        logger.info("[AITRPG] 调用节奏AI进行剧情控制...")
        rhythm_result = await self.rhythm_ai.process(
            intent=intent,
            player_input=player_input,
            game_state=state
        )
        logger.info(f"[AITRPG] 节奏AI结果: {rhythm_result}")

        # 更新游戏状态
        self.session_manager.update_state(session_id, rhythm_result)

        # === 第三步：规则AI - 执行判定 ===
        logger.info("[AITRPG] 调用规则AI进行规则判定...")
        rule_result = await self.rule_ai.judge(
            rhythm_result=rhythm_result,
            player_state=state["player"]
        )
        logger.info(f"[AITRPG] 规则判定结果: {rule_result}")

        # === 第四步：文案AI - 生成叙述 ===
        logger.info("[AITRPG] 调用文案AI生成叙述...")
        narrative_result = await self.narrative_ai.generate(
            rule_result=rule_result,
            rhythm_result=rhythm_result,
            narrative_history=state.get("narrative_history", [])
        )
        logger.info(f"[AITRPG] 文案生成完成")

        # 更新文案历史
        self.session_manager.add_narrative_summary(
            session_id,
            narrative_result["summary"]
        )

        # 格式化输出
        output = self._format_output(
            narrative=narrative_result["narrative"],
            rule_result=rule_result,
            rhythm_result=rhythm_result,
            state=state
        )

        return output

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
        output.append(f"  进度: {int(rhythm_result.get('current_progress', 0) * 100)}%")
        output.append(f"  场景: {state.get('current_location', 'N/A')}")
        if rhythm_result.get('hint'):
            output.append(f"  💡 提示: {rhythm_result['hint']}")
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
        lines.append(f"📈 进度: {int(state.get('progress', 0) * 100)}%")
        lines.append("")
        lines.append(f"🔍 已发现线索: {len(world.get('clues_found', []))}")
        if world.get('clues_found'):
            for clue in world['clues_found']:
                lines.append(f"  • {clue}")

        return "\n".join(lines)

    async def terminate(self):
        """插件销毁"""
        logger.info("[AITRPG] 插件正在卸载...")
