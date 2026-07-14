"""WorldEngine — 主调度器 + Tick 驱动.

管理一次完整 Tick 的调度流程。
不直接修改 Entity——所有状态修改通过 UpdateEngine。

固定流程顺序（不可改变）：
    N7 (事件层) → N11 (感知层) → N15 (推理层) →
    N16 (回应层) → N17 (导演层) → N18 (传播层) →
    N12 (UpdateEngine)

每个 Tick:
1. 时间推进
2. 逐层调用 LLM Node
3. 每层结果立即通过 UpdateEngine 写入
4. 构建 TickResult 返回给调用方
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from engine.context import WorldContext
from engine.memory_store import MemoryStore
from engine.update_engine import UpdateEngine
from engine.tick_result import TickResult

from Schema.event_schema import Event, CurrentEvent, EventScope, EventType
from Schema.director_schema import SceneMemory, DirectorStyleConfig

logger = logging.getLogger(__name__)


# =============================================================================
# WorldEngine
# =============================================================================


class WorldEngine:
    """主引擎 — Tick 驱动与 Node 调度.

    不直接修改 Entity。
    不调用 LLM（通过 Node）。
    不生成新事实。

    用法:
        store = MemoryStore()
        engine = WorldEngine(store)
        engine.initialize(...)
        result = engine.tick("玩家输入")
        print(result.narrative)
    """

    def __init__(self, memory_store: MemoryStore) -> None:
        """初始化 WorldEngine.

        Args:
            memory_store: 数据存储（持有所有 Entity 的引用）.
        """
        self._store = memory_store
        self._context = WorldContext()
        self._update_engine: Optional[UpdateEngine] = None
        self._scene_memory: dict[str, SceneMemory] = {}
        self._last_event_location: str = ""

        # Node 实例（延迟注入——由调用方 set_nodes 设置）
        self._event_node: Any = None
        self._perception_node: Any = None
        self._reasoning_node: Any = None
        self._response_node: Any = None
        self._director_node: Any = None
        self._propagation_node: Any = None

        # 导演样式
        self._director_style = DirectorStyleConfig()

        # Debug 模式
        self._debug_tick: bool = False

    def enable_debug(self) -> None:
        """开启 Debug 模式——每个节点执行后输出中间结果."""
        self._debug_tick = True

    def disable_debug(self) -> None:
        self._debug_tick = False

    # ── Node 注入 ──────────────────────────────────────────

    def set_nodes(
        self,
        event_node: Any = None,
        perception_node: Any = None,
        reasoning_node: Any = None,
        response_node: Any = None,
        director_node: Any = None,
        propagation_node: Any = None,
    ) -> None:
        """注入 LLM Node 实例.

        Args:
            event_node: N7 EventNode.
            perception_node: N11 PerceptionNode.
            reasoning_node: N15 ReasoningNode.
            response_node: N16 ResponseNode.
            director_node: N17 DirectorNode.
            propagation_node: N18 PropagationNode.
        """
        self._event_node = event_node
        self._perception_node = perception_node
        self._reasoning_node = reasoning_node
        self._response_node = response_node
        self._director_node = director_node
        self._propagation_node = propagation_node

    # ── 初始化 ─────────────────────────────────────────────

    def initialize(self) -> None:
        """初始化世界——将 MemoryStore 数据同步到 WorldContext."""
        self._context = WorldContext(
            tick_number=0,
            world_state=self._store.world_state,
            world_definition=self._store.world_definition,
            npcs=self._store.npcs,
            locations=self._store.locations,
            current_event=self._store.current_event,
            dialogue_history=self._store.dialogue_history,
        )
        self._update_engine = UpdateEngine(self._context)

        # 同步场景记忆
        if self._store.current_event:
            self._last_event_location = (
                self._store.current_event.location
            )

    # ── Tick 主循环 ────────────────────────────────────────

    def tick(self, player_input: str) -> TickResult:
        """执行一轮完整 Tick.

        Args:
            player_input: 玩家原始输入文本.

        Returns:
            TickResult — 包含叙事和元信息.
        """
        # 确保已初始化
        if self._update_engine is None:
            self.initialize()

        self._context.tick_number += 1
        tick_num = self._context.tick_number

        try:
            # Step 0: 推进时间
            self._advance_time()

            # Step 1: N7 事件层
            event_output = self._run_event_node(player_input)
            if event_output is None:
                return TickResult(
                    tick_id=tick_num,
                    success=False,
                    error="事件层执行失败",
                )
            self._update_engine.apply_event_result(event_output)

            # Debug
            if self._debug_tick:
                ce = self._context.current_event
                print(f"\n[N7 Event]")
                print(f"  location: {ce.location if ce else 'N/A'}")
                print(f"  addressed_npc: {ce.addressed_npc_id if ce else 'N/A'}")
                print(f"  involved: {ce.involved_npc_ids if ce else 'N/A'}")
                print(f"  scope: {ce.scope if ce else 'N/A'}")

            # 确保 player_location 始终与 CurrentEvent.location 同步
            if self._context.current_event and self._context.current_event.is_active():
                ce_loc = self._context.current_event.location
                if ce_loc and self._context.world_state:
                    self._context.world_state.player_location = ce_loc

            # 无事件 → 直接返回
            if not self._context.current_event:
                return TickResult(
                    tick_id=tick_num,
                    success=True,
                    narrative="",
                    world_time=self._time_string(),
                )

            # CurrentEvent → Event（供后续层使用）
            ce = self._context.current_event
            event = ce.to_event() if ce else None
            if event is None:
                return TickResult(
                    tick_id=tick_num,
                    success=True,
                    narrative="",
                    world_time=self._time_string(),
                )
            self._context.events = [event]

            # Step 2: N11 感知层
            perception_result = self._run_perception_node()
            if perception_result is not None:
                self._update_engine.apply_perception_result(
                    perception_result
                )
                self._context.perception_results = perception_result

            # Step 3: N15 推理层（has_pending 前置检查，程序逻辑，非LLM节点）
            reasoning_result = self._run_reasoning_node()
            if reasoning_result is not None:
                self._update_engine.apply_reasoning_result(
                    reasoning_result
                )
                self._context.reasoning_results = reasoning_result

            # Step 4: N16 回应层
            response_result = self._run_response_node()
            if response_result is not None:
                self._update_engine.apply_response_result(
                    response_result
                )
                self._context.response_results = response_result

            # Step 5: N17 导演层
            director_output = self._run_director_node()
            if director_output is not None:
                self._update_engine.apply_director_result(
                    director_output.result
                )
                self._context.director_result = director_output.result
                # 更新场景记忆
                self._update_scene_memory(director_output)
            else:
                director_output = type('obj', (object,), {
                    'result': None, 'npc_speeches': [], 'narrations': [],
                })()

            # Step 6: N18 传播层
            propagation_output = self._run_propagation_node()
            if propagation_output is not None:
                self._update_engine.apply_propagation_result(
                    propagation_output
                )
                self._context.propagation_result = propagation_output

            # 构建结果
            narrative = ""
            npc_speeches: list[dict] = []
            narrations: list[dict] = []
            scene_update = ""
            if director_output is not None:
                if director_output.result is not None:
                    narrative = director_output.result.narrative
                    scene_update = director_output.result.scene_update
                npc_speeches = getattr(
                    director_output, 'npc_speeches', []
                )
                narrations = getattr(
                    director_output, 'narrations', []
                )

            return TickResult(
                tick_id=tick_num,
                success=True,
                narrative=narrative,
                npc_speeches=npc_speeches,
                narrations=narrations,
                scene_update=scene_update,
                world_time=self._time_string(),
            )

        except Exception as e:
            logger.error(f"Tick {tick_num} 异常: {e}", exc_info=True)
            return TickResult(
                tick_id=tick_num,
                success=False,
                error=str(e),
                world_time=self._time_string(),
            )

    # ══════════════════════════════════════════════════════════
    # 各层执行（私有）
    # ══════════════════════════════════════════════════════════

    def _run_event_node(self, player_input: str) -> Any:
        if self._event_node is None:
            return None
        result = self._event_node.execute_with_input(
            self._context, player_input
        )
        if result.success:
            return result.data
        logger.warning(f"N7 失败: {result.error}")
        return None

    def _run_perception_node(self) -> Any:
        if self._perception_node is None:
            return None
        result = self._perception_node.execute(self._context)
        if result.success:
            return result.data
        logger.warning(f"N11 失败: {result.error}")
        return None

    def _run_reasoning_node(self) -> Any:
        if self._reasoning_node is None:
            return None
        # has_pending 前置检查（程序逻辑，非LLM节点）
        has_pending = any(
            n.has_pending()
            for n in self._context.get_active_npcs()
        )
        if not has_pending:
            return None
        result = self._reasoning_node.execute(self._context)
        if result.success:
            return result.data
        logger.warning(f"N15 失败: {result.error}")
        return None

    def _run_response_node(self) -> Any:
        if self._response_node is None:
            return None
        result = self._response_node.execute(self._context)
        if result.success:
            return result.data
        logger.warning(f"N16 失败: {result.error}")
        return None

    def _run_director_node(self) -> Any:
        if self._director_node is None:
            return None
        result = self._director_node.execute(self._context)
        if result.success:
            return result.data
        logger.warning(f"N17 失败: {result.error}")
        return None

    def _run_propagation_node(self) -> Any:
        if self._propagation_node is None:
            return None
        result = self._propagation_node.execute(self._context)
        if result.success:
            return result.data
        logger.warning(f"N18 失败: {result.error}")
        return None

    # ══════════════════════════════════════════════════════════
    # 时间与场景管理
    # ══════════════════════════════════════════════════════════

    def _advance_time(self) -> None:
        """推进世界时间."""
        if self._context.world_state:
            self._context.world_state.advance_time()

    def _time_string(self) -> str:
        """世界时间描述字符串."""
        if not self._context.world_state:
            return ""
        ws = self._context.world_state
        return f"第{ws.day}天 {ws.time_of_day.value}"

    def _update_scene_memory(self, director_output: Any) -> None:
        """更新场景记忆."""
        if director_output is None:
            return
        dr = director_output.result
        if dr is None:
            return

        location = self._last_event_location
        ce = self._context.current_event
        if ce and ce.is_active():
            location = ce.location

        if not dr.scene_update:
            return

        if location not in self._scene_memory:
            self._scene_memory[location] = SceneMemory(
                location=location,
            )

        mem = self._scene_memory[location]
        mem.scene_description = dr.scene_update
        mem.last_tick = self._context.tick_number
        self._last_event_location = location

    # ══════════════════════════════════════════════════════════
    # 查询
    # ══════════════════════════════════════════════════════════

    @property
    def context(self) -> WorldContext:
        """获取当前运行时上下文（只读）."""
        return self._context

    @property
    def memory_store(self) -> MemoryStore:
        """获取底层数据存储."""
        return self._store

    @property
    def tick_number(self) -> int:
        """当前 Tick 编号."""
        return self._context.tick_number

    def get_previous_scene(self, location: str) -> str:
        """获取上一次此位置的场景描述.

        Args:
            location: 位置名称.

        Returns:
            场景描述字符串。如果是新位置返回空字符串。
        """
        if location != self._last_event_location:
            return ""
        mem = self._scene_memory.get(location)
        return mem.scene_description if mem else ""

    def summary(self) -> str:
        """引擎状态摘要."""
        return self._context.summary()
