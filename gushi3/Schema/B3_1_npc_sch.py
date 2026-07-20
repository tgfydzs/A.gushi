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

    def cap_fields(self):
        """
        截断所有列表类型字段，防止无限增长。
        在每个修改入口（sync_npc_to_lists、N7传播等）后调用。
        """
        # Observation：保留最近N条
        if len(self.observation) > self.MAX_OBSERVATION:
            dropped = len(self.observation) - self.MAX_OBSERVATION
            self.observation = self.observation[-self.MAX_OBSERVATION:]
            logger.debug(f'🧹 [{self.name}] observation截断：丢弃{dropped}条')

        # Memory：保留最近N条
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]

        # PendingReasoning：保留最近N条
        if len(self.pending_reasoning) > self.MAX_PENDING:
            self.pending_reasoning = self.pending_reasoning[-self.MAX_PENDING:]

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
