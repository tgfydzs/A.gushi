


from enum import Enum, unique

from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid, log

logger = log.SimpleLogger()



class B4PlayerLayer:
    """
    base4 玩家层
    职能：玩家对象，存储玩家的库存、实力等信息
    表：当前状态CurrentState、持有物Inventory、描述Description
    """



    def __init__(self, name, current_state: list, inventory: list, description):
        self.name = name
        self.current_state = current_state
        self.inventory = inventory
        self.description = description



    def to_json(self):
        return {'name': self.name,'玩家描述': self.description , '当前状态': self.current_state, '持有物': self.inventory}