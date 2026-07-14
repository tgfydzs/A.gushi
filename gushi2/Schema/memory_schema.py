"""记忆与知识 Schema — memory_schema
========================================

定义NPC的记忆、知识和观察条目。
这三个维护表密切相关，共同构成NPC对世界的认知基础。

关系：
- 记忆 (Memory): NPC亲身经历的事情，带情感色彩，可能随时间模糊
- 知识 (Knowledge): NPC知道的事实，来源可能是亲眼所见/听说/推理
- 观察 (Observation): NPC对周围环境/其他角色的实时观察，最新鲜的信息

注意：
- 所有维护表的修改只能通过更新层完成
- 各表有容量上限（由WorldConfig.max_memory_per_npc控制）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# =============================================================================
# 枚举定义
# =============================================================================


class KnowledgeSource(str, Enum):
    """知识来源枚举.

    标记知识的获取方式，影响知识的可信度和持久性。
    """

    DIRECT = "direct"               # 亲眼所见/亲身经历 — 可信度最高
    HEARD = "heard"                 # 听说/间接得知 — 可信度因来源而异
    REASONING = "reasoning"         # 自己推理得出 — 可信度取决于推理能力
    TAUGHT = "taught"               # 被教导 — 可信度因教导者而异
    READ = "read"                   # 阅读（书籍/信件等） — 预留扩展
    PROPAGATION = "propagation"     # 信息传播（口口相传） — 预留扩展
    UNKNOWN = "unknown"             # 未知来源


class DetailLevel(int, Enum):
    """观察细节程度枚举.

    影响观察结果的丰富程度。
    """

    VAGUE = 1       # 模糊 — 只知大概
    BASIC = 2       # 基本 — 知道主要信息
    NORMAL = 3      # 普通 — 知道一般细节
    DETAILED = 4    # 详细 — 知道大量细节
    PRECISE = 5     # 精确 — 几乎完全还原


# =============================================================================
# 记忆条目
# =============================================================================


@dataclass
class MemoryEntry:
    """NPC记忆条目.

    记录NPC亲身经历的事件。记忆带有情感色彩，可能随时间模糊。
    记忆是NPC个性和行为的重要影响因素。

    Attributes:
        content: 记忆内容（简洁的自然语言描述）.
        timestamp: 记忆形成的时间.
        importance: 重要程度 (1-10)，影响记忆保留概率和检索优先级.
        related_event_id: 关联事件ID（可追溯到原始Event）.
        emotion_tags: 情感标签列表（记录记忆形成时的情感）.
        decay_rate: 衰减率 (0.0-1.0)，每tick记忆强度递减. 0=永不忘.
        recall_count: 被回忆次数，频繁回忆的记忆衰减更慢.
    """

    content: str = ""
    timestamp: str = ""                         # AI可修改: 否 | 程序可修改: 是
    importance: int = 1                         # AI可修改: 是 | 程序可修改: 是
    related_event_id: Optional[str] = None      # AI可修改: 否 | 程序可修改: 是
    emotion_tags: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 是
    decay_rate: float = 0.01                    # AI可修改: 否 | 程序可修改: 是
    recall_count: int = 0                       # AI可修改: 否 | 程序可修改: 是
    # 说明: content由AI产生（感知/推理/回应结果），程序写入。
    # importance由AI判断，程序写入。
    # 衰减机制和回忆计数是程序的自动行为，AI不参与。


# =============================================================================
# 知识条目
# =============================================================================


@dataclass
class KnowledgeEntry:
    """NPC知识条目.

    NPC知道的事实。与记忆不同，知识是去除了情感色彩的客观事实。
    一个知识可能来自多个来源，随着新信息出现而更新。

    Attributes:
        fact: 知识事实（简洁描述）.
        source: 知识来源.
        confidence: 确信度 (0.0 - 1.0)，1.0=完全确定，<0.5=半信半疑.
        timestamp: 获取/更新时间.
        related_npc_id: 关联NPC ID（从谁那里知道的）.
        is_secret: 是否为秘密（NPC不会轻易透露）.
        is_verified: 是否已被验证（多次确认过）.
    """

    fact: str = ""
    source: KnowledgeSource = KnowledgeSource.UNKNOWN
    confidence: float = 1.0                     # AI可修改: 是 | 程序可修改: 是
    timestamp: str = ""                         # AI可修改: 否 | 程序可修改: 是
    related_npc_id: str = ""                    # AI可修改: 是 | 程序可修改: 是
    is_secret: bool = False                     # AI可修改: 是 | 程序可修改: 是
    is_verified: bool = False                   # AI可修改: 否 | 程序可修改: 是
    # 说明: fact和confidence由AI产生（推理层/回应层），程序写入。
    # source标记知识的获取方式。is_secret影响NPC在回应时是否透露此知识。


# =============================================================================
# 观察条目
# =============================================================================


@dataclass
class ObservationEntry:
    """NPC观察条目.

    NPC对周围环境和其他角色的实时观察。
    观察是最新鲜的信息来源，时效性最强。
    每次感知层处理后，新的观察被写入此表。

    Attributes:
        observation: 观察到的内容（自然语言描述）.
        target_id: 观察对象ID（NPC ID / 事件ID / 物品ID / 位置）.
        target_type: 观察对象类型（npc/event/item/location）.
        timestamp: 观察时间.
        detail_level: 细节程度 (1-5)，取决于NPC的认知能力.
        is_anomaly: 是否观察到了异常（触发推理的标志之一）.
        location: 观察发生的地点.
    """

    observation: str = ""
    target_id: str = ""                         # AI可修改: 是 | 程序可修改: 是
    target_type: str = "event"                  # AI可修改: 是 | 程序可修改: 是
    timestamp: str = ""                         # AI可修改: 否 | 程序可修改: 是
    detail_level: int = 1                       # AI可修改: 是 | 程序可修改: 是
    is_anomaly: bool = False                    # AI可修改: 是 | 程序可修改: 是
    location: str = ""                          # AI可修改: 是 | 程序可修改: 是
    # 说明: observation内容由AI产生（感知层LLM的perceived_summary），程序写入。
    # detail_level受NPC的intelligence影响。


# =============================================================================
# 记忆管理
# =============================================================================


@dataclass
class MemoryManager:
    """记忆管理器配置.

    控制记忆的保留、衰减和清理策略。

    Attributes:
        max_entries: 最大记忆条目数.
        decay_enabled: 是否启用记忆衰减.
        importance_threshold: 低于此重要度的记忆可能被遗忘.
        max_age_ticks: 最大记忆年龄（tick数），超过可能被遗忘. 0=永久保留.
        retention_strategy: 保留策略 ("fifo"=先进先出 / "importance"=按重要度).
    """

    max_entries: int = 50               # AI可修改: 否 | 程序可修改: 是
    decay_enabled: bool = True          # AI可修改: 否 | 程序可修改: 是
    importance_threshold: int = 3       # AI可修改: 否 | 程序可修改: 是
    max_age_ticks: int = 0              # AI可修改: 否 | 程序可修改: 是
    retention_strategy: str = "importance"  # AI可修改: 否 | 程序可修改: 是

    def should_retain(self, entry: MemoryEntry, current_tick: int) -> bool:
        """判断一条记忆是否应该保留.

        Args:
            entry: 记忆条目.
            current_tick: 当前tick数.

        Returns:
            是否保留.
        """
        if entry.importance >= self.importance_threshold:
            return True
        if self.max_age_ticks > 0 and current_tick > self.max_age_ticks:
            # 简化：使用timestamp判断（实际需要tick数）
            return False
        effect_strength = entry.importance * (1 - entry.decay_rate * entry.recall_count)
        return effect_strength >= self.importance_threshold

    def prune_memories(self, memories: list[MemoryEntry]) -> list[MemoryEntry]:
        """裁剪记忆列表，移除超出容量且重要度低的记忆.

        Args:
            memories: 当前记忆列表.

        Returns:
            裁剪后的记忆列表.
        """
        if len(memories) <= self.max_entries:
            return memories

        if self.retention_strategy == "importance":
            sorted_mems = sorted(memories, key=lambda m: m.importance, reverse=True)
            return sorted_mems[:self.max_entries]
        else:
            return memories[-self.max_entries:]
