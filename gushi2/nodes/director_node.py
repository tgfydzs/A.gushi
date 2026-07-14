"""DirectorNode — N17 导演层.

整合所有 NPC 回应为连贯叙事，分 NPC 说话（a11）和旁白（a12）输出。

输入 (WorldContext):
    - NPCResponse (来自 N16 的回应列表)
    - WorldState 摘要
    - WorldDefinition.rules
    - CurrentEvent (location/topic)
    - DirectorStyleConfig (来自 Engine 配置)
    - SceneMemory (上轮同位置场景描述)
    - DialogueHistory (场景中 NPC 的历史发言)
    - NPC.current_state.location (位置校验)

输出 (暂态):
    - DirectorNodeOutput = DirectorResult + npc_speeches + narrations

禁止:
    - 新增世界事实
    - 修改 WorldState
    - 生成 NPC 没有说过的话
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from engine.context import WorldContext
from nodes.base import BaseNode, NodeResult

from Schema.dialogue_schema import NPCResponse, ResponseType
from Schema.director_schema import (
    DirectorResult, DirectorStyleConfig, SceneMemory,
)


# =============================================================================
# 暂态包装（DirectorResult + NarrativeOutput 的合并）
# =============================================================================


@dataclass
class DirectorNodeOutput:
    """N17 导演层暂态输出.

    合并 DirectorResult 主体 + NarrativeOutput 的 npc_speeches/narrations。
    因为这两个字段在当前 Schema 中分属不同结构，
    但 N17 Prompt 将其作为统一输出。

    Attributes:
        result: DirectorResult 主体.
        npc_speeches: NPC 说话列表 (对应 a11).
        narrations: 旁白列表 (对应 a12).
    """

    result: DirectorResult = field(default_factory=DirectorResult)
    npc_speeches: list[dict] = field(default_factory=list)
    narrations: list[dict] = field(default_factory=list)


class DirectorNode(BaseNode):
    """N17 — 导演层: 组织 NPC 对话和旁白输出."""

    # 默认导演样式
    _default_style = DirectorStyleConfig()

    @property
    def node_name(self) -> str:
        return "N17"

    @property
    def prompt_name(self) -> str:
        return "director"

    # ── 空结果 ────────────────────────────────────────────

    def _empty_result(self, error_msg: str = "") -> NodeResult:
        return NodeResult(
            success=False,
            data=DirectorNodeOutput(
                result=DirectorResult(narrative="（无回应）"),
            ),
            error=error_msg,
            node_name=self.node_name,
        )

    # ── 主流程 ────────────────────────────────────────────

    def execute(self, context: WorldContext) -> NodeResult:
        """整合 NPC 回应为叙事."""
        try:
            responses = []
            if context.response_results is not None:
                result = context.response_results
                if hasattr(result, 'npc_responses'):
                    responses = result.npc_responses

            if not responses:
                return NodeResult(
                    success=True,
                    data=DirectorNodeOutput(
                        result=DirectorResult(narrative="世界沉默了片刻。"),
                    ),
                    node_name=self.node_name,
                )

            # 单个 NPC 回应 → 直接格式化，不调用 LLM
            if len(responses) == 1:
                data = self._format_single(responses[0], context)
                return NodeResult(
                    success=True, data=data, node_name=self.node_name,
                )

            # 多个回应 → LLM 整合
            system_prompt, user_prompt = self._build_prompt(
                responses, context
            )
            response_text = self._call_llm(system_prompt, user_prompt)
            parsed = self._parse_json(response_text)

            if parsed:
                data = self._to_schema(parsed, context)
            else:
                data = self._format_fallback(responses, context)

            return NodeResult(
                success=True, data=data, node_name=self.node_name,
            )
        except Exception as e:
            return self._empty_result(str(e))

    # ── 构造 Prompt ───────────────────────────────────────

    def _build_prompt(
        self, responses: list[NPCResponse], context: WorldContext,
    ) -> tuple[str, str]:
        """构造导演 Prompt.

        读取权限: NPCResponse, WorldState摘要, WorldDefinition.rules,
                  CurrentEvent, DirectorStyleConfig, SceneMemory,
                  DialogueHistory, NPC.current_state.location
        """
        # 回应描述
        responses_desc = "\n".join(
            f"  - ID={r.npc_id} | {r.npc_name}: "
            f"type={r.response_type} | emotion={r.emotion} | "
            f"target={r.target_id} | "
            f"text=\"{r.response_text}\""
            for r in responses
        )

        # 事件上下文
        ce = context.current_event
        event_context = ""
        if ce and ce.is_active():
            event_context = (
                f"位置: {ce.location}\n"
                f"话题: {ce.topic}\n"
                f"对话对象: {ce.addressed_npc_id or '无'}\n"
                f"参与者: {ce.involved_npc_ids}"
            )
        else:
            event_context = "（无当前事件上下文）"

        # 世界状态摘要
        ws = context.world_state
        world_state_summary = ""
        if ws:
            world_state_summary = (
                f"第{ws.day}天 | {ws.time_of_day.value} | {ws.weather.value}\n"
                f"玩家位置: {ws.player_location or '未知'}"
            )

        # 世界规则
        wd = context.world_definition
        world_rules = "  无"
        if wd and wd.rules:
            world_rules = "\n".join(
                f"  - {r.description}（违反后果: {r.penalty}）"
                for r in wd.rules
            )

        # 导演样式
        style = self._default_style
        director_style = (
            f"风格: {style.style.value}\n"
            f"显示NPC情绪: {'是' if style.show_npc_emotions else '否'}\n"
            f"显示NPC动作: {'是' if style.show_npc_actions else '否'}\n"
            f"显示内心独白: {'是' if style.show_inner_thoughts else '否'}\n"
            f"对话格式: {style.format_dialogue}"
        )

        # 场景衔接
        scene_context = ""
        if ce and ce.is_active():
            scene_context = f"上轮位置: {ce.location}\n"
            scene_context += "（新场景，需要写场景描述）"
        else:
            scene_context = "（首次场景）"

        # 对话历史
        history_desc = "（无历史）"
        if context.dialogue_history:
            recent = context.dialogue_history.entries[-10:]
            if recent:
                history_desc = "\n".join(
                    f"  [Tick {e.tick}] {e.npc_name}: \"{e.text}\""
                    for e in recent
                )

        # NPC 位置列表（用于校验）
        npc_locs = "\n".join(
            f"  {n.npc_id}({n.name}) → {n.current_state.location}"
            for n in context.npcs.values()
        )

        prompt = self._render_prompt(
            responses=responses_desc,
            event_context=event_context,
            world_state_summary=world_state_summary,
            world_rules=world_rules,
            director_style=director_style,
            scene_context=f"{scene_context}\nNPC位置:\n{npc_locs}",
            dialogue_history=history_desc,
        )

        return prompt, ""

    # ── 单个 NPC 格式化 ────────────────────────────────────

    def _format_single(
        self, r: NPCResponse, context: WorldContext,
    ) -> DirectorNodeOutput:
        """单个 NPC 回应时直接格式化（不走 LLM）."""
        text = r.response_text
        is_action = (r.response_type == ResponseType.ACTION)
        is_thought = (r.response_type == ResponseType.THOUGHT)

        if is_action:
            dialogue = f"{r.npc_name}{text}"
        elif is_thought:
            dialogue = f"（{r.npc_name}心想: {text}）"
        else:
            dialogue = f"{r.npc_name}：「{text}」"

        return DirectorNodeOutput(
            result=DirectorResult(
                narrative=dialogue,
                scene_update="",
                npc_responses_summary=[f"{r.npc_name}: {text}"],
                narration_type="dialogue",
            ),
            npc_speeches=[] if is_action else [
                {"npc_id": r.npc_id, "name": r.npc_name,
                 "text": text, "emotion": r.emotion}
            ],
            narrations=[] if not is_action else [
                {"text": dialogue, "type": "action"}
            ],
        )

    # ── Fallback ──────────────────────────────────────────

    def _format_fallback(
        self, responses: list[NPCResponse], context: WorldContext,
    ) -> DirectorNodeOutput:
        """无 LLM 时的回退：直接拼接."""
        parts: list[str] = []
        speeches: list[dict] = []
        narrations: list[dict] = []

        for r in responses:
            text = r.response_text
            if not text:
                continue
            if r.response_type == ResponseType.SPEECH:
                parts.append(f"{r.npc_name}：「{text}」")
                speeches.append({
                    "npc_id": r.npc_id, "name": r.npc_name,
                    "text": text, "emotion": r.emotion,
                })
            elif r.response_type == ResponseType.ACTION:
                line = f"{r.npc_name}{text}"
                parts.append(line)
                narrations.append({"text": line, "type": "action"})
            elif r.response_type == ResponseType.THOUGHT:
                line = f"（{r.npc_name}心想: {text}）"
                parts.append(line)
                narrations.append({"text": line, "type": "thought"})
            else:
                parts.append(f"{r.npc_name}: {text}")
                speeches.append({
                    "npc_id": r.npc_id, "name": r.npc_name,
                    "text": text, "emotion": r.emotion,
                })

        return DirectorNodeOutput(
            result=DirectorResult(
                narrative="\n".join(parts) if parts else "（无回应）",
                scene_update="",
                npc_responses_summary=[
                    f"{r.npc_name}: {r.response_text}"
                    for r in responses if r.response_text
                ],
                narration_type="dialogue",
            ),
            npc_speeches=speeches,
            narrations=narrations,
        )

    # ── JSON → DirectorResult ─────────────────────────────

    def _to_schema(self, parsed: dict, context: WorldContext) -> DirectorNodeOutput:
        return DirectorNodeOutput(
            result=DirectorResult(
                narrative=parsed.get("narrative", ""),
                scene_update=parsed.get("scene_update", ""),
                npc_responses_summary=parsed.get("npc_responses_summary", []),
                world_changes=parsed.get("world_changes", []),
                scene_notes=parsed.get("scene_notes", ""),
                narration_type=parsed.get("narration_type", "dialogue"),
            ),
            npc_speeches=parsed.get("npc_speeches", []),
            narrations=parsed.get("narrations", []),
        )
