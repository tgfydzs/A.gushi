"""测试 UpdateEngine — N12 唯一写入口正确性.

验证：
- Case 1: 事件结果成功写入 CurrentEvent
- Case 2: ReasoningResult 不会直接覆盖旧 belief
- Case 3: ResponseResult 好感度变化正确写入 Relationship
- Case 4: DirectorResult 不会修改 WorldState
- Case 5: Propagation 写入 KnowledgeEntry 后 confidence 正确
"""

import pytest

from engine.context import WorldContext
from engine.update_engine import UpdateEngine

from Schema.world_schema import WorldState, WorldDefinition, WorldRule
from Schema.npc_schema import (
    NPC, NPCIdentity, CurrentStateData, PersonalityData,
    NPCRole, Emotion,
)
from Schema.event_schema import CurrentEvent
from Schema.perception_schema import NPCPerception, PerceptionResult
from Schema.memory_schema import (
    KnowledgeEntry, ObservationEntry, KnowledgeSource,
)
from Schema.belief_schema import BeliefEntry, PendingEntry, NPCReasoning, ReasoningResult
from Schema.relationship_schema import RelationshipValue, RelationshipType
from Schema.dialogue_schema import NPCResponse, ResponseResult, ResponseType
from Schema.director_schema import DirectorResult
from Schema.action_schema import GoalEntry


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

def _make_npc(npc_id: str, name: str, location: str = "test_inn"):
    return NPC(
        identity=NPCIdentity(npc_id=npc_id, name=name, role=NPCRole.COMMONER),
        current_state=CurrentStateData(location=location),
        personality=PersonalityData(intelligence=50),
    )


def _make_context(npcs: dict | None = None) -> WorldContext:
    npc_dict = npcs or {
        "npc_a": _make_npc("npc_a", "NPC_A"),
        "npc_b": _make_npc("npc_b", "NPC_B"),
    }
    return WorldContext(
        tick_number=1,
        world_state=WorldState(day=1),
        world_definition=WorldDefinition(
            world_id="test", name="Test World",
            rules=[WorldRule(rule_id="r1", description="no violence")]
        ),
        npcs=npc_dict,
    )


# ══════════════════════════════════════════════════════════════════
# Case 1: Event → CurrentEvent
# ══════════════════════════════════════════════════════════════════

class TestEventWrite:
    """验证事件写入 CurrentEvent."""

    def test_new_event_creates_current_event(self):
        ctx = _make_context()
        engine = UpdateEngine(ctx)

        # Simulate EventNodeOutput
        from nodes.event_node import EventNodeOutput
        output = EventNodeOutput(
            continue_context=False,
            location="test_inn",
            topic="asking about quests",
            addressed_npc_id="npc_a",
            involved_npc_ids=["npc_a"],
            event_type="dialogue",
            scope="private",
        )

        engine.apply_event_result(output)

        assert ctx.current_event is not None
        assert ctx.current_event.location == "test_inn"
        assert ctx.current_event.topic == "asking about quests"
        assert ctx.current_event.addressed_npc_id == "npc_a"
        assert ctx.current_event.involved_npc_ids == ["npc_a"]
        assert ctx.current_event.scope == "private"

    def test_continue_event_updates_topic(self):
        ctx = _make_context()
        # Pre-existing current event
        ctx.current_event = CurrentEvent(
            location="test_inn",
            topic="old topic",
            addressed_npc_id="npc_a",
            involved_npc_ids=["npc_a"],
        )

        engine = UpdateEngine(ctx)

        from nodes.event_node import EventNodeOutput
        output = EventNodeOutput(
            continue_context=True,
            topic="new topic",
            added_npc_ids=["npc_b"],
        )

        engine.apply_event_result(output)

        assert ctx.current_event.topic == "new topic"
        assert "npc_b" in ctx.current_event.involved_npc_ids


# ══════════════════════════════════════════════════════════════════
# Case 2: Reasoning → Belief (不覆盖旧信念)
# ══════════════════════════════════════════════════════════════════

class TestReasoningWrite:
    """验证推理结果写入——不覆盖已有信念."""

    def test_new_belief_appended_not_overwritten(self):
        ctx = _make_context()
        npc = ctx.npcs["npc_a"]

        # Pre-existing belief
        npc.belief.append(BeliefEntry(
            belief="old belief", strength=0.9, basis="experience",
        ))

        engine = UpdateEngine(ctx)

        reasoning = ReasoningResult(npc_reasonings=[
            NPCReasoning(
                npc_id="npc_a",
                new_knowledge=["inferred fact"],
                new_beliefs=["new belief"],
            ),
        ])

        records = engine.apply_tick_results(reasoning_result=reasoning)

        # Old belief still exists
        old_beliefs = [b.belief for b in npc.belief]
        assert "old belief" in old_beliefs
        assert "new belief" in old_beliefs
        assert len(npc.belief) == 2  # old + new, not replaced

    def test_reasoning_knowledge_has_reasoning_source(self):
        ctx = _make_context()
        npc = ctx.npcs["npc_a"]
        engine = UpdateEngine(ctx)

        reasoning = ReasoningResult(npc_reasonings=[
            NPCReasoning(
                npc_id="npc_a",
                new_knowledge=["inferred knowledge"],
            ),
        ])

        engine.apply_tick_results(reasoning_result=reasoning)

        added = [k for k in npc.knowledge if k.fact == "inferred knowledge"]
        assert len(added) == 1
        assert added[0].source == KnowledgeSource.REASONING
        assert added[0].confidence == 0.7

    def test_resolved_pending_removed(self):
        ctx = _make_context()
        npc = ctx.npcs["npc_a"]
        npc.pending_reason.append(PendingEntry(
            event_description="something unexplained",
        ))

        engine = UpdateEngine(ctx)

        reasoning = ReasoningResult(npc_reasonings=[
            NPCReasoning(
                npc_id="npc_a",
                resolved_pending=["something unexplained"],
            ),
        ])

        engine.apply_tick_results(reasoning_result=reasoning)

        assert len(npc.pending_reason) == 0


# ══════════════════════════════════════════════════════════════════
# Case 3: Response → Relationship
# ══════════════════════════════════════════════════════════════════

class TestResponseWrite:
    """验证回应层写入——好感度变化."""

    def test_favorability_change_applied(self):
        ctx = _make_context()
        npc_a = ctx.npcs["npc_a"]

        # Pre-existing relationship
        npc_a.relationship["npc_b"] = RelationshipValue(
            target_npc_id="npc_b",
            relationship_type=RelationshipType.STRANGER,
            favorability=0,
            trust=50,
        )

        engine = UpdateEngine(ctx)

        resp = NPCResponse(
            npc_id="npc_a",
            npc_name="NPC_A",
            response_text="You are so kind!",
            response_type=ResponseType.SPEECH,
            emotion="happy",
            favorability_changes=[{
                "target_npc_id": "npc_b",
                "favorability_delta": 10,
                "trust_delta": 5,
                "reason": "player was kind",
            }],
        )
        response_result = ResponseResult(npc_responses=[resp])

        engine.apply_tick_results(response_result=response_result)

        rel = npc_a.relationship["npc_b"]
        assert rel.favorability == 10
        assert rel.trust == 55

    def test_favorability_clamped_to_range(self):
        ctx = _make_context()
        npc_a = ctx.npcs["npc_a"]

        npc_a.relationship["npc_b"] = RelationshipValue(
            target_npc_id="npc_b",
            favorability=95,
        )

        engine = UpdateEngine(ctx)

        resp = NPCResponse(
            npc_id="npc_a",
            npc_name="NPC_A",
            response_text="!",
            favorability_changes=[{
                "target_npc_id": "npc_b",
                "favorability_delta": 20,
                "trust_delta": 0,
                "reason": "test",
            }],
        )
        response_result = ResponseResult(npc_responses=[resp])

        engine.apply_tick_results(response_result=response_result)

        # Should be clamped to 100
        assert npc_a.relationship["npc_b"].favorability == 100

    def test_large_favorability_change_rejected(self):
        """超过30的单次好感度变化应被校验拦截."""
        ctx = _make_context()
        npc_a = ctx.npcs["npc_a"]

        npc_a.relationship["npc_b"] = RelationshipValue(
            target_npc_id="npc_b",
        )

        engine = UpdateEngine(ctx)

        resp = NPCResponse(
            npc_id="npc_a",
            npc_name="NPC_A",
            response_text="!",
            favorability_changes=[{
                "target_npc_id": "npc_b",
                "favorability_delta": 50,  # too large
                "trust_delta": 0,
                "reason": "test",
            }],
        )
        response_result = ResponseResult(npc_responses=[resp])

        engine.apply_tick_results(response_result=response_result)

        # Should NOT have changed — validation rejected it
        assert npc_a.relationship["npc_b"].favorability == 0
        assert len(engine.errors) > 0


# ══════════════════════════════════════════════════════════════════
# Case 4: Director → NOT WorldState
# ══════════════════════════════════════════════════════════════════

class TestDirectorWrite:
    """验证导演层不修改 WorldState."""

    def test_director_does_not_modify_world_state(self):
        ctx = _make_context()
        original_day = ctx.world_state.day
        original_weather = ctx.world_state.weather

        engine = UpdateEngine(ctx)

        director_result = DirectorResult(
            narrative="Test narrative",
            scene_update="A new scene description",
            world_changes=["something happened"],  # narrative hint, not real change
        )

        engine.apply_tick_results(director_result=director_result)

        # WorldState must NOT be modified
        assert ctx.world_state.day == original_day
        assert ctx.world_state.weather == original_weather


# ══════════════════════════════════════════════════════════════════
# Case 5: Propagation → KnowledgeEntry
# ══════════════════════════════════════════════════════════════════

class TestPropagationWrite:
    """验证传播写入 KnowledgeEntry 后 confidence 正确."""

    def test_propagation_knowledge_has_correct_confidence(self):
        ctx = _make_context()
        npc_b = ctx.npcs["npc_b"]

        # Set up an event
        from Schema.event_schema import Event, EventScope, EventType
        ctx.events = [Event(
            event_id="ev_1",
            description="A fire broke out",
            scope=EventScope.PUBLIC,
            location="test_inn",
        )]

        engine = UpdateEngine(ctx)

        from nodes.propagation_node import (
            PropagationNodeOutput,
            PropagationSuggestion,
            GlobalEventSuggestion,
        )

        output = PropagationNodeOutput(
            suggestions=[
                PropagationSuggestion(
                    target_npc_id="npc_b",
                    should_receive=True,
                    propagation_method="word_of_mouth",
                    content="A fire broke out in the inn",
                    confidence=0.65,
                    distortion=0.15,
                    reason="nearby NPC",
                ),
            ],
        )

        engine.apply_tick_results(propagation_output=output)

        added = [
            k for k in npc_b.knowledge
            if "fire" in k.fact
        ]
        assert len(added) == 1
        assert added[0].confidence == 0.65
        # Source should be HEARD (not DIRECT), because it's propagation
        assert added[0].source != KnowledgeSource.DIRECT

    def test_rumor_not_recorded_as_direct(self):
        ctx = _make_context()
        npc_a = ctx.npcs["npc_a"]
        from Schema.event_schema import Event, EventScope
        ctx.events = [Event(
            event_id="ev_2",
            description="rumor event",
            scope=EventScope.PUBLIC,
            location="test_inn",
        )]

        engine = UpdateEngine(ctx)

        from nodes.propagation_node import (
            PropagationNodeOutput, PropagationSuggestion,
        )

        output = PropagationNodeOutput(
            suggestions=[
                PropagationSuggestion(
                    target_npc_id="npc_a",
                    should_receive=True,
                    propagation_method="rumor",
                    content="Something weird happened, maybe",
                    confidence=0.35,
                    distortion=0.50,
                    reason="rumor spread",
                ),
            ],
        )

        engine.apply_tick_results(propagation_output=output)

        added = [
            k for k in npc_a.knowledge
            if "Something weird" in k.fact
        ]
        assert len(added) == 1
        # Rumor source must NOT be DIRECT
        assert added[0].source != KnowledgeSource.DIRECT
        # Low confidence preserved
        assert added[0].confidence == 0.35
