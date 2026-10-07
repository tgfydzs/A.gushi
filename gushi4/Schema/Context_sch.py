from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid, log







class ContextHistory:
    """
    创建上下文，实际是对话记录，内部为列表，即：["对话轮次号 | 时间 | 发言人：说的话", ...]

    时间戳使用游戏时钟（game_clock），未设置时回退现实时间。
    """

    def __init__(self, context=None, count=0, game_clock=None):
        if context is not None:
            self.context = context
            self.count = count
        else:
            self.context = []  # 上下文，列表内部存储str。"对话轮次号 | 时间 | 发言人：说的话"
            self.count = 0     # 对话轮次计数
        self.game_clock = game_clock


    def add_context(self, context):
        """
        添加话语至上下文。
        :param context: 上下文内容，格式（str）->发言人：说的话
        """
        self.count += 1
        if self.game_clock:
            timestamp = self.game_clock.now_str()
        else:
            timestamp = time_int.ts_to_readable(time_int.get_minute_ts())
        save = f"{self.count} | {timestamp} | {context}"
        self.context.append(save)

    def set_game_clock(self, game_clock):
        """设置游戏时钟引用（初始化后注入）"""
        self.game_clock = game_clock

    def new_context(self):
        """
        一般用于事件层，新建事件上下文
        """
        self.context = []


    def __len__(self):
        return len(self.context)

















