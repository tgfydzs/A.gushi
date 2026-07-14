"""MemoryStore — 内存数据存储.

管理运行期间的 Entity 集合。
纯数据容器——不负责规则判断、推理、关系修改、事件触发。

职责：
- 保存所有持久 Entity 的引用
- 提供按 ID 查询的接口
- 支持快照（浅拷贝）用于回滚/存档

禁止：
- 修改 Entity 数据（由 UpdateEngine 负责）
- 触发事件
- 执行推理
- 调用 LLM
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
class MemoryStore:
    """内存数据存储.

    持有所有持久 Entity 的集合引用。
    Entity 本身是可变的，但修改只能通过 UpdateEngine。

    Attributes:
        world_definition: 世界定义（规则、隐藏事件等）.
        world_state: 世界可变状态.
        npcs: NPC 字典 {npc_id: NPC}.
        locations: 位置字典 {location_id: Location}.
        dialogue_history: 对话历史.
        current_event: 跨tick持久的事件上下文.
    """

    world_definition: WorldDefinition = field(
        default_factory=WorldDefinition
    )
    world_state: WorldState = field(default_factory=WorldState)
    npcs: dict[str, NPC] = field(default_factory=dict)
    locations: dict[str, Location] = field(default_factory=dict)
    dialogue_history: DialogueHistory = field(
        default_factory=DialogueHistory
    )
    current_event: Optional[CurrentEvent] = None

    # ── NPC 查询 ───────────────────────────────────────────

    def get_npc(self, npc_id: str) -> Optional[NPC]:
        """按 ID 获取 NPC."""
        return self.npcs.get(npc_id)

    def list_npcs(self) -> list[NPC]:
        """列出所有 NPC."""
        return list(self.npcs.values())

    def get_active_npcs(self) -> list[NPC]:
        """获取存活且清醒的 NPC."""
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

    def add_npc(self, npc: NPC) -> None:
        """添加 NPC（仅初始化时使用，运行时通过 UpdateEngine）."""
        self.npcs[npc.npc_id] = npc

    # ── Location 查询 ──────────────────────────────────────

    def get_location(self, location_id: str) -> Optional[Location]:
        """按 ID 获取位置."""
        return self.locations.get(location_id)

    def add_location(self, location: Location) -> None:
        """添加位置."""
        self.locations[location.location_id] = location

    # ── Event 查询 ─────────────────────────────────────────

    def get_world(self) -> dict:
        """获取世界摘要信息."""
        return {
            "world_name": self.world_definition.name,
            "day": self.world_state.day,
            "time_of_day": self.world_state.time_of_day.value,
            "weather": self.world_state.weather.value,
            "npc_count": len(self.npcs),
            "location_count": len(self.locations),
        }

    # ── 生命周期 ───────────────────────────────────────────

    def save_snapshot(self) -> MemoryStore:
        """创建当前状态的浅拷贝（用于回滚/存档）.

        注意：这是浅拷贝——NPC 对象引用共享。
        主要用于故障恢复，不是完整的序列化。
        """
        return MemoryStore(
            world_definition=self.world_definition,
            world_state=self.world_state,
            npcs=dict(self.npcs),
            locations=dict(self.locations),
            dialogue_history=self.dialogue_history,
            current_event=self.current_event,
        )

    def load_snapshot(self, snapshot: MemoryStore) -> None:
        """从快照恢复状态.

        Args:
            snapshot: 之前 save_snapshot() 返回的快照.
        """
        self.world_definition = snapshot.world_definition
        self.world_state = snapshot.world_state
        self.npcs = snapshot.npcs
        self.locations = snapshot.locations
        self.dialogue_history = snapshot.dialogue_history
        self.current_event = snapshot.current_event

    # ── 统计 ───────────────────────────────────────────────

    def summary(self) -> str:
        """生成存储状态摘要（调试用）."""
        ws = self.world_state
        lines = [
            f"=== MemoryStore ===",
            f"世界: {self.world_definition.name}",
            f"时间: 第{ws.day}天 {ws.time_of_day.value} {ws.weather.value}",
            f"NPC: {len(self.npcs)} 人 "
            f"(活跃 {len(self.get_active_npcs())})",
            f"位置: {len(self.locations)} 个",
            f"对话历史: {len(self.dialogue_history.entries)} 条",
        ]
        if self.current_event and self.current_event.is_active():
            lines.append(
                f"当前事件: {self.current_event.topic}"
            )
        return "\n".join(lines)
