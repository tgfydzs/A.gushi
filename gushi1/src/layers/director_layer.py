"""DirectorLayer — 导演层.

职责：整合所有NPC回应，润色为连贯叙事。
需要LLM。
禁止新增世界事实。
"""

from dataclasses import dataclass, field
from typing import Any

from src.core.llm_client import LLMClient
from src.layers.base import BaseLayer, LayerResult
from src.layers.response_layer import NPCResponse
from src.models.result import BaseResult
from src.utils.data_loader import render_prompt
from src.utils.json_utils import parse_json


# =============================================================================
# Result
# =============================================================================

@dataclass
class DirectorResult(BaseResult):
    """导演层处理结果.

    Attributes:
        narrative: 给玩家看的文本（对话+可能的场景切换描述）.
        scene_update: 本轮更新的场景描述。Engine存储后在后续tick传入。
            相同位置不更新就不传，切场景时传新场景描述.
        npc_responses_summary: NPC回应摘要.
        world_changes: 世界变化（预留）.
        scene_notes: 导演注释（预留）.
    """

    narrative: str = ""
    scene_update: str = ""
    npc_responses_summary: list[str] = field(default_factory=list)
    world_changes: list[str] = field(default_factory=list)
    scene_notes: str = ""

    def __post_init__(self) -> None:
        self.layer_name = "DirectorLayer"


# =============================================================================
# Schema
# =============================================================================

@dataclass
class DirectorInput:
    """DirectorLayer 输入."""

    npc_responses: list[NPCResponse] = field(default_factory=list)
    world_state_summary: dict = field(default_factory=dict)
    previous_scene: str = ""
    event_location: str = ""
    npc_locations: dict[str, str] = field(default_factory=dict)
    # 对话历史 — 当前场景中NPC的历史发言
    dialogue_history: list[dict] = field(default_factory=list)


# =============================================================================
# Parser
# =============================================================================

@dataclass
class DirectorParser:
    """将LLM JSON输出解析为DirectorResult."""

    def parse(self, llm_output: dict) -> DirectorResult:
        return DirectorResult(
            success=True,
            narrative=llm_output.get("narrative", ""),
            scene_update=llm_output.get("scene_update", ""),
            npc_responses_summary=llm_output.get("npc_responses_summary", []),
            world_changes=llm_output.get("world_changes", []),
            scene_notes=llm_output.get("scene_notes", ""),
        )


# =============================================================================
# Layer
# =============================================================================

@dataclass
class DirectorLayer(BaseLayer):
    """导演层 — 整合润色NPC回应."""

    llm_client: LLMClient | None = None
    parser: DirectorParser = field(default_factory=DirectorParser)

    @property
    def layer_name(self) -> str:
        return "DirectorLayer"

    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        # 标准化输入
        if isinstance(input_data, DirectorInput):
            pass
        elif isinstance(input_data, dict):
            responses_raw = input_data.get("npc_responses", [])
            responses = []
            for r in responses_raw:
                if isinstance(r, NPCResponse):
                    responses.append(r)
                elif isinstance(r, dict):
                    responses.append(NPCResponse(
                        npc_id=r.get("npc_id", ""), npc_name=r.get("npc_name", ""),
                        response_text=r.get("response_text", ""),
                        response_type=r.get("response_type", "speech"),
                        target_id=r.get("target_id", ""),
                        emotion=r.get("emotion", "neutral"),
                    ))
            input_data = DirectorInput(
                npc_responses=responses,
                world_state_summary=input_data.get("world_state_summary", {}),
                previous_scene=input_data.get("previous_scene", ""),
                event_location=input_data.get("event_location", ""),
                npc_locations=input_data.get("npc_locations", {}),
                dialogue_history=input_data.get("dialogue_history", []),
            )
        elif hasattr(input_data, "npc_responses"):
            input_data = DirectorInput(
                npc_responses=input_data.npc_responses,
                world_state_summary=context.get("world_state_summary", {}),
                previous_scene=context.get("previous_scene", ""),
                event_location=context.get("event_location", ""),
                npc_locations=context.get("npc_locations", {}),
                dialogue_history=context.get("dialogue_history", []),
            )
        else:
            return LayerResult(
                success=False,
                error=f"DirectorLayer 无法处理输入类型 {type(input_data)}",
            )

        if not input_data.npc_responses:
            return LayerResult(
                success=True,
                data=DirectorResult(success=True, narrative="（无回应）"),
            )

        try:
            if self.llm_client is not None:
                result = self._direct_with_llm(input_data)
            else:
                result = self._direct_fallback(input_data)
            return LayerResult(success=True, data=result)
        except Exception as e:
            return LayerResult(success=False, error=f"DirectorLayer 处理失败: {e}")

    def _direct_with_llm(self, input_data: DirectorInput) -> DirectorResult:
        # 单个NPC回应 → 直接格式化
        if len(input_data.npc_responses) == 1:
            return self._direct_single(
                input_data.npc_responses[0],
                input_data.event_location,
                input_data.previous_scene,
                input_data.dialogue_history,
            )

        prompt = self._build_prompt(input_data)
        response = self.llm_client.send(prompt)
        parsed = parse_json(response.raw_text) if response.success else None
        if parsed is None:
            return self._direct_fallback(input_data)
        result = self.parser.parse(parsed)
        # 提取场景更新
        if parsed and parsed.get("scene_update"):
            result.scene_update = parsed["scene_update"]
        return result

    def _direct_single(self, r: NPCResponse, location: str = "",
                       previous_scene: str = "", dialogue_history: list[dict] | None = None) -> DirectorResult:
        """单NPC回应.

        如果同一场景已描述过 → 直接输出对话。
        如果是新场景 → 用LLM生成一句场景描写。
        如果对话历史中本NPC已在说话 → 不做任何包装，直接输出。
        """
        dialogue = (
            f"{r.npc_name}{r.response_text}"
            if r.response_type == "action"
            else f"{r.npc_name}：「{r.response_text}」"
        )

        # 已有对话历史 → 直接输出，不生成场景也不包装
        if dialogue_history and len(dialogue_history) > 0:
            return DirectorResult(
                success=True,
                narrative=dialogue,
                npc_responses_summary=[f"{r.npc_name}: {r.response_text}"],
            )

        # 已有场景上下文 → 直接输出对话，不重复描述
        if previous_scene.strip():
            return DirectorResult(
                success=True,
                narrative=dialogue,
                npc_responses_summary=[f"{r.npc_name}: {r.response_text}"],
            )

        # 新场景 → 用LLM生成一句简短的环境描述
        scene_intro = self._generate_scene_intro(location, r.npc_name)
        narrative = f"{scene_intro}\n\n{dialogue}" if scene_intro else dialogue
        return DirectorResult(
            success=True,
            narrative=narrative,
            scene_update=scene_intro,
            npc_responses_summary=[f"{r.npc_name}: {r.response_text}"],
        )

    def _generate_scene_intro(self, location: str, npc_name: str) -> str:
        """为新场景生成一句简短的环境描述."""
        if not self.llm_client or not location:
            return f"— {location} —" if location else ""

        prompt = (
            f"你是一个极简场景描述器。用一句中文描述以下地点此时此刻的样子。\n\n"
            f"地点: {location}\n"
            f"在场NPC: {npc_name}\n\n"
            f"规则:\n"
            f"- 只写一句，不超过30个字\n"
            f"- 只写客观环境，不写NPC外貌动作心理\n"
            f"- 禁止阳光、天气、氛围、比喻\n"
            f"- 禁止\"阳光透过\"\"空气中弥漫\"这类套路\n"
            f"- 示例: \"辉石城冒险者公会大厅，柜台后的公告板上贴着几张泛黄的任务单。\"\n\n"
            f"只返回那一句话，不要JSON，不要引号。"
        )
        response = self.llm_client.send(prompt)
        if response.success and response.raw_text.strip():
            text = response.raw_text.strip().strip('"').strip("'")
            return text
        return f"— {location} —"

    def _direct_fallback(self, input_data: DirectorInput) -> DirectorResult:
        parts: list[str] = []
        summaries: list[str] = []
        for r in input_data.npc_responses:
            if r.response_text:
                if r.response_type == "speech":
                    parts.append(f"{r.npc_name}：「{r.response_text}」")
                elif r.response_type == "action":
                    parts.append(f"{r.npc_name}{r.response_text}")
                else:
                    parts.append(f"{r.npc_name}: {r.response_text}")
                summaries.append(f"{r.npc_name}: {r.response_text}")

        dialogue = "\n".join(parts) if parts else "（无回应）"
        scene_update = ""

        # 无LLM回退：新场景加位置标记
        if not input_data.previous_scene.strip() and input_data.event_location:
            narrative = f"— {input_data.event_location} —\n{dialogue}"
            scene_update = f"— {input_data.event_location} —"
        else:
            narrative = dialogue

        return DirectorResult(
            success=True, narrative=narrative,
            scene_update=scene_update, npc_responses_summary=summaries,
        )

    def _build_prompt(self, input_data: DirectorInput) -> str:
        responses_desc = "\n".join(
            f"  - {r.npc_name}: text=\"{r.response_text}\""
            for r in input_data.npc_responses
        )

        # 对话历史摘要 — 告诉导演什么已经说过
        history_desc = ""
        if input_data.dialogue_history:
            history_lines = []
            for h in input_data.dialogue_history[-10:]:
                history_lines.append(f"  第{h['tick']}轮 {h['npc_name']}: \"{h['text']}\"")
            history_desc = "\n".join(history_lines) if history_lines else "（无）"
        else:
            history_desc = "（无历史，这是首次对话）"

        return render_prompt("director",
            responses_desc=responses_desc,
            history_desc=history_desc,
        )
