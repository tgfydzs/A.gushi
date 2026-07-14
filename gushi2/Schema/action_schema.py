"""行动与物品 Schema — action_schema
============================================

定义NPC的行动、目标、物品持有和更新记录。
此Schema涵盖了NPC维护表中的：
- form4: 生活目标表
- form8: 持有物表
- form10: 表更新成本

注意：
- 所有修改只能通过更新层完成
- 目标驱动NPC的长期行为，物品影响NPC的能力和选择
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class GoalPriority(int, Enum):
    """目标优先级枚举."""

    TRIVIAL = 1         # 琐事 — 可有可无
    LOW = 3             # 低优先级
    NORMAL = 5          # 普通
    HIGH = 7            # 高优先级
    CRITICAL = 9        # 关键 — 生死攸关
    URGENT = 10         # 紧急 — 必须立刻处理


class GoalCategory(str, Enum):
    """目标分类枚举."""

    SURVIVAL = "survival"           # 生存 — 食物、安全、健康
    SOCIAL = "social"               # 社交 — 建立/维护关系
    PROFESSION = "profession"       # 职业 — 工作相关
    PERSONAL = "personal"           # 个人 — 爱好、追求
    QUEST = "quest"                 # 任务 — 特定剧情任务
    LONG_TERM = "long_term"         # 长期 — 人生目标
    REACTIVE = "reactive"           # 反应性 — 因事件而产生
    SECRET = "secret"               # 秘密 — 不为人知的目标


class ItemType(str, Enum):
    """物品类型枚举."""

    WEAPON = "weapon"               # 武器
    ARMOR = "armor"                 # 防具
    CONSUMABLE = "consumable"       # 消耗品（药水、食物等）
    KEY_ITEM = "key_item"           # 关键物品（剧情相关）
    MONEY = "money"                 # 金钱
    DOCUMENT = "document"           # 文档/信件
    TOOL = "tool"                   # 工具
    TREASURE = "treasure"           # 财宝
    MATERIAL = "material"           # 材料 — 预留扩展
    MAGIC_ITEM = "magic_item"       # 魔法物品 — 预留扩展
    CUSTOM = "custom"               # 自定义


class ActionType(str, Enum):
    """NPC动作类型枚举."""

    IDLE = "idle"                   # 空闲
    MOVING = "moving"               # 移动中
    WORKING = "working"             # 工作中
    TALKING = "talking"             # 交谈中
    FIGHTING = "fighting"           # 战斗中
    TRADING = "trading"             # 交易中
    RESTING = "resting"             # 休息中
    INVESTIGATING = "investigating" # 调查中
    HIDING = "hiding"               # 躲藏中 — 预留扩展
    CASTING = "casting"             # 施法中 — 预留扩展


# =============================================================================
# 目标条目
# =============================================================================


@dataclass
class GoalEntry:
    """NPC目标条目.

    驱动NPC的长期行为。目标是NPC做出选择和回应的核心驱动力。

    Attributes:
        goal: 目标描述（自然语言）.
            示例: "找到可靠的冒险者", "确保城门安全", "生意兴隆"
        priority: 优先级 (1-10).
        category: 目标分类.
        progress: 进展描述（自然语言）.
        is_completed: 是否已完成.
        is_abandoned: 是否已放弃.
        timestamp: 设定时间.
        parent_goal_id: 父目标ID（如果这个目标是另一个目标的子目标）.
        prerequisites: 前置条件列表（完成这些目标后才能开始此目标）.
    """

    goal: str = ""
    priority: int = 5                           # AI可修改: 是 | 程序可修改: 是
    category: GoalCategory = GoalCategory.PERSONAL  # AI可修改: 是 | 程序可修改: 是
    progress: str = ""                          # AI可修改: 是 | 程序可修改: 是
    is_completed: bool = False                  # AI可修改: 否 | 程序可修改: 是
    is_abandoned: bool = False                  # AI可修改: 否 | 程序可修改: 是
    timestamp: str = ""                         # AI可修改: 否 | 程序可修改: 是
    parent_goal_id: str = ""                    # AI可修改: 否 | 程序可修改: 是
    prerequisites: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 是
    # 说明: goal内容由AI产生（推理层/回应层），程序写入。
    # priority和category可由AI判断调整。
    # is_completed和is_abandoned由程序管理，AI通过completed_goals标记建议。

    def is_active(self) -> bool:
        """判断目标是否仍在进行中.

        Returns:
            是否未完成且未放弃.
        """
        return not self.is_completed and not self.is_abandoned


# =============================================================================
# 物品与持有物
# =============================================================================


@dataclass
class Item:
    """物品数据类.

    NPC持有或世界中的物品。

    Attributes:
        item_id: 物品唯一标识.
        name: 物品名称.
        description: 物品描述.
        item_type: 物品类型.
        quantity: 持有数量.
        is_equipped: 是否已装备.
        is_usable: 是否可使用.
        value: 价值（货币单位）.
        owner_npc_id: 所有者NPC ID（空表示无主）.
        location: 物品所在位置（当不在NPC身上时）.
    """

    item_id: str = ""
    name: str = ""
    description: str = ""                       # AI可修改: 是 | 程序可修改: 是(初始化)
    item_type: ItemType = ItemType.CUSTOM       # AI可修改: 否 | 程序可修改: 是(初始化)
    quantity: int = 1                           # AI可修改: 否 | 程序可修改: 是
    is_equipped: bool = False                   # AI可修改: 否 | 程序可修改: 是
    is_usable: bool = True                      # AI可修改: 否 | 程序可修改: 是(初始化)
    value: int = 0                              # AI可修改: 否 | 程序可修改: 是(初始化)
    owner_npc_id: str = ""                      # AI可修改: 否 | 程序可修改: 是
    location: str = ""                          # AI可修改: 否 | 程序可修改: 是
    # 说明: 物品属性由程序管理。AI可以通过事件建议物品转移（如"把剑给XXX"），
    # 实际转移由更新层执行。


@dataclass
class Inventory:
    """NPC持有物集合.

    管理NPC的所有物品。

    Attributes:
        items: 物品列表.
        max_capacity: 最大容量（物品类型数）.
        max_total_quantity: 最大总数量.
    """

    items: list[Item] = field(default_factory=list)
    max_capacity: int = 20                      # AI可修改: 否 | 程序可修改: 是
    max_total_quantity: int = 100               # AI可修改: 否 | 程序可修改: 是

    def add_item(self, item: Item) -> bool:
        """添加物品（如已有同ID则叠加数量）.

        Args:
            item: 要添加的物品.

        Returns:
            是否添加成功.
        """
        for existing in self.items:
            if existing.item_id == item.item_id:
                existing.quantity += item.quantity
                return True
        if len(self.items) >= self.max_capacity:
            return False
        self.items.append(item)
        return True

    def remove_item(self, item_id: str, quantity: int = 1) -> bool:
        """移除物品.

        Args:
            item_id: 物品ID.
            quantity: 移除数量.

        Returns:
            是否移除成功.
        """
        for item in self.items:
            if item.item_id == item_id:
                if item.quantity <= quantity:
                    self.items.remove(item)
                else:
                    item.quantity -= quantity
                return True
        return False

    def has_item(self, item_id: str) -> bool:
        """判断是否拥有指定物品.

        Args:
            item_id: 物品ID.

        Returns:
            是否拥有.
        """
        return any(i.item_id == item_id for i in self.items)

    def get_item(self, item_id: str) -> Item | None:
        """获取指定物品.

        Args:
            item_id: 物品ID.

        Returns:
            Item或None.
        """
        for item in self.items:
            if item.item_id == item_id:
                return item
        return None

    def get_equipped_items(self) -> list[Item]:
        """获取所有已装备的物品.

        Returns:
            已装备物品列表.
        """
        return [i for i in self.items if i.is_equipped]


# =============================================================================
# NPC动作记录
# =============================================================================


@dataclass
class NPCAction:
    """NPC动作记录.

    记录NPC执行的具体动作。

    Attributes:
        npc_id: 执行动作的NPC ID.
        action_type: 动作类型.
        description: 动作描述（自然语言）.
        target_id: 动作目标ID.
        location: 动作发生地点.
        timestamp: 动作时间.
        result: 动作结果描述.
    """

    npc_id: str = ""
    action_type: ActionType = ActionType.IDLE   # AI可修改: 是 | 程序可修改: 否
    description: str = ""                       # AI可修改: 是 | 程序可修改: 否
    target_id: str = ""                         # AI可修改: 是 | 程序可修改: 否
    location: str = ""                          # AI可修改: 是 | 程序可修改: 否
    timestamp: str = ""                         # AI可修改: 否 | 程序可修改: 是
    result: str = ""                            # AI可修改: 是 | 程序可修改: 否
    # 说明: NPCAction由AI产生（回应层/推理层），程序记录。


# =============================================================================
# 更新记录与成本
# =============================================================================


@dataclass
class NPCUpdateRecord:
    """单个NPC的更新记录.

    记录本轮对NPC维护表的所有修改，用于日志和回滚。

    Attributes:
        npc_id: NPC标识.
        updated_tables: 被更新的维护表名称列表.
        added_memories: 新增记忆内容列表.
        added_observations: 新增观察内容列表.
        added_knowledge: 新增知识内容列表.
        added_pending: 新增待解释事件列表.
        added_beliefs: 新增信念列表.
        updated_relationships: 被更新关系的目标NPC ID列表.
        added_goals: 新增目标列表.
        completed_goals: 已完成目标列表.
        items_added: 新增物品列表.
        items_removed: 移除物品列表.
    """

    npc_id: str = ""
    updated_tables: list[str] = field(default_factory=list)            # AI可修改: 否 | 程序可修改: 是
    added_memories: list[str] = field(default_factory=list)            # AI可修改: 否 | 程序可修改: 是
    added_observations: list[str] = field(default_factory=list)        # AI可修改: 否 | 程序可修改: 是
    added_knowledge: list[str] = field(default_factory=list)           # AI可修改: 否 | 程序可修改: 是
    added_pending: list[str] = field(default_factory=list)             # AI可修改: 否 | 程序可修改: 是
    added_beliefs: list[str] = field(default_factory=list)             # AI可修改: 否 | 程序可修改: 是
    updated_relationships: list[str] = field(default_factory=list)     # AI可修改: 否 | 程序可修改: 是
    added_goals: list[str] = field(default_factory=list)               # AI可修改: 否 | 程序可修改: 是
    completed_goals: list[str] = field(default_factory=list)           # AI可修改: 否 | 程序可修改: 是
    items_added: list[str] = field(default_factory=list)               # AI可修改: 否 | 程序可修改: 是
    items_removed: list[str] = field(default_factory=list)             # AI可修改: 否 | 程序可修改: 是
    # 说明: NPCUpdateRecord完全由程序（更新层）产生，AI不参与。


@dataclass
class UpdateCost:
    """表更新成本（流程图中的 form10）.

    追踪每个NPC维护表的更新成本，用于性能优化和调试。

    Attributes:
        npc_id: NPC标识.
        table_name: 表名（memory/knowledge/belief/observation等）.
        entry_count: 当前条目数.
        last_update_tick: 最后一次更新的tick.
        updates_this_tick: 本轮更新次数.
        total_updates: 总更新次数.
        avg_cost_ms: 平均每次更新耗时（毫秒）.
    """

    npc_id: str = ""
    table_name: str = ""                        # AI可修改: 否 | 程序可修改: 是
    entry_count: int = 0                        # AI可修改: 否 | 程序可修改: 是
    last_update_tick: int = 0                   # AI可修改: 否 | 程序可修改: 是
    updates_this_tick: int = 0                  # AI可修改: 否 | 程序可修改: 是
    total_updates: int = 0                      # AI可修改: 否 | 程序可修改: 是
    avg_cost_ms: float = 0.0                    # AI可修改: 否 | 程序可修改: 是
    # 说明: 更新成本用于监控性能，当某表过于频繁更新时可触发优化策略。


@dataclass
class UpdateCostTracker:
    """更新成本追踪器.

    管理所有NPC所有维护表的更新成本。

    Attributes:
        costs: (npc_id, table_name) → UpdateCost 的映射.
    """

    costs: dict[str, UpdateCost] = field(default_factory=dict)

    def record_update(self, npc_id: str, table_name: str,
                      entry_count: int, tick: int, cost_ms: float = 0.0) -> None:
        """记录一次表更新.

        Args:
            npc_id: NPC ID.
            table_name: 表名.
            entry_count: 更新后的条目数.
            tick: 当前tick.
            cost_ms: 本次更新耗时.
        """
        key = f"{npc_id}:{table_name}"
        if key not in self.costs:
            self.costs[key] = UpdateCost(npc_id=npc_id, table_name=table_name)
        cost = self.costs[key]
        cost.entry_count = entry_count
        cost.last_update_tick = tick
        cost.updates_this_tick += 1
        cost.total_updates += 1
        if cost.total_updates > 0:
            cost.avg_cost_ms = (
                (cost.avg_cost_ms * (cost.total_updates - 1) + cost_ms)
                / cost.total_updates
            )

    def reset_tick_counts(self) -> None:
        """重置本轮更新计数（每个新tick开始时调用）."""
        for cost in self.costs.values():
            cost.updates_this_tick = 0

    def get_hot_tables(self, threshold: int = 5) -> list[UpdateCost]:
        """获取本轮更新过于频繁的表.

        Args:
            threshold: 频繁更新阈值.

        Returns:
            updates_this_tick >= threshold 的UpdateCost列表.
        """
        return [c for c in self.costs.values() if c.updates_this_tick >= threshold]
