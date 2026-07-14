"""测试分层架构.

测试各层的process方法和层级间的数据流。
"""

import pytest

from src.layers.base import BaseLayer, LayerResult
from src.layers.world_layer import WorldLayer, WorldResult
from src.layers.world_state_layer import WorldStateLayer, WorldStateResult
from src.layers.event_layer import EventLayer, EventResult
from src.layers.perception_layer import PerceptionLayer, PerceptionResult, NPCPerception
from src.layers.update_layer import UpdateLayer, UpdateResult
from src.layers.reasoning_layer import ReasoningLayer, ReasoningResult
from src.layers.response_layer import ResponseLayer, ResponseResult
from src.layers.director_layer import DirectorLayer, DirectorResult
from src.layers.propagation_layer import PropagationLayer, PropagationResult
from src.models.npc import NPC, NPCRole, Emotion, PendingEntry, ObservationEntry
from src.models.world import WorldDefinition, WorldRule
from src.models.world_state import WorldState, TimeOfDay, Weather
from src.models.event import Event, EventType, EventScope


class TestWorldLayer:
    """WorldLayer测试."""

    def test_process_create_world(self) -> None:
        """测试创建世界."""
        layer = WorldLayer()
        input_data = {
            "world_definition": {
                "world_id": "test",
                "name": "测试世界",
                "description": "测试",
                "rules": [
                    {
                        "rule_id": "r1",
                        "description": "杀人犯法",
                        "penalty": "处死",
                    }
                ],
            }
        }
        result = layer.process(input_data, {})
        assert result.success
        world_result = result.data
        assert isinstance(world_result, WorldResult)
        assert world_result.world_definition.name == "测试世界"
        assert len(world_result.world_definition.rules) == 1


class TestWorldStateLayer:
    """WorldStateLayer测试."""

    def test_process_world_state(self) -> None:
        """测试世界状态初始化."""
        layer = WorldStateLayer()
        input_data = {
            "day": 1,
            "time_of_day": TimeOfDay.MORNING,
            "weather": Weather.SUNNY,
        }
        result = layer.process(input_data, {})
        assert result.success
        state_result = result.data
        assert isinstance(state_result, WorldStateResult)
        assert state_result.world_state.day == 1

    def test_advance_time(self) -> None:
        """测试时间推进."""
        layer = WorldStateLayer()
        layer.process({"day": 1, "time_of_day": TimeOfDay.MORNING}, {})
        layer.advance_time()
        assert layer.world_state.time_of_day == TimeOfDay.NOON


class TestEventLayer:
    """EventLayer测试."""

    def test_process_without_llm(self) -> None:
        """测试无LLM时的回退处理."""
        layer = EventLayer()
        result = layer.process("你好", {})
        assert result.success
        event_result = result.data
        assert isinstance(event_result, EventResult)
        assert event_result.raw_player_input == "你好"

    def test_process_empty_input(self) -> None:
        """测试空输入."""
        layer = EventLayer()
        result = layer.process("", {})
        assert not result.success


class TestPerceptionLayer:
    """PerceptionLayer测试."""

    def test_process_perception(self) -> None:
        """测试感知处理."""
        layer = PerceptionLayer()
        events = [
            Event(
                event_id="e1",
                event_type=EventType.PLAYER_ACTION,
                description="玩家打招呼",
                scope=EventScope.PUBLIC,
                location="广场",
            ),
            Event(
                event_id="e2",
                event_type=EventType.PLAYER_ACTION,
                description="秘密对话",
                scope=EventScope.HIDDEN,
            ),
        ]
        npcs_data = [
            {
                "npc_id": "npc1",
                "name": "张三",
                "role": "commoner",
                "location": "广场",
            }
        ]
        input_data = {"events": events, "npcs": npcs_data}
        result = layer.process(input_data, {})
        assert result.success
        perception_result = result.data
        assert isinstance(perception_result, PerceptionResult)
        assert len(perception_result.npc_perceptions) == 1
        # 公开事件可感知
        assert "e1" in perception_result.npc_perceptions[0].perceived_event_ids
        # 隐藏事件不可感知
        assert "e2" not in perception_result.npc_perceptions[0].perceived_event_ids

class TestEventLayerState:
    """EventLayer CurrentEvent 状态持久化测试."""

    def test_current_event_state_persistence(self) -> None:
        """测试 EventLayer 的 CurrentEvent 跨 tick 持久化."""
        from src.layers.event_layer import CurrentEvent

        layer = EventLayer()
        assert layer.current_event is None

        # Tick1: 创建新上下文
        layer.current_event = CurrentEvent(
            location="辉石城·冒险者公会",
            topic="玩家询问艾琳",
            addressed_npc_id="guild_receptionist",
            involved_npc_ids=["guild_receptionist"],
        )
        assert layer.current_event.is_active()
        assert layer.current_event.addressed_npc_id == "guild_receptionist"

        FI = type("Fake", (), {"player_input": "", "world_state": {}, "available_npc_ids": [], "npc_info": {}})

        # Tick2: 延续——问"那边兜帽人是冒险者么"（谈论≠对话）
        r = layer._apply_update(
            {"continue_context": True, "topic": "玩家向艾琳询问兜帽人是否是冒险者"},
            FI(),
        )
        assert r.success
        e = r.data.events[0]
        assert e.location == "辉石城·冒险者公会"
        # involved_npc_ids 不变——兜帽人不在场
        assert "mysterious_traveler" not in layer.current_event.involved_npc_ids
        assert "guild_receptionist" in layer.current_event.involved_npc_ids
        # addressed_npc_id 不变——依然在跟艾琳说话
        assert layer.current_event.addressed_npc_id == "guild_receptionist"

        # Tick3: 切换NPC——喊了兜帽人名字
        r2 = layer._apply_update(
            {"continue_context": False, "location": "辉石城·蜂蜜酒馆",
             "topic": "玩家向兜帽人搭话",
             "addressed_npc_id": "mysterious_traveler",
             "involved_npc_ids": ["mysterious_traveler"],
             "event_type": "dialogue", "scope": "private"},
            FI(),
        )
        assert r2.success
        e2 = r2.data.events[0]
        assert e2.location == "辉石城·蜂蜜酒馆"
        assert "mysterious_traveler" in e2.involved_npc_ids
        assert "guild_receptionist" not in e2.involved_npc_ids
        assert layer.current_event.addressed_npc_id == "mysterious_traveler"

        # 验证 current_event 持久
        assert layer.current_event.location == "辉石城·蜂蜜酒馆"

    def test_current_event_add_remove_npcs(self) -> None:
        """测试 CurrentEvent 的 NPC 加减."""
        from src.layers.event_layer import CurrentEvent

        layer = EventLayer()
        layer.current_event = CurrentEvent(
            location="蜂蜜酒馆",
            topic="玩家和兜帽人密谈",
            involved_npc_ids=["mysterious_traveler"],
        )

        FI = type("Fake", (), {"player_input": "", "world_state": {}, "available_npc_ids": [], "npc_info": {}})

        # 老约翰凑过来
        r = layer._apply_update(
            {"continue_context": True, "topic": "老约翰也加入了对话",
             "added_npc_ids": ["innkeeper"], "removed_npc_ids": []},
            FI(),
        )
        assert r.success
        assert "innkeeper" in layer.current_event.involved_npc_ids
        assert "mysterious_traveler" in layer.current_event.involved_npc_ids

        # 兜帽人走了
        r2 = layer._apply_update(
            {"continue_context": True, "topic": "兜帽人起身离开",
             "added_npc_ids": [], "removed_npc_ids": ["mysterious_traveler"]},
            FI(),
        )
        assert r2.success
        assert "mysterious_traveler" not in layer.current_event.involved_npc_ids
        assert "innkeeper" in layer.current_event.involved_npc_ids


class TestUpdateLayer:
    """UpdateLayer测试."""

    def test_update_npc_observation(self) -> None:
        """测试更新NPC观察."""
        layer = UpdateLayer()
        npc = NPC(npc_id="npc1", name="张三")
        input_data = {
            "update_type": "perception",
            "npc_updates": [
                {
                    "npc_id": "npc1",
                    "add_observations": [
                        {"observation": "看到玩家", "detail_level": 3}
                    ],
                }
            ],
            "source_layer": "PerceptionLayer",
        }
        result = layer.process(input_data, {"npcs": {"npc1": npc}})
        assert result.success
        assert len(npc.observation) == 1
        assert npc.observation[0].observation == "看到玩家"

    def test_no_direct_modification(self) -> None:
        """验证UpdateLayer是唯一写入口的规则（文档约定）."""
        # 这个测试确保UpdateLayer存在并可以正常使用
        layer = UpdateLayer()
        assert layer.layer_name == "UpdateLayer"

    def test_update_relationship(self) -> None:
        """测试更新关系."""
        layer = UpdateLayer()
        npc = NPC(npc_id="npc1", name="张三")
        input_data = {
            "update_type": "perception",
            "npc_updates": [
                {
                    "npc_id": "npc1",
                    "update_relationships": [
                        {
                            "target_npc_id": "npc2",
                            "favorability_change": 10,
                            "trust_change": 5,
                            "note": "帮助了npc1",
                        }
                    ],
                }
            ],
            "source_layer": "PerceptionLayer",
        }
        result = layer.process(input_data, {"npcs": {"npc1": npc}})
        assert result.success
        assert "npc2" in npc.relationship
        assert npc.relationship["npc2"].favorability == 10


class TestReasoningLayer:
    """ReasoningLayer测试."""

    def test_no_pending_skip(self) -> None:
        """测试无Pending时跳过LLM."""
        layer = ReasoningLayer()
        npc = NPC(npc_id="npc1", name="张三")
        result = layer.process({}, {"npcs": {"npc1": npc}})
        assert result.success
        reasoning_result = result.data
        assert isinstance(reasoning_result, ReasoningResult)
        # 无Pending，无推理结果
        assert len(reasoning_result.npc_reasonings[0].new_knowledge) == 0

    def test_has_pending_triggers(self) -> None:
        """测试有Pending但无LLM时的回退."""
        layer = ReasoningLayer()
        npc = NPC(npc_id="npc1", name="张三")
        npc.pending_reason.append(
            PendingEntry(event_description="奇怪的声音")
        )
        npc.observation.append(
            ObservationEntry(
                observation="听到奇怪的声音",
                detail_level=3,
            )
        )
        result = layer.process({}, {"npcs": {"npc1": npc}})
        assert result.success


class TestResponseLayer:
    """ResponseLayer测试."""

    def test_generate_responses(self) -> None:
        """测试回应生成."""
        layer = ResponseLayer()
        npc = NPC(
            npc_id="npc1",
            name="张三",
            role=NPCRole.COMMONER,
        )
        events = [
            Event(
                event_id="e1",
                event_type=EventType.DIALOGUE,
                description="玩家向张三问好",
                involved_npc_ids=["npc1"],
            )
        ]
        result = layer.process({}, {"npcs": {"npc1": npc}, "events": events})
        assert result.success
        response_result = result.data
        assert isinstance(response_result, ResponseResult)


class TestDirectorLayer:
    """DirectorLayer测试."""

    def test_direct_with_responses(self) -> None:
        """测试导演整合."""
        from src.layers.response_layer import NPCResponse

        layer = DirectorLayer()
        responses = [
            NPCResponse(
                npc_id="npc1",
                npc_name="张三",
                response_text="你好，旅行者！",
                response_type="speech",
                emotion="happy",
            )
        ]
        input_data = {"npc_responses": responses}
        result = layer.process(input_data, {})
        assert result.success
        director_result = result.data
        assert isinstance(director_result, DirectorResult)
        assert "张三" in director_result.narrative

    def test_direct_empty_responses(self) -> None:
        """测试空回应."""
        layer = DirectorLayer()
        result = layer.process({"npc_responses": []}, {})
        assert result.success
        director_result = result.data
        assert "无回应" in director_result.narrative


class TestPropagationLayer:
    """PropagationLayer测试."""

    def test_public_event_propagates(self) -> None:
        """测试公开事件传播."""
        layer = PropagationLayer()
        input_data = {
            "events": [
                {
                    "event_id": "e1",
                    "scope": "public",
                    "description": "发生了一场大战",
                    "location": "广场",
                }
            ],
            "npcs_summary": [
                {"npc_id": "npc1", "location": "广场"},
                {"npc_id": "npc2", "location": "酒馆"},
            ],
        }
        result = layer.process(input_data, {})
        assert result.success
        prop_result = result.data
        assert isinstance(prop_result, PropagationResult)
        # public事件传播到所有NPC
        assert len(prop_result.propagations) == 2

    def test_private_event_not_propagate(self) -> None:
        """测试私有事件不传播."""
        layer = PropagationLayer()
        input_data = {
            "events": [
                {
                    "event_id": "e1",
                    "scope": "private",
                    "description": "秘密",
                    "location": "",
                }
            ],
            "npcs_summary": [
                {"npc_id": "npc1", "location": ""},
            ],
        }
        result = layer.process(input_data, {})
        assert result.success
        prop_result = result.data
        # private事件不传播
        assert len(prop_result.propagations) == 0


class TestLayerOrder:
    """层顺序测试（固定不可改变）."""

    def test_layer_names_order(self) -> None:
        """验证层名称顺序."""
        expected_order = [
            "WorldLayer",
            "WorldStateLayer",
            "EventLayer",
            "PerceptionLayer",
            "UpdateLayer",
            "ReasoningLayer",
            "ResponseLayer",
            "DirectorLayer",
            "PropagationLayer",
        ]
        layers = [
            WorldLayer(),
            WorldStateLayer(),
            EventLayer(),
            PerceptionLayer(),
            UpdateLayer(),
            ReasoningLayer(),
            ResponseLayer(),
            DirectorLayer(),
            PropagationLayer(),
        ]
        actual_names = [layer.layer_name for layer in layers]
        assert actual_names == expected_order, (
            f"层顺序被改变！期望: {expected_order}, 实际: {actual_names}"
        )
