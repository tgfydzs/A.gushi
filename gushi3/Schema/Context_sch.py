from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid, log







class ContextHistory:
    """
    创建上下文，实际是对话记录，内部为列表，即：["对话轮次号 | 时间 | 发言人：说的话", ...]
    """
    def __init__(self, context=None, count=0):
        if context is not None:
            self.context = context
            self.count = count
        else:
            self.context = [] #上下文，列表内部存储str。"对话轮次号 | 时间 | 发言人：说的话"
            self.count = 0 #对话轮次计数


    def add_context(self, context):
        """
        添加话语至上下文。
        另外这个上下文的时间戳目前是现实时间，是否有必要换成游戏时间？
        :param context: 上下文内容，格式（str）->发言人：说的话
        """
        self.count += 1
        timestamp = time_int.get_minute_ts()
        save = f"{self.count} | {time_int.ts_to_readable(timestamp)} | {context}"
        self.context.append(save)

    def new_context(self):
        """
        一般用于事件层，新建事件上下文
        """
        self.context = []


    def __len__(self):
        return len(self.context)

















