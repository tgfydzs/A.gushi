from common import time_int, uuid
from common.log import get_logger

logger = get_logger(__name__)


class B3NPCLayer:
    """
    base3.1 单个 NPC 层

    **11 张表**，按「信息性质」分成两组（这正是 L2/L3 拆分的依据）：

    | 组 | 表 | 谁写 |
    |----|-----|------|
    | **主观**（"我怎么想"） | 知识记忆 · 信念 · 性格 · 生活目标 · 好感度 · 观察 · 对玩家观察 · 待解释事件 | **L2** |
    | **客观**（"世界实际怎样"） | 当前状态 · 持有物 | **L3** |
    | —（只读） | 表更新成本 | 不修改 |

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

    # ---- 截断上限。init_all_npcs() 会用世界配置覆写，见 GameController ----
    # ⚠️ gushi3 只截了 3 张，另外 4 张（对玩家观察/信念/目标/好感度）
    #    **无上限无限增长**，而每轮全量喂给角色层 —— 长跑必然溢出上下文。
    MAX_OBSERVATION = 20
    MAX_MEMORY = 30
    MAX_PENDING = 10
    MAX_PLAYER_OBSERVATION = 20
    MAX_BELIEF = 12
    MAX_GOALS = 10
    MAX_RELATIONSHIP = 15

    # 表名 → 上限属性名。cap_fields 与 memory.py 共用同一张映射，
    # 避免两处各写一份键名（gushi3 就是这么埋下"只截 3 张"的坑的）。
    CAP_MAP = {
        '知识记忆Memory': 'MAX_MEMORY',
        '观察Observation': 'MAX_OBSERVATION',
        '待解释事件PendingReasoning': 'MAX_PENDING',
        '对玩家观察PlayerObservation': 'MAX_PLAYER_OBSERVATION',
        '信念Belief': 'MAX_BELIEF',
        '生活目标Goals': 'MAX_GOALS',
        '好感度Relationship': 'MAX_RELATIONSHIP',
    }

    def cap_fields(self):
        """
        截断所有 list 型字段，防止无限增长。

        在每个修改入口（sync_npc_to_lists、R1 传播、L2 落盘等）后调用。
        **这里是"直接丢弃"的兜底**——记忆巩固（memory.py）会在后台
        把超额部分压成摘要，两者配合：巩固负责保信息，cap 负责保下限。
        """
        d = self.to_dict()
        for table, attr in self.CAP_MAP.items():
            cap = getattr(self, attr, None)
            items = d.get(table)
            if cap is None or not isinstance(items, list):
                continue
            if len(items) > cap:
                dropped = len(items) - cap
                d[table] = items[-cap:]
                logger.debug(f'🧹 [{self.name}] {table} 截断：丢弃 {dropped} 条')
        # 就地刷新自身字段
        self.memory = d['知识记忆Memory']
        self.belief = d['信念Belief']
        self.goals = d['生活目标Goals']
        self.relationship = d['好感度Relationship']
        self.observation = d['观察Observation']
        self.player_observation = d['对玩家观察PlayerObservation']
        self.pending_reasoning = d['待解释事件PendingReasoning']

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
            '表更新成本UpdateCost': self.update_cost,
        }
