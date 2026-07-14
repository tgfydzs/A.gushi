"""ReasoningLayer — 推理层.

职责：NPC根据更新后维护表推理未知信息，尝试解释Pending事件。
Python判断是否需要LLM，只在达到推理条件时调用。
"""

from dataclasses import dataclass, field
from typing import Any

from src.core.llm_client import LLMClient
from src.layers.base import BaseLayer, LayerResult
from src.models.result import BaseResult
from src.utils.data_loader import render_prompt
from src.utils.json_utils import parse_json


# =============================================================================
# Result
# =============================================================================

@dataclass
class NPCReasoning:
    """单个NPC的推理结果."""

    npc_id: str
    new_knowledge: list[str] = field(default_factory=list)
    new_beliefs: list[str] = field(default_factory=list)
    resolved_pending: list[str] = field(default_factory=list)
    goals_updated: list[str] = field(default_factory=list)
    reasoning_output: str = ""


@dataclass
class ReasoningResult(BaseResult):
    """推理层处理结果."""

    npc_reasonings: list[NPCReasoning] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.layer_name = "ReasoningLayer"


# =============================================================================
# Parser
# =============================================================================

@dataclass
class ReasoningParser:
    """将LLM JSON输出解析为ReasoningResult。纯数据转换."""

    def parse(self, llm_outputs: list[tuple[str, dict]]) -> ReasoningResult:
        reasonings = [
            NPCReasoning(
                npc_id=npc_id,
                new_knowledge=output.get("new_knowledge", []),
                new_beliefs=output.get("new_beliefs", []),
                resolved_pending=output.get("resolved_pending", []),
                goals_updated=output.get("goals_updated", []),
                reasoning_output=output.get("reasoning_output", ""),
            )
            for npc_id, output in llm_outputs
        ]
        return ReasoningResult(success=True, npc_reasonings=reasonings)


# =============================================================================
# Layer
# =============================================================================

@dataclass
class ReasoningLayer(BaseLayer):
    """推理层 — NPC基于维护表进行推理."""

    llm_client: LLMClient | None = None
    parser: ReasoningParser = field(default_factory=ReasoningParser)

    @property
    def layer_name(self) -> str:
        return "ReasoningLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        npcs: dict[str, Any] = context.get("npcs", {})
        if not npcs:
            return LayerResult(success=False, error="ReasoningLayer 需要 context['npcs']")

        try:
            reasonings = self._process_reasonings(npcs)
            result = self.parser.parse(reasonings)
            return LayerResult(success=True, data=result)
        except Exception as e:
            return LayerResult(success=False, error=f"ReasoningLayer 处理失败: {e}")

    def _process_reasonings(self, npcs: dict[str, Any]) -> list[tuple[str, dict]]:
        results: list[tuple[str, dict]] = []
        empty = {"new_knowledge": [], "new_beliefs": [], "resolved_pending": [], "goals_updated": [], "reasoning_output": ""}

        for npc_id, npc in npcs.items():
            if not npc.has_pending():
                results.append((npc_id, {**empty, "reasoning_output": "无待解释事件，跳过推理"}))
                continue
            if not npc.observation:
                results.append((npc_id, {**empty, "reasoning_output": "无新观察，跳过推理"}))
                continue

            if self.llm_client is not None:
                results.append((npc_id, self._llm_reason(npc)))
            else:
                results.append((npc_id, {**empty, "reasoning_output": "无LLM，跳过推理"}))

        return results

    def _llm_reason(self, npc: Any) -> dict:
        prompt = self._build_prompt(npc)
        response = self.llm_client.send(prompt)
        parsed = parse_json(response.raw_text) if response.success else None
        if parsed is None:
            return {"new_knowledge": [], "new_beliefs": [], "resolved_pending": [], "goals_updated": [], "reasoning_output": "LLM调用失败"}
        return parsed

    def _build_prompt(self, npc: Any) -> str:
        pending = [p.event_description for p in npc.pending_reason[-5:]]
        observations = [o.observation for o in npc.observation[-5:]]
        memories = [m.content for m in npc.memory[-5:]]
        knowledge = [k.fact for k in npc.knowledge[-5:]]
        goals = [g.goal for g in npc.goal if not g.is_completed]
        personality = (
            f"开放:{npc.personality.openness} 尽责:{npc.personality.conscientiousness} "
            f"外向:{npc.personality.extraversion} 宜人:{npc.personality.agreeableness} "
            f"神经质:{npc.personality.neuroticism} 认知:{npc.personality.intelligence}/100"
        )

        return render_prompt("reasoning",
            npc_name=npc.name, npc_id=npc.npc_id, npc_role=npc.role.value,
            personality=personality,
            pending_events="\n".join(f"  - {p}" for p in pending) if pending else "  (无)",
            observations="\n".join(f"  - {o}" for o in observations) if observations else "  (无)",
            memories="\n".join(f"  - {m}" for m in memories) if memories else "  (无)",
            knowledge="\n".join(f"  - {k}" for k in knowledge) if knowledge else "  (无)",
            goals="\n".join(f"  - {g}" for g in goals) if goals else "  (无)",
        )
