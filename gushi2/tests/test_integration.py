"""集成测试 — 完整 E2E 流程.

验证：
- main.py create_engine() 可正常运行
- LLMManager 正确注入到所有 Node
- 配置可加载
- 数据加载器工作正常
"""

import pytest

from engine.llm_manager import MockLLMManager, NodeLLMConfig
from engine.loader import initialize_store, load_world, load_npcs
from engine.memory_store import MemoryStore
from engine.persistence import save, load
from engine.logger import TickLogger
from config import load_llm_config, load_runtime_config

from nodes.event_node import EventNode
from nodes.perception_node import PerceptionNode


# ═══════════════════════════════════════════════════════
# Test: LLMManager
# ═══════════════════════════════════════════════════════

class TestLLMManager:
    def test_mock_llm_manager_works(self):
        mgr = MockLLMManager()
        mgr.register_response(
            "event", '{"continue_context": false}'
        )
        result = mgr.chat("event", "system prompt", "user prompt")
        assert "continue_context" in result

    def test_mock_llm_manager_node_specific(self):
        mgr = MockLLMManager()
        mgr.register_response("event", '{"ev": 1}')
        mgr.register_response("perception", '{"perc": 1}')

        # 不同节点可以有不同的响应
        assert "ev" in mgr.chat("event", "", "")
        assert mgr.chat("event", "", "") != mgr.chat("perception", "test", "")

    def test_node_config(self):
        mgr = MockLLMManager()
        config = NodeLLMConfig(model="gpt-4", temperature=0.3)
        mgr.set_node_config("event", config)
        assert mgr.get_node_config("event").temperature == 0.3


# ═══════════════════════════════════════════════════════
# Test: Node 注入 LLMManager
# ═══════════════════════════════════════════════════════

class TestNodeLLMManager:
    def test_event_node_uses_llm_manager(self):
        mgr = MockLLMManager()
        mgr.register_response(
            "event",
            '{"continue_context":false,"location":"inn",'
            '"topic":"test","addressed_npc_id":"npc_1",'
            '"involved_npc_ids":["npc_1"],'
            '"event_type":"dialogue","scope":"private"}'
        )
        node = EventNode(llm_manager=mgr)
        assert node.llm_manager is mgr
        # Prompt name matches: "event"
        assert node.prompt_name == "event"

    def test_perception_node_uses_llm_manager(self):
        node = PerceptionNode(llm_manager=MockLLMManager())
        assert node.prompt_name == "perception"


# ═══════════════════════════════════════════════════════
# Test: 数据加载
# ═══════════════════════════════════════════════════════

class TestDataLoader:
    def test_load_world(self):
        world = load_world("cangyue")
        assert world.name == "苍月大陆"
        assert len(world.rules) > 0
        assert len(world.hidden_events) > 0

    def test_load_npcs(self):
        npcs = load_npcs("cangyue_npcs")
        assert len(npcs) > 0
        assert npcs[0].name in ("老约翰",)
        # 验证角色映射
        roles = {n.npc_id: n.identity.role.value for n in npcs}
        assert roles.get("innkeeper") == "merchant"

    def test_initialize_store(self):
        store = initialize_store("cangyue", "cangyue_npcs")
        assert len(store.npcs) > 0
        assert store.world_definition.name == "苍月大陆"
        assert store.world_state.day == 1


# ═══════════════════════════════════════════════════════
# Test: Persistence
# ═══════════════════════════════════════════════════════

class TestPersistence:
    def test_save_and_load(self):
        store = initialize_store("cangyue", "cangyue_npcs")
        original_day = store.world_state.day

        # 修改状态
        store.world_state.day = 5
        path = save(store, save_name="test_persistence")

        # 恢复
        store.world_state.day = original_day
        ok = load(store, "test_persistence")
        assert ok
        assert store.world_state.day == 5


# ═══════════════════════════════════════════════════════
# Test: Logger
# ═══════════════════════════════════════════════════════

class TestLogger:
    def test_logger_writes_file(self):
        logger = TickLogger(enabled=True)
        logger.start_tick(tick_id=9999, player_input="test")
        logger.log_node("N7", success=True, raw_output='{"ok":true}')
        path = logger.finish_tick(narrative="test narrative")
        assert path is not None
        assert "tick_9999.json" in path


# ═══════════════════════════════════════════════════════
# Test: 配置加载
# ═══════════════════════════════════════════════════════

class TestConfig:
    def test_load_llm_config(self):
        cfg = load_llm_config()
        assert cfg.provider in ("deepseek", "openai")
        assert cfg.model in ("deepseek-chat", "gpt-4")

    def test_load_runtime_config(self):
        cfg = load_runtime_config()
        assert isinstance(cfg.max_ticks, int)

    def test_node_configs(self):
        cfg = load_llm_config()
        event_cfg = cfg.nodes.get("event")
        if event_cfg:
            assert event_cfg.temperature is not None


# ═══════════════════════════════════════════════════════
# Test: E2E — main.py create_engine + full tick
# ═══════════════════════════════════════════════════════

class TestEndToEnd:
    def test_create_engine_and_tick(self):
        """验证 create_engine() → WorldEngine 可正常运行一次 tick."""
        from main import create_engine

        engine = create_engine(
            world_name="cangyue",
            npc_file="cangyue_npcs",
            force_mock=True,
        )

        # 检查 NPC 加载
        assert len(engine.context.npcs) > 0
        assert "innkeeper" in engine.context.npcs

        # 执行 tick
        result = engine.tick("你好！")
        assert result.success
        assert result.tick_id == 1
