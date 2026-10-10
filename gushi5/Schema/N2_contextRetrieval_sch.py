


from enum import Enum, unique
from common import time_int, uuid
from common.log import get_logger
from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B3_npclist_sch import B3NPCListLayer
from Schema.B3_1_npc_sch import B3NPCLayer
from Schema.Context_sch import ContextHistory

logger = get_logger(__name__)



class N2ContextRetrievalLayer:
    """
    将 世界层、npc对象列表、事件上下文 封装为事件层
    :param world_state: B1WorldLayer 世界层
    :param npc_list: B3NPCListLayer NPC列表层
    :param event_context: ContextHistory 事件上下文对象
    """

    def __init__(self, world_state: B1WorldLayer, npc_list: B3NPCListLayer, event_context: ContextHistory):
        self.context_retrieval_id = uuid.generate_session_id() # 事件id
        self.context_retrieval_timestamp = time_int.get_minute_ts() # 事件时间戳
        self.WorldState = world_state # 世界层
        self.npc_list = npc_list # npc列表
        self.event_context = event_context # 当前事件上下文对象


    def to_dict(self):
        return {
            '事件id': self.context_retrieval_id,
            '事件时间戳': time_int.ts_to_readable(self.context_retrieval_timestamp),
            '世界层': self.WorldState.dict,
            'NPC列表': self.npc_list.to_dict(),
            '当前事件上下文': self.event_context.context
        }

