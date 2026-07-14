"""对话与回应 Schema — dialogue_schema
============================================

定义对话/回应层和全局上下文相关的数据结构。
对话系统是NPC与玩家互动的核心输出通道。

范围：
- NPC回应（单个NPC对玩家的回应）
- 回应类型
- 对话历史
- 对话上下文
- 全局上下文（跨tick累积的公共信息）
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class ResponseType(str, Enum):
    """回应类型枚举.

    区分NPC回应的不同形式。
    """

    SPEECH = "speech"           # 说话 — 口头发言
    ACTION = "action"           # 动作 — 肢体行为（如点头、摇头、递东西）
    THOUGHT = "thought"         # 内心独白 — 玩家能看到的NPC内心想法
    WHISPER = "whisper"         # 悄悄话 — 只对特定目标说的秘密
    SHOUT = "shout"             # 大喊 — 周围都能听到 — 预留扩展
    EMOTE = "emote"             # 表情动作 — 预留扩展


class NarrationType(str, Enum):
    """旁白类型枚举.

    导演层输出的不同叙事形式。
    """

    DIALOGUE = "dialogue"       # NPC对话 — "NPC名：「...」"
    NARRATION = "narration"     # 旁白/叙述 — 环境、氛围、动作描述
    SCENE_TRANSITION = "scene_transition"  # 场景切换描述
    SYSTEM = "system"           # 系统消息（如"时间推进到第二天"）


# =============================================================================
# 单个NPC回应
# =============================================================================


@dataclass
class NPCResponse:
    """单个NPC的回应.

    由回应层LLM产生，传递给导演层整合。

    Attributes:
        npc_id: 回应NPC的ID.
        npc_name: 回应NPC的名称.
        response_text: 回应内容（自然语言）.
        response_type: 回应类型.
        target_id: 回应的目标ID（"player" 或其他NPC ID）.
        emotion: 回应时的情绪.
        is_addressed: 此NPC是否被玩家直接对话（影响是否强制回应）.
        favorability_changes: 此回应可能导致的好感度变化 — 预留扩展.
    """

    npc_id: str = ""
    npc_name: str = ""
    response_text: str = ""                     # AI可修改: 是 | 程序可修改: 否
    response_type: ResponseType = ResponseType.SPEECH  # AI可修改: 是 | 程序可修改: 否
    target_id: str = "player"                   # AI可修改: 是 | 程序可修改: 否
    emotion: str = "neutral"                    # AI可修改: 是 | 程序可修改: 否
    is_addressed: bool = False                  # AI可修改: 否 | 程序可修改: 是
    favorability_changes: list = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 否
    # 说明: NPCResponse的核心内容由AI（回应层LLM）产生。
    # is_addressed由程序根据CurrentEvent.addressed_npc_id判断。

    def is_empty(self) -> bool:
        """判断回应是否为空.

        Returns:
            回应文本是否为空.
        """
        return not self.response_text.strip()


# =============================================================================
# 回应层输出
# =============================================================================


@dataclass
class ResponseResult:
    """回应层输出.

    包含本轮所有NPC的回应。
    传递给导演层进行整合润色。

    Attributes:
        npc_responses: 所有NPC的回应列表（只含实际有回应的NPC）.
        skipped_npc_ids: 因无感知/无回应需求而跳过的NPC ID列表.
    """

    npc_responses: list[NPCResponse] = field(default_factory=list)
    skipped_npc_ids: list[str] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是

    def has_responses(self) -> bool:
        """判断是否有任何NPC产生了回应.

        Returns:
            是否有回应.
        """
        return len(self.npc_responses) > 0

    def get_speech_responses(self) -> list[NPCResponse]:
        """获取所有说话类型的回应.

        Returns:
            类型为SPEECH的回应列表.
        """
        return [r for r in self.npc_responses if r.response_type == ResponseType.SPEECH]

    def get_action_responses(self) -> list[NPCResponse]:
        """获取所有动作类型的回应.

        Returns:
            类型为ACTION的回应列表.
        """
        return [r for r in self.npc_responses if r.response_type == ResponseType.ACTION]


# =============================================================================
# 对话历史
# =============================================================================


@dataclass
class DialogueHistoryEntry:
    """单条对话历史记录.

    记录一轮对话中某个NPC的发言。

    Attributes:
        npc_id: 发言NPC ID.
        npc_name: 发言NPC名称.
        text: 发言内容.
        tick: 发生的tick编号.
        response_type: 回应类型.
        emotion: 回应时的情绪.
    """

    npc_id: str = ""
    npc_name: str = ""
    text: str = ""                              # AI可修改: 是 | 程序可修改: 否
    tick: int = 0                               # AI可修改: 否 | 程序可修改: 是
    response_type: str = "speech"               # AI可修改: 是 | 程序可修改: 否
    emotion: str = "neutral"                    # AI可修改: 是 | 程序可修改: 否


@dataclass
class DialogueHistory:
    """对话历史管理.

    跨tick累积的对话记录，用于：
    - 防止NPC重复说话（回应层传给LLM）
    - 场景连续性（导演层做叙事衔接）
    - Debug日志

    Attributes:
        entries: 对话历史条目列表.
        max_entries: 最大保留条数.
    """

    entries: list[DialogueHistoryEntry] = field(default_factory=list)
    max_entries: int = 200                      # AI可修改: 否 | 程序可修改: 是

    def add(self, npc_id: str, npc_name: str, text: str,
            tick: int, response_type: str = "speech", emotion: str = "neutral") -> None:
        """添加一条对话记录.

        Args:
            npc_id: NPC ID.
            npc_name: NPC名称.
            text: 发言内容.
            tick: tick编号.
            response_type: 回应类型.
            emotion: 情绪.
        """
        self.entries.append(DialogueHistoryEntry(
            npc_id=npc_id, npc_name=npc_name,
            text=text, tick=tick,
            response_type=response_type, emotion=emotion,
        ))
        self._trim()

    def get_for_npc(self, npc_id: str, limit: int = 5) -> list[DialogueHistoryEntry]:
        """获取指定NPC的对话历史.

        Args:
            npc_id: NPC ID.
            limit: 最多返回的条数（取最近N条）.

        Returns:
            DialogueHistoryEntry列表.
        """
        my_entries = [e for e in self.entries if e.npc_id == npc_id]
        return my_entries[-limit:]

    def get_for_scene(self, npc_ids: set[str], limit: int = 10) -> list[DialogueHistoryEntry]:
        """获取场景中NPC的对话历史（给导演层用）.

        Args:
            npc_ids: 场景中的NPC ID集合.
            limit: 最多返回条数.

        Returns:
            DialogueHistoryEntry列表.
        """
        scene_entries = [e for e in self.entries if e.npc_id in npc_ids]
        return scene_entries[-limit:]

    def get_recent(self, limit: int = 20) -> list[DialogueHistoryEntry]:
        """获取最近的对话历史.

        Args:
            limit: 最多返回条数.

        Returns:
            最近的对话历史列表.
        """
        return self.entries[-limit:]

    def _trim(self) -> None:
        """裁剪超出容量的旧记录."""
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]

    def clear(self) -> None:
        """清空对话历史."""
        self.entries.clear()


# =============================================================================
# 对话上下文
# =============================================================================


@dataclass
class DialogueContext:
    """对话上下文.

    传递给回应层LLM的完整对话上下文。

    Attributes:
        npc: 当前NPC的完整数据（所有维护表）.
        perceived_summary: NPC本轮感知到的内容.
        is_addressed: 玩家是否直接对该NPC说话.
        dialogue_history: 该NPC的历史发言.
        player_input: 玩家原始输入（兜底用）.
    """

    npc: object = None                          # NPC对象（只读引用）
    perceived_summary: str = ""
    is_addressed: bool = False
    dialogue_history: list[DialogueHistoryEntry] = field(default_factory=list)
    player_input: str = ""


# =============================================================================
# 全局上下文
# =============================================================================


@dataclass
class GlobalContext:
    """全局上下文（流程图中的 list1）.

    跨tick累积的全局信息，供所有层参考。
    与WorldState不同：WorldState是客观世界状态，GlobalContext是叙事上下文。

    Attributes:
        player_inputs: 历史玩家输入列表（最近N条）.
        significant_events: 重大事件记录（关键剧情节点）.
        revealed_secrets: 已揭示的秘密列表.
        world_state_snapshots: 世界状态快照（按tick记录关键变化）.
    """

    player_inputs: list[str] = field(default_factory=list)     # AI可修改: 否 | 程序可修改: 是
    significant_events: list[str] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是
    revealed_secrets: list[str] = field(default_factory=list)   # AI可修改: 否 | 程序可修改: 是
    world_state_snapshots: list[dict] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是

    max_inputs: int = 50                        # AI可修改: 否 | 程序可修改: 是
    max_events: int = 100                       # AI可修改: 否 | 程序可修改: 是

    def add_player_input(self, text: str) -> None:
        """记录玩家输入.

        Args:
            text: 玩家输入文本.
        """
        self.player_inputs.append(text)
        if len(self.player_inputs) > self.max_inputs:
            self.player_inputs = self.player_inputs[-self.max_inputs:]

    def add_significant_event(self, event_desc: str) -> None:
        """记录重大事件.

        Args:
            event_desc: 事件描述.
        """
        self.significant_events.append(event_desc)
        if len(self.significant_events) > self.max_events:
            self.significant_events = self.significant_events[-self.max_events:]

    def add_revealed_secret(self, secret: str) -> None:
        """记录已揭示的秘密.

        Args:
            secret: 秘密描述.
        """
        if secret not in self.revealed_secrets:
            self.revealed_secrets.append(secret)
