from enum import Enum, unique
from wsgiref.util import request_uri

from common import time_int, uuid, log

logger = log.SimpleLogger()




class B1WorldLayer:
    """
    base1 世界层
    职能：封装世界规则、状态
    world_dict: dict | 传入世界规则字典，内部init通过对应key提取
    """


    def __init__(self, world_dict: dict):
        self.rule = world_dict['世界规则列表']
        self.state = world_dict['世界状态列表']
        self.world_knowledge = world_dict['世界知识列表']

    def to_dict(self):
        return {
            '世界规则列表': self.rule,
            '世界状态列表': self.state,
            '世界知识列表': self.world_knowledge
        }







if __name__ == '__main__':
    dict1 = {
        'rule': ['杀人犯法', '有宵禁'],
        'state': ['南部有暴风雪', '将于公元102年3月颁布新的法令'],
        'world_knowledge': ['公元102年3月颁布的法令：农业减税... ...']
    }
    worldState = B1WorldLayer(dict1)