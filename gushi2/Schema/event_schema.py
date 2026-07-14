"""事件定义与上下文 Schema — event_schema
============================================

定义事件层使用的所有数据结构。
事件是玩家输入或NPC动作的抽象表示。

范围：
- 事件类型和公开程度枚举
- 事件主体和事件细节
- 跨tick持久的事件上下文（CurrentEvent）
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class EventScope(str, Enum):
    """事件公开程度枚举.

    决定谁能感知到该事件。
    scope是感知层判断的首要因素。
    """

    PUBLIC = "public"       # 所有人可见 — 同区域NPC都能感知，可跨区域传播
    AREA = "area"           # 特定区域可见 — 同location的NPC能感知
    GROUP = "group"         # 特定群体可见 — 属于对应群体/faction的NPC能感知
    PRIVATE = "private"     # 仅目标NPC可见 — 只有involved_npcs里的NPC能感知
    HIDDEN = "hidden"       # 完全隐藏 — 仅GM知道，NPC和玩家都不可见


class EventType(str, Enum):
    """事件类型枚举."""

    PLAYER_ACTION = "player_action"   # 玩家动作（移动、使用物品等）
    NPC_ACTION = "npc_action"         # NPC动作
    DIALOGUE = "dialogue"             # 对话
    WORLD_EVENT = "world_event"       # 世界事件（天灾、节日等）
    DISCOVERY = "discovery"           # 发现（找到物品/地点/线索）
    COMBAT = "combat"                 # 战斗
    TRADE = "trade"                   # 交易
    STEALTH = "stealth"               # 潜行/隐秘行动 — 预留扩展
    RITUAL = "ritual"                 # 仪式/魔法 — 预留扩展
    CUSTOM = "custom"                 # 自定义


class EventStatus(str, Enum):
    """事件状态枚举 — 预留扩展.

    当前系统中事件是即时处理的无状态，但后续可支持异步事件。
    """

    PENDING = "pending"       # 待处理
    ACTIVE = "active"         # 处理中
    RESOLVED = "resolved"     # 已解决
    CANCELLED = "cancelled"   # 已取消


# =============================================================================
# 事件细节
# =============================================================================


@dataclass
class EventDetail:
    """事件中的单个细节.

    一个事件可能包含多个细节（如一句话中包含多个信息）。
    用于后续的细粒度感知判断。

    Attributes:
        detail_id: 细节唯一标识（如 "detail_0_1"）.
        action: 动作描述（如 "说话", "攻击", "给予"）.
        subject_id: 主体ID — 谁执行的（玩家/NPC ID）.
        target_id: 目标ID — 对谁执行的（NPC ID / 物品ID）.
        content: 具体内容（说的话、给的东西等）.
        location: 发生地点.
        importance: 重要程度 (1-10)，影响记忆保留概率.
    """

    detail_id: str = ""
    action: str = ""
    subject_id: str = ""            # AI可修改: 是 | 程序可修改: 否
    target_id: str = ""             # AI可修改: 是 | 程序可修改: 否
    content: str = ""               # AI可修改: 是 | 程序可修改: 否
    location: str = ""              # AI可修改: 是 | 程序可修改: 否
    importance: int = 5             # AI可修改: 是 | 程序可修改: 否
    # 说明: EventDetail由AI（EventLayer的LLM）从玩家输入中解析生成。
    # 程序只负责存储和传递，不参与内容的判断。


# =============================================================================
# 事件主体
# =============================================================================


@dataclass
class Event:
    """事件数据类.

    由EventLayer从玩家输入解析生成（通过LLM）。
    包含事件的所有信息：谁、做了什么、在哪、公开程度等。

    Attributes:
        event_id: 事件唯一标识.
        event_type: 事件类型.
        description: 事件整体描述（自然语言摘要）.
        details: 事件细节列表.
        scope: 公开程度.
        location: 发生地点.
        involved_npc_ids: 涉及的NPC ID列表（参与者/知情者）.
        timestamp: 发生时间.
        parent_event_id: 父事件ID（此事件由哪个事件延续而来）.
        status: 事件状态.
        source: 事件来源（player/npc/world/propagation）.
    """

    event_id: str = ""
    event_type: EventType = EventType.PLAYER_ACTION
    description: str = ""                           # AI可修改: 是 | 程序可修改: 否
    details: list[EventDetail] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 否
    scope: EventScope = EventScope.PRIVATE          # AI可修改: 是 | 程序可修改: 否
    location: str = ""                              # AI可修改: 是 | 程序可修改: 否
    involved_npc_ids: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 否
    timestamp: str = ""                             # AI可修改: 是 | 程序可修改: 是
    parent_event_id: str = ""                       # AI可修改: 否 | 程序可修改: 是
    status: EventStatus = EventStatus.ACTIVE        # AI可修改: 否 | 程序可修改: 是
    source: str = "player"                          # AI可修改: 是 | 程序可修改: 否
    # 说明: Event的核心字段由AI生成。parent_event_id和status由程序管理。
    # source标记事件来源：player=玩家输入, npc=NPC行为, world=世界事件, propagation=传播.

    def get_all_npc_ids(self) -> list[str]:
        """获取事件涉及的所有NPC ID（从details和involved_npc_ids合并）.

        Returns:
            去重后的NPC ID列表.
        """
        ids: set[str] = set(self.involved_npc_ids)
        for detail in self.details:
            if detail.subject_id:
                ids.add(detail.subject_id)
            if detail.target_id:
                ids.add(detail.target_id)
        return list(ids)

    def get_event_table_entry(self) -> str:
        """生成事件表条目（给感知层LLM用）.

        Returns:
            单行事件描述字符串.
        """
        return (
            f"[{self.event_id}] type={self.event_type.value} "
            f"scope={self.scope.value} location=\"{self.location}\" "
            f"summary=\"{self.description}\" "
            f"involved_npcs={self.involved_npc_ids}"
        )


# =============================================================================
# 跨tick事件上下文
# =============================================================================


@dataclass
class CurrentEvent:
    """跨tick持久的事件上下文.

    由EventLayer维护，在多次tick之间持久化。
    核心原则：Python只存不判——只存储AI返回的上下文数据，
    不自行判断是否延续。LLM决定更新。

    Attributes:
        location: 场景位置（玩家当前所在位置）.
        topic: 当前话题.
        addressed_npc_id: 玩家在跟谁说话（对话对象ID）.
        involved_npc_ids: 事件参与者列表（谁在场/知情）.
        event_type: 事件类型.
        scope: 公开程度.
        tick_started: 此上下文从哪个tick开始.
        tick_updated: 最后一次更新的tick.
    """

    location: str = ""                              # AI可修改: 是 | 程序可修改: 否
    topic: str = ""                                 # AI可修改: 是 | 程序可修改: 否
    addressed_npc_id: str = ""                      # AI可修改: 是 | 程序可修改: 否
    involved_npc_ids: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 否
    event_type: str = "dialogue"                    # AI可修改: 是 | 程序可修改: 否
    scope: str = "private"                          # AI可修改: 是 | 程序可修改: 否
    tick_started: int = 0                           # AI可修改: 否 | 程序可修改: 是
    tick_updated: int = 0                           # AI可修改: 否 | 程序可修改: 是
    # 说明: CurrentEvent的前6个字段完全由AI维护。
    # tick_started和tick_updated由程序自动记录。

    def is_active(self) -> bool:
        """判断当前事件上下文是否有效.

        Returns:
            是否有位置和参与者（至少1个NPC）.
        """
        return bool(self.location and self.involved_npc_ids)

    def to_event(self) -> Event:
        """将CurrentEvent转换为下游层可用的Event对象.

        Returns:
            Event对象.
        """
        scope_map: dict[str, EventScope] = {
            "public": EventScope.PUBLIC, "area": EventScope.AREA,
            "group": EventScope.GROUP, "private": EventScope.PRIVATE, "hidden": EventScope.HIDDEN,
        }
        type_map: dict[str, EventType] = {
            "dialogue": EventType.DIALOGUE, "player_action": EventType.PLAYER_ACTION,
        }

        return Event(
            event_id="current",
            event_type=type_map.get(self.event_type, EventType.DIALOGUE),
            description=self.topic,
            scope=scope_map.get(self.scope, EventScope.PRIVATE),
            location=self.location,
            involved_npc_ids=list(self.involved_npc_ids),
        )


# =============================================================================
# 事件表（多事件集合）
# =============================================================================


@dataclass
class EventTable:
    """事件上下文表.

    一个tick可能产生多个事件，此表统一管理。
    用于在感知层和后续层之间传递事件集合。

    Attributes:
        events: 本轮所有事件列表.
        is_continuation: 是否延续上一轮上下文.
        previous_event_id: 上一轮事件ID（如果有延续关系）.
    """

    events: list[Event] = field(default_factory=list)
    is_continuation: bool = False       # AI可修改: 否 | 程序可修改: 是
    previous_event_id: str = ""         # AI可修改: 否 | 程序可修改: 是

    def get_visible_events(self) -> list[Event]:
        """获取所有可见事件（排除HIDDEN事件）.

        Returns:
            非HIDDEN的Event列表.
        """
        return [e for e in self.events if e.scope != EventScope.HIDDEN]

    def get_event_ids(self) -> list[str]:
        """获取所有事件ID列表.

        Returns:
            事件ID列表.
        """
        return [e.event_id for e in self.events]
