"""TickResult — 单次 Tick 的完整输出.

供 Engine 调用方（CLI/API/测试）使用。
不包含内部中间状态——只包含玩家应该看到的内容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TickResult:
    """单次 Tick 的最终输出.

    Attributes:
        tick_id: Tick 编号 (从 1 开始).
        success: 本轮是否成功完成.
        narrative: 给玩家看的叙事文本（来自 N17 导演层）.
        npc_speeches: NPC 说话列表（对应 a11）.
        narrations: 旁白列表（对应 a12）.
        scene_update: 场景更新描述.
        world_time: 当前世界时间描述（如 "第3天 morning"）.
        error: 失败时的错误信息.
    """

    tick_id: int = 0
    success: bool = True
    narrative: str = ""
    npc_speeches: list[dict] = field(default_factory=list)
    narrations: list[dict] = field(default_factory=list)
    scene_update: str = ""
    world_time: str = ""
    error: str = ""

    def has_content(self) -> bool:
        """本轮是否有可展示的内容."""
        return bool(self.narrative.strip())

    def get_display_text(self) -> str:
        """获取展示文本（NPC说话 + 旁白组合）."""
        parts: list[str] = []
        for narration in self.narrations:
            parts.append(narration.get("text", ""))
        for speech in self.npc_speeches:
            name = speech.get("name", "")
            text = speech.get("text", "")
            parts.append(f"{name}：「{text}」")
        return "\n".join(parts) if parts else self.narrative
