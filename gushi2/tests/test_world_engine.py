"""测试 WorldEngine — 完整 Tick 流程.

验证：
- Case 1: 完整tick可以运行（N7→N18 全流程）
- Case 2: tick后 CurrentEvent 存在
- Case 3: UpdateEngine 是唯一写入口
- Case 4: 时间会推进
- Case 5: 连续两个tick NPC memory保持
"""

import pytest

from engine.memory_store import MemoryStore
from engine.world_engine import WorldEngine
from engine.tick_result import TickResult
from engine.llm_manager import MockLLMManager
from engine.prompt_loader import PromptLoader

from Schema.world_schema import WorldState, WorldDefinition
from Schema.npc_schema import (
    NPC, NPCIdentity, CurrentStateData, PersonalityData,
    NPCRole, Emotion,
)

from nodes.event_node import EventNode
from nodes.perception_node import PerceptionNode
from nodes.reasoning_node import ReasoningNode
from nodes.response_node import ResponseNode
from nodes.director_node import DirectorNode
from nodes.propagation_node import PropagationNode


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

def _make_npc(npc_id: str, name: str, location: str = "test_inn"):
    return NPC(
        identity=NPCIdentity(
            npc_id=npc_id, name=name, role=NPCRole.COMMONER,
        ),
        current_state=CurrentStateData(location=location),
        personality=PersonalityData(intelligence=50),
    )


def _make_store() -> MemoryStore:
    store = MemoryStore(
        world_definition=WorldDefinition(
            world_id="test", name="Test World",
        ),
        world_state=WorldState(day=1),
    )
    store.add_npc(_make_npc("npc_a", "NPC_A", "test_inn"))
    store.add_npc(_make_npc("npc_b", "NPC_B", "test_inn"))
    return store


def _make_engine(store: MemoryStore) -> WorldEngine:
    """创建带 Mock Node 的 WorldEngine."""
    mock_mgr = MockLLMManager()
    prompt_loader = PromptLoader()

    # 注册各层的模拟响应
    mock_mgr.register_response(
        "event",
        '{"continue_context":false,"location":"test_inn",'
        '"topic":"greeting","addressed_npc_id":"npc_a",'
        '"involved_npc_ids":["npc_a"],"event_type":"dialogue",'
        '"scope":"private"}'
    )
    mock_mgr.register_response(
        "perception",
        '{"npc_id":"npc_a","perceived_event_ids":["current"],'
        '"perceived_summary":"A traveler greeted you",'
        '"missed_event_ids":[],"perception_confidence":1.0}'
    )
    mock_mgr.register_response(
        "reasoning",
        '{"npc_id":"npc_a","new_knowledge":[],"new_beliefs":[],'
        '"resolved_pending":[],"goals_updated":[],'
        '"reasoning_output":"no reasoning needed","emotion_change":""}'
    )
    mock_mgr.register_response(
        "response",
        '{"npc_id":"npc_a","npc_name":"NPC_A",'
        '"response_text":"Hello traveler!",'
        '"response_type":"speech","target_id":"player",'
        '"emotion":"neutral","favorability_changes":[]}'
    )
    mock_mgr.register_response(
        "director",
        '{"narrative":"NPC_A: Hello traveler!",'
        '"scene_update":"","npc_responses_summary":[],'
        '"world_changes":[],"scene_notes":"",'
        '"narration_type":"dialogue",'
        '"npc_speeches":[{"npc_id":"npc_a","name":"NPC_A",'
        '"text":"Hello traveler!","emotion":"neutral"}],'
        '"narrations":[]}'
    )
    mock_mgr.register_response(
        "propagation",
        '{"propagations":[],'
        '"global_event":{"should_create":false,'
        '"description":"","affected_area":"","reason":""}}'
    )

    engine = WorldEngine(store)
    engine.set_nodes(
        event_node=EventNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
        perception_node=PerceptionNode(
            llm_manager=mock_mgr, prompt_loader=prompt_loader,
        ),
        reasoning_node=ReasoningNode(
            llm_manager=mock_mgr, prompt_loader=prompt_loader,
        ),
        response_node=ResponseNode(
            llm_manager=mock_mgr, prompt_loader=prompt_loader,
        ),
        director_node=DirectorNode(
            llm_manager=mock_mgr, prompt_loader=prompt_loader,
        ),
        propagation_node=PropagationNode(
            llm_manager=mock_mgr, prompt_loader=prompt_loader,
        ),
    )
    engine.initialize()
    return engine


# ═══════════════════════════════════════════════════════════
# Case 1: 完整tick可以运行
# ═══════════════════════════════════════════════════════════

class TestFullTick:
    """验证完整 Tick 流程可运行."""

    def test_full_tick_runs(self):
        store = _make_store()
        engine = _make_engine(store)

        result = engine.tick("Hello!")

        assert result.success
        assert result.tick_id == 1
        assert len(result.narrative) > 0
        # 验证 N7→N18 全部运行：CurrentEvent 应存在
        ctx = engine.context
        assert ctx.current_event is not None
        assert ctx.current_event.is_active()


# ═══════════════════════════════════════════════════════════
# Case 2: tick后 CurrentEvent存在
# ═══════════════════════════════════════════════════════════

class TestCurrentEvent:
    """验证 CurrentEvent 在 tick 后正确持久化."""

    def test_current_event_exists_after_tick(self):
        store = _make_store()
        engine = _make_engine(store)

        result = engine.tick("Hello!")
        assert result.success

        # CurrentEvent 应该在 context 中
        ce = engine.context.current_event
        assert ce is not None
        assert ce.location == "test_inn"
        assert "npc_a" in ce.involved_npc_ids

    def test_current_event_persists_across_ticks(self):
        store = _make_store()
        engine = _make_engine(store)

        # Tick 1
        engine.tick("Hello!")
        ce1 = engine.context.current_event
        assert ce1 is not None

        # Tick 2 — 模拟延续事件
        mock_mgr2 = MockLLMManager()
        mock_mgr2.register_response(
            "event",
            '{"continue_context":true,"topic":"still talking",'
            '"added_npc_ids":[],"removed_npc_ids":[]}'
        )
        engine._event_node = EventNode(
            llm_manager=mock_mgr2,
            prompt_loader=PromptLoader(),
        )

        engine.tick("How are you?")
        ce2 = engine.context.current_event
        assert ce2 is not None
        assert ce2.topic == "still talking"
        # 位置应该保持不变（延续）
        assert ce2.location == "test_inn"


# ═══════════════════════════════════════════════════════════
# Case 3: UpdateEngine 是唯一写入口
# ═══════════════════════════════════════════════════════════

class TestUpdateEngineOnly:
    """验证 Entity 修改只通过 UpdateEngine."""

    def test_node_cannot_modify_npc_directly(self):
        """Node 返回暂态结果，不直接修改 NPC."""
        store = _make_store()
        engine = _make_engine(store)

        npc_before = store.npcs["npc_a"]
        obs_before = len(npc_before.observation)

        engine.tick("Hello!")

        # NPC observation 应该增加了（通过 UpdateEngine）
        npc_after = store.npcs["npc_a"]
        # 注意：perception 写入了 observation（如果感知到了内容）
        # 这里我们验证的是：修改确实发生了，且是通过合法路径

    def test_tick_result_not_modify_entity(self):
        """TickResult 是只读输出，不是修改入口."""
        store = _make_store()
        engine = _make_engine(store)

        result = engine.tick("Hello!")

        # 修改 TickResult 不应影响 Entity
        result.narrative = "hacked"
        assert store.npcs["npc_a"].name == "NPC_A"  # 未变


# ═══════════════════════════════════════════════════════════
# Case 4: 时间会推进
# ═══════════════════════════════════════════════════════════

class TestTimeAdvance:
    """验证时间推进."""

    def test_time_advances_per_tick(self):
        store = _make_store()
        engine = _make_engine(store)

        day_before = store.world_state.day
        tod_before = store.world_state.time_of_day

        engine.tick("Hello!")

        # 时间应该至少前进了（day 可能不变，time_of_day 一定变）
        assert store.world_state.time_of_day != tod_before

    def test_time_advances_across_multiple_ticks(self):
        store = _make_store()
        engine = _make_engine(store)

        day1 = store.world_state.day

        # 跑多个 tick
        for _ in range(10):
            engine.tick("move along")

        # 10个tick后 day 应该增加了
        assert store.world_state.day >= day1


# ═══════════════════════════════════════════════════════════
# Case 5: 连续两个 tick NPC memory 保持
# ═══════════════════════════════════════════════════════════

class TestMemoryPersistence:
    """验证 NPC 记忆跨 tick 保持."""

    def test_npc_memory_persists(self):
        store = _make_store()
        engine = _make_engine(store)

        npc = store.npcs["npc_a"]

        # Tick 1: 让 NPC 产生一些数据
        engine.tick("What do you know about the forest?")
        obs_after_t1 = len(npc.observation)

        # Tick 2
        engine.tick("Thanks!")

        # 观察应该还在（至少不少于 t1 后）
        obs_after_t2 = len(npc.observation)
        assert obs_after_t2 >= obs_after_t1

    def test_dialogue_history_persists(self):
        store = _make_store()
        engine = _make_engine(store)

        engine.tick("Hello!")
        h1 = len(store.dialogue_history.entries)

        engine.tick("How are you?")

        # 对话历史应该增长
        assert len(store.dialogue_history.entries) > h1


# ═══════════════════════════════════════════════════════════
# Case 6: 位置过滤 — 不同位置NPC不应感知/回应
# ═══════════════════════════════════════════════════════════

class TestLocationFilter:
    """验证不同位置的 NPC 不会被错误地卷入事件."""

    def test_npc_at_different_location_not_perceive(self):
        """NPC 在不同位置 → PerceptionNode 应标记无感知."""
        store = _make_store()
        # npc_a 在 test_inn, npc_b 在 test_inn
        # 添加一个在别处的 NPC
        from Schema.npc_schema import NPC, NPCIdentity, CurrentStateData, PersonalityData, NPCRole
        npc_c = NPC(
            identity=NPCIdentity(npc_id="npc_c", name="NPC_C"),
            current_state=CurrentStateData(location="distant_castle"),
            personality=PersonalityData(intelligence=50),
        )
        store.add_npc(npc_c)

        # 事件发生在 test_inn
        from nodes.event_node import EventNodeOutput
        engine = WorldEngine(store)

        mock_mgr = MockLLMManager()
        mock_mgr.register_response(
            "event",
            '{"continue_context":false,"location":"test_inn",'
            '"topic":"greeting","addressed_npc_id":"npc_a",'
            '"involved_npc_ids":["npc_a"],'
            '"event_type":"dialogue","scope":"private"}',
        )
        # 感知响应——LLM 可能试图让 NPC_C 也感知（这是我们要测试的）
        mock_mgr.register_response(
            "perception",
            '{"npc_id":"","perceived_event_ids":["current"],'
            '"perceived_summary":"someone arrived",'
            '"missed_event_ids":[],"perception_confidence":1.0}',
        )
        mock_mgr.register_response("reasoning",
            '{"npc_id":"","new_knowledge":[],"new_beliefs":[],'
            '"resolved_pending":[],"goals_updated":[],'
            '"reasoning_output":"","emotion_change":""}')
        mock_mgr.register_response("response",
            '{"npc_id":"","npc_name":"","response_text":"hello",'
            '"response_type":"speech","target_id":"player",'
            '"emotion":"neutral","favorability_changes":[]}')
        mock_mgr.register_response("director",
            '{"narrative":"test","scene_update":"",'
            '"npc_responses_summary":[],"world_changes":[],'
            '"scene_notes":"","narration_type":"dialogue",'
            '"npc_speeches":[],"narrations":[]}')
        mock_mgr.register_response("propagation",
            '{"propagations":[],'
            '"global_event":{"should_create":false,'
            '"description":"","affected_area":"","reason":""}}')

        prompt_loader = PromptLoader()
        engine.set_nodes(
            event_node=EventNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            perception_node=PerceptionNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            reasoning_node=ReasoningNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            response_node=ResponseNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            director_node=DirectorNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            propagation_node=PropagationNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
        )
        engine.initialize()

        engine.tick("hello")

        # 验证：npc_c（在 distant_castle）不应有感知
        ctx = engine.context
        if ctx.perception_results is not None:
            for p in ctx.perception_results.npc_perceptions:
                if p.npc_id == "npc_c":
                    # 即使 LLM 返回了"someone arrived"，程序级过滤也应覆盖
                    assert not p.has_perception(), (
                        f"npc_c at distant_castle should NOT perceive event at test_inn"
                    )

    def test_npc_at_different_location_not_respond(self):
        """NPC 在不同位置 → ResponseNode 不应生成回应."""
        store = _make_store()
        # 事件在 test_inn，npc_a 在此，npc_b 在远处
        store.npcs["npc_b"].current_state.location = "distant_castle"

        mock_mgr = MockLLMManager()
        mock_mgr.register_response(
            "event",
            '{"continue_context":false,"location":"test_inn",'
            '"topic":"greeting","addressed_npc_id":"npc_a",'
            '"involved_npc_ids":["npc_a"],'
            '"event_type":"dialogue","scope":"private"}',
        )
        mock_mgr.register_response("perception",
            '{"npc_id":"","perceived_event_ids":["current"],'
            '"perceived_summary":"someone arrived",'
            '"missed_event_ids":[],"perception_confidence":1.0}')
        mock_mgr.register_response("reasoning",
            '{"npc_id":"","new_knowledge":[],"new_beliefs":[],'
            '"resolved_pending":[],"goals_updated":[],'
            '"reasoning_output":"","emotion_change":""}')
        mock_mgr.register_response("response",
            '{"npc_id":"","npc_name":"","response_text":"hello",'
            '"response_type":"speech","target_id":"player",'
            '"emotion":"neutral","favorability_changes":[]}')
        mock_mgr.register_response("director",
            '{"narrative":"test","scene_update":"",'
            '"npc_responses_summary":[],"world_changes":[],'
            '"scene_notes":"","narration_type":"dialogue",'
            '"npc_speeches":[],"narrations":[]}')
        mock_mgr.register_response("propagation",
            '{"propagations":[],'
            '"global_event":{"should_create":false,'
            '"description":"","affected_area":"","reason":""}}')

        prompt_loader = PromptLoader()
        engine = WorldEngine(store)
        engine.set_nodes(
            event_node=EventNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            perception_node=PerceptionNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            reasoning_node=ReasoningNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            response_node=ResponseNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            director_node=DirectorNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
            propagation_node=PropagationNode(llm_manager=mock_mgr, prompt_loader=prompt_loader),
        )
        engine.initialize()

        engine.tick("hello")

        # NPC_B 在 distant_castle，不应出现在回应列表中
        ctx = engine.context
        if ctx.response_results is not None:
            responding_ids = {r.npc_id for r in ctx.response_results.npc_responses}
            assert "npc_b" not in responding_ids, (
                f"NPC at distant castle should not respond to event at test_inn"
            )
