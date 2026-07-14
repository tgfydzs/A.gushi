"""WorldStateLayer — 世界状态层.

职责：维护全局公示状态（时间、天气、全局事件）。
不调用LLM，纯Python逻辑。
"""

from dataclasses import dataclass, field
from typing import Any

from src.layers.base import BaseLayer, LayerResult
from src.models.world_state import WorldState, TimeOfDay, Weather, GlobalEvent
from src.models.result import BaseResult


# =============================================================================
# Result
# =============================================================================

@dataclass
class WorldStateResult(BaseResult):
    """世界状态层处理结果."""

    world_state: WorldState | None = None

    def __post_init__(self) -> None:
        self.layer_name = "WorldStateLayer"


# =============================================================================
# Schema
# =============================================================================

@dataclass
class WorldStateInput:
    """WorldStateLayer 输入Schema."""

    day: int = 1
    time_of_day: TimeOfDay = TimeOfDay.MORNING
    weather: Weather = Weather.SUNNY
    global_events: list[dict] = field(default_factory=list)


# =============================================================================
# Parser
# =============================================================================

@dataclass
class WorldStateParser:
    """将输入转换为WorldStateResult。纯数据转换."""

    def parse(self, input_data: WorldStateInput) -> WorldStateResult:
        world_state = WorldState(
            day=input_data.day,
            time_of_day=input_data.time_of_day,
            weather=input_data.weather,
            global_events=[
                GlobalEvent(
                    event_id=e.get("event_id", ""),
                    description=e.get("description", ""),
                    affected_area=e.get("affected_area", "all"),
                    is_active=e.get("is_active", True),
                    timestamp=e.get("timestamp", ""),
                )
                for e in input_data.global_events
            ],
        )
        return WorldStateResult(success=True, world_state=world_state)


# =============================================================================
# Layer
# =============================================================================

@dataclass
class WorldStateLayer(BaseLayer):
    """世界状态层 — 全局公示世界环境."""

    parser: WorldStateParser = field(default_factory=WorldStateParser)
    _world_state: WorldState | None = None

    @property
    def layer_name(self) -> str:
        return "WorldStateLayer"

    @property
    def world_state(self) -> WorldState | None:
        return self._world_state

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        if isinstance(input_data, WorldState):
            self._world_state = input_data
            return LayerResult(
                success=True,
                data=WorldStateResult(success=True, world_state=input_data),
            )

        if isinstance(input_data, dict):
            tod_raw = input_data.get("time_of_day", TimeOfDay.MORNING)
            if isinstance(tod_raw, str):
                tod_raw = TimeOfDay(tod_raw)
            weather_raw = input_data.get("weather", Weather.SUNNY)
            if isinstance(weather_raw, str):
                weather_raw = Weather(weather_raw)

            input_data = WorldStateInput(
                day=input_data.get("day", 1),
                time_of_day=tod_raw,
                weather=weather_raw,
                global_events=input_data.get("global_events", []),
            )

        if not isinstance(input_data, WorldStateInput):
            return LayerResult(
                success=False,
                error=f"WorldStateLayer 期望 WorldStateInput，收到 {type(input_data)}",
            )

        try:
            result = self.parser.parse(input_data)
            self._world_state = result.world_state
            return LayerResult(success=True, data=result)
        except Exception as e:
            return LayerResult(success=False, error=f"WorldStateLayer 处理失败: {e}")

    def advance_time(self) -> None:
        if self._world_state:
            self._world_state.advance_time()
