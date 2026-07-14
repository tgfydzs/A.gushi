"""EventLayer — 事件层.

职责：维护当前事件上下文（跨tick持久），只在玩家改变了什么时才更新。
- 世界规则、世界状态、参与NPC默认不发生改变。
- 只有玩家明确提到新话题、把某NPC拽进/踢出，才更新当前事件。
"""

from dataclasses import dataclass, field
from typing import Any

from src.core.llm_client import LLMClient
from src.layers.base import BaseLayer, LayerResult
from src.models.event import Event, EventDetail, EventType, EventScope
from src.models.result import BaseResult
from src.utils.data_loader import render_prompt
from src.utils.json_utils import parse_json

# =============================================================================
# CurrentEvent — 跨tick持久的事件上下文
# =============================================================================


@dataclass
class CurrentEvent:
    """跨tick持久的事件上下文 — 由AI维护，Python只存不判.

    Attributes:
        location: 场景位置.
        topic: 当前话题.
        addressed_npc_id: 玩家在跟谁说话.
        involved_npc_ids: 事件参与者（谁在场/知情）.
        event_type: 事件类型.
        scope: 公开程度.
    """

    location: str = ""
    topic: str = ""
    addressed_npc_id: str = ""
    involved_npc_ids: list[str] = field(default_factory=list)
    event_type: str = "dialogue"
    scope: str = "private"

    def is_active(self) -> bool:
        return bool(self.location and self.involved_npc_ids)

    def to_event(self) -> Event:
        """转为下游层可用的Event."""
        return Event(
            event_id="current",
            event_type=EventType.DIALOGUE if self.event_type == "dialogue" else EventType.PLAYER_ACTION,
            description=self.topic,
            scope=_SCOPE_MAP.get(self.scope, EventScope.PRIVATE),
            location=self.location,
            involved_npc_ids=list(self.involved_npc_ids),
        )

# =============================================================================
# 类型映射（容错）
# =============================================================================

_EVENT_TYPE_MAP: dict[str, EventType] = {
    "player_action": EventType.PLAYER_ACTION, "npc_action": EventType.NPC_ACTION,
    "dialogue": EventType.DIALOGUE, "world_event": EventType.WORLD_EVENT,
    "discovery": EventType.DISCOVERY, "combat": EventType.COMBAT,
    "trade": EventType.TRADE, "custom": EventType.CUSTOM,
    "move": EventType.PLAYER_ACTION, "说话": EventType.DIALOGUE,
    "对话": EventType.DIALOGUE, "移动": EventType.PLAYER_ACTION,
    "action": EventType.PLAYER_ACTION, "speech": EventType.DIALOGUE,
}

_SCOPE_MAP: dict[str, EventScope] = {
    "public": EventScope.PUBLIC, "area": EventScope.AREA,
    "group": EventScope.GROUP, "private": EventScope.PRIVATE,
    "hidden": EventScope.HIDDEN,
}


# =============================================================================
# Result
# =============================================================================

@dataclass
class EventResult(BaseResult):
    """事件层处理结果."""

    events: list[Event] = field(default_factory=list)
    raw_player_input: str = ""

    def __post_init__(self) -> None:
        self.layer_name = "EventLayer"

    def get_all_npc_ids(self) -> list[str]:
        ids: set[str] = set()
        for event in self.events:
            for npc_id in event.get_npc_ids():
                ids.add(npc_id)
        return list(ids)


# =============================================================================
# Schema
# =============================================================================

@dataclass
class EventInput:
    """EventLayer 输入."""

    player_input: str = ""
    world_state: dict = field(default_factory=dict)
    available_npc_ids: list[str] = field(default_factory=list)
    npc_info: dict = field(default_factory=dict)
    player_location: str = ""
    world_rules: list[dict] = field(default_factory=list)


# =============================================================================
# Parser
# =============================================================================

@dataclass
class EventParser:
    """将LLM JSON输出解析为EventResult。纯数据转换."""

    def parse(self, llm_output: dict | list, raw_player_input: str = "") -> EventResult:
        if isinstance(llm_output, list):
            raw_events = llm_output
        else:
            raw_events = llm_output.get("events", [])

        events: list[Event] = []

        for i, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, dict):
                continue

            event_type = _EVENT_TYPE_MAP.get(
                raw_event.get("event_type", "player_action"), EventType.CUSTOM
            )

            raw_details = raw_event.get("details", [])
            if isinstance(raw_details, dict):
                raw_details = [raw_details]

            details = []
            for j, raw_detail in enumerate(raw_details):
                if not isinstance(raw_detail, dict):
                    continue
                details.append(EventDetail(
                    detail_id=raw_detail.get("detail_id", f"detail_{i}_{j}"),
                    action=raw_detail.get("action", ""),
                    subject_id=raw_detail.get("subject_id", ""),
                    target_id=raw_detail.get("target_id", ""),
                    content=raw_detail.get("content", ""),
                    location=raw_detail.get("location", raw_event.get("location", "")),
                ))

            # 兼容 summary(新) 和 description(旧)
            summary = str(raw_event.get("summary", raw_event.get("description", "")))

            events.append(Event(
                event_id=str(raw_event.get("event_id", f"event_{i}")),
                event_type=event_type,
                description=summary,
                details=details,
                scope=_SCOPE_MAP.get(raw_event.get("scope", "private"), EventScope.PRIVATE),
                location=str(raw_event.get("location", "")),
                involved_npc_ids=[str(x) for x in raw_event.get("involved_npc_ids", [])],
                timestamp=str(raw_event.get("timestamp", "")),
            ))

        return EventResult(success=True, events=events, raw_player_input=raw_player_input)


# =============================================================================
# Layer
# =============================================================================

@dataclass
class EventLayer(BaseLayer):
    """事件层 — 维护跨tick持久的事件上下文.

    有状态——通过current_event延续上一轮的事件上下文。
    只有玩家输入明确改变时，LLM才更新上下文。
    """

    llm_client: LLMClient | None = None
    parser: EventParser = field(default_factory=EventParser)

    # 持久化状态 — 跨tick延续
    current_event: CurrentEvent | None = None

    @property
    def layer_name(self) -> str:
        return "EventLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        if isinstance(input_data, str):
            input_data = EventInput(
                player_input=input_data,
                world_state=context.get("world_state", {}),
                available_npc_ids=context.get("available_npc_ids", []),
            )
        elif isinstance(input_data, dict):
            input_data = EventInput(
                player_input=input_data.get("player_input", ""),
                world_state=input_data.get("world_state", {}),
                available_npc_ids=input_data.get("available_npcs", []),
                npc_info=input_data.get("npc_info", {}),
                player_location=input_data.get("player_location", ""),
                world_rules=input_data.get("world_rules", []),
            )

        if not isinstance(input_data, EventInput):
            return LayerResult(
                success=False,
                error=f"EventLayer 期望 EventInput/str，收到 {type(input_data)}",
            )

        if not input_data.player_input.strip():
            return LayerResult(success=False, error="玩家输入为空")

        if self.llm_client is not None:
            return self._process_with_llm(input_data)
        return self._process_fallback(input_data)

    def _process_with_llm(self, input_data: EventInput) -> LayerResult:
        prompt = self._build_prompt(input_data)
        response = self.llm_client.send(prompt)
        if not response.success:
            return LayerResult(success=False, error=f"LLM调用失败: {response.error}")

        parsed = parse_json(response.raw_text)
        if parsed is None:
            return LayerResult(success=False, error="LLM输出JSON解析失败")

        try:
            result = self._apply_update(parsed, input_data)
            # 存储LLM原始输出在 EventResult 上供日志使用
            if result.data:
                result.data._llm_raw = response.raw_text
                result.data._llm_parsed = parsed
            return result
        except Exception as e:
            return LayerResult(success=False, error=f"EventLayer解析失败: {e}")

    def _apply_update(self, parsed: dict, input_data: EventInput) -> LayerResult:
        """将LLM返回的变更应用到current_event."""
        should_continue = parsed.get("continue_context", True)

        if should_continue and self.current_event and self.current_event.is_active():
            # 更新话题
            new_topic = parsed.get("topic", "")
            if new_topic:
                self.current_event.topic = new_topic

            # 更新对话对象
            new_addressed = parsed.get("addressed_npc_id", "")
            if new_addressed:
                self.current_event.addressed_npc_id = new_addressed

            # 加人
            for nid in parsed.get("added_npc_ids", []):
                if nid not in self.current_event.involved_npc_ids:
                    self.current_event.involved_npc_ids.append(nid)

            # 踢人
            for nid in parsed.get("removed_npc_ids", []):
                if nid in self.current_event.involved_npc_ids:
                    self.current_event.involved_npc_ids.remove(nid)

            event = self.current_event.to_event()
            if new_topic:
                event.description = new_topic
            return LayerResult(
                success=True,
                data=EventResult(success=True, events=[event], raw_player_input=input_data.player_input),
            )

        # 创建新事件
        self.current_event = CurrentEvent(
            location=parsed.get("location", ""),
            topic=parsed.get("topic", ""),
            addressed_npc_id=parsed.get("addressed_npc_id", ""),
            involved_npc_ids=[str(x) for x in parsed.get("involved_npc_ids", [])],
            event_type=parsed.get("event_type", "dialogue"),
            scope=parsed.get("scope", "private"),
        )

        return LayerResult(
            success=True,
            data=EventResult(
                success=True,
                events=[self.current_event.to_event()],
                raw_player_input=input_data.player_input,
            ),
        )

    def _process_fallback(self, input_data: EventInput) -> LayerResult:
        event = (
            self.current_event.to_event()
            if self.current_event and self.current_event.is_active()
            else Event(event_id="fallback", event_type=EventType.PLAYER_ACTION, description=input_data.player_input)
        )
        return LayerResult(success=True, data=EventResult(success=True, events=[event], raw_player_input=input_data.player_input))

    def _build_prompt(self, input_data: EventInput) -> str:
        # NPC列表
        npc_parts: list[str] = []
        for npc_id in input_data.available_npc_ids[:50]:
            info = input_data.npc_info.get(npc_id, {})
            name = info.get("name", npc_id)
            loc = info.get("location", "未知")
            npc_parts.append(f"{npc_id}({name}/{loc})")
        npc_list = ", ".join(npc_parts)
        if len(input_data.available_npc_ids) > 50:
            npc_list += f"... (共{len(input_data.available_npc_ids)}个NPC)"

        # 当前事件快照 + NPC位置速查表
        ce = self.current_event
        pl = input_data.player_location or "未知"
        current_context = ""
        if ce and ce.is_active():
            npc_names = []
            for nid in ce.involved_npc_ids:
                info = input_data.npc_info.get(nid, {})
                npc_names.append(f"{info.get('name', nid)}({nid})")
            addressed_name = ""
            if ce.addressed_npc_id:
                a_info = input_data.npc_info.get(ce.addressed_npc_id, {})
                addressed_name = f"{a_info.get('name', ce.addressed_npc_id)}({ce.addressed_npc_id})"
            current_context = (
                f"玩家当前位置: {pl}\n"
                f"场景位置: {ce.location}\n"
                f"对话对象: {addressed_name}\n"
                f"参与者: {', '.join(npc_names) if npc_names else '无'}\n"
                f"话题: {ce.topic}\n"
                f"\n"
                f"所有NPC位置速查:\n"
                + "\n".join(
                    f"  {nid}({info.get('name', nid)}) → {info.get('location', '未知')}"
                    for nid, info in input_data.npc_info.items()
                )
            )
        else:
            current_context = (
                f"玩家当前位置: {pl}\n"
                f"（无当前事件，需要创建）\n"
                "\n"
                "所有NPC位置速查:\n"
                + "\n".join(
                    f"  {nid}({info.get('name', nid)}) → {info.get('location', '未知')}"
                    for nid, info in input_data.npc_info.items()
                )
            )

        # 世界规则
        rules_lines = "\n".join(
            f"  - {r['rule']}（违反后果: {r['penalty']}）"
            for r in input_data.world_rules
        ) if input_data.world_rules else "  无"

        return render_prompt("event",
            day=str(input_data.world_state.get("day", 1)),
            time_of_day=str(input_data.world_state.get("time_of_day", "morning")),
            weather=str(input_data.world_state.get("weather", "sunny")),
            npc_list=npc_list,
            player_input=input_data.player_input,
            current_context=current_context,
            world_rules=rules_lines,
        )
