from enum import Enum, unique
from wsgiref.util import request_uri

from common import time_int, uuid
from common.log import get_logger

logger = get_logger(__name__)




class B1WorldLayer:
    """
    base1 世界层
    职能：封装世界规则、状态
    world_dict: dict | 传入世界规则字典，内部init通过对应key提取
    """


    def __init__(self, world_dict: dict):
        self.dict = world_dict







