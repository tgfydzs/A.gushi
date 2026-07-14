"""ReasoningNode — N15 推理层.

NPC 根据 pending 事件 + 已有知识/记忆/信念进行推理。

输入 (WorldContext):
    - npc.pending_reason (PendingEntry 列表)
    - npc.observation (最近N条)
    - npc.memory (最近N条)
    - npc.knowledge (最近N条)
    - npc.belief (最近N条)
    - npc.goal (未完成)
    - npc.personality.intelligence

输出 (暂态):
    - ReasoningResult (包含所有需要推理的 NPC 的 NPCReasoning)

禁止:
    - 修改 NPC 维护表
    - 产生没有依据的事实
    - 跨 NPC 推理
"""

from __future__ import annotations

from typing import Any

from engine.context import WorldContext
from nodes.base import BaseNode, NodeResult

from Schema.belief_schema import NPCReasoning, ReasoningResult


class ReasoningNode(BaseNode):
    """N15 — 推理层: NPC 对未知信息进行推理.

    仅处理有 pending 事件的 NPC。
    N14 (p1) 的程序判断（has_pending）由 Engine 在调用前完成。
    """

    @property
    def node_name(self) -> str:
        return "N15"

    @property
    def prompt_name(self) -> str:
        return "reasoning"

    # ── 空结果 ────────────────────────────────────────────

    def _empty_result(self, error_msg: str = "") -> NodeResult:
        return NodeResult(
            success=False,
            data=ReasoningResult(),
            error=error_msg,
            node_name=self.node_name,
        )

    # ── 主流程 ────────────────────────────────────────────

    def execute(self, context: WorldContext) -> NodeResult:
        """对每个有 pending 的 NPC 执行推理."""
        try:
            reasonings: list[NPCReasoning] = []
            skipped: list[str] = []

            for npc in context.get_active_npcs():
                if not npc.has_pending():
                    skipped.append(npc.npc_id)
                    reasonings.append(NPCReasoning(
                        npc_id=npc.npc_id,
                        reasoning_output="无待解释事件，跳过推理",
                    ))
                    continue

                try:
                    system_prompt, user_prompt = self._build_prompt_for_npc(
                        npc
                    )
                    response_text = self._call_llm(
                        system_prompt, user_prompt
                    )
                    parsed = self._parse_json(response_text)

                    if parsed:
                        reasoning = self._to_schema_for_npc(
                            parsed, npc.npc_id
                        )
                    else:
                        reasoning = NPCReasoning(
                            npc_id=npc.npc_id,
                            reasoning_output="LLM 返回无法解析",
                        )
                except Exception:
                    reasoning = NPCReasoning(
                        npc_id=npc.npc_id,
                        reasoning_output="推理过程异常",
                    )

                reasonings.append(reasoning)

            return NodeResult(
                success=True,
                data=ReasoningResult(
                    npc_reasonings=reasonings,
                    skipped_npc_ids=skipped,
                ),
                node_name=self.node_name,
            )
        except Exception as e:
            return self._empty_result(str(e))

    # ── 构造 Prompt ───────────────────────────────────────

    def _build_prompt_for_npc(self, npc: Any) -> tuple[str, str]:
        """为一个 NPC 构造推理 Prompt.

        读取权限: NPC.pending_reason, NPC.observation, NPC.memory,
                  NPC.knowledge, NPC.belief, NPC.goal, NPC.personality
        """
        pending = [p.event_description for p in npc.pending_reason[-5:]]
        observations = [o.observation for o in npc.observation[-5:]]
        memories = [m.content for m in npc.memory[-5:]]
        knowledge = [k.fact for k in npc.knowledge[-5:]]
        goals = [
            g.goal for g in npc.goal
            if not g.is_completed and not g.is_abandoned
        ]
        beliefs = [b.belief for b in npc.belief[-3:]]

        personality = (
            f"开放:{npc.personality.openness} "
            f"尽责:{npc.personality.conscientiousness} "
            f"外向:{npc.personality.extraversion} "
            f"宜人:{npc.personality.agreeableness} "
            f"神经质:{npc.personality.neuroticism} "
            f"认知:{npc.personality.intelligence}/100"
        )

        prompt = self._render_prompt(
            npc_name=npc.name,
            npc_id=npc.npc_id,
            npc_role=npc.identity.role.value,
            personality=personality,
            pending_events=(
                "\n".join(f"  - {p}" for p in pending)
                if pending else "  (无)"
            ),
            observations=(
                "\n".join(f"  - {o}" for o in observations)
                if observations else "  (无)"
            ),
            memories=(
                "\n".join(f"  - {m}" for m in memories)
                if memories else "  (无)"
            ),
            knowledge=(
                "\n".join(f"  - {k}" for k in knowledge)
                if knowledge else "  (无)"
            ),
            goals=(
                "\n".join(f"  - {g}" for g in goals)
                if goals else "  (无)"
            ),
            # beliefs 占位符
            beliefs=(
                "\n".join(f"  - {b}" for b in beliefs)
                if beliefs else "  (无)"
            ),
        )

        return prompt, ""

    # ── JSON → NPCReasoning ───────────────────────────────

    def _to_schema_for_npc(
        self, parsed: dict, npc_id: str
    ) -> NPCReasoning:
        return NPCReasoning(
            npc_id=npc_id,
            new_knowledge=parsed.get("new_knowledge", []),
            new_beliefs=parsed.get("new_beliefs", []),
            resolved_pending=parsed.get("resolved_pending", []),
            goals_updated=parsed.get("goals_updated", []),
            reasoning_output=parsed.get("reasoning_output", ""),
            emotion_change=parsed.get("emotion_change", ""),
        )

    # ── _build_prompt / _to_schema 占位 ──────────────────

    def _build_prompt(self, context: WorldContext) -> tuple[str, str]:
        return "", ""

    def _to_schema(self, parsed: dict, context: WorldContext) -> Any:
        return None
