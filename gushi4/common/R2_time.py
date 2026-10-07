"""
R2 时钟推进（纯规则，不调用 LLM）

**耗时本身由 L3 判断，本层只负责推进时钟 + 下限保护。**

# 这里原来有什么，为什么删了

原来 R2 自己算移动耗时：

    移动分钟 = 声明分钟
             | 段数 × 移动耗时_每段分钟 ÷ 移动方式倍数

两条路都有问题：

| 原来的做法 | 为什么不对 |
|-----------|-----------|
| `段数 × 每段分钟` | 把距离简化成"差几层节点"。同一段距离，走山路和走官道、白天和夜里、有没有飞行，耗时能差几个数量级 |
| `÷ 移动方式倍数` | 倍数是张表（步行 1.0 / 骑马 4 / 御剑 20…）。表要穷举近义写法，永远补不完；补不到就静默按步行算 |

**根子和 R1 一样**：让 LLM 吐一个数字、Python 用表把数字翻译成结论。
距离和耗时恰恰是没法用几十个键值对描述的东西。

# 现在的分工

    L3 输出 "本轮耗时分钟"（原地活动 + 赶路，一起判断）
      ↓
    R2 只做：下限保护 + game_clock.advance()

**"同一轮多个 NPC 是并行的所以取最大不求和"** 这条也没有了——L3 看到所有 NPC
的意图，它给出的就是一个数，本来就是"这轮过了多久"。
"""

from common.log import get_logger

logger = get_logger('R2_Time')

#: 一轮最少推进这么多分钟。防止 L3 给出 0 或负数让时钟不动/倒流。
MIN_MINUTES = 1


class R2_Time:
    """时钟推进器：把 L3 判断的分钟数落到 GameClock 上"""

    def __init__(self, world_layer=None):
        #: 保留只为兼容调用点，本层已不做任何距离/位置计算
        self.world_layer = world_layer

    def set_world(self, world_layer):
        self.world_layer = world_layer

    @staticmethod
    def clamp(raw) -> int:
        """
        夹取 L3 给的耗时。**任何异常都回落 1 分钟**——
        耗时是直接推进时钟的，一个 null 就能让游戏时间崩掉。
        """
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return MIN_MINUTES
        return max(MIN_MINUTES, v)

    def process(self, game_clock, minutes=None, **kwargs) -> int:
        """
        推进游戏时钟。

        :param minutes: L3 判断的本轮总耗时（原地活动 + 赶路）
        :param kwargs: **已不再使用**（from_loc/to_loc/multiplier/declared_minutes/
                       npc_minutes 等旧参数），保留仅为兼容调用点
        :return: 实际推进的分钟数
        """
        total = self.clamp(minutes)
        game_clock.advance(total)
        logger.info(f'⏰ 游戏时间 → {game_clock.now_str()}（+{total}分钟）')
        return total
