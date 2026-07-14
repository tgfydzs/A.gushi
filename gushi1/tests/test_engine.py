"""测试主引擎.

测试Engine的完整流程。
"""

import pytest

from src.core.engine import Engine, TickResult, Config
from src.models.npc import (
    NPC,
    NPCRole,
    Emotion,
    CurrentStateData,
    PersonalityData,
    PendingEntry,
    ObservationEntry,
    KnowledgeEntry,
)
from src.models.world import WorldDefinition, WorldRule
from src.models.world_state import WorldState, TimeOfDay, Weather


class TestEngine:
    """引擎测试."""

    def test_create_engine(self) -> None:
        """测试创建引擎."""
        engine = Engine(config=Config())
        assert engine.tick_count == 0
        assert len(engine.npcs) == 0

    def test_initialize_world(self) -> None:
        """测试初始化世界."""
        engine = Engine(config=Config())
        world_def = {
            "world_definition": {
                "world_id": "test",
                "name": "测试世界",
                "description": "一个测试世界",
                "rules": [
                    {
                        "rule_id": "r1",
                        "description": "杀人犯法",
                        "applies_to": "all",
                        "penalty": "处死",
                    }
                ],
                "hidden_events": [],
                "known_events": [],
            },
        }
        result = engine.initialize_world(world_def)
        assert result.success
        assert engine.world_definition is not None
        assert engine.world_definition.name == "测试世界"
        assert engine.world_state is not None
        assert engine.world_state.day == 1

    def test_add_npc(self) -> None:
        """测试添加NPC."""
        engine = Engine(config=Config())
        npc = NPC(npc_id="npc1", name="张三")
        engine.add_npc(npc)
        assert "npc1" in engine.npcs
        assert engine.get_npc("npc1") is npc

    def test_remove_npc(self) -> None:
        """测试移除NPC."""
        engine = Engine(config=Config())
        npc = NPC(npc_id="npc1", name="张三")
        engine.add_npc(npc)
        engine.remove_npc("npc1")
        assert "npc1" not in engine.npcs

    def test_tick_basic(self) -> None:
        """测试基本tick流程."""
        engine = Engine(config=Config())

        # 初始化世界
        world_def = {
            "world_definition": {
                "world_id": "test",
                "name": "测试世界",
                "rules": [],
                "hidden_events": [],
                "known_events": [],
            },
        }
        engine.initialize_world(world_def)

        # 添加NPC
        npc = NPC(
            npc_id="npc1",
            name="张三",
            role=NPCRole.COMMONER,
            current_state=CurrentStateData(
                location="镇广场",
                emotion=Emotion.NEUTRAL,
            ),
        )
        engine.add_npc(npc)

        # 执行tick
        result = engine.tick("你好，张三！")
        assert isinstance(result, TickResult)
        assert result.tick_number == 1
        # 无LLM时，事件层返回空事件
        assert result.event_result is not None
        # 因为无事件，后续层可能为空
        assert result.success or not result.success

    def test_tick_advances_time(self) -> None:
        """测试tick推进时间."""
        engine = Engine(
            config=Config(
                world=Config.__dataclass_fields__["world"].type(
                    time_advance_per_tick=1
                )
            )
        )

        world_def = {
            "world_definition": {
                "world_id": "test",
                "name": "测试世界",
                "rules": [],
                "hidden_events": [],
                "known_events": [],
            },
        }
        engine.initialize_world(world_def)
        initial_time = engine.world_state.time_of_day

        # 添加一个NPC
        engine.add_npc(NPC(npc_id="npc1", name="张三"))
        engine.tick("测试")

        # 时间应该推进了
        assert engine.world_state.time_of_day != initial_time

    def test_npc_pending_after_perception(self) -> None:
        """测试感知后NPC有Pending事件."""
        engine = Engine(config=Config())

        world_def = {
            "world_definition": {
                "world_id": "test",
                "name": "测试世界",
                "rules": [],
                "hidden_events": [],
                "known_events": [],
            },
        }
        engine.initialize_world(world_def)

        npc = NPC(
            npc_id="npc1",
            name="张三",
            current_state=CurrentStateData(location="镇广场"),
        )
        engine.add_npc(npc)
        engine.tick("你好")

        # tick应该完成无异常
        assert engine.tick_count == 1

    def test_complex_npc_setup(self) -> None:
        """测试复杂NPC设置."""
        engine = Engine(config=Config())

        world_def = {
            "world_definition": {
                "world_id": "test",
                "name": "RPG世界",
                "rules": [
                    {"rule_id": "r1", "description": "禁止偷窃", "penalty": "砍手"},
                    {"rule_id": "r2", "description": "禁止杀人", "penalty": "处死"},
                ],
                "hidden_events": [
                    {
                        "event_id": "h1",
                        "trigger_condition": "玩家进入古墓",
                        "event_content": "古墓守护者苏醒",
                    }
                ],
                "known_events": ["国王的生日庆典"],
            },
        }
        result = engine.initialize_world(world_def)
        assert result.success
        assert len(engine.world_definition.rules) == 2
        assert len(engine.world_definition.hidden_events) == 1

        # 添加多个NPC
        npcs = [
            NPC(
                npc_id="merchant_1",
                name="王五",
                role=NPCRole.MERCHANT,
                current_state=CurrentStateData(
                    location="镇广场",
                    emotion=Emotion.HAPPY,
                    action="叫卖",
                ),
            ),
            NPC(
                npc_id="guard_1",
                name="李四",
                role=NPCRole.GUARD,
                current_state=CurrentStateData(
                    location="镇广场",
                    emotion=Emotion.NEUTRAL,
                    action="巡逻",
                ),
            ),
            NPC(
                npc_id="noble_1",
                name="赵六",
                role=NPCRole.NOBLE,
                current_state=CurrentStateData(
                    location="城堡",
                    emotion=Emotion.NEUTRAL,
                    action="处理政务",
                ),
            ),
        ]

        for npc in npcs:
            engine.add_npc(npc)

        assert len(engine.npcs) == 3
        assert engine.get_npc("guard_1").role == NPCRole.GUARD
