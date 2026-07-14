"""世界定义与状态 Schema — world_schema
============================================

定义所有与世界规则、世界状态相关的数据结构。
世界层是跨所有层的基础约束层，其状态优先级高于其他层。

范围：
- 世界定义（规则、隐藏事件、已知事件）
- 世界状态（时间、天气、全局事件）
- 事件传播记录
- 世界配置参数

注意：
- 世界规则默认不变，但传播层可能触发规则/法律的更新（如革命、立法等）
- 世界状态的修改只能通过程序控制（时间推进等），AI不可直接修改
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class TimeOfDay(str, Enum):
    """时段枚举.

    一天被划分为8个时段，每个时段约3小时.
    可通过 WorldConfig.time_advance_per_tick 控制每tick推进几个时段.
    """

    DAWN = "dawn"           # 黎明 (约5-7点)
    MORNING = "morning"     # 早晨 (约7-9点)
    FORENOON = "forenoon"   # 上午 (约9-12点) — 当前未使用，预留扩展
    NOON = "noon"           # 正午 (约12-14点)
    AFTERNOON = "afternoon" # 下午 (约14-17点)
    DUSK = "dusk"           # 黄昏 (约17-19点)
    NIGHT = "night"         # 夜晚 (约19-23点)
    MIDNIGHT = "midnight"   # 深夜 (约23-5点)


class Weather(str, Enum):
    """天气枚举.

    天气影响NPC活动、可见度和事件传播范围.
    """

    SUNNY = "sunny"         # 晴天
    CLOUDY = "cloudy"       # 多云
    RAINY = "rainy"         # 下雨
    STORMY = "stormy"       # 暴风雨
    SNOWY = "snowy"         # 下雪
    FOGGY = "foggy"         # 大雾 — 降低感知范围
    WINDY = "windy"         # 大风
    HEATWAVE = "heatwave"   # 酷热 — 预留扩展
    BLIZZARD = "blizzard"   # 暴风雪 — 预留扩展


class WorldEventStatus(str, Enum):
    """全局事件状态枚举."""

    ACTIVE = "active"           # 进行中
    CONCLUDED = "concluded"     # 已结束
    SUSPENDED = "suspended"     # 暂停（被更高优先级事件覆盖）


class PropagationMethod(str, Enum):
    """信息传播方式枚举."""

    DIRECT_OBSERVE = "direct_observe"     # 亲眼所见
    WORD_OF_MOUTH = "word_of_mouth"       # 口口相传
    RUMOR = "rumor"                       # 谣言
    OFFICIAL_NEWS = "official_news"       # 官方公告
    LETTER = "letter"                     # 书信
    MAGIC = "magic"                       # 魔法传讯 — 预留扩展


# =============================================================================
# 世界定义
# =============================================================================


@dataclass
class WorldRule:
    """世界规则.

    定义世界中的法律、物理规则、社会规范等。
    默认不可变，但传播层可能通过特定事件更新（如政变后法律变更）。

    Attributes:
        rule_id: 规则唯一标识.
        description: 规则描述（自然语言）.
        applies_to: 适用范围 ("all" / 特定区域名 / 特定群体名).
        penalty: 违反后果描述.
        category: 规则分类（法律/物理/社会/魔法）.
        is_mutable: 是否允许被修改（如法律可以被革命改变，物理规律不能）.
        priority: 优先级 (1-10)，冲突时高优先级覆盖低优先级.
    """

    rule_id: str = ""
    description: str = ""
    applies_to: str = "all"
    penalty: str = ""
    category: str = "law"           # law | physics | social | magic
    is_mutable: bool = False        # AI可修改: 否(默认) | 程序可修改: 是(初始化+传播层)
    priority: int = 5               # AI可修改: 否 | 程序可修改: 是
    # 说明: 世界规则在初始化时由程序设定，多数情况下不可变。
    # 特殊场景下（如NPC起义成功修改法律），传播层可通过世界状态
    # 的 global_events 记录规则变更，然后由程序更新。


@dataclass
class HiddenEvent:
    """隐藏事件.

    触发后才暴露给玩家/NPC的事件，用于剧情推进。

    Attributes:
        event_id: 事件唯一标识.
        trigger_condition: 触发条件描述（关键词匹配）.
        event_content: 事件具体内容.
        is_triggered: 是否已触发.
        one_shot: 是否只能触发一次.
        category: 事件分类.
        priority: 优先级 (1-10).
    """

    event_id: str = ""
    trigger_condition: str = ""
    event_content: str = ""
    is_triggered: bool = False      # AI可修改: 否 | 程序可修改: 是
    one_shot: bool = True           # AI可修改: 否 | 程序可修改: 是(初始化)
    category: str = "general"       # AI可修改: 否 | 程序可修改: 是(初始化)
    priority: int = 5               # AI可修改: 否 | 程序可修改: 是(初始化)


@dataclass
class KnownEvent:
    """已知事件.

    所有角色初始就知道的世界事件（背景设定）。

    Attributes:
        event_id: 事件唯一标识.
        description: 事件描述.
        is_active: 是否仍在进行.
        timestamp: 发生/记录时间.
    """

    event_id: str = ""
    description: str = ""
    is_active: bool = True          # AI可修改: 否 | 程序可修改: 是
    timestamp: str = ""


@dataclass
class WorldDefinition:
    """世界定义.

    包含世界的基础设定：规则、隐藏事件、已知事件。
    这些数据在引擎初始化时加载，在整个模拟过程中作为背景约束。
    WorldDefinition 是不可变的游戏设定，可变状态请见 WorldState。

    Attributes:
        world_id: 世界唯一标识.
        name: 世界名称.
        description: 世界背景描述（长篇）.
        short_description: 世界简短描述（给LLM用，控制token消耗）.
        rules: 世界规则列表.
        hidden_events: 隐藏事件列表.
        known_events: 已知事件列表.
        version: Schema版本号，便于后续迁移.
    """

    world_id: str = ""
    name: str = ""
    description: str = ""
    short_description: str = ""     # AI可修改: 否 | 程序可修改: 是(初始化)
    rules: list[WorldRule] = field(default_factory=list)
    hidden_events: list[HiddenEvent] = field(default_factory=list)
    known_events: list[KnownEvent] = field(default_factory=list)
    version: str = "1.0"            # AI可修改: 否 | 程序可修改: 是(初始化)

    def check_triggered_events(self, condition: str) -> list[HiddenEvent]:
        """检查并返回满足触发条件的隐藏事件.

        Args:
            condition: 当前条件描述（如玩家输入或NPC行为描述）.

        Returns:
            满足触发条件的隐藏事件列表.
        """
        triggered: list[HiddenEvent] = []
        for event in self.hidden_events:
            if not event.is_triggered and condition in event.trigger_condition:
                event.is_triggered = True
                triggered.append(event)
        return triggered


# =============================================================================
# 世界状态
# =============================================================================


@dataclass
class GlobalEvent:
    """全局事件.

    所有NPC都能感知到的事件（天灾、节日、战争等）。

    Attributes:
        event_id: 事件唯一标识.
        description: 事件描述.
        affected_area: 影响区域 ("all" / 具体区域名).
        status: 事件状态.
        priority: 优先级 (1-10)，高优先级事件可能暂停低优先级事件.
        timestamp: 发生时间.
        resolved_timestamp: 结束时间（空表示未结束）.
        source: 事件来源（world/propagation/npc_action）.
    """

    event_id: str = ""
    description: str = ""
    affected_area: str = "all"      # AI可修改: 否 | 程序可修改: 是
    status: WorldEventStatus = WorldEventStatus.ACTIVE  # AI可修改: 否 | 程序可修改: 是
    priority: int = 5               # AI可修改: 否 | 程序可修改: 是
    timestamp: str = ""             # AI可修改: 否 | 程序可修改: 是
    resolved_timestamp: str = ""    # AI可修改: 否 | 程序可修改: 是
    source: str = "world"           # AI可修改: 否 | 程序可修改: 是
    # 说明: 全局事件的来源：
    #   "world" — 世界初始化时预设
    #   "propagation" — 传播层产生的（如NPC起义成功的消息传遍世界）
    #   "hidden" — 隐藏事件触发后公开化


@dataclass
class EventPropagation:
    """事件传播记录.

    记录事件在世界中的传播状态。

    Attributes:
        source_event_id: 源事件ID.
        propagated_to: 传播到的目标（NPC ID列表 / 区域名列表）.
        propagation_method: 传播方式.
        distortion: 信息失真度 (0.0-1.0)，每次传播递增.
        hop_count: 传播跳数.
        timestamp: 传播时间.
        is_complete: 是否传播完成.
    """

    source_event_id: str = ""
    propagated_to: list[str] = field(default_factory=list)     # AI可修改: 否 | 程序可修改: 是
    propagation_method: PropagationMethod = PropagationMethod.WORD_OF_MOUTH  # AI可修改: 否 | 程序可修改: 是
    distortion: float = 0.0           # AI可修改: 否 | 程序可修改: 是
    hop_count: int = 0                # AI可修改: 否 | 程序可修改: 是
    timestamp: str = ""               # AI可修改: 否 | 程序可修改: 是
    is_complete: bool = False         # AI可修改: 否 | 程序可修改: 是
    # 说明: 传播由 PropagationLayer 全权控制，AI不参与传播判断


@dataclass
class WorldState:
    """世界状态.

    维护可变的全局公示状态。所有NPC和玩家共享此状态。
    世界状态的优先级高于传播层变动——如果传播的事件与世界状态冲突，
    世界状态优先（例如世界规则说"攻击魔法违法"，传播层不能将其改为合法）。

    Attributes:
        day: 当前天数（从1开始）.
        time_of_day: 当前时段.
        weather: 当前天气.
        player_location: 玩家当前位置.
        global_events: 进行中的全局事件列表.
        event_propagations: 事件传播记录列表.
        available_items: 全局可用物品列表（商店、公共物品等）.
        world_age: 世界纪元/年份 — 预留扩展.
    """

    day: int = 1                              # AI可修改: 否 | 程序可修改: 是
    time_of_day: TimeOfDay = TimeOfDay.MORNING  # AI可修改: 否 | 程序可修改: 是
    weather: Weather = Weather.SUNNY          # AI可修改: 否 | 程序可修改: 是
    player_location: str = ""                 # AI可修改: 否 | 程序可修改: 是
    global_events: list[GlobalEvent] = field(default_factory=list)
    event_propagations: list[EventPropagation] = field(default_factory=list)
    available_items: list[str] = field(default_factory=list)
    world_age: int = 1                        # AI可修改: 否 | 程序可修改: 是

    def advance_time(self) -> None:
        """推进时间（一个时段）."""
        order = list(TimeOfDay)
        current_idx = order.index(self.time_of_day)
        next_idx = (current_idx + 1) % len(order)
        if next_idx == 0:
            self.day += 1
        self.time_of_day = order[next_idx]

    def add_global_event(self, event: GlobalEvent) -> None:
        """添加全局事件.

        Args:
            event: 要添加的全局事件.
        """
        self.global_events.append(event)

    def get_active_events(self) -> list[GlobalEvent]:
        """获取所有进行中的全局事件.

        Returns:
            状态为 ACTIVE 的全局事件列表.
        """
        return [e for e in self.global_events if e.status == WorldEventStatus.ACTIVE]

    def get_events_by_area(self, area: str) -> list[GlobalEvent]:
        """获取影响指定区域的全局事件.

        Args:
            area: 区域名.

        Returns:
            影响该区域的全局事件列表.
        """
        return [
            e for e in self.global_events
            if e.status == WorldEventStatus.ACTIVE
            and (e.affected_area == "all" or e.affected_area == area)
        ]

    def get_summary(self) -> dict:
        """获取世界状态摘要（给LLM的最小上下文）.

        Returns:
            包含 day, time_of_day, weather, player_location, active_events 的dict.
        """
        return {
            "day": self.day,
            "time_of_day": self.time_of_day.value,
            "weather": self.weather.value,
            "player_location": self.player_location,
            "active_events": [e.description for e in self.get_active_events()],
        }


# =============================================================================
# 世界配置
# =============================================================================


@dataclass
class WorldConfig:
    """世界配置参数.

    控制世界模拟的各项参数。

    Attributes:
        world_name: 世界名称.
        max_npcs: 最大NPC数量.
        max_memory_per_npc: 每个NPC最大记忆条数.
        time_advance_per_tick: 每tick时间推进的时段数.
        enable_hidden_events: 是否启用隐藏事件检测.
        enable_propagation: 是否启用信息传播.
        propagation_max_hops: 信息传播最大跳数.
        propagation_delay: 传播延迟（tick数），0为即.
        auto_advance_time: 是否自动推进时间.
    """

    world_name: str = "DefaultWorld"
    max_npcs: int = 100                       # AI可修改: 否 | 程序可修改: 是(初始化)
    max_memory_per_npc: int = 50              # AI可修改: 否 | 程序可修改: 是
    time_advance_per_tick: int = 1            # AI可修改: 否 | 程序可修改: 是
    enable_hidden_events: bool = True         # AI可修改: 否 | 程序可修改: 是
    enable_propagation: bool = True           # AI可修改: 否 | 程序可修改: 是
    propagation_max_hops: int = 3             # AI可修改: 否 | 程序可修改: 是
    propagation_delay: int = 0                # AI可修改: 否 | 程序可修改: 是
    auto_advance_time: bool = True            # AI可修改: 否 | 程序可修改: 是
