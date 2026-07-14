"""世界定义数据模型 — 全局非公示世界环境.

世界层定义不变的世界规则和隐藏事件。
"""

from dataclasses import dataclass, field


@dataclass
class WorldRule:
    """世界规则.

    Attributes:
        rule_id: 规则唯一标识.
        description: 规则描述.
        applies_to: 适用范围 (all / 特定区域 / 特定群体).
        penalty: 违反后果.
    """

    rule_id: str
    description: str
    applies_to: str = "all"
    penalty: str = ""


@dataclass
class HiddenEvent:
    """隐藏事件 — 触发后才暴露给玩家/NPC.

    Attributes:
        event_id: 事件唯一标识.
        trigger_condition: 触发条件描述.
        event_content: 事件具体内容.
        is_triggered: 是否已触发.
    """

    event_id: str
    trigger_condition: str
    event_content: str
    is_triggered: bool = False


@dataclass
class WorldDefinition:
    """世界定义数据类.

    包含世界的全局规则、已知事件和隐藏事件。
    这些是不变的世界设定，可变状态放在WorldState中。

    Attributes:
        world_id: 世界唯一标识.
        name: 世界名称.
        description: 世界背景描述.
        rules: 世界规则列表.
        hidden_events: 隐藏事件列表.
        known_events: 已知事件列表.
    """

    world_id: str
    name: str
    description: str = ""
    rules: list[WorldRule] = field(default_factory=list)
    hidden_events: list[HiddenEvent] = field(default_factory=list)
    known_events: list[str] = field(default_factory=list)

    def check_triggered_events(self, condition: str) -> list[HiddenEvent]:
        """检查并返回满足触发条件的隐藏事件.

        Args:
            condition: 当前条件描述.

        Returns:
            满足触发条件的隐藏事件列表.
        """
        triggered = []
        for event in self.hidden_events:
            if not event.is_triggered and condition in event.trigger_condition:
                event.is_triggered = True
                triggered.append(event)
        return triggered
