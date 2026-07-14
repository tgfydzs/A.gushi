"""导演与叙事 Schema — director_schema
============================================

定义导演层和叙事输出相关的数据结构。
导演层是整个流程的输出端——整合所有NPC回应，润色为连贯叙事。

范围：
- 导演样式（控制叙事风格）
- 场景记忆（跨tick持久化的场景描述）
- 导演层输出（叙事文本 + 场景更新）
- 最终叙事输出（NPC说话 / 旁白分离）
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class DirectorStyle(str, Enum):
    """导演样式枚举.

    控制叙事输出的风格和详细程度。
    流程图中的 list2 即引用此样式配置。
    """

    NOVEL = "novel"                     # 小说体 — 详细描写，偏文学
    SCREENPLAY = "screenplay"           # 剧本体 — 对话为主，少量环境
    MINIMAL = "minimal"                 # 极简 — 只输出对话，无修饰
    ORAL = "oral"                       # 说书体 — 口语化叙事 — 预留扩展
    GAME_LOG = "game_log"               # 游戏日志体 — 第一人称叙事 — 预留扩展


class SceneTransition(str, Enum):
    """场景切换类型.

    导演层判断是否需要场景切换描述。
    """

    CONTINUE = "continue"               # 同一场景继续，不切换
    SAME_LOCATION = "same_location"     # 同一地点，但需要刷新描述
    NEW_LOCATION = "new_location"       # 切换到了新地点
    TIME_PASSAGE = "time_passage"       # 时间跳跃 — 预留扩展


# =============================================================================
# 导演样式配置
# =============================================================================


@dataclass
class DirectorStyleConfig:
    """导演样式配置（流程图中的 list2）.

    控制导演层的叙事风格、详细程度和行为偏好。

    Attributes:
        style: 叙事风格.
        scene_description_length: 场景描述最大字数（0=不限制）.
        show_npc_emotions: 是否显示NPC情绪.
        show_npc_actions: 是否显示NPC动作（非对话）.
        show_inner_thoughts: 是否显示NPC内心独白.
        format_dialogue: 对话格式模板（"{name}：「{text}」"）.
        format_action: 动作格式模板（"{name}{action}"）.
        max_response_per_scene: 每个场景最多显示几个NPC的回应.
        allow_duplicate_dialogue: 是否允许重复已有的对话内容.
    """

    style: DirectorStyle = DirectorStyle.NOVEL
    scene_description_length: int = 30          # AI可修改: 否 | 程序可修改: 是(初始化)
    show_npc_emotions: bool = True              # AI可修改: 否 | 程序可修改: 是
    show_npc_actions: bool = True               # AI可修改: 否 | 程序可修改: 是
    show_inner_thoughts: bool = False           # AI可修改: 否 | 程序可修改: 是
    format_dialogue: str = '{name}：「{text}」'  # AI可修改: 否 | 程序可修改: 是(初始化)
    format_action: str = '{name}{action}'       # AI可修改: 否 | 程序可修改: 是(初始化)
    max_response_per_scene: int = 10            # AI可修改: 否 | 程序可修改: 是
    allow_duplicate_dialogue: bool = False      # AI可修改: 否 | 程序可修改: 是
    # 说明: 导演样式是全局配置，程序在初始化时设定，一般运行中不变。
    # 后续可以支持玩家通过指令动态切换（如 "切换为极简模式"）。


# =============================================================================
# 场景记忆
# =============================================================================


@dataclass
class SceneMemory:
    """场景记忆.

    跨tick持久化的场景描述存储。
    用于场景衔接——同一位置不重复描述，切换位置时写新描述。

    Attributes:
        location: 场景位置.
        scene_description: 上一次该位置的场景描述.
        last_tick: 最后一次更新此场景的tick.
        npcs_present: 上次描述时在场的NPC ID列表.
        notable_features: 场景显著特征（不变的元素，如"角落里有一张破旧的公告板"）.
    """

    location: str = ""
    scene_description: str = ""                 # AI可修改: 是 | 程序可修改: 否
    last_tick: int = 0                          # AI可修改: 否 | 程序可修改: 是
    npcs_present: list[str] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是
    notable_features: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 否
    # 说明: scene_description由导演层LLM生成。
    # notable_features用于在同一场景中保持环境一致性。


@dataclass
class SceneMemoryStore:
    """场景记忆存储.

    管理所有位置的场景记忆。

    Attributes:
        memories: 位置 → SceneMemory 的映射.
        last_event_location: 上一次事件的位置（用于判断场景切换）.
    """

    memories: dict[str, SceneMemory] = field(default_factory=dict)
    last_event_location: str = ""

    def get(self, location: str) -> SceneMemory | None:
        """获取指定位置的场景记忆.

        Args:
            location: 位置名称.

        Returns:
            SceneMemory或None.
        """
        return self.memories.get(location)

    def update(self, location: str, description: str,
               npcs_present: list[str] | None = None, tick: int = 0) -> None:
        """更新场景记忆.

        Args:
            location: 位置名称.
            description: 新的场景描述.
            npcs_present: 在场的NPC ID列表.
            tick: 当前tick.
        """
        if location not in self.memories:
            self.memories[location] = SceneMemory(location=location)
        mem = self.memories[location]
        if description:
            mem.scene_description = description
        mem.last_tick = tick
        if npcs_present is not None:
            mem.npcs_present = npcs_present
        self.last_event_location = location

    def is_same_location(self, location: str) -> bool:
        """判断是否与上一次事件同一位置.

        Args:
            location: 当前位置.

        Returns:
            是否同一位置.
        """
        return location == self.last_event_location and location != ""

    def get_previous_scene(self, location: str) -> str:
        """获取上一次此位置的场景描述（如果同位置）.

        Args:
            location: 当前位置.

        Returns:
            场景描述字符串，如果是新位置或第一次访问则返回空字符串.
        """
        if not self.is_same_location(location):
            return ""
        mem = self.memories.get(location)
        return mem.scene_description if mem else ""


# =============================================================================
# 导演层输入
# =============================================================================


@dataclass
class DirectorInput:
    """导演层输入.

    由Engine构建，传递给DirectorLayer。

    Attributes:
        npc_responses: 本轮NPC回应列表.
        world_state_summary: 世界状态摘要.
        event_location: 当前事件发生位置.
        npc_locations: NPC ID → 当前位置映射.
        previous_scene: 上一轮同位置的场景描述.
        dialogue_history: 当前场景中的对话历史.
        style: 导演样式配置.
    """

    npc_responses: list = field(default_factory=list)   # list[NPCResponse]
    world_state_summary: dict = field(default_factory=dict)
    event_location: str = ""
    npc_locations: dict[str, str] = field(default_factory=dict)
    previous_scene: str = ""
    dialogue_history: list = field(default_factory=list)  # list[DialogueHistoryEntry]
    style: DirectorStyleConfig = field(default_factory=DirectorStyleConfig)


# =============================================================================
# 导演层输出
# =============================================================================


@dataclass
class DirectorResult:
    """导演层输出.

    导演层整合所有NPC回应后产生的最终叙事。

    Attributes:
        narrative: 给玩家看的完整叙事文本（对话 + 场景描述）.
        scene_update: 本轮更新的场景描述（Engine存储后在后续tick传入）.
        npc_responses_summary: NPC回应摘要列表.
        world_changes: 世界状态变化列表（告知玩家发生了什么全局变化）.
        scene_notes: 导演注释（debug用）.
        narration_type: 叙事类型.
    """

    narrative: str = ""                         # AI可修改: 是 | 程序可修改: 否
    scene_update: str = ""                      # AI可修改: 是 | 程序可修改: 否
    npc_responses_summary: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 否
    world_changes: list[str] = field(default_factory=list)          # AI可修改: 是 | 程序可修改: 否
    scene_notes: str = ""                       # AI可修改: 是 | 程序可修改: 否
    narration_type: str = "dialogue"            # AI可修改: 是 | 程序可修改: 否
    # 说明: DirectorResult的大部分内容由AI（导演层LLM）产生。
    # 单NPC回应场景下，程序可以做简化处理（直接格式化），不走LLM。

    def is_empty(self) -> bool:
        """判断叙事是否为空.

        Returns:
            是否有内容.
        """
        return not self.narrative.strip()

    def has_scene_update(self) -> bool:
        """判断是否有场景更新.

        Returns:
            是否有场景描述更新.
        """
        return bool(self.scene_update.strip())


# =============================================================================
# 最终叙事输出（给玩家的展示层）
# =============================================================================


@dataclass
class NarrativeOutput:
    """最终叙事输出.

    将导演层输出拆分为NPC说话和旁白两部分，
    对应流程图中的 a11（NPC说话）和 a12（旁白说话）。

    Attributes:
        npc_speeches: NPC说话列表 [{name, text, emotion}, ...].
        narrations: 旁白/叙述文本列表 [{text, type}, ...].
        raw_narrative: 原始完整叙事文本（未拆分）.
    """

    npc_speeches: list[dict] = field(default_factory=list)   # AI可修改: 是 | 程序可修改: 否
    narrations: list[dict] = field(default_factory=list)     # AI可修改: 是 | 程序可修改: 否
    raw_narrative: str = ""                                   # AI可修改: 是 | 程序可修改: 否

    def get_display_text(self) -> str:
        """获取展示文本（NPC说话 + 旁白组合）.

        Returns:
            格式化后的展示文本.
        """
        parts: list[str] = []
        for narration in self.narrations:
            parts.append(narration.get("text", ""))
        for speech in self.npc_speeches:
            name = speech.get("name", "")
            text = speech.get("text", "")
            parts.append(f"{name}：「{text}」")
        return "\n".join(parts) if parts else self.raw_narrative
