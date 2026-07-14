"""ResponseNode — N16 回应层.

NPC 根据感知到的内容 + 自身维护表生成回应。

输入 (WorldContext):
    - NPCPerception.perceived_summary
    - NPC.current_state (location/emotion/action)
    - NPC.personality (intelligence/traits)
    - NPC.knowledge (最近N条)
    - NPC.belief (最近N条)
    - NPC.observation (最近N条)
    - NPC.memory (最近N条)
    - NPC.pending_reason (最近N条)
    - NPC.goal (未完成)
    - NPC.relationship (关系表)
    - DialogueHistory (该NPC的历史发言)
    - CurrentEvent.addressed_npc_id

输出 (暂态):
    - ResponseResult (包含所有 NPC 的 NPCResponse 列表)

禁止:
    - 生成旁白（N17 职责）
    - 决定响应顺序（N17 职责）
    - 修改 NPC 维护表
"""

from __future__ import annotations

from typing import Any, Optional

from engine.context import WorldContext
from nodes.base import BaseNode, NodeResult

from Schema.dialogue_schema import NPCResponse, ResponseResult
from Schema.perception_schema import PerceptionResult


class ResponseNode(BaseNode):
    """N16 — 回应层: NPC 根据自身状态产生回应.

    每个 NPC 只能看到自己的 perceived_summary（来自 N11），
    绝不能看到完整事件表。
    """

    @property
    def node_name(self) -> str:
        return "N16"

    @property
    def prompt_name(self) -> str:
        return "response"

    # ── 空结果 ────────────────────────────────────────────

    def _empty_result(self, error_msg: str = "") -> NodeResult:
        return NodeResult(
            success=False,
            data=ResponseResult(),
            error=error_msg,
            node_name=self.node_name,
        )

    # ── 主流程 ────────────────────────────────────────────

    def execute(self, context: WorldContext) -> NodeResult:
        """为有感知或被对话的 NPC 生成回应."""
        try:
            perception_result: Optional[PerceptionResult] = (
                context.perception_results  # type: ignore[assignment]
            )

            # 对话目标
            addressed_id = ""
            if context.current_event:
                addressed_id = context.current_event.addressed_npc_id

            responses: list[NPCResponse] = []
            skipped: list[str] = []

            # 事件位置（用于程序级位置过滤）
            event_location = ""
            if context.events and context.events[0].location:
                event_location = context.events[0].location

            for npc in context.get_active_npcs():
                npc_location = npc.current_state.location

                # 是否被直接对话
                is_addressed = (
                    npc.npc_id == addressed_id and addressed_id != ""
                )

                # 程序级过滤：NPC既不在事件位置、也不是对话目标 → 不可能回应
                if event_location and npc_location != event_location and not is_addressed:
                    skipped.append(npc.npc_id)
                    continue

                # 获取该 NPC 的感知摘要
                perceived_summary = ""
                if perception_result is not None:
                    perc = perception_result.get_for_npc(npc.npc_id)
                    if perc is not None:
                        perceived_summary = perc.perceived_summary

                # 无感知且不是被对话对象 → 跳过
                if self._has_no_perception(perceived_summary) and not is_addressed:
                    skipped.append(npc.npc_id)
                    continue

                # 兜底：被点名但感知为空 → 注入玩家原话
                effective_perception = perceived_summary
                if is_addressed and self._has_no_perception(perceived_summary):
                    # 从事件的 description 构造兜底文本
                    event_desc = ""
                    if context.events:
                        event_desc = context.events[0].description
                    effective_perception = (
                        f'玩家对你说: "{event_desc}"'
                    )

                try:
                    system_prompt, user_prompt = self._build_prompt_for_npc(
                        npc, effective_perception, is_addressed
                    )
                    response_text = self._call_llm(
                        system_prompt, user_prompt
                    )
                    parsed = self._parse_json(response_text)

                    if parsed:
                        resp = self._to_schema_for_npc(parsed, npc)
                    else:
                        resp = NPCResponse(
                            npc_id=npc.npc_id,
                            npc_name=npc.name,
                            response_text="",
                            is_addressed=is_addressed,
                        )
                except Exception:
                    resp = NPCResponse(
                        npc_id=npc.npc_id,
                        npc_name=npc.name,
                        response_text="",
                        is_addressed=is_addressed,
                    )

                # 被对话但空回应 → 不加入（这样 N17 会知道无人回应）
                if is_addressed and not resp.response_text.strip():
                    resp.response_text = "……"  # 沉默也表达什么

                # 空回应且非对话对象 → 跳过
                if not resp.response_text.strip() and not is_addressed:
                    skipped.append(npc.npc_id)
                    continue

                responses.append(resp)

            return NodeResult(
                success=True,
                data=ResponseResult(
                    npc_responses=responses,
                    skipped_npc_ids=skipped,
                ),
                node_name=self.node_name,
            )
        except Exception as e:
            return self._empty_result(str(e))

    # ── 判断无感知 ────────────────────────────────────────

    _NO_PERCEPTION_MARKERS = (
        "没有感知到任何异常",
        "没有感知到任何",
        "无可见事件",
        "没有感知到",
        "本轮没有感知",
        "什么都没感知到",
    )

    def _has_no_perception(self, summary: str) -> bool:
        stripped = summary.strip()
        if not stripped:
            return True
        for marker in self._NO_PERCEPTION_MARKERS:
            if marker in stripped:
                return True
        return False

    # ── 构造 Prompt ───────────────────────────────────────

    def _build_prompt_for_npc(
        self, npc: Any, perceived_summary: str, is_addressed: bool,
    ) -> tuple[str, str]:
        """为一个 NPC 构造回应 Prompt.

        读取权限: NPC.current_state, NPC.personality, NPC.knowledge,
                  NPC.belief, NPC.observation, NPC.memory,
                  NPC.pending_reason, NPC.goal, NPC.relationship,
                  DialogueHistory
        """
        knowledge = [k.fact for k in npc.knowledge[-5:]]
        beliefs = [b.belief for b in npc.belief[-3:]]
        goals = [
            g.goal for g in npc.goal
            if not g.is_completed and not g.is_abandoned
        ]
        observations = [o.observation for o in npc.observation[-3:]]
        memories = [m.content for m in npc.memory[-3:]]
        pending = [p.event_description for p in npc.pending_reason[-3:]]

        # 对话历史（该NPC自己的）
        history_lines = "（这是你们第一次对话）"
        # Note: dialogue_history 来自 WorldContext，在 WorldEngine 中管理

        # 人际关系
        relationships: list[str] = []
        for target_id, rel in npc.relationship.items():
            relationships.append(
                f"  对{target_id}: {rel.relationship_type.value} "
                f"好感{rel.favorability} 信任{rel.trust}"
            )

        prompt = self._render_prompt(
            npc_name=npc.name,
            npc_id=npc.npc_id,
            npc_role=npc.identity.role.value,
            npc_location=npc.current_state.location,
            npc_action=npc.current_state.action or "（无）",
            emotion=npc.current_state.emotion.value,
            intelligence=str(npc.personality.intelligence),
            personality_traits=", ".join(npc.personality.traits)
            if npc.personality.traits else "无特殊特质",
            is_addressed=(
                "是，玩家正在对你说话——你必须回应"
                if is_addressed else "否"
            ),
            perceived_content=(
                perceived_summary
                or "（本轮没有感知到任何事情）"
            ),
            conversation_history=history_lines,
            knowledge=(
                "\n".join(f"  - {k}" for k in knowledge)
                if knowledge else "  无"
            ),
            beliefs=(
                "\n".join(f"  - {b}" for b in beliefs)
                if beliefs else "  无"
            ),
            goals=(
                "\n".join(f"  - {g}" for g in goals)
                if goals else "  无"
            ),
            observations=(
                "\n".join(f"  - {o}" for o in observations)
                if observations else "  无"
            ),
            memories=(
                "\n".join(f"  - {m}" for m in memories)
                if memories else "  无"
            ),
            pending=(
                "\n".join(f"  - {p}" for p in pending)
                if pending else "  无"
            ),
            relationships=(
                "\n".join(relationships)
                if relationships else "  无"
            ),
        )

        return prompt, ""

    # ── JSON → NPCResponse ─────────────────────────────────

    def _to_schema_for_npc(self, parsed: dict, npc: Any) -> NPCResponse:
        return NPCResponse(
            npc_id=npc.npc_id,
            npc_name=npc.name,
            response_text=parsed.get("response_text", ""),
            response_type=parsed.get("response_type", "speech"),
            target_id=parsed.get("target_id", "player"),
            emotion=parsed.get("emotion", "neutral"),
            is_addressed=False,  # 由 Engine 根据 context 设置
            favorability_changes=parsed.get("favorability_changes", []),
        )

    # ── _build_prompt / _to_schema 占位 ──────────────────

    def _build_prompt(self, context: WorldContext) -> tuple[str, str]:
        return "", ""

    def _to_schema(self, parsed: dict, context: WorldContext) -> Any:
        return None
