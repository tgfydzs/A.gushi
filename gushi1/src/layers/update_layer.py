"""UpdateLayer — 更新层（核心）.

职责：所有NPC维护表的唯一修改入口。
包括：Memory, Knowledge, Belief, Observation, PendingReason,
      Relationship, Inventory, Status, Goal。

禁止：其他Layer直接修改NPC维护表。
不调用LLM，纯Python逻辑。
"""

from dataclasses import dataclass, field
from typing import Any

from src.layers.base import BaseLayer, LayerResult
from src.models.npc import (
    NPC, MemoryEntry, ObservationEntry, KnowledgeEntry,
    BeliefEntry, PendingEntry, RelationshipValue, Item, GoalEntry,
)
from src.models.result import BaseResult


# =============================================================================
# Result
# =============================================================================

@dataclass
class NPCUpdateRecord:
    """单个NPC的更新记录."""

    npc_id: str
    updated_tables: list[str] = field(default_factory=list)
    added_memories: list[str] = field(default_factory=list)
    added_observations: list[str] = field(default_factory=list)
    added_knowledge: list[str] = field(default_factory=list)
    added_pending: list[str] = field(default_factory=list)
    updated_relationships: list[str] = field(default_factory=list)


@dataclass
class UpdateResult(BaseResult):
    """更新层处理结果."""

    npc_updates: list[NPCUpdateRecord] = field(default_factory=list)
    update_type: str = ""

    def __post_init__(self) -> None:
        self.layer_name = "UpdateLayer"


# =============================================================================
# Schema
# =============================================================================

@dataclass
class UpdateInput:
    """UpdateLayer 统一更新接口."""

    update_type: str = ""
    npc_updates: list[dict] = field(default_factory=list)
    source_layer: str = ""


# =============================================================================
# Parser
# =============================================================================

@dataclass
class UpdateParser:
    """将各层输出转换为更新请求。纯数据转换."""

    def parse_perception_to_updates(self, perception_result: Any) -> UpdateInput:
        npc_updates = []
        for perception in perception_result.npc_perceptions:
            # 感知到的内容写入观察
            observations = []
            pending = []
            if perception.perceived_summary and perception.perceived_summary != "无可见事件":
                observations.append({
                    "observation": perception.perceived_summary, "detail_level": 3,
                })
                # 感知到了但还没理解的事件 → 挂起待推理
                pending.append({
                    "event_description": perception.perceived_summary, "priority": 5,
                })
            npc_updates.append({
                "npc_id": perception.npc_id,
                "add_observations": observations,
                "add_pending": pending,
            })
        return UpdateInput(update_type="perception", npc_updates=npc_updates, source_layer="PerceptionLayer")

    def parse_reasoning_to_updates(self, reasoning_result: Any) -> UpdateInput:
        npc_updates = []
        for reasoning in reasoning_result.npc_reasonings:
            npc_updates.append({
                "npc_id": reasoning.npc_id,
                "add_knowledge": [
                    {"fact": k, "source": "reasoning", "confidence": 0.7}
                    for k in reasoning.new_knowledge
                ],
                "add_beliefs": [
                    {"belief": b, "basis": "reasoning", "strength": 0.6}
                    for b in reasoning.new_beliefs
                ],
                "resolve_pending": reasoning.resolved_pending,
            })
        return UpdateInput(update_type="reasoning", npc_updates=npc_updates, source_layer="ReasoningLayer")

    def build_result(self, records: list[NPCUpdateRecord], update_type: str) -> UpdateResult:
        return UpdateResult(success=True, npc_updates=records, update_type=update_type)


# =============================================================================
# Layer
# =============================================================================

@dataclass
class UpdateLayer(BaseLayer):
    """更新层 — 所有NPC维护表的唯一修改入口."""

    parser: UpdateParser = field(default_factory=UpdateParser)

    @property
    def layer_name(self) -> str:
        return "UpdateLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        if isinstance(input_data, dict):
            input_data = UpdateInput(
                update_type=input_data.get("update_type", ""),
                npc_updates=input_data.get("npc_updates", []),
                source_layer=input_data.get("source_layer", ""),
            )

        if not isinstance(input_data, UpdateInput):
            return LayerResult(
                success=False,
                error=f"UpdateLayer 期望 UpdateInput，收到 {type(input_data)}",
            )

        npcs: dict[str, NPC] = context.get("npcs", {})
        if not npcs:
            return LayerResult(success=False, error="UpdateLayer 需要 context['npcs']")

        try:
            records = self._apply_updates(input_data, npcs)
            result = self.parser.build_result(records, input_data.update_type)
            return LayerResult(success=True, data=result)
        except Exception as e:
            return LayerResult(success=False, error=f"UpdateLayer 处理失败: {e}")

    def _apply_updates(self, input_data: UpdateInput, npcs: dict[str, NPC]) -> list[NPCUpdateRecord]:
        records: list[NPCUpdateRecord] = []

        for npc_update in input_data.npc_updates:
            npc_id = npc_update.get("npc_id", "")
            npc = npcs.get(npc_id)
            if npc is None:
                continue

            record = NPCUpdateRecord(npc_id=npc_id)
            updated: list[str] = []

            for obs in npc_update.get("add_observations", []):
                npc.observation.append(ObservationEntry(
                    observation=obs.get("observation", ""),
                    target_id=obs.get("target_id", ""),
                    detail_level=obs.get("detail_level", 1),
                ))
            if npc_update.get("add_observations"):
                updated.append("observation")

            for mem in npc_update.get("add_memories", []):
                npc.memory.append(MemoryEntry(
                    content=mem.get("content", ""),
                    importance=mem.get("importance", 1),
                    related_event_id=mem.get("related_event_id"),
                ))
                record.added_memories.append(mem.get("content", ""))
            if npc_update.get("add_memories"):
                updated.append("memory")

            for know in npc_update.get("add_knowledge", []):
                npc.knowledge.append(KnowledgeEntry(
                    fact=know.get("fact", ""),
                    source=know.get("source", "unknown"),
                    confidence=know.get("confidence", 1.0),
                ))
                record.added_knowledge.append(know.get("fact", ""))
            if npc_update.get("add_knowledge"):
                updated.append("knowledge")

            for bel in npc_update.get("add_beliefs", []):
                npc.belief.append(BeliefEntry(
                    belief=bel.get("belief", ""),
                    basis=bel.get("basis", ""),
                    strength=bel.get("strength", 0.5),
                ))
            if npc_update.get("add_beliefs"):
                updated.append("belief")

            for pending in npc_update.get("add_pending", []):
                npc.pending_reason.append(PendingEntry(
                    event_description=pending.get("event_description", ""),
                    reason=pending.get("reason", ""),
                    priority=pending.get("priority", 5),
                ))
                record.added_pending.append(pending.get("event_description", ""))
            if npc_update.get("add_pending"):
                updated.append("pending_reason")

            for resolved in npc_update.get("resolve_pending", []):
                npc.pending_reason = [
                    p for p in npc.pending_reason
                    if p.event_description != resolved
                ]

            for rel in npc_update.get("update_relationships", []):
                target_id = rel.get("target_npc_id", "")
                if target_id not in npc.relationship:
                    npc.relationship[target_id] = RelationshipValue(target_npc_id=target_id)
                existing = npc.relationship[target_id]
                existing.favorability = max(-100, min(100, existing.favorability + rel.get("favorability_change", 0)))
                existing.trust = max(0, min(100, existing.trust + rel.get("trust_change", 0)))
                note = rel.get("note", "")
                if note:
                    existing.history.append(note)
                record.updated_relationships.append(target_id)
            if npc_update.get("update_relationships"):
                updated.append("relationship")

            if "new_location" in npc_update:
                npc.current_state.location = npc_update["new_location"]
                updated.append("current_state")
            if "new_emotion" in npc_update:
                npc.current_state.emotion = npc_update["new_emotion"]
                updated.append("current_state")
            if "new_action" in npc_update:
                npc.current_state.action = npc_update["new_action"]
                updated.append("current_state")

            for item in npc_update.get("add_items", []):
                npc.inventory.append(Item(
                    item_id=item.get("item_id", ""), name=item.get("name", ""),
                    description=item.get("description", ""), quantity=item.get("quantity", 1),
                ))
            if npc_update.get("add_items"):
                updated.append("inventory")
            for item_id in npc_update.get("remove_items", []):
                npc.inventory = [i for i in npc.inventory if i.item_id != item_id]
                updated.append("inventory")

            for goal in npc_update.get("add_goals", []):
                npc.goal.append(GoalEntry(
                    goal=goal.get("goal", ""), priority=goal.get("priority", 5),
                ))
            if npc_update.get("add_goals"):
                updated.append("goal")
            for goal_text in npc_update.get("complete_goals", []):
                for g in npc.goal:
                    if g.goal == goal_text:
                        g.is_completed = True
                        break

            record.updated_tables = updated
            records.append(record)

        return records
