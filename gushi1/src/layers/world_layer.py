"""WorldLayer — 世界层.

职责：定义世界规则、管理隐藏事件、标记可知事件。
不调用LLM，纯Python逻辑。
"""

from dataclasses import dataclass, field
from typing import Any

from src.layers.base import BaseLayer, LayerResult
from src.models.world import WorldDefinition, WorldRule, HiddenEvent
from src.models.result import BaseResult


# =============================================================================
# Result
# =============================================================================

@dataclass
class WorldResult(BaseResult):
    """世界层处理结果."""

    world_definition: WorldDefinition | None = None

    def __post_init__(self) -> None:
        self.layer_name = "WorldLayer"


# =============================================================================
# Schema
# =============================================================================

@dataclass
class WorldInput:
    """WorldLayer 输入Schema."""

    world_definition: dict = field(default_factory=dict)
    initial_state: dict = field(default_factory=dict)


# =============================================================================
# Parser
# =============================================================================

@dataclass
class WorldParser:
    """将输入dict转换为WorldResult。纯数据转换，不含业务逻辑."""

    def parse(self, input_data: WorldInput) -> WorldResult:
        world_def = WorldDefinition(
            world_id=input_data.world_definition.get("world_id", ""),
            name=input_data.world_definition.get("name", ""),
            description=input_data.world_definition.get("description", ""),
            rules=[
                WorldRule(
                    rule_id=r.get("rule_id", ""),
                    description=r.get("description", ""),
                    applies_to=r.get("applies_to", "all"),
                    penalty=r.get("penalty", ""),
                )
                for r in input_data.world_definition.get("rules", [])
            ],
            hidden_events=[
                HiddenEvent(
                    event_id=e.get("event_id", ""),
                    trigger_condition=e.get("trigger_condition", ""),
                    event_content=e.get("event_content", ""),
                )
                for e in input_data.world_definition.get("hidden_events", [])
            ],
            known_events=input_data.world_definition.get("known_events", []),
        )
        return WorldResult(success=True, world_definition=world_def)


# =============================================================================
# Layer
# =============================================================================

@dataclass
class WorldLayer(BaseLayer):
    """世界层 — 全局非公示世界环境."""

    parser: WorldParser = field(default_factory=WorldParser)

    @property
    def layer_name(self) -> str:
        return "WorldLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        if isinstance(input_data, dict):
            input_data = WorldInput(
                world_definition=input_data.get("world_definition", {}),
                initial_state=input_data.get("initial_state", {}),
            )

        if not isinstance(input_data, WorldInput):
            return LayerResult(
                success=False,
                error=f"WorldLayer 期望 WorldInput/dict，收到 {type(input_data)}",
            )

        try:
            result = self.parser.parse(input_data)
            return LayerResult(success=True, data=result)
        except Exception as e:
            return LayerResult(success=False, error=f"WorldLayer 处理失败: {e}")
