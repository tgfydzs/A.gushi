"""感知系统 Schema — perception_schema
========================================

定义感知层的输入、输出和判断规则。
感知层是信息分发的闸门——所有事件必须经过感知层分发，
每个NPC只收到自己能感知到的那部分内容。

职责：
- 接收EventLayer的事件上下文表
- 用LLM判断每个NPC的感知情况
- 只将相关事件信息传递给能感知到的NPC
"""

from dataclasses import dataclass, field


# =============================================================================
# 感知层输入
# =============================================================================


@dataclass
class PerceptionInput:
    """感知层输入.

    由Engine构建，传递给PerceptionLayer。
    包含本轮所有事件和所有NPC的最小上下文。

    Attributes:
        events: 本轮事件列表（来自EventLayer）.
        npcs: NPC摘要列表（minimal context，每个NPC只包含LLM判断需要的信息）.
        world_state: 世界状态摘要.
        player_input: 玩家原始输入（用于感知摘要中包含实际对话内容）.
    """

    events: list = field(default_factory=list)          # list[Event]
    npcs: list[dict] = field(default_factory=list)       # list[NPCSimpleInfo]
    world_state: dict = field(default_factory=dict)
    player_input: str = ""


# =============================================================================
# 单个NPC的感知结果
# =============================================================================


@dataclass
class NPCPerception:
    """单个NPC的感知结果.

    LLM对每个NPC独立判断后产出的结果。
    后续层（推理层/回应层）必须根据此结果过滤——
    只给NPC看他们感知到的事件，绝不暴露整个事件表。

    Attributes:
        npc_id: NPC唯一标识.
        perceived_event_ids: 该NPC能感知到的事件ID列表.
        perceived_summary: 用自然语言描述的该NPC感知内容（给后续层LLM用）.
            必须包含玩家说的具体内容（引用关键问题），不只是概括话题。
            认知低 → 细节少，认知高 → 细节多。
        missed_event_ids: 该NPC未能感知到的事件ID列表.
        perception_confidence: 感知确信度 (0.0-1.0) — 预留扩展.
            受认知能力、距离、环境因素影响.
    """

    npc_id: str = ""
    perceived_event_ids: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 否
    perceived_summary: str = ""                                    # AI可修改: 是 | 程序可修改: 否
    missed_event_ids: list[str] = field(default_factory=list)      # AI可修改: 是 | 程序可修改: 否
    perception_confidence: float = 1.0                             # AI可修改: 是 | 程序可修改: 否
    # 说明: NPCPerception完全由AI（PerceptionLayer的LLM）产生。
    # 程序负责：检测hidden事件、确保involved_npc_ids中的NPC至少感知到事件（硬保护）。

    def has_perception(self) -> bool:
        """判断此NPC是否有可感知的内容.

        Returns:
            是否有感知到的事件.
        """
        return len(self.perceived_event_ids) > 0

    def has_no_perception(self) -> bool:
        """判断感知摘要是否表示本轮无可感知事件.

        Returns:
            是否无感知内容.
        """
        stripped = self.perceived_summary.strip()
        if not stripped:
            return True
        markers = (
            "没有感知到任何异常",
            "没有感知到任何",
            "无可见事件",
            "没有感知到",
            "本轮没有感知",
            "什么都没感知到",
        )
        for marker in markers:
            if marker in stripped:
                return True
        return False


# =============================================================================
# 感知层输出
# =============================================================================


@dataclass
class PerceptionResult:
    """感知层输出.

    包含所有NPC的感知结果，是所有后续层的信息闸门。

    Attributes:
        npc_perceptions: 每个NPC的感知结果列表.
        all_event_ids: 本轮所有事件ID（用于追踪哪些事件被处理了）.
        hidden_event_ids: 本轮被隐藏的事件ID（scope=hidden）.
    """

    npc_perceptions: list[NPCPerception] = field(default_factory=list)
    all_event_ids: list[str] = field(default_factory=list)        # AI可修改: 否 | 程序可修改: 是
    hidden_event_ids: list[str] = field(default_factory=list)     # AI可修改: 否 | 程序可修改: 是

    def get_for_npc(self, npc_id: str) -> NPCPerception | None:
        """获取指定NPC的感知结果.

        Args:
            npc_id: NPC标识.

        Returns:
            NPCPerception或None.
        """
        for p in self.npc_perceptions:
            if p.npc_id == npc_id:
                return p
        return None

    def get_perceived_npc_ids(self) -> list[str]:
        """获取本轮有感知的所有NPC ID列表.

        Returns:
            有感知内容的NPC ID列表.
        """
        return [p.npc_id for p in self.npc_perceptions if p.has_perception()]


# =============================================================================
# 感知判断规则（供LLM Prompt参考，非代码逻辑）
# =============================================================================


@dataclass
class PerceptionJudgeRule:
    """感知判断规则定义.

    这些规则嵌入在PerceptionLayer的LLM Prompt中，LLM据此判断。
    此数据结构用于文档化和可能的动态规则调整。

    Attributes:
        rule_name: 规则名称.
        description: 规则描述（自然语言）.
        priority: 优先级 (1-10)，数值越高越先判断.
        is_hard_constraint: 是否为硬约束（程序强制执行，LLM不可违反）.
    """

    rule_name: str = ""
    description: str = ""
    priority: int = 5
    is_hard_constraint: bool = False

    @staticmethod
    def default_rules() -> list["PerceptionJudgeRule"]:
        """获取默认感知判断规则集.

        Returns:
            规则列表.
        """
        return [
            PerceptionJudgeRule(
                rule_name="scope_filter",
                description="scope是首要判断因素。private: 只有involved_npcs能感知。"
                            "area: 同location能感知。public: 同location直接感知。"
                            "group: 需属于对应群体。hidden: 所有人不可见。",
                priority=10,
                is_hard_constraint=True,
            ),
            PerceptionJudgeRule(
                rule_name="location_constraint",
                description="位置是硬约束。NPC不在事件发生地 → 不可能直接感知。",
                priority=9,
                is_hard_constraint=True,
            ),
            PerceptionJudgeRule(
                rule_name="intelligence_quality",
                description="认知能力(intelligence)影响感知质量。"
                            "低(0-30): 感知粗糙、可能漏细节。"
                            "中(31-60): 正常感知。"
                            "高(61-85): 注意细节/语气/暗示。"
                            "极高(86-100): 洞察隐藏信息/推测意图。",
                priority=5,
                is_hard_constraint=False,
            ),
            PerceptionJudgeRule(
                rule_name="busy_level_filter",
                description="忙碌的NPC容易忽略周围的事。busy_level越高，感知越模糊。",
                priority=4,
                is_hard_constraint=False,
            ),
            PerceptionJudgeRule(
                rule_name="trait_boost",
                description="警觉/好奇的NPC更容易注意到异常。"
                            "性格特质含'警觉''好奇'等的NPC感知范围扩大。",
                priority=3,
                is_hard_constraint=False,
            ),
        ]
