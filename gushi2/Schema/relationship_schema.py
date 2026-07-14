"""关系与好感度 Schema — relationship_schema
==============================================

定义NPC之间的关系数据。
关系是双向或单向的——NPC_A对NPC_B的好感度不一定等于NPC_B对NPC_A的好感度。

范围：
- 关系类型枚举
- 单向关系值（好感度、信任度、关系历史）
- 好感度变更规则
- 全局关系图（所有NPC间的关系矩阵）
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class RelationshipType(str, Enum):
    """关系类型枚举.

    定义两个NPC之间的社会关系类型。
    关系类型影响NPC之间的互动模式和信息共享意愿。
    """

    STRANGER = "stranger"               # 陌生人 — 互不认识
    ACQUAINTANCE = "acquaintance"       # 熟人 — 有点头之交
    FRIEND = "friend"                   # 朋友
    CLOSE_FRIEND = "close_friend"       # 密友
    LOVER = "lover"                     # 恋人
    FAMILY = "family"                   # 家人
    RIVAL = "rival"                     # 竞争对手
    ENEMY = "enemy"                     # 敌人
    ALLY = "ally"                       # 盟友
    SUPERIOR = "superior"               # 上级
    SUBORDINATE = "subordinate"         # 下属
    MENTOR = "mentor"                   # 导师 — 预留扩展
    STUDENT = "student"                 # 学生 — 预留扩展


class FavorabilityTier(str, Enum):
    """好感度等级枚举.

    将连续的好感度数值映射到离散等级，便于LLM理解和判断。
    """

    HOSTILE = "hostile"             # 敌对 (-100 到 -61)
    UNFRIENDLY = "unfriendly"       # 不友善 (-60 到 -21)
    NEUTRAL = "neutral"             # 中立 (-20 到 20)
    FRIENDLY = "friendly"           # 友善 (21 到 60)
    CLOSE = "close"                 # 亲密 (61 到 100)


# =============================================================================
# 关系值
# =============================================================================


@dataclass
class RelationshipValue:
    """NPC对另一个NPC的单向关系值.

    NPC_A对NPC_B的关系由NPC_A的关系表中存储。
    NPC_B对NPC_A的关系由NPC_B的关系表中存储。
    两者独立，不一定对称。

    Attributes:
        target_npc_id: 目标NPC ID（关系的对方）.
        relationship_type: 关系类型.
        favorability: 好感度 (-100 到 100).
            负数=厌恶，0=中立，正数=好感。
        trust: 信任度 (0-100).
            0=完全不信任，100=完全信任。影响信息共享和委托意愿。
        respect: 敬重度 (0-100) — 预留扩展.
            影响对目标意见的重视程度。
        fear: 恐惧度 (0-100) — 预留扩展.
            影响在被威胁时的行为。
        history: 重大关系事件记录列表.
        first_met_tick: 首次相遇的tick数.
        last_interaction_tick: 最后一次互动的tick数.
    """

    target_npc_id: str = ""
    relationship_type: RelationshipType = RelationshipType.STRANGER
    favorability: int = 0                       # AI可修改: 否 | 程序可修改: 是
    trust: int = 50                             # AI可修改: 否 | 程序可修改: 是
    respect: int = 30                           # AI可修改: 否 | 程序可修改: 是
    fear: int = 0                               # AI可修改: 否 | 程序可修改: 是
    history: list[str] = field(default_factory=list)   # AI可修改: 否 | 程序可修改: 是
    first_met_tick: int = 0                     # AI可修改: 否 | 程序可修改: 是
    last_interaction_tick: int = 0              # AI可修改: 否 | 程序可修改: 是
    # 说明: 好感度和信任度的修改只能通过更新层。
    # AI可以通过推理/回应建议改变，但实际修改由更新层执行。
    # 变更通常由回应层LLM判断"此对话对关系的影响"，交给更新层执行。

    def get_favorability_tier(self) -> FavorabilityTier:
        """获取好感度等级.

        Returns:
            FavorabilityTier枚举值.
        """
        if self.favorability <= -61:
            return FavorabilityTier.HOSTILE
        elif self.favorability <= -21:
            return FavorabilityTier.UNFRIENDLY
        elif self.favorability <= 20:
            return FavorabilityTier.NEUTRAL
        elif self.favorability <= 60:
            return FavorabilityTier.FRIENDLY
        else:
            return FavorabilityTier.CLOSE

    def change_favorability(self, delta: int, reason: str = "") -> None:
        """变更好感度.

        自动钳制到 [-100, 100] 范围。

        Args:
            delta: 变更量（正=增加好感，负=减少好感）.
            reason: 变更原因（记录到history）.
        """
        old_value = self.favorability
        self.favorability = max(-100, min(100, self.favorability + delta))
        if reason:
            change_desc = f"好感度 {old_value}→{self.favorability} ({delta:+d}): {reason}"
            self.history.append(change_desc)

    def change_trust(self, delta: int, reason: str = "") -> None:
        """变更信任度.

        自动钳制到 [0, 100] 范围。

        Args:
            delta: 变更量.
            reason: 变更原因.
        """
        self.trust = max(0, min(100, self.trust + delta))
        if reason:
            self.history.append(f"信任度变化 ({delta:+d}): {reason}")

    def get_summary(self) -> str:
        """获取关系摘要（给LLM用）.

        Returns:
            关系描述字符串.
        """
        return (
            f"{self.relationship_type.value} "
            f"好感{self.favorability}({self.get_favorability_tier().value}) "
            f"信任{self.trust}"
        )


# =============================================================================
# 好感度变更记录
# =============================================================================


@dataclass
class FavorabilityChange:
    """好感度变更请求.

    由回应层/推理层产生，传给更新层执行。
    不应由AI直接修改RelationshipValue。

    Attributes:
        source_npc_id: 好感度变化的主体NPC.
        target_npc_id: 目标NPC.
        favorability_delta: 好感度变化量.
        trust_delta: 信任度变化量.
        reason: 变更原因.
        tick: 发生的tick.
    """

    source_npc_id: str = ""
    target_npc_id: str = ""
    favorability_delta: int = 0                 # AI可修改: 是 | 程序可修改: 否
    trust_delta: int = 0                        # AI可修改: 是 | 程序可修改: 否
    reason: str = ""                            # AI可修改: 是 | 程序可修改: 否
    tick: int = 0                               # AI可修改: 否 | 程序可修改: 是
    # 说明: AI判断关系变化的原因和幅度，程序执行实际修改。


# =============================================================================
# 全局关系图
# =============================================================================


@dataclass
class RelationshipGraph:
    """全局关系图.

    所有NPC之间关系的完整矩阵。
    用于快速查询两个NPC之间的关系、查找某个NPC的所有关系等。

    Attributes:
        edges: 关系边字典.
            key = "source_npc_id->target_npc_id"
            value = RelationshipValue.
        factions: 阵营/势力关系 — 预留扩展.
    """

    edges: dict[str, RelationshipValue] = field(default_factory=dict)
    factions: dict[str, list[str]] = field(default_factory=dict)
    # 说明: factions是预留字段，用于后续实现阵营系统。

    def get_relationship(self, from_npc_id: str, to_npc_id: str) -> RelationshipValue | None:
        """获取两个NPC之间的单向关系.

        Args:
            from_npc_id: 发起方NPC ID.
            to_npc_id: 目标NPC ID.

        Returns:
            RelationshipValue或None.
        """
        key = f"{from_npc_id}->{to_npc_id}"
        return self.edges.get(key)

    def set_relationship(self, from_npc_id: str, relationship: RelationshipValue) -> None:
        """设置单向关系.

        Args:
            from_npc_id: 发起方NPC ID.
            relationship: 关系值.
        """
        key = f"{from_npc_id}->{relationship.target_npc_id}"
        self.edges[key] = relationship

    def get_all_relationships_of(self, npc_id: str) -> dict[str, RelationshipValue]:
        """获取某NPC的所有关系.

        Args:
            npc_id: NPC ID.

        Returns:
            目标NPC ID → RelationshipValue 的字典.
        """
        result: dict[str, RelationshipValue] = {}
        prefix = f"{npc_id}->"
        for key, value in self.edges.items():
            if key.startswith(prefix):
                result[value.target_npc_id] = value
        return result

    def get_mutual_relationships(self, npc_id_a: str, npc_id_b: str) -> tuple[
        RelationshipValue | None, RelationshipValue | None
    ]:
        """获取两个NPC之间的双向关系.

        Args:
            npc_id_a: NPC A的ID.
            npc_id_b: NPC B的ID.

        Returns:
            (A对B的关系, B对A的关系) 的元组.
        """
        return (
            self.get_relationship(npc_id_a, npc_id_b),
            self.get_relationship(npc_id_b, npc_id_a),
        )
