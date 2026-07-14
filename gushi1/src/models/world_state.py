"""世界状态数据模型 — 全局公示世界环境.

维护可变的全局状态：时间、天气、全局事件、可用物品等。
"""

from dataclasses import dataclass, field
from enum import Enum


class TimeOfDay(str, Enum):
    """时段枚举."""

    DAWN = "dawn"
    MORNING = "morning"
    NOON = "noon"
    AFTERNOON = "afternoon"
    DUSK = "dusk"
    NIGHT = "night"
    MIDNIGHT = "midnight"


class Weather(str, Enum):
    """天气枚举."""

    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAINY = "rainy"
    STORMY = "stormy"
    SNOWY = "snowy"
    FOGGY = "foggy"
    WINDY = "windy"


@dataclass
class GlobalEvent:
    """全局事件 — 所有NPC都能感知到的事件.

    Attributes:
        event_id: 事件唯一标识.
        description: 事件描述.
        affected_area: 影响区域.
        is_active: 是否仍在生效.
        timestamp: 发生时间.
    """

    event_id: str
    description: str
    affected_area: str = "all"
    is_active: bool = True
    timestamp: str = ""


@dataclass
class EventPropagation:
    """事件传播记录.

    Attributes:
        source_event_id: 源事件ID.
        propagated_to: 传播到的区域/NPC.
        propagation_method: 传播方式（rumor/news/word_of_mouth）.
        timestamp: 传播时间.
    """

    source_event_id: str
    propagated_to: list[str] = field(default_factory=list)
    propagation_method: str = "word_of_mouth"
    timestamp: str = ""


@dataclass
class WorldState:
    """世界状态数据类.

    维护可变的全局公示状态。

    Attributes:
        day: 当前天数.
        time_of_day: 当前时段.
        weather: 当前天气.
        global_events: 进行中的全局事件.
        event_propagations: 事件传播记录.
        available_items: 全局可用物品列表.
    """

    day: int = 1
    time_of_day: TimeOfDay = TimeOfDay.MORNING
    weather: Weather = Weather.SUNNY
    player_location: str = ""
    global_events: list[GlobalEvent] = field(default_factory=list)
    event_propagations: list[EventPropagation] = field(default_factory=list)
    available_items: list[str] = field(default_factory=list)

    def advance_time(self) -> None:
        """推进时间（一个时段）."""
        order = list(TimeOfDay)
        current_idx = order.index(self.time_of_day)
        next_idx = (current_idx + 1) % len(order)
        if next_idx == 0:
            self.day += 1
        self.time_of_day = order[next_idx]

    def add_global_event(self, event: GlobalEvent) -> None:
        """添加全局事件."""
        self.global_events.append(event)

    def get_active_events(self) -> list[GlobalEvent]:
        """获取所有进行中的全局事件."""
        return [e for e in self.global_events if e.is_active]
