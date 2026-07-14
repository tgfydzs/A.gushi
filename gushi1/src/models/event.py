"""事件数据模型.

事件是玩家输入或NPC动作的抽象表示。
"""

from dataclasses import dataclass, field
from enum import Enum


class EventScope(str, Enum):
    """事件公开程度."""

    PUBLIC = "public"          # 所有人可见
    AREA = "area"              # 特定区域可见
    GROUP = "group"            # 特定群体可见
    PRIVATE = "private"        # 仅目标NPC可见
    HIDDEN = "hidden"          # 完全隐藏


class EventType(str, Enum):
    """事件类型."""

    PLAYER_ACTION = "player_action"
    NPC_ACTION = "npc_action"
    WORLD_EVENT = "world_event"
    DIALOGUE = "dialogue"
    DISCOVERY = "discovery"
    COMBAT = "combat"
    TRADE = "trade"
    CUSTOM = "custom"


@dataclass
class EventDetail:
    """事件中的单个细节.

    一个事件可能包含多个细节（如一句话中包含多个信息）。

    Attributes:
        detail_id: 细节唯一标识.
        action: 动作描述.
        subject_id: 主体（谁做的）.
        target_id: 目标（对谁做的）.
        content: 具体内容.
        location: 发生地点.
    """

    detail_id: str
    action: str
    subject_id: str = ""
    target_id: str = ""
    content: str = ""
    location: str = ""


@dataclass
class Event:
    """事件数据类.

    由EventLayer从玩家输入解析生成。
    包含事件的所有信息：谁、做了什么、在哪、公开程度等。

    Attributes:
        event_id: 事件唯一标识.
        event_type: 事件类型.
        description: 事件整体描述.
        details: 事件细节列表.
        scope: 公开程度.
        location: 发生地点.
        involved_npc_ids: 涉及NPC ID列表.
        timestamp: 发生时间.
    """

    event_id: str
    event_type: EventType = EventType.PLAYER_ACTION
    description: str = ""
    details: list[EventDetail] = field(default_factory=list)
    scope: EventScope = EventScope.PUBLIC
    location: str = ""
    involved_npc_ids: list[str] = field(default_factory=list)
    timestamp: str = ""

    def get_npc_ids(self) -> list[str]:
        """获取事件涉及的所有NPC ID."""
        ids = set(self.involved_npc_ids)
        for detail in self.details:
            if detail.subject_id:
                ids.add(detail.subject_id)
            if detail.target_id:
                ids.add(detail.target_id)
        return list(ids)
