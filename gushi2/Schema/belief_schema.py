"""信念与待解释事件 Schema — belief_schema
================================================

定义NPC的信念系统和待解释事件（Pending）。
信念是NPC对世界的主观认知，可能正确也可能错误。
待解释事件是NPC感知到但尚未理解的事件，需要推理层处理。

核心循环：
感知 → 产生Pending → 推理层处理Pending → 产出Knowledge/Belief → 清除Pending
"""

from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# 信念条目
# =============================================================================


@dataclass
class BeliefEntry:
    """NPC信念条目.

    NPC个人认定的"事实"——可能是正确的，也可能是偏见或错误认知。
    信念比知识更主观，改变难度更大。

    Attributes:
        belief: 信念内容（自然语言描述）.
            示例: "贵族都是自私的", "魔法是危险的", "城外的森林里藏着宝藏"
        basis: 信念依据（来源/推理过程）.
        strength: 信念强度 (0.0 - 1.0).
            越高越难改变。>0.8=根深蒂固，<0.3=容易动摇.
        timestamp: 形成时间.
        related_event_id: 关联事件ID.
        counter_evidence: 反对证据列表（积累足够多时信念可能动摇）.
    """

    belief: str = ""
    basis: str = ""                             # AI可修改: 是 | 程序可修改: 是
    strength: float = 0.5                       # AI可修改: 是 | 程序可修改: 是
    timestamp: str = ""                         # AI可修改: 否 | 程序可修改: 是
    related_event_id: Optional[str] = None      # AI可修改: 否 | 程序可修改: 是
    counter_evidence: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 是
    # 说明: belief和basis由AI（推理层）产生，程序写入。
    # strength可被后续事件影响：遇到相反证据时降低，确认证据时提高。

    def is_strong(self) -> bool:
        """判断是否为强信念（不易改变）.

        Returns:
            是否强度 >= 0.8.
        """
        return self.strength >= 0.8

    def is_weak(self) -> bool:
        """判断是否为弱信念（容易改变）.

        Returns:
            是否强度 <= 0.3.
        """
        return self.strength <= 0.3

    def add_counter_evidence(self, evidence: str) -> None:
        """添加反对证据并降低信念强度.

        Args:
            evidence: 反对证据描述.
        """
        self.counter_evidence.append(evidence)
        self.strength = max(0.0, self.strength - 0.05 * len(self.counter_evidence))

    def reinforce(self, evidence: str) -> None:
        """用支持证据加强信念.

        Args:
            evidence: 支持证据描述.
        """
        self.strength = min(1.0, self.strength + 0.05)


# =============================================================================
# 待解释事件
# =============================================================================


@dataclass
class PendingEntry:
    """待解释事件条目.

    NPC感知到了某事，但不完全理解——需要推理层来解释。
    这是推动NPC主动思考的核心机制。

    Attributes:
        event_description: 需要解释的事件描述（自然语言）.
        reason: 为什么需要解释（推理层处理后填充）.
            为空表示尚未推理。
        priority: 优先级 (1-10)，越紧急/越相关越先处理.
        timestamp: 加入时间.
        related_event_id: 关联事件ID.
        attempts: 已尝试推理的次数.
        max_attempts: 最大推理尝试次数，超过后自动清除.
    """

    event_description: str = ""
    reason: str = ""                            # AI可修改: 是 | 程序可修改: 是
    priority: int = 5                           # AI可修改: 是 | 程序可修改: 是
    timestamp: str = ""                         # AI可修改: 否 | 程序可修改: 是
    related_event_id: Optional[str] = None      # AI可修改: 否 | 程序可修改: 是
    attempts: int = 0                           # AI可修改: 否 | 程序可修改: 是
    max_attempts: int = 3                       # AI可修改: 否 | 程序可修改: 是
    # 说明: event_description由感知层产生。reason由推理层填充。
    # 程序管理attempts计数，超过max_attempts后自动清除（不再浪费推理资源）.

    def can_retry(self) -> bool:
        """判断是否可以再次尝试推理.

        Returns:
            是否还可以重试.
        """
        return self.attempts < self.max_attempts

    def mark_attempted(self) -> None:
        """标记一次推理尝试."""
        self.attempts += 1


# =============================================================================
# 推理层输入
# =============================================================================


@dataclass
class ReasoningInput:
    """推理层输入.

    由Engine构建，传递给ReasoningLayer。
    包含触发推理的NPC及其相关维护表数据。

    Attributes:
        npc_id: NPC标识.
        npc_name: NPC名称.
        npc_role: NPC角色.
        personality_summary: 性格摘要.
        intelligence: 认知能力.
        pending_events: 待解释事件列表（只取最近的N条）.
        observations: 最近观察列表（只取最近的N条）.
        memories: 最近记忆列表（只取最近的N条）.
        knowledge: 已有知识列表（只取最近的N条）.
        goals: 当前未完成目标列表.
    """

    npc_id: str = ""
    npc_name: str = ""
    npc_role: str = ""
    personality_summary: str = ""
    intelligence: int = 50
    pending_events: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)


# =============================================================================
# 单个NPC的推理结果
# =============================================================================


@dataclass
class NPCReasoning:
    """单个NPC的推理结果.

    LLM对NPC的pending事件进行推理后产出的结果。
    产出新的知识、信念，并标记哪些pending已被解释。

    Attributes:
        npc_id: NPC标识.
        new_knowledge: 推理出的新知识列表.
        new_beliefs: 新形成的信念列表.
        resolved_pending: 已解释的待解释事件描述列表.
        goals_updated: 更新后的目标列表（如因推理而改变目标）.
        reasoning_output: 推理过程简述（用于debug和日志）.
        emotion_change: 推理导致的情绪变化 — 预留扩展.
    """

    npc_id: str = ""
    new_knowledge: list[str] = field(default_factory=list)    # AI可修改: 是 | 程序可修改: 否
    new_beliefs: list[str] = field(default_factory=list)      # AI可修改: 是 | 程序可修改: 否
    resolved_pending: list[str] = field(default_factory=list) # AI可修改: 是 | 程序可修改: 否
    goals_updated: list[str] = field(default_factory=list)    # AI可修改: 是 | 程序可修改: 否
    reasoning_output: str = ""                                 # AI可修改: 是 | 程序可修改: 否
    emotion_change: str = ""                                   # AI可修改: 是 | 程序可修改: 否
    # 说明: NPCReasoning完全由AI（推理层LLM）产生。
    # 程序负责将其传给更新层写入维护表。

    def has_results(self) -> bool:
        """判断推理是否产生了有意义的结果.

        Returns:
            是否有新知识/信念/目标更新/情绪变化.
        """
        return bool(
            self.new_knowledge
            or self.new_beliefs
            or self.resolved_pending
            or self.goals_updated
        )


# =============================================================================
# 推理层输出
# =============================================================================


@dataclass
class ReasoningResult:
    """推理层输出.

    包含所有需要推理的NPC的推理结果。

    Attributes:
        npc_reasonings: 每个NPC的推理结果列表.
        skipped_npc_ids: 因无pending/无观察而跳过推理的NPC ID列表.
    """

    npc_reasonings: list[NPCReasoning] = field(default_factory=list)
    skipped_npc_ids: list[str] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是

    def get_for_npc(self, npc_id: str) -> NPCReasoning | None:
        """获取指定NPC的推理结果.

        Args:
            npc_id: NPC标识.

        Returns:
            NPCReasoning或None.
        """
        for r in self.npc_reasonings:
            if r.npc_id == npc_id:
                return r
        return None

    def has_any_results(self) -> bool:
        """判断是否有任何NPC产生了推理结果.

        Returns:
            是否有推理结果.
        """
        return any(r.has_results() for r in self.npc_reasonings)
