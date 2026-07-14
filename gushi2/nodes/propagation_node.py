"""PropagationNode — N18 信息传播层.

判断事件信息如何扩散到未直接感知的 NPC。

两阶段处理：
1. 程序筛选候选接收者（基于 scope + location + faction）
2. LLM 判断每个候选者是否应接收、通过什么渠道、内容如何改写

输入 (WorldContext):
    - Event (scope/location/description/involved_npc_ids)
    - DirectorResult.narrative (导演层输出)
    - NPC (location/faction/role)
    - WorldState.global_events
    - WorldDefinition.rules

输出 (暂态):
    - PropagationResult (传播建议列表 + 可能的全局事件)

禁止:
    - 直接写入 NPC.knowledge（由 N12 UpdateEngine 执行）
    - 修改 NPC 其他维护表
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from engine.context import WorldContext
from nodes.base import BaseNode, NodeResult

from Schema.event_schema import EventScope


# =============================================================================
# 暂态输出
# =============================================================================


@dataclass
class PropagationSuggestion:
    """单条传播建议.

    Attributes:
        target_npc_id: 接收者 NPC ID.
        should_receive: 是否应接收此信息.
        propagation_method: 传播渠道.
        content: 传播后的知识内容（可能已改写）.
        confidence: 可信度 (0.0-1.0).
        distortion: 失真度 (0.0-1.0).
        reason: 原因说明.
    """

    target_npc_id: str = ""
    should_receive: bool = False
    propagation_method: str = ""
    content: str = ""
    confidence: float = 0.0
    distortion: float = 0.0
    reason: str = ""


@dataclass
class GlobalEventSuggestion:
    """全局事件升级建议.

    Attributes:
        should_create: 是否应升级为全局事件.
        description: 全局事件描述.
        affected_area: 影响区域.
        reason: 升级原因.
    """

    should_create: bool = False
    description: str = ""
    affected_area: str = ""
    reason: str = ""


@dataclass
class PropagationNodeOutput:
    """N18 传播层暂态输出.

    Attributes:
        suggestions: 每个候选接收者的传播建议.
        global_event: 全局事件升级建议.
    """

    suggestions: list[PropagationSuggestion] = field(default_factory=list)
    global_event: GlobalEventSuggestion = field(
        default_factory=GlobalEventSuggestion
    )


# =============================================================================
# Node
# =============================================================================


class PropagationNode(BaseNode):
    """N18 — 信息传播层: 判断信息如何在世界中扩散."""

    # 传播参数（可配置）
    MAX_HOPS: int = 3
    BASE_DISTORTION: float = 0.15
    CURRENT_HOP: int = 1  # 第一跳

    @property
    def node_name(self) -> str:
        return "N18"

    @property
    def prompt_name(self) -> str:
        return "propagation"

    # ── 空结果 ────────────────────────────────────────────

    def _empty_result(self, error_msg: str = "") -> NodeResult:
        return NodeResult(
            success=False,
            data=PropagationNodeOutput(),
            error=error_msg,
            node_name=self.node_name,
        )

    # ── 主流程 ────────────────────────────────────────────

    def execute(self, context: WorldContext) -> NodeResult:
        """执行传播判断.

        1. 程序筛选候选接收者
        2. LLM 判断每个候选者
        """
        try:
            if not context.events:
                return NodeResult(
                    success=True,
                    data=PropagationNodeOutput(),
                    node_name=self.node_name,
                )

            event = context.events[0]
            scope = event.scope

            # scope=private 或 hidden → 不传播
            if scope in (EventScope.PRIVATE, EventScope.HIDDEN):
                # 例外：叙事中明确提到定向传播（如写信）
                narrative = self._get_narrative(context)
                if narrative and not self._has_directed_propagation(narrative):
                    return NodeResult(
                        success=True,
                        data=PropagationNodeOutput(),
                        node_name=self.node_name,
                    )

            # 筛选候选接收者
            candidates = self._filter_candidates(context, event)

            if not candidates:
                return NodeResult(
                    success=True,
                    data=PropagationNodeOutput(),
                    node_name=self.node_name,
                )

            # LLM 判断
            system_prompt, user_prompt = self._build_prompt(
                context, event, candidates
            )
            response_text = self._call_llm(system_prompt, user_prompt)
            parsed = self._parse_json(response_text)

            if parsed:
                data = self._to_schema(parsed, context)
                return NodeResult(
                    success=True, data=data, node_name=self.node_name,
                )
            else:
                # LLM 失败 → 只做程序级传播（public → 同位置NPC）
                return NodeResult(
                    success=True,
                    data=self._programmatic_propagation(context, event),
                    node_name=self.node_name,
                )

        except Exception as e:
            return self._empty_result(str(e))

    # ── 候选筛选（程序逻辑） ──────────────────────────────

    def _filter_candidates(
        self, context: WorldContext, event: Any,
    ) -> list[dict]:
        """程序筛选可能接收传播的 NPC.

        排除：
        - 事件参与者（已从 N11 直接感知）
        - 不在世界规则范围内的 NPC
        - 昏迷/死亡的 NPC
        """
        involved = set(event.involved_npc_ids)
        event_loc = event.location

        candidates: list[dict] = []
        for npc in context.get_active_npcs():
            if npc.npc_id in involved:
                continue

            npc_loc = npc.current_state.location

            # 距离判断
            if npc_loc == event_loc:
                distance_tag = "同位置"
            elif self._is_same_area(npc_loc, event_loc):
                distance_tag = "同区域"
            elif self._is_same_city(npc_loc, event_loc):
                distance_tag = "同城市"
            else:
                distance_tag = "偏远"

            # 跳过太远的（超过2跳的物理距离）
            if distance_tag == "偏远":
                continue

            # 计算已有的相关知识的简单统计
            relevant_count = 0
            event_keywords = event.description.lower().split()
            for k in npc.knowledge:
                if any(kw in k.fact.lower() for kw in event_keywords[:3]):
                    relevant_count += 1

            candidates.append({
                "npc_id": npc.npc_id,
                "npc_name": npc.name,
                "npc_role": npc.identity.role.value,
                "npc_location": npc_loc,
                "npc_faction": npc.identity.faction or "无",
                "distance": distance_tag,
                "is_participant": False,
                "relevant_knowledge_count": relevant_count,
            })

        return candidates

    @staticmethod
    def _is_same_area(loc_a: str, loc_b: str) -> bool:
        """简单判断是否同一区域."""
        if not loc_a or not loc_b:
            return False
        # 按 "·" 分割，前一部分相同则为同区域
        parts_a = loc_a.split("·")
        parts_b = loc_b.split("·")
        return parts_a[0] == parts_b[0] if parts_a and parts_b else False

    @staticmethod
    def _is_same_city(loc_a: str, loc_b: str) -> bool:
        """简单判断是否同一城市."""
        if not loc_a or not loc_b:
            return False
        # 第一段相同即为同城市
        parts_a = loc_a.split("·")
        parts_b = loc_b.split("·")
        return parts_a[0] == parts_b[0] if parts_a and parts_b else False

    @staticmethod
    def _get_narrative(context: WorldContext) -> str:
        """从 DirectorResult 获取叙事文本."""
        if context.director_result is not None:
            dr = context.director_result
            if hasattr(dr, 'narrative'):
                return dr.narrative or ""
        return ""

    @staticmethod
    def _has_directed_propagation(narrative: str) -> bool:
        """检查叙事中是否包含定向传播动作（写信、派人通知等）."""
        keywords = ["写信", "派人", "送信", "传讯", "飞鸽", "传书"]
        return any(kw in narrative for kw in keywords)

    # ── 构造 Prompt ───────────────────────────────────────

    def _build_prompt(
        self, context: WorldContext, event: Any, candidates: list[dict],
    ) -> tuple[str, str]:
        """构造传播 Prompt.

        读取权限: Event, DirectorResult.narrative, NPC摘要,
                  WorldState, WorldDefinition.rules
        """
        narrative = self._get_narrative(context)

        # 构造 NPC 候选列表文本
        if candidates:
            npc_entries: list[str] = []
            for c in candidates:
                npc_entries.append(
                    f"{c['npc_id']}: {c['npc_name']}\n"
                    f"  角色: {c['npc_role']}\n"
                    f"  位置: {c['npc_location']}\n"
                    f"  阵营: {c['npc_faction']}\n"
                    f"  与事件地点的距离: {c['distance']}\n"
                    f"  是否事件参与者: 否\n"
                    f"  已有相关知识的条数: {c['relevant_knowledge_count']}"
                )
            candidates_text = "\n\n".join(npc_entries)
        else:
            candidates_text = "（无候选接收者）"

        # 世界规则文本
        wd = context.world_definition
        rules_text = ""
        if wd and wd.rules:
            for r in wd.rules:
                rules_text += f"  - {r.description}\n"

        # 添加规则到 system_prompt
        system_rules = ""
        if rules_text:
            system_rules = (
                f"## 世界规则约束\n\n{rules_text}\n\n"
                f"传播信息时不得违反以上规则。"
            )

        prompt = self._render_prompt(
            event_id=event.event_id,
            event_description=event.description,
            event_location=event.location,
            event_scope=event.scope.value,
            involved_npc_ids=str(event.involved_npc_ids),
            narrative=narrative or "（无叙事文本）",
            max_hops=str(self.MAX_HOPS),
            current_hop=str(self.CURRENT_HOP),
            base_distortion_rate=str(self.BASE_DISTORTION),
        )

        # 将候选列表和规则注入 Prompt
        # 提示: 在 prompt 末尾附加候选者列表
        full_prompt = (
            prompt
            + "\n\n---\n\n## 候选接收者列表\n\n"
            + candidates_text
            + "\n\n"
            + system_rules
        )

        return full_prompt, ""

    # ── JSON → PropagationNodeOutput ───────────────────────

    def _to_schema(
        self, parsed: dict, context: WorldContext
    ) -> PropagationNodeOutput:
        suggestions: list[PropagationSuggestion] = []
        for entry in parsed.get("propagations", []):
            suggestions.append(PropagationSuggestion(
                target_npc_id=entry.get("target_npc_id", ""),
                should_receive=entry.get("should_receive", False),
                propagation_method=entry.get("propagation_method", ""),
                content=entry.get("content", ""),
                confidence=float(entry.get("confidence", 0.0)),
                distortion=float(entry.get("distortion", 0.0)),
                reason=entry.get("reason", ""),
            ))

        ge = parsed.get("global_event", {})
        global_event = GlobalEventSuggestion(
            should_create=ge.get("should_create", False),
            description=ge.get("description", ""),
            affected_area=ge.get("affected_area", ""),
            reason=ge.get("reason", ""),
        )

        return PropagationNodeOutput(
            suggestions=suggestions,
            global_event=global_event,
        )

    # ── 程序级传播（LLM 失败时的回退） ──────────────────────

    def _programmatic_propagation(
        self, context: WorldContext, event: Any,
    ) -> PropagationNodeOutput:
        """纯程序传播：public → 同位置 NPC."""
        suggestions: list[PropagationSuggestion] = []
        event_loc = event.location
        involved = set(event.involved_npc_ids)

        if event.scope != EventScope.PUBLIC:
            return PropagationNodeOutput()

        for npc in context.get_active_npcs():
            if npc.npc_id in involved:
                continue
            if npc.current_state.location == event_loc:
                suggestions.append(PropagationSuggestion(
                    target_npc_id=npc.npc_id,
                    should_receive=True,
                    propagation_method="word_of_mouth",
                    content=event.description,
                    confidence=0.7,
                    distortion=0.1,
                    reason="同位置 NPC 通过口口相传（程序回退）",
                ))

        return PropagationNodeOutput(suggestions=suggestions)
