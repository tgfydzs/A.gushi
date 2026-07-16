from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid, log







class ContextHistory:
    def __init__(self):
        self.context = [] #上下文，列表内部存储str。"对话轮次号 | 时间 | 发言人：说的话"
        self.count = 0 #对话轮次计数


    def add_context(self, context):
        """
        添加话语至上下文。
        另外这个上下文的时间戳目前是现实时间，是否有必要换成游戏时间？
        :param context: 上下文内容，格式（str）：  发言人：说的话
        """
        self.count += 1
        timestamp = time_int.get_minute_ts()
        save = f"{self.count} | {time_int.ts_to_readable(timestamp)} | {context}"
        self.context.append(save)

    def get_all_context(self):
        return self.context


















