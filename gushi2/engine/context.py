"""WorldContext — 运行时上下文容器.

WorldContext 不是 Schema，不持久化。
仅负责 Node 和 Engine 之间传递当前运行环境。

职责：
- 持有所有 Node 需要读取的当前世界状态
- 提供 clone() 用于创建快照
- 提供 summary() 用于调试

禁止：
- 写入 Schema 文件
- 替代 WorldState
- 包含业务逻辑
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from Schema.world_schema import WorldState, WorldDefinition
from Schema.npc_schema import NPC
from Schema.event_schema import Event, CurrentEvent
from Schema.location_schema import Location
from Schema.dialogue_schema import DialogueHistory


@dataclass
class WorldContext:
    """运行时上下文容器.

    聚合单次 tick 中所有 Node 需要读取的当前世界状态。
    所有字段均可选——在初始化和 tick 推进中逐步填充。

    Attributes:
        tick_number: 当前 tick 编号 (从 1 开始).
        world_state: 当前世界状态（时间、天气、全局事件等）.
        world_definition: 世界定义（规则、隐藏事件等）.
        npcs: 所有 NPC 的字典 {npc_id: NPC}.
        events: 本轮事件列表（来自 N7 事件层）.
        current_event: 跨tick持久的事件上下文.
        locations: 位置字典 {location_id: Location}.
        perception_results: 感知层结果（来自 N11）— 通过属性 add_* 方法设置.
        reasoning_results: 推理层结果（来自 N15）.
        response_results: 回应层结果（来自 N16）.
        director_result: 导演层结果（来自 N17）.
        propagation_result: 传播层结果（来自 N18）.
        dialogue_history: 跨tick对话历史.
    """

    # 基础
    tick_number: int = 0

    # 世界
    world_state: Optional[WorldState] = None
    world_definition: Optional[WorldDefinition] = None

    # 实体
    npcs: dict[str, NPC] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    current_event: Optional[CurrentEvent] = None
    locations: dict[str, Location] = field(default_factory=dict)

    # 各层结果（每轮 tick 更新）
    perception_results: Optional[object] = None    # PerceptionResult
    reasoning_results: Optional[object] = None     # ReasoningResult
    response_results: Optional[object] = None      # ResponseResult
    director_result: Optional[object] = None       # DirectorResult
    propagation_result: Optional[object] = None    # PropagationResult（暂用 Any）

    # 跨tick持久
    dialogue_history: Optional[DialogueHistory] = None

    # ── 便捷方法 ──────────────────────────────────────────

    def has_world(self) -> bool:
        """世界是否已初始化."""
        return self.world_state is not None and self.world_definition is not None

    def has_events(self) -> bool:
        """本轮是否有事件."""
        return len(self.events) > 0

    def get_npc(self, npc_id: str) -> Optional[NPC]:
        """安全获取 NPC."""
        return self.npcs.get(npc_id)

    def get_location(self, location_id: str) -> Optional[Location]:
        """安全获取位置."""
        return self.locations.get(location_id)

    def get_active_npcs(self) -> list[NPC]:
        """获取所有存活且清醒的 NPC."""
        return [
            n for n in self.npcs.values()
            if n.current_state.is_alive and n.current_state.is_conscious
        ]

    def get_npcs_at_location(self, location: str) -> list[NPC]:
        """获取指定位置的所有 NPC."""
        return [
            n for n in self.npcs.values()
            if n.current_state.location == location
        ]

    # ── clone ────────────────────────────────────────────

    def clone(self) -> WorldContext:
        """创建浅拷贝（用于快照/回滚）.

        注意：这是浅拷贝——NPC 对象引用共享。
        不要通过 clone 来修改 NPC 数据。
        """
        return WorldContext(
            tick_number=self.tick_number,
            world_state=self.world_state,
            world_definition=self.world_definition,
            npcs=dict(self.npcs),
            events=list(self.events),
            current_event=self.current_event,
            locations=dict(self.locations),
            perception_results=self.perception_results,
            reasoning_results=self.reasoning_results,
            response_results=self.response_results,
            director_result=self.director_result,
            propagation_result=self.propagation_result,
            dialogue_history=self.dialogue_history,
        )

    # ── summary ──────────────────────────────────────────

    def summary(self) -> str:
        """生成调试摘要（非 LLM 用，只是开发调试）."""
        lines: list[str] = []
        lines.append(f"=== Tick {self.tick_number} ===")

        if self.world_state is not None:
            ws = self.world_state
            lines.append(
                f"  世界: 第{ws.day}天 {ws.time_of_day.value} {ws.weather.value}"
            )
            lines.append(f"  玩家位置: {ws.player_location or '未知'}")

        lines.append(f"  NPC 总数: {len(self.npcs)}")
        active = self.get_active_npcs()
        lines.append(f"  NPC 活跃: {len(active)}")

        if self.current_event is not None:
            ce = self.current_event
            if ce.is_active():
                lines.append(
                    f"  当前事件: {ce.topic} @ {ce.location}"
                )
                lines.append(
                    f"    对话对象: {ce.addressed_npc_id or '无'}"
                    f"  参与者: {ce.involved_npc_ids}"
                )

        lines.append(f"  本轮事件数: {len(self.events)}")

        for npc in self.npcs.values():
            lines.append(
                f"  [{npc.npc_id}] {npc.name}"
                f" @ {npc.current_state.location}"
                f" {npc.current_state.emotion.value}"
                f" Pending:{len(npc.pending_reason)}"
            )

        return "\n".join(lines)
