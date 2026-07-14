"""NPC 数据模型 — 包含所有维护表.

所有维护表的修改只能通过 UpdateLayer 完成。
其他Layer禁止直接修改此数据结构。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# =============================================================================
# 枚举定义
# =============================================================================


class Emotion(str, Enum):
    """NPC情绪枚举."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    FEARFUL = "fearful"
    SURPRISED = "surprised"
    DISGUSTED = "disgusted"
    CURIOUS = "curious"
    SUSPICIOUS = "suspicious"


class NPCRole(str, Enum):
    """NPC角色类型."""

    COMMONER = "commoner"
    MERCHANT = "merchant"
    GUARD = "guard"
    NOBLE = "noble"
    SCHOLAR = "scholar"
    PEASANT = "peasant"
    PRIEST = "priest"
    SOLDIER = "soldier"
    OUTLAW = "outlaw"
    CUSTOM = "custom"


class RelationshipType(str, Enum):
    """关系类型."""

    STRANGER = "stranger"
    ACQUAINTANCE = "acquaintance"
    FRIEND = "friend"
    CLOSE_FRIEND = "close_friend"
    LOVER = "lover"
    FAMILY = "family"
    RIVAL = "rival"
    ENEMY = "enemy"
    ALLY = "ally"
    SUPERIOR = "superior"
    SUBORDINATE = "subordinate"


# =============================================================================
# 维护表条目数据类
# =============================================================================


@dataclass
class MemoryEntry:
    """NPC记忆条目.

    Attributes:
        content: 记忆内容（简洁描述）.
        timestamp: 记忆形成的时间.
        importance: 重要程度 (1-10).
        related_event_id: 关联事件ID.
    """

    content: str
    timestamp: str = ""
    importance: int = 1
    related_event_id: Optional[str] = None


@dataclass
class KnowledgeEntry:
    """NPC知识条目.

    Attributes:
        fact: 知识事实.
        source: 知识来源（亲眼所见/听说/推理）.
        confidence: 确信度 (0.0 - 1.0).
        timestamp: 获取时间.
    """

    fact: str
    source: str = "unknown"
    confidence: float = 1.0
    timestamp: str = ""


@dataclass
class BeliefEntry:
    """NPC信念条目 — NPC个人认定的既定事实.

    Attributes:
        belief: 信念内容.
        basis: 信念依据.
        strength: 信念强度 (0.0 - 1.0)，越高越难改变.
        timestamp: 形成时间.
    """

    belief: str
    basis: str = ""
    strength: float = 0.5
    timestamp: str = ""


@dataclass
class ObservationEntry:
    """NPC观察条目.

    Attributes:
        observation: 观察到的内容.
        target_id: 观察对象ID（NPC/物品/事件）.
        timestamp: 观察时间.
        detail_level: 细节程度 (1-5).
    """

    observation: str
    target_id: str = ""
    timestamp: str = ""
    detail_level: int = 1


@dataclass
class PendingEntry:
    """待解释事件条目.

    Attributes:
        event_description: 需要解释的事件描述.
        reason: 为什么需要解释（当前为空，待推理层填充）.
        priority: 优先级 (1-10).
        timestamp: 加入时间.
    """

    event_description: str
    reason: str = ""
    priority: int = 5
    timestamp: str = ""


@dataclass
class RelationshipValue:
    """NPC对另一个NPC的关系值.

    Attributes:
        target_npc_id: 目标NPC ID.
        relationship_type: 关系类型.
        favorability: 好感度 (-100 到 100).
        trust: 信任度 (0-100).
        history: 重大关系事件记录.
    """

    target_npc_id: str
    relationship_type: RelationshipType = RelationshipType.STRANGER
    favorability: int = 0
    trust: int = 50
    history: list[str] = field(default_factory=list)


@dataclass
class Item:
    """物品数据类.

    Attributes:
        item_id: 物品唯一标识.
        name: 物品名称.
        description: 物品描述.
        quantity: 持有数量.
        is_equipped: 是否已装备.
    """

    item_id: str
    name: str
    description: str = ""
    quantity: int = 1
    is_equipped: bool = False


@dataclass
class CurrentStateData:
    """NPC当前状态.

    Attributes:
        location: 当前位置.
        emotion: 当前情绪.
        action: 当前正在执行的动作.
        health: 健康状态 (0-100).
        is_alive: 是否存活.
    """

    location: str = ""
    emotion: Emotion = Emotion.NEUTRAL
    action: str = ""
    health: int = 100
    is_alive: bool = True


@dataclass
class GoalEntry:
    """NPC目标条目.

    Attributes:
        goal: 目标描述.
        priority: 优先级 (1-10).
        progress: 进展描述.
        is_completed: 是否已完成.
        timestamp: 设定时间.
    """

    goal: str
    priority: int = 5
    progress: str = ""
    is_completed: bool = False
    timestamp: str = ""


@dataclass
class PersonalityData:
    """NPC性格特征.

    使用大五人格模型简化版.

    Attributes:
        openness: 开放性 (0-100).
        conscientiousness: 尽责性 (0-100).
        extraversion: 外向性 (0-100).
        agreeableness: 宜人性 (0-100).
        neuroticism: 神经质 (0-100).
        intelligence: 认知能力 (0-100). 影响感知细节量、推理深度、回应复杂度.
            0-30: 愚钝 — 感知粗糙、推理简单、回应短促
            31-60: 普通 — 正常感知、常规推理、日常回应
            61-85: 聪明 — 细节丰富、深入推理、措辞讲究
            86-100: 天才 — 洞察隐藏信息、多步推理、优雅表达
        traits: 额外性格标签.
    """

    openness: int = 50
    conscientiousness: int = 50
    extraversion: int = 50
    agreeableness: int = 50
    neuroticism: int = 50
    intelligence: int = 50
    traits: list[str] = field(default_factory=list)


# =============================================================================
# NPC 主体数据类
# =============================================================================


@dataclass
class NPC:
    """NPC数据类 — 包含所有维护表.

    所有维护表的修改只能通过 UpdateLayer 完成。
    其他Layer禁止直接修改NPC的任何属性。

    Attributes:
        npc_id: NPC唯一标识.
        name: NPC名称.
        role: NPC角色类型.
        personality: 性格特征.
        current_state: 当前状态.
        memory: 记忆表.
        knowledge: 知识表.
        belief: 信念表.
        observation: 观察表.
        pending_reason: 待解释事件表.
        relationship: 关系表 (target_npc_id -> RelationshipValue).
        inventory: 持有物表.
        goal: 目标表.
    """

    npc_id: str
    name: str
    role: NPCRole = NPCRole.COMMONER

    # 核心属性
    personality: PersonalityData = field(default_factory=PersonalityData)
    current_state: CurrentStateData = field(default_factory=CurrentStateData)

    # 维护表
    memory: list[MemoryEntry] = field(default_factory=list)
    knowledge: list[KnowledgeEntry] = field(default_factory=list)
    belief: list[BeliefEntry] = field(default_factory=list)
    observation: list[ObservationEntry] = field(default_factory=list)
    pending_reason: list[PendingEntry] = field(default_factory=list)
    relationship: dict[str, RelationshipValue] = field(default_factory=dict)
    inventory: list[Item] = field(default_factory=list)
    goal: list[GoalEntry] = field(default_factory=list)

    def has_pending(self) -> bool:
        """判断是否有待处理事件."""
        return len(self.pending_reason) > 0

    def get_relationship(self, target_id: str) -> Optional[RelationshipValue]:
        """获取对目标NPC的关系."""
        return self.relationship.get(target_id)

    def knows(self, fact: str) -> bool:
        """判断NPC是否知道某个事实（在知识或记忆中）."""
        for k in self.knowledge:
            if fact.lower() in k.fact.lower():
                return True
        for m in self.memory:
            if fact.lower() in m.content.lower():
                return True
        return False

    def believes(self, belief: str) -> bool:
        """判断NPC是否持有某个信念."""
        for b in self.belief:
            if belief.lower() in b.belief.lower():
                return True
        return False
