"""EventNode — N7 事件层.

判断玩家输入是否产生新事件或延续已有事件。

输入 (WorldContext):
    - world_state (day/time_of_day/weather/player_location)
    - world_definition.rules
    - npcs (id/name/location)
    - current_event (上一轮)
    - global_context.player_inputs

输出 (暂态):
    - CurrentEvent (AI 可修改的6个字段)

    或延续事件时返回的更新建议 dict。

禁止:
    - 修改 WorldContext
    - 创建 Event（由 Engine 从 CurrentEvent.to_event() 转换）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from engine.context import WorldContext
from nodes.base import BaseNode, NodeResult


# =============================================================================
# 暂态输出（EventNode 特有）
# =============================================================================


@dataclass
class EventNodeOutput:
    """N7 事件层暂态输出.

    Attributes:
        continue_context: 是否延续当前事件.
        location: 场景位置（新事件时必须）.
        topic: 当前话题.
        addressed_npc_id: 对话对象ID.
        involved_npc_ids: 参与者ID列表（新事件必须）.
        added_npc_ids: 加入的NPC（延续事件时）.
        removed_npc_ids: 离开的NPC（延续事件时）.
        event_type: 事件类型 (dialogue/player_action).
        scope: 公开程度 (private/area/group/public/hidden).
    """

    continue_context: bool = True
    location: str = ""
    topic: str = ""
    addressed_npc_id: str = ""
    involved_npc_ids: list[str] = field(default_factory=list)
    added_npc_ids: list[str] = field(default_factory=list)
    removed_npc_ids: list[str] = field(default_factory=list)
    event_type: str = "dialogue"
    scope: str = "private"


# =============================================================================
# Node
# =============================================================================


class EventNode(BaseNode):
    """N7 — 事件层: 判断事件延续/新建."""

    @property
    def node_name(self) -> str:
        return "N7"

    @property
    def prompt_name(self) -> str:
        return "event"

    # ── 空结果 ────────────────────────────────────────────

    def _empty_result(self, error_msg: str = "") -> NodeResult:
        return NodeResult(
            success=False,
            data=EventNodeOutput(),
            error=error_msg,
            node_name=self.node_name,
        )

    # ── 构造 Prompt ───────────────────────────────────────

    def _build_prompt(self, context: WorldContext) -> tuple[str, str]:
        """从 WorldContext 收集 N7 权限范围内的数据并构造 Prompt.

        读取权限 (schema_mapping.md N6+N7):
            - WorldState 摘要
            - WorldDefinition.rules
            - NPC 摘要 (id/name/location)
            - CurrentEvent (上一轮)
            - GlobalContext.player_inputs
        """
        ws = context.world_state
        wd = context.world_definition

        # 世界状态
        day = str(ws.day) if ws else "1"
        time_of_day = ws.time_of_day.value if ws else "morning"
        weather = ws.weather.value if ws else "sunny"
        player_location = ws.player_location if ws else ""

        # NPC 列表 (id/name/location 摘要)
        npc_parts: list[str] = []
        for npc in context.npcs.values():
            npc_parts.append(
                f"{npc.npc_id}({npc.name}/{npc.current_state.location})"
            )
        npc_list = ", ".join(npc_parts)

        # 世界规则
        rules_lines = "  无"
        if wd and wd.rules:
            rules_lines = "\n".join(
                f"  - {r.description}（违反后果: {r.penalty}）"
                for r in wd.rules
            )

        # 当前事件上下文快照
        ce = context.current_event
        if ce and ce.is_active():
            involved_names = []
            for nid in ce.involved_npc_ids:
                npc = context.get_npc(nid)
                if npc:
                    involved_names.append(f"{npc.name}({nid})")
            addr_name = ""
            if ce.addressed_npc_id:
                anpc = context.get_npc(ce.addressed_npc_id)
                if anpc:
                    addr_name = f"{anpc.name}({ce.addressed_npc_id})"

            current_context = (
                f"玩家当前位置: {player_location}\n"
                f"场景位置: {ce.location}\n"
                f"对话对象: {addr_name}\n"
                f"参与者: {', '.join(involved_names) if involved_names else '无'}\n"
                f"话题: {ce.topic}\n"
                f"\n"
                f"所有NPC位置速查:\n"
                + "\n".join(
                    f"  {n.npc_id}({n.name}) → {n.current_state.location}"
                    for n in context.npcs.values()
                )
            )
        else:
            current_context = (
                f"玩家当前位置: {player_location}\n"
                f"（无当前事件，需要创建）\n"
                f"\n"
                f"所有NPC位置速查:\n"
                + "\n".join(
                    f"  {n.npc_id}({n.name}) → {n.current_state.location}"
                    for n in context.npcs.values()
                )
            )

        # 历史输入
        historical = "（无）"
        if context.dialogue_history:
            recent = context.dialogue_history.entries[-5:]
            if recent:
                historical = "\n".join(
                    f"  [Tick {e.tick}] {e.text}" for e in recent
                )

        # 渲染 Prompt
        prompt = self._render_prompt(
            day=day,
            time_of_day=time_of_day,
            weather=weather,
            npc_list=npc_list,
            world_rules=rules_lines,
            current_context=current_context,
            player_input="",  # 将在下面用 user_prompt 替代
            historical_inputs=historical,
        )

        # 分离 system/user
        # event.md 的 Input 部分包含占位符 {player_input}
        # 将其作为单独的 user_prompt 传入
        system_prompt = prompt.replace("{player_input}", "（见下方用户输入）")
        user_prompt = ""  # Engine 将在调用时填入实际玩家输入

        return system_prompt, user_prompt

    # ── 直接使用玩家输入的方法 ─────────────────────────────

    def execute_with_input(
        self, context: WorldContext, player_input: str
    ) -> NodeResult:
        """执行 N7 并传入玩家输入.

        这是 EventNode 的主要使用方式——因为 player_input
        在构造 Prompt 时需要动态注入。

        Args:
            context: 运行时上下文.
            player_input: 玩家本轮原始输入.

        Returns:
            NodeResult.
        """
        try:
            system_prompt, _ = self._build_prompt(context)

            # 将玩家输入放入 user prompt
            user_prompt = f'本轮玩家输入:\n\n"{player_input}"'

            response_text = self._call_llm(system_prompt, user_prompt)
            parsed = self._parse_json(response_text)

            if not parsed:
                return self._empty_result("LLM 返回无法解析的 JSON")

            data = self._to_schema(parsed, context)
            return NodeResult(
                success=True,
                data=data,
                raw_json=parsed,
                node_name=self.node_name,
            )
        except Exception as e:
            return self._empty_result(str(e))

    # ── JSON → Schema ─────────────────────────────────────

    def _to_schema(self, parsed: dict, context: WorldContext) -> EventNodeOutput:
        """将 LLM JSON 转为 EventNodeOutput.

        Args:
            parsed: LLM 输出的 dict.
            context: 运行时上下文（用于继承上一轮的 CurrentEvent 值）.

        Returns:
            EventNodeOutput.
        """
        continue_ctx = parsed.get("continue_context", True)
        ce = context.current_event

        if continue_ctx:
            # 延续事件：部分更新
            new_topic = parsed.get("topic", "")
            new_addressed = parsed.get("addressed_npc_id", "")
            return EventNodeOutput(
                continue_context=True,
                location=(ce.location if ce else ""),
                topic=new_topic if new_topic else (ce.topic if ce else ""),
                addressed_npc_id=(
                    new_addressed if new_addressed
                    else (ce.addressed_npc_id if ce else "")
                ),
                added_npc_ids=parsed.get("added_npc_ids", []),
                removed_npc_ids=parsed.get("removed_npc_ids", []),
                event_type=(ce.event_type if ce else "dialogue"),
                scope=(ce.scope if ce else "private"),
            )
        else:
            # 新建事件
            return EventNodeOutput(
                continue_context=False,
                location=parsed.get("location", ""),
                topic=parsed.get("topic", ""),
                addressed_npc_id=parsed.get("addressed_npc_id", ""),
                involved_npc_ids=parsed.get("involved_npc_ids", []),
                event_type=parsed.get("event_type", "dialogue"),
                scope=parsed.get("scope", "private"),
            )
