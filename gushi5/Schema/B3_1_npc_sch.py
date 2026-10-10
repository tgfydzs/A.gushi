from enum import Enum, unique

from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid
from common.log import get_logger

logger = get_logger(__name__)



class B3NPCLayer:
    """
    base3.1 单个npc层
    职能：每一个npc为一个对象，对象内部维护多个表
    表：知识记忆Memory，信念Belief， 性格Personality， 生活目标Goals， 好感度Relationship， 当前状态CurrentState， 观察Observation， 对玩家观察PlayerObservation， 持有物Inventory， 待解释事件PendingReasoning， 表更新成本UpdateCost

    注意：此类的实例由 B3NPCListLayer 统一管理，不直接对外暴露。
    """

    def __init__(self, dict: dict):
        self.name = dict['名字Name']
        self.memory = dict['知识记忆Memory']
        self.belief = dict['信念Belief']
        self.personality = dict['性格Personality']
        self.goals = dict['生活目标Goals']
        self.relationship = dict['好感度Relationship']
        self.current_state = dict['当前状态CurrentState']
        self.observation = dict['观察Observation']
        self.player_observation = dict['对玩家观察PlayerObservation']
        self.inventory = dict['持有物Inventory']
        self.pending_reasoning = dict['待解释事件PendingReasoning']
        self.update_cost = dict['表更新成本UpdateCost']

    MAX_OBSERVATION = 20
    MAX_MEMORY = 30
    MAX_PENDING = 10
    # 以下 4 项 gushi3 当年**没有上限**：表会无限增长，而且每轮随 to_dict() 全量喂给 LLM。
    # 上限值取自世界配置 `NPC_上限_<属性名>`，键名拼法见 CAP_MAP。
    MAX_PLAYER_OBSERVATION = 20
    MAX_BELIEF = 12
    MAX_GOALS = 10
    MAX_RELATIONSHIP = 15

    #: 世界配置键名 `NPC_上限_<属性名>` 的拼法表（字段名 → 类属性名）。
    #: `init_all_npcs` 按它逐个覆写；`cap_fields` 按它逐个截断。
    #: ⚠️ 键名是 `NPC_上限_MAX_MEMORY` 这种形式，**不是** `NPC_记忆上限`——
    #: 旧名与代码查的键零交集，那三项从来没生效过，只因值恰好等于类默认值才没暴露。
    CAP_MAP = {
        '观察Observation': 'MAX_OBSERVATION',
        '知识记忆Memory': 'MAX_MEMORY',
        '待解释事件PendingReasoning': 'MAX_PENDING',
        '对玩家观察PlayerObservation': 'MAX_PLAYER_OBSERVATION',
        '信念Belief': 'MAX_BELIEF',
        '生活目标Goals': 'MAX_GOALS',
        '好感度Relationship': 'MAX_RELATIONSHIP',
    }

    def cap_fields(self):
        """
        截断所有列表类型字段，防止无限增长。
        在每个修改入口（sync_npc_to_lists、N7传播等）后调用。

        gushi3 只截 3 张表（observation/memory/pending），另 4 张无上限无限增长——
        这里 7 张全截。上限是**类属性**，由 `init_all_npcs` 从世界配置覆写。
        """
        for attr, field, label in (
            ('MAX_OBSERVATION', 'observation', '观察'),
            ('MAX_MEMORY', 'memory', '知识记忆'),
            ('MAX_PENDING', 'pending_reasoning', '待解释事件'),
            ('MAX_PLAYER_OBSERVATION', 'player_observation', '对玩家观察'),
            ('MAX_BELIEF', 'belief', '信念'),
            ('MAX_GOALS', 'goals', '生活目标'),
            ('MAX_RELATIONSHIP', 'relationship', '好感度'),
        ):
            limit = getattr(self, attr, None)
            value = getattr(self, field, None)
            if not limit or not isinstance(value, list) or len(value) <= limit:
                continue
            dropped = len(value) - limit
            setattr(self, field, value[-limit:])
            logger.debug(f'🧹 [{self.name}] {label} 截断：丢弃{dropped}条')

    def to_dict(self) -> dict:
        return {
            '名字Name': self.name,
            '知识记忆Memory': self.memory,
            '信念Belief': self.belief,
            '性格Personality': self.personality,
            '生活目标Goals': self.goals,
            '好感度Relationship': self.relationship,
            '当前状态CurrentState': self.current_state,
            '观察Observation': self.observation,
            '对玩家观察PlayerObservation': self.player_observation,
            '持有物Inventory': self.inventory,
            '待解释事件PendingReasoning': self.pending_reasoning,
            '表更新成本UpdateCost': self.update_cost
        }
