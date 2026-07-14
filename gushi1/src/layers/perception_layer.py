"""PerceptionLayer — 感知层.

职责：接收事件上下文表，用LLM判断每个NPC的感知情况，
然后分发——只将相关事件信息传递给能感知到的NPC。

此层是信息分发的闸门：
- 上游：EventLayer的事件上下文表
- 下游：每个NPC只收到自己能感知到的那部分

禁止修改NPC维护表。
"""

from dataclasses import dataclass, field
from typing import Any

from src.core.llm_client import LLMClient
from src.layers.base import BaseLayer, LayerResult
from src.models.event import Event, EventScope
from src.models.result import BaseResult
from src.utils.data_loader import render_prompt
from src.utils.json_utils import parse_json


# =============================================================================
# Result
# =============================================================================

@dataclass
class NPCPerception:
    """单个NPC的感知结果.

    perceived_summary: 用自然语言描述该NPC感知到的内容（给后续层用）。
    """

    npc_id: str
    perceived_event_ids: list[str] = field(default_factory=list)
    perceived_summary: str = ""
    missed_event_ids: list[str] = field(default_factory=list)


@dataclass
class PerceptionResult(BaseResult):
    """感知层输出.

    包含每个NPC的感知结果。后续层（Reasoning/Response）
    必须根据此结果过滤——只给NPC看他们感知到的事件。
    """

    npc_perceptions: list[NPCPerception] = field(default_factory=list)
    all_event_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.layer_name = "PerceptionLayer"

    def get_for_npc(self, npc_id: str) -> NPCPerception | None:
        for p in self.npc_perceptions:
            if p.npc_id == npc_id:
                return p
        return None


# =============================================================================
# Schema
# =============================================================================

@dataclass
class PerceptionInput:
    events: list[Event] = field(default_factory=list)
    npcs: list[dict] = field(default_factory=list)
    world_state: dict = field(default_factory=dict)
    player_input: str = ""


# =============================================================================
# Layer
# =============================================================================

@dataclass
class PerceptionLayer(BaseLayer):
    """感知层 — 事件上下文的分发闸门.

    Python只做数据搬运。LLM做所有感知判断。
    """

    llm_client: LLMClient | None = None

    @property
    def layer_name(self) -> str:
        return "PerceptionLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        if isinstance(input_data, dict):
            input_data = PerceptionInput(
                events=input_data.get("events", []),
                npcs=input_data.get("npcs", []),
                world_state=input_data.get("world_state", {}),
                player_input=input_data.get("player_input", ""),
            )

        if not isinstance(input_data, PerceptionInput):
            return LayerResult(
                success=False,
                error=f"PerceptionLayer 期望 PerceptionInput，收到 {type(input_data)}",
            )

        all_ids = [e.event_id for e in input_data.events]

        if not input_data.events or not input_data.npcs:
            return LayerResult(
                success=True,
                data=PerceptionResult(
                    npc_perceptions=[], all_event_ids=all_ids,
                ),
            )

        # 过滤hidden事件
        visible_events = [e for e in input_data.events if e.scope != EventScope.HIDDEN]

        if not visible_events:
            return LayerResult(
                success=True,
                data=PerceptionResult(
                    npc_perceptions=[
                        NPCPerception(
                            npc_id=n["npc_id"],
                            perceived_event_ids=[],
                            perceived_summary="无可见事件",
                            missed_event_ids=all_ids,
                        )
                        for n in input_data.npcs
                    ],
                    all_event_ids=all_ids,
                ),
            )

        try:
            perceptions = self._judge_all(input_data.npcs, visible_events, input_data.player_input)
            return LayerResult(
                success=True,
                data=PerceptionResult(
                    npc_perceptions=perceptions, all_event_ids=all_ids,
                ),
            )
        except Exception as e:
            return LayerResult(
                success=False, error=f"PerceptionLayer 失败: {e}",
            )

    def _judge_all(
        self, npcs: list[dict], events: list[Event], player_input: str = "",
    ) -> list[NPCPerception]:
        """对每个NPC调用LLM判断感知."""
        results: list[NPCPerception] = []
        for npc_data in npcs:
            if self.llm_client is not None:
                results.append(self._llm_judge(npc_data, events, player_input))
            else:
                results.append(self._fallback_judge(npc_data, events))
        return results

    def _llm_judge(self, npc_data: dict, events: list[Event], player_input: str = "") -> NPCPerception:
        """LLM判断单个NPC的感知."""
        prompt = self._build_prompt(npc_data, events, player_input)
        response = self.llm_client.send(prompt)

        if not response.success:
            # LLM网络失败 → 用fallback保证involved NPC至少感知
            return self._fallback_judge(npc_data, events)

        parsed = parse_json(response.raw_text)
        if parsed is None:
            # LLM调用失败 → 用fallback
            return self._fallback_judge(npc_data, events)

        npc_id = npc_data.get("npc_id", "")
        perceived = parsed.get("perceived_event_ids", [])
        missed = parsed.get("missed_event_ids", [])

        # 代码级保护: NPC在involved_npc_ids中的事件必须感知
        for event in events:
            if npc_id in event.involved_npc_ids and event.event_id not in perceived:
                perceived.append(event.event_id)
                if event.event_id in missed:
                    missed.remove(event.event_id)

        return NPCPerception(
            npc_id=npc_id,
            perceived_event_ids=perceived,
            perceived_summary=parsed.get("perceived_summary", ""),
            missed_event_ids=missed,
        )

    def _fallback_judge(
        self, npc_data: dict, events: list[Event],
    ) -> NPCPerception:
        """无LLM时按scope+位置+involved回退判断."""
        npc_id = npc_data.get("npc_id", "")
        npc_loc = npc_data.get("location", "")
        perceived_ids: list[str] = []
        missed_ids: list[str] = []

        for event in events:
            # 规则0: NPC在involved_npc_ids中 → 无条件感知（代码级硬保护）
            if npc_id in event.involved_npc_ids:
                perceived_ids.append(event.event_id)
            # 规则1: 同位置
            elif event.location and npc_loc and event.location == npc_loc:
                perceived_ids.append(event.event_id)
            # 规则2: 无位置的事件
            elif not event.location:
                perceived_ids.append(event.event_id)
            else:
                missed_ids.append(event.event_id)

        return NPCPerception(
            npc_id=npc_data.get("npc_id", ""),
            perceived_event_ids=perceived_ids,
            perceived_summary="",
            missed_event_ids=missed_ids,
        )

    def _build_prompt(self, npc_data: dict, events: list[Event], player_input: str = "") -> str:
        # 事件表
        event_lines = []
        for e in events:
            event_lines.append(
                f"  [{e.event_id}] type={e.event_type.value} "
                f"scope={e.scope.value} location=\"{e.location}\" "
                f"summary=\"{e.description}\" "
                f"involved_npcs={e.involved_npc_ids}"
            )
        # 玩家原话 — 关键：让感知摘要包含实际内容
        player_line = f"玩家原话: \"{player_input}\"" if player_input else ""

        # NPC信息
        pers = npc_data.get("personality", {})
        traits = pers.get("traits", [])
        intelligence = pers.get("intelligence", 50)
        personality = (
            f"开放度{pers.get('openness',50)} "
            f"外向{pers.get('extraversion',50)} "
            f"宜人{pers.get('agreeableness',50)} "
            f"认知{intelligence}/100 "
            f"{'特质: ' + ','.join(traits) if traits else ''}"
        )

        knowledge = npc_data.get("knowledge", [])
        goals = npc_data.get("goals", [])

        return render_prompt(
            "perception",
            npc_name=str(npc_data.get("name", "")),
            npc_id=str(npc_data.get("npc_id", "")),
            npc_role=str(npc_data.get("role", "")),
            npc_location=str(npc_data.get("location", "未知")),
            npc_emotion=str(npc_data.get("emotion", "neutral")),
            intelligence=str(intelligence),
            personality=personality,
            knowledge="\n".join(f"  - {k}" for k in knowledge) if knowledge else "  无",
            goals="\n".join(f"  - {g}" for g in goals) if goals else "  无",
            event_table="\n".join(event_lines),
            player_said=player_line,
        )
