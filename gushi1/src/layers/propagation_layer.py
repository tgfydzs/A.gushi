"""PropagationLayer — 信息传播层.

职责：判断信息是否需要传播、传播范围、信息变形。
不调用LLM，纯Python逻辑。
"""

from dataclasses import dataclass, field
from typing import Any

from src.layers.base import BaseLayer, LayerResult
from src.models.event import EventScope
from src.models.result import BaseResult


# =============================================================================
# Result
# =============================================================================

@dataclass
class PropagationEntry:
    """单条传播记录."""

    source_event_id: str
    propagated_content: str
    from_npc_id: str = ""
    to_target: str = ""
    distortion: float = 0.0
    timestamp: str = ""


@dataclass
class PropagationResult(BaseResult):
    """传播层处理结果."""

    propagations: list[PropagationEntry] = field(default_factory=list)
    should_stop: bool = True

    def __post_init__(self) -> None:
        self.layer_name = "PropagationLayer"


# =============================================================================
# Schema
# =============================================================================

@dataclass
class PropagationInput:
    """PropagationLayer 输入."""

    director_result: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    npcs_summary: list[dict] = field(default_factory=list)
    world_state: dict = field(default_factory=dict)


# =============================================================================
# Parser
# =============================================================================

@dataclass
class PropagationParser:
    """将传播数据转换为PropagationResult."""

    def parse(self, propagation_data: list[dict]) -> PropagationResult:
        entries = [
            PropagationEntry(
                source_event_id=e.get("source_event_id", ""),
                propagated_content=e.get("propagated_content", ""),
                from_npc_id=e.get("from_npc_id", ""),
                to_target=e.get("to_target", ""),
                distortion=e.get("distortion", 0.0),
                timestamp=e.get("timestamp", ""),
            )
            for e in propagation_data
        ]
        return PropagationResult(
            success=True, propagations=entries,
            should_stop=len(entries) == 0,
        )


# =============================================================================
# Layer
# =============================================================================

@dataclass
class PropagationLayer(BaseLayer):
    """信息传播层 — 判断和处理信息传播."""

    parser: PropagationParser = field(default_factory=PropagationParser)

    PROPAGATION_THRESHOLD: float = 0.3
    DISTORTION_PER_HOP: float = 0.15
    MAX_HOPS: int = 3

    @property
    def layer_name(self) -> str:
        return "PropagationLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        if isinstance(input_data, dict):
            input_data = PropagationInput(
                director_result=input_data.get("director_result", {}),
                events=input_data.get("events", []),
                npcs_summary=input_data.get("npcs_summary", []),
                world_state=input_data.get("world_state", {}),
            )

        if not isinstance(input_data, PropagationInput):
            return LayerResult(
                success=False,
                error=f"PropagationLayer 期望 PropagationInput，收到 {type(input_data)}",
            )

        try:
            propagations = self._determine_propagation(input_data)
            result = self.parser.parse(propagations)
            return LayerResult(success=True, data=result)
        except Exception as e:
            return LayerResult(success=False, error=f"PropagationLayer 处理失败: {e}")

    def _determine_propagation(self, input_data: PropagationInput) -> list[dict]:
        propagations: list[dict] = []

        for event in input_data.events:
            scope = event.get("scope", "private")
            event_id = event.get("event_id", "")
            description = event.get("description", "")

            if scope in (EventScope.PRIVATE.value, EventScope.HIDDEN.value):
                continue

            if scope == EventScope.PUBLIC.value:
                for npc in input_data.npcs_summary:
                    propagations.append({
                        "source_event_id": event_id,
                        "propagated_content": description,
                        "from_npc_id": "",
                        "to_target": npc.get("npc_id", ""),
                        "distortion": 0.0,
                        "timestamp": "",
                    })

            elif scope == EventScope.AREA.value:
                event_location = event.get("location", "")
                for npc in input_data.npcs_summary:
                    if npc.get("location", "") == event_location:
                        propagations.append({
                            "source_event_id": event_id,
                            "propagated_content": description,
                            "from_npc_id": "",
                            "to_target": npc.get("npc_id", ""),
                            "distortion": 0.0,
                            "timestamp": "",
                        })

        return propagations

    def simulate_distortion(self, content: str, hops: int) -> tuple[str, float]:
        distortion = min(1.0, self.DISTORTION_PER_HOP * hops)
        if distortion > 0.8:
            return f"(严重变形) {content[:20]}...", distortion
        elif distortion > 0.5:
            return f"(部分变形) {content}", distortion
        return content, distortion
