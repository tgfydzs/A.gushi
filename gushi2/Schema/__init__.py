"""Schema 包 — 统一的Schema规范.

本包定义了整个系统的纯数据结构标准。
所有Python代码和AI Prompt都基于此Schema进行开发。

Schema文件列表:
- world_schema:       世界定义、世界状态、世界规则
- npc_schema:         NPC身份、性格、当前状态
- event_schema:       事件定义、事件细节、跨tick上下文
- perception_schema:  感知系统（输入/输出/判断规则）
- memory_schema:      记忆、知识、观察条目
- belief_schema:      信念、待解释事件、推理结果
- relationship_schema: 关系、好感度、信任度
- dialogue_schema:    对话、回应、对话历史、全局上下文
- director_schema:    导演样式、场景记忆、叙事输出
- action_schema:      目标、物品、动作、更新记录、更新成本
- location_schema:    位置实体、位置状态、位置关系

使用方式:
    from Schema.world_schema import WorldDefinition, WorldState
    from Schema.npc_schema import NPC, PersonalityData

重要原则:
- 同一变量只在定义处出现一次，其他地方通过引用使用
- 每个字段标记"AI可修改"和"程序可修改"
- 变量名/字段名/文件名使用英文
- 注释/文档/说明使用中文
- Schema 只负责数据定义，不包含业务逻辑
"""

# 核心Schema（按依赖顺序导出最常用的类）

# 世界
from .world_schema import (
    WorldDefinition, WorldRule, HiddenEvent, KnownEvent,
    WorldState, GlobalEvent, EventPropagation,
    TimeOfDay, Weather, WorldEventStatus, PropagationMethod,
    WorldConfig,
)

# NPC
from .npc_schema import (
    NPC, NPCIdentity, NPCRole, PersonalityData, CurrentStateData,
    Emotion, HealthStatus, Gender, NPCSummary,
)

# 事件
from .event_schema import (
    Event, EventDetail, EventType, EventScope, EventStatus,
    CurrentEvent, EventTable,
)

# 感知
from .perception_schema import (
    PerceptionInput, NPCPerception, PerceptionResult,
    PerceptionJudgeRule,
)

# 记忆
from .memory_schema import (
    MemoryEntry, KnowledgeEntry, ObservationEntry,
    KnowledgeSource, DetailLevel, MemoryManager,
)

# 信念
from .belief_schema import (
    BeliefEntry, PendingEntry,
    ReasoningInput, NPCReasoning, ReasoningResult,
)

# 关系
from .relationship_schema import (
    RelationshipValue, RelationshipType, FavorabilityTier,
    FavorabilityChange, RelationshipGraph,
)

# 对话
from .dialogue_schema import (
    NPCResponse, ResponseType, ResponseResult,
    DialogueHistoryEntry, DialogueHistory,
    DialogueContext, GlobalContext, NarrationType,
)

# 导演
from .director_schema import (
    DirectorStyle, DirectorStyleConfig, SceneTransition,
    SceneMemory, SceneMemoryStore,
    DirectorInput, DirectorResult, NarrativeOutput,
)

# 行动
from .action_schema import (
    GoalEntry, GoalPriority, GoalCategory,
    Item, ItemType, Inventory,
    ActionType, NPCAction,
    NPCUpdateRecord, UpdateCost, UpdateCostTracker,
)

# 位置
from .location_schema import (
    Location, LocationType, LocationAccess,
    LocationTable, LocationState,
)

__all__ = [
    # World
    "WorldDefinition", "WorldRule", "HiddenEvent", "KnownEvent",
    "WorldState", "GlobalEvent", "EventPropagation",
    "TimeOfDay", "Weather", "WorldEventStatus", "PropagationMethod",
    "WorldConfig",
    # NPC
    "NPC", "NPCIdentity", "NPCRole", "PersonalityData", "CurrentStateData",
    "Emotion", "HealthStatus", "Gender", "NPCSummary",
    # Event
    "Event", "EventDetail", "EventType", "EventScope", "EventStatus",
    "CurrentEvent", "EventTable",
    # Perception
    "PerceptionInput", "NPCPerception", "PerceptionResult",
    "PerceptionJudgeRule",
    # Memory
    "MemoryEntry", "KnowledgeEntry", "ObservationEntry",
    "KnowledgeSource", "DetailLevel", "MemoryManager",
    # Belief
    "BeliefEntry", "PendingEntry",
    "ReasoningInput", "NPCReasoning", "ReasoningResult",
    # Relationship
    "RelationshipValue", "RelationshipType", "FavorabilityTier",
    "FavorabilityChange", "RelationshipGraph",
    # Dialogue
    "NPCResponse", "ResponseType", "ResponseResult",
    "DialogueHistoryEntry", "DialogueHistory",
    "DialogueContext", "GlobalContext", "NarrationType",
    # Director
    "DirectorStyle", "DirectorStyleConfig", "SceneTransition",
    "SceneMemory", "SceneMemoryStore",
    "DirectorInput", "DirectorResult", "NarrativeOutput",
    # Action
    "GoalEntry", "GoalPriority", "GoalCategory",
    "Item", "ItemType", "Inventory",
    "ActionType", "NPCAction",
    "NPCUpdateRecord", "UpdateCost", "UpdateCostTracker",
    # Location
    "Location", "LocationType", "LocationAccess",
    "LocationTable", "LocationState",
]
