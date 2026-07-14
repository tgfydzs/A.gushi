"""NPC核心标识与状态 Schema — npc_schema
============================================

定义NPC的核心身份、性格、当前状态。
不包含维护表（记忆/知识/信念等），那些定义在各自的Schema文件中。

注意：
- 所有NPC维护表的修改只能通过更新层完成
- 其他层禁止直接修改NPC的任何属性
- 性格数据在NPC初始化后一般不变（除非有重大事件改变性格）
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class Emotion(str, Enum):
    """NPC情绪枚举.

    情绪影响NPC的回应风格和感知倾向.
    """

    NEUTRAL = "neutral"         # 平静
    HAPPY = "happy"             # 高兴
    SAD = "sad"                 # 悲伤
    ANGRY = "angry"             # 愤怒
    FEARFUL = "fearful"         # 恐惧
    SURPRISED = "surprised"     # 惊讶
    DISGUSTED = "disgusted"     # 厌恶
    CURIOUS = "curious"         # 好奇
    SUSPICIOUS = "suspicious"   # 怀疑
    ANXIOUS = "anxious"         # 焦虑 — 预留扩展
    EXCITED = "excited"         # 兴奋 — 预留扩展
    CONFUSED = "confused"       # 困惑 — 预留扩展


class NPCRole(str, Enum):
    """NPC角色类型枚举.

    角色类型影响NPC的初始知识、行为模式和社会地位.
    """

    COMMONER = "commoner"       # 平民
    MERCHANT = "merchant"       # 商人
    GUARD = "guard"             # 守卫
    NOBLE = "noble"             # 贵族
    SCHOLAR = "scholar"         # 学者
    PEASANT = "peasant"         # 农民
    PRIEST = "priest"           # 牧师/僧侣
    SOLDIER = "soldier"         # 士兵
    OUTLAW = "outlaw"           # 盗贼/法外之徒
    ADVENTURER = "adventurer"   # 冒险者 — 预留扩展
    MAGE = "mage"               # 法师 — 预留扩展
    ROYALTY = "royalty"         # 王室成员 — 预留扩展
    CUSTOM = "custom"           # 自定义


class HealthStatus(str, Enum):
    """NPC健康状态枚举."""

    HEALTHY = "healthy"             # 健康
    INJURED = "injured"             # 轻伤
    WOUNDED = "wounded"             # 重伤
    CRITICAL = "critical"           # 濒危
    SICK = "sick"                   # 生病
    POISONED = "poisoned"           # 中毒 — 预留扩展
    CURSED = "cursed"               # 诅咒 — 预留扩展


class Gender(str, Enum):
    """NPC性别枚举 — 预留扩展."""

    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


# =============================================================================
# NPC身份标识
# =============================================================================


@dataclass
class NPCIdentity:
    """NPC身份标识.

    NPC的不变身份信息。

    Attributes:
        npc_id: NPC唯一标识（程序内部使用）.
        name: NPC显示名称.
        role: NPC角色类型.
        gender: 性别.
        age: 年龄（-1表示未知）.
        title: 头衔/称号（如"铁卫"）.
        faction: 所属势力/阵营.
        background: 背景故事简述.
        appearance: 外貌描述 — 预留扩展.
    """

    npc_id: str = ""
    name: str = ""
    role: NPCRole = NPCRole.COMMONER
    gender: Gender = Gender.OTHER       # AI可修改: 否 | 程序可修改: 是(初始化)
    age: int = -1                        # AI可修改: 否 | 程序可修改: 是(初始化)
    title: str = ""                      # AI可修改: 是(通过事件) | 程序可修改: 是
    faction: str = ""                    # AI可修改: 是(通过事件) | 程序可修改: 是
    background: str = ""                 # AI可修改: 否 | 程序可修改: 是(初始化)
    appearance: str = ""                 # AI可修改: 否 | 程序可修改: 是(初始化)
    # 说明: background和appearance在初始化时设定，一般不变。
    # faction和title可能因剧情事件改变（受勋、叛变等），由更新层修改。


# =============================================================================
# 性格数据
# =============================================================================


@dataclass
class PersonalityData:
    """NPC性格特征.

    使用大五人格（Big Five）模型简化版。
    性格在NPC创建时设定，一般不变。极端事件可能触发微小变化。

    Attributes:
        openness: 开放性 (0-100).
            高：好奇、富有想象力、喜欢新体验
            低：保守、实际、喜欢惯例
        conscientiousness: 尽责性 (0-100).
            高：有组织、自律、可靠
            低：随性、灵活、冲动
        extraversion: 外向性 (0-100).
            高：社交、活跃、健谈
            低：内向、安静、含蓄
        agreeableness: 宜人性 (0-100).
            高：合作、友善、信任他人
            低：竞争、怀疑、直言不讳
        neuroticism: 神经质 (0-100).
            高：焦虑、情绪不稳定、容易紧张
            低：冷静、情绪稳定、抗压
        intelligence: 认知能力 (0-100).
            影响感知细节量、推理深度、回应复杂度。
            0-30:  愚钝 — 感知粗糙、推理简单、回应短促
            31-60: 普通 — 正常感知、常规推理、日常回应
            61-85: 聪明 — 细节丰富、深入推理、措辞讲究
            86-100: 天才 — 洞察隐藏信息、多步推理、优雅表达
        charisma: 魅力 (0-100) — 预留扩展.
            影响NPC对他人好感度变化的速度和幅度
        traits: 额外性格标签列表（如 ["热情", "见多识广", "警觉"]）.
    """

    openness: int = 50              # AI可修改: 否 | 程序可修改: 是(初始化)
    conscientiousness: int = 50     # AI可修改: 否 | 程序可修改: 是(初始化)
    extraversion: int = 50          # AI可修改: 否 | 程序可修改: 是(初始化)
    agreeableness: int = 50         # AI可修改: 否 | 程序可修改: 是(初始化)
    neuroticism: int = 50           # AI可修改: 否 | 程序可修改: 是(初始化)
    intelligence: int = 50          # AI可修改: 否 | 程序可修改: 是(初始化)
    charisma: int = 50              # AI可修改: 否 | 程序可修改: 是(初始化)
    traits: list[str] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是(初始化)
    # 说明: 性格数据在NPC创建时设定，之后通常不变。
    # 如果后续需要"重大事件改变性格"的功能，通过更新层以事件驱动方式修改。

    def get_intelligence_tier(self) -> str:
        """获取认知能力等级描述.

        Returns:
            "low", "medium", "high", 或 "genius".
        """
        if self.intelligence <= 30:
            return "low"
        elif self.intelligence <= 60:
            return "medium"
        elif self.intelligence <= 85:
            return "high"
        else:
            return "genius"

    def get_summary(self) -> str:
        """获取性格摘要（给LLM用的简短描述）.

        Returns:
            性格描述字符串.
        """
        parts = [
            f"开放:{self.openness}",
            f"尽责:{self.conscientiousness}",
            f"外向:{self.extraversion}",
            f"宜人:{self.agreeableness}",
            f"神经质:{self.neuroticism}",
            f"认知:{self.intelligence}/100",
        ]
        if self.traits:
            parts.append(f"特质:{','.join(self.traits)}")
        return " ".join(parts)


# =============================================================================
# 当前状态
# =============================================================================


@dataclass
class CurrentStateData:
    """NPC当前状态.

    表示NPC在游戏世界中的实时状态。
    此数据结构会频繁被更新层修改。

    Attributes:
        location: 当前位置（如 "辉石城·蜂蜜酒馆"）.
        emotion: 当前情绪.
        action: 当前正在执行的动作描述（如 "擦拭吧台"）.
        health_status: 健康状态.
        health_value: 生命值 (0-100)，100=完全健康，0=死亡.
        is_alive: 是否存活.
        is_conscious: 是否清醒（昏迷时不参与感知/推理/回应）.
        busy_level: 忙碌程度 (0-100)，越高越容易忽略周围事件.
    """

    location: str = ""                          # AI可修改: 否 | 程序可修改: 是
    emotion: Emotion = Emotion.NEUTRAL          # AI可修改: 否 | 程序可修改: 是
    action: str = ""                            # AI可修改: 否 | 程序可修改: 是
    health_status: HealthStatus = HealthStatus.HEALTHY  # AI可修改: 否 | 程序可修改: 是
    health_value: int = 100                     # AI可修改: 否 | 程序可修改: 是
    is_alive: bool = True                       # AI可修改: 否 | 程序可修改: 是
    is_conscious: bool = True                   # AI可修改: 否 | 程序可修改: 是
    busy_level: int = 0                         # AI可修改: 否 | 程序可修改: 是
    # 说明: 当前状态的所有修改必须通过更新层。
    # 位置变更由事件层判断后交给更新层执行。
    # 情绪变更由回应层/推理层判断后交给更新层执行。


# =============================================================================
# NPC摘要
# =============================================================================


@dataclass
class NPCSummary:
    """NPC摘要.

    发给LLM的最小NPC上下文，减少token消耗。
    只包含LLM决策需要的关键信息。

    Attributes:
        npc_id: NPC唯一标识.
        name: NPC名称.
        role: 角色类型.
        location: 当前位置.
        emotion: 当前情绪.
        pending_count: 待处理事件数.
        goal_count: 未完成目标数.
    """

    npc_id: str = ""
    name: str = ""
    role: str = ""              # NPCRole.value
    location: str = ""
    emotion: str = ""           # Emotion.value
    pending_count: int = 0      # AI可修改: 否 | 程序可修改: 是
    goal_count: int = 0         # AI可修改: 否 | 程序可修改: 是


# =============================================================================
# NPC主体
# =============================================================================


@dataclass
class NPC:
    """NPC主体数据类.

    包含NPC的所有数据：身份、性格、状态、以及所有维护表。
    维护表的引用在此聚合，但具体结构定义在各自的Schema文件中。

    重要约束：
    - 所有维护表的修改只能通过更新层完成
    - 其他层只能读取，禁止直接写入

    Attributes:
        identity: NPC身份标识.
        personality: 性格特征.
        current_state: 当前状态.
        memory: 记忆表 — 定义见 memory_schema.MemoryEntry.
        knowledge: 知识表 — 定义见 memory_schema.KnowledgeEntry.
        observation: 观察表 — 定义见 memory_schema.ObservationEntry.
        belief: 信念表 — 定义见 belief_schema.BeliefEntry.
        pending_reason: 待解释事件表 — 定义见 belief_schema.PendingEntry.
        goal: 目标表 — 定义见 action_schema.GoalEntry.
        relationship: 关系表 — 定义见 relationship_schema.RelationshipValue.
        inventory: 持有物表 — 定义见 action_schema.Item.
    """

    # 核心标识
    identity: NPCIdentity = field(default_factory=NPCIdentity)

    # 性格与状态
    personality: PersonalityData = field(default_factory=PersonalityData)
    current_state: CurrentStateData = field(default_factory=CurrentStateData)

    # 维护表（类型标注使用 Any 避免循环引用，实际类型见各Schema文件）
    memory: list = field(default_factory=list)          # list[MemoryEntry]
    knowledge: list = field(default_factory=list)       # list[KnowledgeEntry]
    observation: list = field(default_factory=list)     # list[ObservationEntry]
    belief: list = field(default_factory=list)          # list[BeliefEntry]
    pending_reason: list = field(default_factory=list)  # list[PendingEntry]
    goal: list = field(default_factory=list)            # list[GoalEntry]
    relationship: dict = field(default_factory=dict)    # dict[str, RelationshipValue]
    inventory: list = field(default_factory=list)       # list[Item]

    # 便捷属性（兼容旧代码）
    @property
    def npc_id(self) -> str:
        """NPC唯一标识（便捷访问）."""
        return self.identity.npc_id

    @property
    def name(self) -> str:
        """NPC名称（便捷访问）."""
        return self.identity.name

    @property
    def role(self) -> NPCRole:
        """NPC角色类型（便捷访问）."""
        return self.identity.role

    # 便捷方法
    def has_pending(self) -> bool:
        """判断是否有待处理的推理事件.

        Returns:
            是否有pending事件.
        """
        return len(self.pending_reason) > 0

    def get_relationship(self, target_id: str):
        """获取对目标NPC的关系值.

        Args:
            target_id: 目标NPC的ID.

        Returns:
            RelationshipValue 或 None.
        """
        return self.relationship.get(target_id)

    def knows(self, fact: str) -> bool:
        """判断NPC是否知道某个事实（在知识或记忆中）.

        Args:
            fact: 要查询的事实.

        Returns:
            是否知道.
        """
        for k in self.knowledge:
            if fact.lower() in k.fact.lower():
                return True
        for m in self.memory:
            if fact.lower() in m.content.lower():
                return True
        return False

    def believes(self, belief: str) -> bool:
        """判断NPC是否持有某个信念.

        Args:
            belief: 要查询的信念.

        Returns:
            是否持有.
        """
        for b in self.belief:
            if belief.lower() in b.belief.lower():
                return True
        return False

    def get_summary(self) -> NPCSummary:
        """生成NPC摘要（给LLM的最小上下文）.

        Returns:
            NPCSummary对象.
        """
        return NPCSummary(
            npc_id=self.identity.npc_id,
            name=self.identity.name,
            role=self.identity.role.value,
            location=self.current_state.location,
            emotion=self.current_state.emotion.value,
            pending_count=len(self.pending_reason),
            goal_count=len([g for g in self.goal if not g.is_completed]),
        )
