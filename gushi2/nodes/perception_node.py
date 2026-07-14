"""PerceptionNode — N11 感知层.

判断每个 NPC 对当前事件感知到了什么。

输入 (WorldContext):
    - events (Event 列表)
    - npcs (每个 NPC 的 identity/current_state/personality)
    - npc.knowledge (最近N条)
    - npc.observation (最近N条)
    - npc.goal (未完成)

输出 (暂态):
    - PerceptionResult (包含所有 NPC 的 NPCPerception 列表)

禁止:
    - 修改 NPC 维护表
    - 使用完整事件表（只在此节点内部构建 prompt，不暴露给下游）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from engine.context import WorldContext
from nodes.base import BaseNode, NodeResult

from Schema.event_schema import EventScope
from Schema.perception_schema import NPCPerception, PerceptionResult


class PerceptionNode(BaseNode):
    """N11 — 感知层: 判断每个 NPC 对事件的感知.

    这是系统的信息闸门——后续所有层只能使用此节点的输出，
    绝不能看到完整的事件表。
    """

    @property
    def node_name(self) -> str:
        return "N11"

    @property
    def prompt_name(self) -> str:
        return "perception"

    # ── 空结果 ────────────────────────────────────────────

    def _empty_result(self, error_msg: str = "") -> NodeResult:
        return NodeResult(
            success=False,
            data=PerceptionResult(),
            error=error_msg,
            node_name=self.node_name,
        )

    # ── 主流程覆盖 ────────────────────────────────────────

    def execute(self, context: WorldContext) -> NodeResult:
        """对每个活跃 NPC 执行感知判断.

        为每个 NPC 单独调用 LLM（每个 NPC 只知道自己的信息）。
        """
        try:
            events = context.events
            if not events:
                return NodeResult(
                    success=True,
                    data=PerceptionResult(
                        npc_perceptions=[],
                        all_event_ids=[],
                        hidden_event_ids=[],
                    ),
                    node_name=self.node_name,
                )

            active_npcs = context.get_active_npcs()
            if not active_npcs:
                return NodeResult(
                    success=True,
                    data=PerceptionResult(
                        npc_perceptions=[],
                        all_event_ids=[e.event_id for e in events],
                        hidden_event_ids=[
                            e.event_id for e in events
                            if e.scope == EventScope.HIDDEN
                        ],
                    ),
                    node_name=self.node_name,
                )

            # 过滤 hidden 事件
            visible_events = [
                e for e in events if e.scope != EventScope.HIDDEN
            ]
            all_ids = [e.event_id for e in events]
            hidden_ids = [
                e.event_id for e in events
                if e.scope == EventScope.HIDDEN
            ]

            if not visible_events:
                # 全部 hidden → 所有 NPC 无感知
                perceptions = [
                    NPCPerception(
                        npc_id=n.npc_id,
                        perceived_event_ids=[],
                        perceived_summary="没有感知到任何异常",
                        missed_event_ids=all_ids,
                    )
                    for n in active_npcs
                ]
                return NodeResult(
                    success=True,
                    data=PerceptionResult(
                        npc_perceptions=perceptions,
                        all_event_ids=all_ids,
                        hidden_event_ids=hidden_ids,
                    ),
                    node_name=self.node_name,
                )

            # 为每个 NPC 单独判断
            # 程序级预过滤：NPC不在事件位置且不在 involved_npc_ids 中 → 不可能直接感知，跳过 LLM
            perceptions: list[NPCPerception] = []
            event_location = visible_events[0].location if visible_events else ""
            involved_set: set[str] = set()
            for e in visible_events:
                involved_set.update(e.involved_npc_ids)

            for npc in active_npcs:
                npc_location = npc.current_state.location

                # 硬约束1：NPC在involved_npc_ids中 → 必须感知（程序保证）
                if npc.npc_id in involved_set:
                    try:
                        system_prompt, user_prompt = self._build_prompt_for_npc(
                            npc, visible_events, context
                        )
                        response_text = self._call_llm(system_prompt, user_prompt)
                        parsed = self._parse_json(response_text)
                        if parsed:
                            perception = self._to_schema_for_npc(parsed, npc.npc_id, visible_events)
                        else:
                            perception = self._fallback_perception(npc, visible_events)
                    except Exception:
                        perception = self._fallback_perception(npc, visible_events)
                    perceptions.append(perception)
                    continue

                # 硬约束2：NPC不在事件位置 → 不可能直接感知，跳过LLM调用
                if event_location and npc_location != event_location:
                    perceptions.append(NPCPerception(
                        npc_id=npc.npc_id,
                        perceived_event_ids=[],
                        perceived_summary="没有感知到任何异常",
                        missed_event_ids=all_ids,
                    ))
                    continue

                # NPC在同位置 → 调用LLM判断感知细节
                try:
                    system_prompt, user_prompt = self._build_prompt_for_npc(
                        npc, visible_events, context
                    )
                    response_text = self._call_llm(system_prompt, user_prompt)
                    parsed = self._parse_json(response_text)

                    if parsed:
                        perception = self._to_schema_for_npc(
                            parsed, npc.npc_id, visible_events
                        )
                    else:
                        # LLM 失败 → fallback: 同位置 NPC 感知到 area/public 事件
                        perception = self._fallback_perception(
                            npc, visible_events
                        )
                except Exception:
                    perception = self._fallback_perception(
                        npc, visible_events
                    )

                perceptions.append(perception)

            return NodeResult(
                success=True,
                data=PerceptionResult(
                    npc_perceptions=perceptions,
                    all_event_ids=all_ids,
                    hidden_event_ids=hidden_ids,
                ),
                node_name=self.node_name,
            )
        except Exception as e:
            return self._empty_result(str(e))

    # ── 构造 Prompt（单 NPC） ──────────────────────────────

    def _build_prompt_for_npc(
        self, npc: Any, events: list[Any], context: WorldContext
    ) -> tuple[str, str]:
        """为一个 NPC 构造感知 Prompt.

        读取权限: NPC.identity, NPC.current_state, NPC.personality,
                  NPC.knowledge, NPC.observation, NPC.goal
        """
        # 事件表
        event_lines: list[str] = []
        for e in events:
            event_lines.append(
                f"  [{e.event_id}] type={e.event_type.value} "
                f"scope={e.scope.value} location=\"{e.location}\" "
                f"summary=\"{e.description}\" "
                f"involved_npcs={e.involved_npc_ids}"
            )

        # NPC 性格
        pers = npc.personality
        traits = pers.traits
        intelligence = pers.intelligence
        personality_text = (
            f"开放度{pers.openness} "
            f"外向{pers.extraversion} "
            f"宜人{pers.agreeableness} "
            f"认知{intelligence}/100 "
            f"{'特质: ' + ','.join(traits) if traits else ''}"
        )

        knowledge = [k.fact for k in npc.knowledge[-5:]]
        goals = [
            g.goal for g in npc.goal
            if not g.is_completed and not g.is_abandoned
        ]

        prompt = self._render_prompt(
            npc_name=npc.name,
            npc_id=npc.npc_id,
            npc_role=npc.identity.role.value,
            npc_location=npc.current_state.location,
            npc_emotion=npc.current_state.emotion.value,
            npc_action=npc.current_state.action or "（无）",
            intelligence=str(intelligence),
            personality=personality_text,
            knowledge=(
                "\n".join(f"  - {k}" for k in knowledge)
                if knowledge else "  无"
            ),
            goals=(
                "\n".join(f"  - {g}" for g in goals)
                if goals else "  无"
            ),
            event_table="\n".join(event_lines),
            player_said="",  # 将在下面填充
            # observations 占位
            observations="  无",
        )

        # 玩家原话从第一个事件的 description 提取
        player_line = ""
        if events:
            player_line = f'玩家原话: "{events[0].description}"'

        # 填充感知 prompt 中的占位符
        for var_name, var_value in [
            ("observations", "  无"),
            ("player_said", player_line),
        ]:
            prompt = prompt.replace(
                "{" + var_name + "}", str(var_value)
            )

        return prompt, ""

    # ── JSON → NPCPerception ──────────────────────────────

    def _to_schema_for_npc(
        self, parsed: dict, npc_id: str, events: list[Any]
    ) -> NPCPerception:
        perceived = parsed.get("perceived_event_ids", [])
        missed = parsed.get("missed_event_ids", [])

        # 硬保护: involved_npc_ids 中的 NPC 必须感知
        for e in events:
            if npc_id in e.involved_npc_ids:
                if e.event_id not in perceived:
                    perceived.append(e.event_id)
                if e.event_id in missed:
                    missed.remove(e.event_id)

        return NPCPerception(
            npc_id=npc_id,
            perceived_event_ids=perceived,
            perceived_summary=parsed.get("perceived_summary", ""),
            missed_event_ids=missed,
            perception_confidence=float(
                parsed.get("perception_confidence", 1.0)
            ),
        )

    # ── Fallback ─────────────────────────────────────────

    def _fallback_perception(
        self, npc: Any, events: list[Any]
    ) -> NPCPerception:
        """无 LLM 时的回退判断：按位置 + scope + involved 决定."""
        npc_id = npc.npc_id
        npc_loc = npc.current_state.location
        perceived_ids: list[str] = []
        missed_ids: list[str] = []

        for e in events:
            if npc_id in e.involved_npc_ids:
                perceived_ids.append(e.event_id)
            elif e.location and npc_loc and e.location == npc_loc:
                perceived_ids.append(e.event_id)
            elif not e.location:
                perceived_ids.append(e.event_id)
            else:
                missed_ids.append(e.event_id)

        return NPCPerception(
            npc_id=npc_id,
            perceived_event_ids=perceived_ids,
            perceived_summary=(
                "（回退感知：同位置或参与者）" if perceived_ids
                else "没有感知到任何异常"
            ),
            missed_event_ids=missed_ids,
        )

    # ── _build_prompt / _to_schema 占位 ──────────────────

    def _build_prompt(self, context: WorldContext) -> tuple[str, str]:
        """不使用——参见 execute()."""
        return "", ""

    def _to_schema(self, parsed: dict, context: WorldContext) -> Any:
        """不使用——参见 execute()."""
        return None
