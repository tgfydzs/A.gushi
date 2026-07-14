"""测试数据模型."""

import pytest

from src.models.npc import (
    NPC,
    NPCRole,
    Emotion,
    PersonalityData,
    CurrentStateData,
    MemoryEntry,
    KnowledgeEntry,
    BeliefEntry,
    ObservationEntry,
    PendingEntry,
    RelationshipValue,
    RelationshipType,
    Item,
    GoalEntry,
)
from src.models.world import (
    WorldDefinition,
    WorldRule,
    HiddenEvent,
)
from src.models.world_state import (
    WorldState,
    TimeOfDay,
    Weather,
    GlobalEvent,
    EventPropagation,
)
from src.models.event import (
    Event,
    EventDetail,
    EventType,
    EventScope,
)
from src.models.result import BaseResult


class TestNPC:
    """NPC数据模型测试."""

    def test_create_npc(self) -> None:
        """测试创建NPC."""
        npc = NPC(npc_id="test_1", name="张三", role=NPCRole.MERCHANT)
        assert npc.npc_id == "test_1"
        assert npc.name == "张三"
        assert npc.role == NPCRole.MERCHANT
        assert npc.current_state.emotion == Emotion.NEUTRAL
        assert npc.personality.openness == 50

    def test_empty_pending(self) -> None:
        """测试空Pending判断."""
        npc = NPC(npc_id="test_1", name="张三")
        assert not npc.has_pending()

    def test_has_pending(self) -> None:
        """测试有Pending时判断."""
        npc = NPC(npc_id="test_1", name="张三")
        npc.pending_reason.append(
            PendingEntry(event_description="可疑的声音")
        )
        assert npc.has_pending()

    def test_knows_fact(self) -> None:
        """测试NPC知识查询."""
        npc = NPC(npc_id="test_1", name="张三")
        npc.knowledge.append(
            KnowledgeEntry(fact="王都戒严中", source="heard")
        )
        assert npc.knows("戒严")
        assert not npc.knows("李四的阴谋")

    def test_believes(self) -> None:
        """测试NPC信念查询."""
        npc = NPC(npc_id="test_1", name="张三")
        npc.belief.append(
            BeliefEntry(belief="这个世界是公平的", strength=0.8)
        )
        assert npc.believes("公平")
        assert not npc.believes("邪恶")

    def test_full_npc_creation(self) -> None:
        """测试完整NPC创建."""
        npc = NPC(
            npc_id="merchant_1",
            name="王五",
            role=NPCRole.MERCHANT,
            personality=PersonalityData(
                openness=70,
                conscientiousness=65,
                extraversion=80,
                agreeableness=55,
                neuroticism=30,
                traits=["精明", "健谈"],
            ),
            current_state=CurrentStateData(
                location="镇广场",
                emotion=Emotion.HAPPY,
                action="叫卖",
            ),
        )
        assert npc.personality.openness == 70
        assert npc.personality.traits == ["精明", "健谈"]
        assert npc.current_state.location == "镇广场"
        assert npc.current_state.action == "叫卖"


class TestWorld:
    """世界定义测试."""

    def test_create_world(self) -> None:
        """测试创建世界."""
        world = WorldDefinition(
            world_id="test_world",
            name="测试世界",
            description="一个用于测试的世界",
        )
        assert world.world_id == "test_world"
        assert world.name == "测试世界"

    def test_trigger_hidden_event(self) -> None:
        """测试触发隐藏事件."""
        world = WorldDefinition(
            world_id="test_world",
            name="测试世界",
            hidden_events=[
                HiddenEvent(
                    event_id="hidden_1",
                    trigger_condition="玩家进入古墓",
                    event_content="古墓中出现了幽灵",
                )
            ],
        )
        triggered = world.check_triggered_events("玩家进入古墓")
        assert len(triggered) == 1
        assert triggered[0].event_id == "hidden_1"
        assert triggered[0].is_triggered

    def test_world_rules(self) -> None:
        """测试世界规则."""
        world = WorldDefinition(
            world_id="test_world",
            name="测试世界",
            rules=[
                WorldRule(
                    rule_id="rule_1",
                    description="杀人犯法",
                    penalty="处死或流放",
                )
            ],
        )
        assert len(world.rules) == 1
        assert world.rules[0].penalty == "处死或流放"


class TestWorldState:
    """世界状态测试."""

    def test_advance_time(self) -> None:
        """测试时间推进."""
        state = WorldState(day=1, time_of_day=TimeOfDay.MORNING)
        state.advance_time()
        assert state.time_of_day == TimeOfDay.NOON
        # 推进到第二天（再6步从NOON回到MORNING）
        for _ in range(6):
            state.advance_time()
        assert state.day == 2
        assert state.time_of_day == TimeOfDay.MORNING

    def test_global_events(self) -> None:
        """测试全局事件."""
        state = WorldState()
        state.add_global_event(
            GlobalEvent(
                event_id="ge_1",
                description="王都戒严",
                affected_area="王都",
            )
        )
        active = state.get_active_events()
        assert len(active) == 1

        state.global_events[0].is_active = False
        active = state.get_active_events()
        assert len(active) == 0


class TestEvent:
    """事件模型测试."""

    def test_create_event(self) -> None:
        """测试创建事件."""
        event = Event(
            event_id="evt_1",
            event_type=EventType.DIALOGUE,
            description="玩家向NPC问好",
            scope=EventScope.PUBLIC,
            location="镇广场",
        )
        assert event.event_type == EventType.DIALOGUE
        assert event.scope == EventScope.PUBLIC

    def test_get_npc_ids(self) -> None:
        """测试获取事件涉及的NPC."""
        event = Event(
            event_id="evt_1",
            involved_npc_ids=["npc_1", "npc_2"],
            details=[
                EventDetail(
                    detail_id="d_1",
                    action="对话",
                    subject_id="player",
                    target_id="npc_3",
                )
            ],
        )
        ids = event.get_npc_ids()
        assert "npc_1" in ids
        assert "npc_2" in ids
        assert "npc_3" in ids
        assert "player" in ids


class TestBaseResult:
    """基础Result测试."""

    def test_success_result(self) -> None:
        """测试成功结果."""
        result = BaseResult(success=True, layer_name="Test")
        assert result.is_success()
        assert not result.is_error()

    def test_error_result(self) -> None:
        """测试失败结果."""
        result = BaseResult(
            success=False,
            error="测试错误",
            layer_name="Test",
        )
        assert not result.is_success()
        assert result.is_error()
        assert result.error == "测试错误"
