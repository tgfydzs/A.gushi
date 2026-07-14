"""ResponseLayer — 回应层.

LLM根据NPC感知到的内容和维护表，决定是否回应及如何回应。

关键原则：每个NPC只收到自己感知到的那部分事件。
绝不把整个事件表发给所有NPC。
"""

from dataclasses import dataclass, field
from typing import Any

from src.core.llm_client import LLMClient
from src.layers.base import BaseLayer, LayerResult
from src.models.result import BaseResult
from src.utils.data_loader import render_prompt
from src.utils.json_utils import parse_json


# =============================================================================
# Result
# =============================================================================

@dataclass
class NPCResponse:
    """单个NPC的回应."""

    npc_id: str
    npc_name: str = ""
    response_text: str = ""
    response_type: str = "speech"
    target_id: str = ""
    emotion: str = "neutral"


@dataclass
class ResponseResult(BaseResult):
    """回应层处理结果."""

    npc_responses: list[NPCResponse] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.layer_name = "ResponseLayer"


# =============================================================================
# Layer
# =============================================================================

@dataclass
class ResponseLayer(BaseLayer):
    """回应层 — 每个NPC只能拿到自己感知到的事件来回应."""

    llm_client: LLMClient | None = None

    @property
    def layer_name(self) -> str:
        return "ResponseLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        npcs: dict[str, Any] = context.get("npcs", {})
        perception_result = context.get("perception_result")

        if not npcs:
            return LayerResult(success=False, error="ResponseLayer 需要 context['npcs']")

        # 对话目标 + 对话历史 + 玩家原话兜底
        addressed_npc_id: str = context.get("addressed_npc_id", "") or ""
        dialogue_history: list[dict] = context.get("dialogue_history", [])
        player_input: str = context.get("player_input", "") or ""

        try:
            responses = self._generate_all(npcs, perception_result, addressed_npc_id, dialogue_history, player_input)
            return LayerResult(
                success=True,
                data=ResponseResult(npc_responses=responses),
            )
        except Exception as e:
            return LayerResult(success=False, error=f"ResponseLayer 失败: {e}")

    # ------------------------------------------------------------------
    # 无感知标记短语 — 匹配这些表示NPC本轮没有可回应的事件
    # ------------------------------------------------------------------
    _NO_PERCEPTION_MARKERS: tuple[str, ...] = (
        "没有感知到任何异常",
        "没有感知到任何",
        "无可见事件",
        "没有感知到",
        "本轮没有感知",
        "什么都没感知到",
    )

    def _has_no_perception(self, perceived_summary: str) -> bool:
        """判断感知摘要是否表示本轮无可感知事件."""
        stripped = perceived_summary.strip()
        if not stripped:
            return True
        for marker in self._NO_PERCEPTION_MARKERS:
            if marker in stripped:
                return True
        return False

    def _generate_all(
        self, npcs: dict[str, Any], perception_result: Any,
        addressed_npc_id: str = "", dialogue_history: list[dict] | None = None,
        player_input: str = "",
    ) -> list[NPCResponse]:
        """为有感知的NPC生成回应.

        关键约束：只有addressed_npc（玩家直接说话的对象）强制回应。
        其他NPC只在感知到了且相关时回应。
        """
        if dialogue_history is None:
            dialogue_history = []
        responses: list[NPCResponse] = []

        for npc_id, npc in npcs.items():
            # 从感知结果中取该NPC感知到的内容
            perceived_summary = ""
            if perception_result is not None:
                perc = perception_result.get_for_npc(npc_id)
                if perc is not None:
                    perceived_summary = perc.perceived_summary

            # 只此NPC是玩家对话目标 → 必须回应
            is_addressed = (npc_id == addressed_npc_id and addressed_npc_id != "")

            # 无感知 且 不是被直接对话的NPC → 跳过
            if self._has_no_perception(perceived_summary) and not is_addressed:
                continue

            # 兜底: 被点名的NPC如果感知为空，注入玩家原话
            effective_perception = perceived_summary
            if is_addressed and self._has_no_perception(perceived_summary) and player_input:
                effective_perception = f"玩家对你说: \"{player_input}\""

            if self.llm_client is not None:
                # 该NPC的历史对话
                my_history = [h for h in dialogue_history if h["npc_id"] == npc_id]
                llm_json = self._llm_respond(npc, effective_perception, is_addressed, my_history)
            else:
                llm_json = {
                    "response_text": "",
                    "response_type": "speech",
                    "target_id": "",
                    "emotion": npc.current_state.emotion.value,
                }

            # 即使LLM返回了回应，也二次校验：空文本不加入
            response_text = (llm_json.get("response_text", "") or "").strip()
            if not response_text:
                continue

            responses.append(NPCResponse(
                npc_id=npc_id,
                npc_name=npc.name,
                response_text=llm_json.get("response_text", ""),
                response_type=llm_json.get("response_type", "speech"),
                target_id=llm_json.get("target_id", ""),
                emotion=llm_json.get("emotion", "neutral"),
            ))

        return responses

    def _llm_respond(self, npc: Any, perceived_summary: str,
                     is_addressed: bool = False, my_history: list[dict] | None = None) -> dict:
        prompt = self._build_prompt(npc, perceived_summary, is_addressed, my_history)
        response = self.llm_client.send(prompt)
        parsed = parse_json(response.raw_text) if response.success else None
        if parsed is None:
            return {
                "response_text": "", "response_type": "speech",
                "target_id": "", "emotion": "neutral",
            }
        return parsed

    def _build_prompt(self, npc: Any, perceived_summary: str,
                      is_addressed: bool = False, my_history: list[dict] | None = None) -> str:
        """构建回应Prompt — 含对话历史，防止NPC重复说话."""
        knowledge = [k.fact for k in npc.knowledge[-5:]]
        beliefs = [b.belief for b in npc.belief[-3:]]
        goals = [g.goal for g in npc.goal if not g.is_completed]
        observations = [o.observation for o in npc.observation[-3:]]
        memories = [m.content for m in npc.memory[-3:]]
        pending = [p.event_description for p in npc.pending_reason[-3:]]

        # 对话历史（本NPC之前说过的）
        history_lines = ""
        if my_history:
            recent = my_history[-5:]
            history_lines = "\n".join(
                f"  第{h['tick']}轮你说过: \"{h['text']}\""
                for h in recent
            )

        relationships = []
        for target_id, rel in npc.relationship.items():
            relationships.append(
                f"  对{target_id}: {rel.relationship_type.value} "
                f"好感{rel.favorability} 信任{rel.trust}"
            )

        return render_prompt(
            "response",
            npc_name=npc.name,
            npc_id=npc.npc_id,
            npc_role=npc.role.value,
            npc_location=npc.current_state.location,
            npc_action=npc.current_state.action,
            emotion=npc.current_state.emotion.value,
            intelligence=str(npc.personality.intelligence),
            perceived_content=perceived_summary or "（本轮没有感知到任何事情）",
            is_addressed="是，玩家正在对你说话——你必须回应" if is_addressed else "否",
            conversation_history=history_lines if history_lines else "（这是你们第一次对话）",
            knowledge="\n".join(f"  - {k}" for k in knowledge) if knowledge else "  无",
            beliefs="\n".join(f"  - {b}" for b in beliefs) if beliefs else "  无",
            goals="\n".join(f"  - {g}" for g in goals) if goals else "  无",
            observations="\n".join(f"  - {o}" for o in observations) if observations else "  无",
            memories="\n".join(f"  - {m}" for m in memories) if memories else "  无",
            pending="\n".join(f"  - {p}" for p in pending) if pending else "  无",
            relationships="\n".join(relationships) if relationships else "  无",
            personality_traits=", ".join(npc.personality.traits) if npc.personality.traits else "无特殊特质",
        )
