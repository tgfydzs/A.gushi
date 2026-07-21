


from enum import Enum, unique

from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid
from common.log import get_logger

logger = get_logger(__name__)



class B4PlayerLayer:
    """
    base4 玩家层
    职能：玩家对象，存储玩家的库存、实力等信息
    :param player_dict: 玩家数据字典
    """

    def __init__(self, player_dict: dict):
        self.player_dict = dict(player_dict)  # 深拷贝防止修改入参
        self.player_dict.pop('备注', None)