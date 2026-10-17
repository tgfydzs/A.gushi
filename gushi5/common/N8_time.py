"""
N8 时间层 — 纯规则聚合，不再调用 LLM。

耗时来源（都已在上游产生，本层只做聚合）：
  1. NPC互动：N6 收尾时顺带估出的「本轮耗时分钟」。同一轮多个NPC是**并行**的，
     互相不叠加，取最大值。
  2. 玩家自主行动：N6-Player 估出的「本轮耗时分钟」。
  3. 移动：**玩家怎么说就怎么算**——玩家明说的耗时 > LLM 判定的这段路耗时 > 代码按
     「段数 × 每段分钟」保守兜底。⚠️ 这里不需要"移动方式"这种枚举：读得懂输入的那层
     直接给分钟数，代码不再拿词表去猜"撕裂虚空算不算瞬移"。
  4. **玩家明确声明的时间跳过**：N2 判出"（一个月后）""修炼一年""等三天"这类
     **时间真的流逝**的说法，换算成分钟。与"提及时间"严格分开——玩家说
     "一年后我要你做到"只是在提要求，时钟一分钟都不该动。

移动耗时的三个来源（高 → 低）：玩家声明 > LLM 判定 > 段数×每段分钟（兜底）。

聚合规则：活动耗时（NPC/玩家取大） + 移动耗时（先后发生，叠加），下限 1 分钟；
**有明确时间跳过时取 max(跳过, 上述合计)**——跳过描述的是"直接跳到之后某一刻"，
不是"这轮干了多久"。

原 N8 的 LLM 估算职责已并入 N6，本层因此从关键路径上消失（少一次串行往返）。
"""

from common.log import get_logger

logger = get_logger('N8_Time')


class N8_Time:
    """N8 时间层 — 纯规则聚合器"""

    # 移动每段（地点树相距一级）的默认分钟数，可由世界配置覆写
    DEFAULT_MINUTES_PER_SEGMENT = 10

    # 玩家声明/LLM 判定耗时的上限（100年），防止天文数字
    MAX_DECLARED_MINUTES = 52_560_000

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N8_Time')


    @staticmethod
    def _parse_declared(raw) -> int:
        """解析玩家声明的移动耗时，非法值返回 0（= 没声明）。"""
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return 0
        return v if v > 0 else 0

    # ===================== 移动耗时 =====================

    def estimate_move_minutes(self, from_loc: str, to_loc: str, world_layer=None,
                              declared_minutes=0, judged_minutes=0) -> int:
        """
        移动耗时。**谁读得懂输入谁说了算，代码只兜底。**

        优先级（高 → 低）：
          1. `declared_minutes` —— 玩家自己明说"赶了半个月路"
          2. `judged_minutes`   —— LLM 读完这一轮输入后判"这段路实际花了多久"
          3. 都没给 → 代码兜底：段数 × 每段分钟（**保守估值，不是语义判断**）

        ⚠️ 这里曾经有一张"移动方式 → 速度倍率"的表（御剑飞行×20、传送×100…）。
        那是在用词表猜语义：实测玩家"撕裂虚空"不在表里，被按步行收了 70 分钟，
        而且只打一行 INFO。词表永远补不完，所以整张表已删除——
        "这段路多久"改由读得懂输入的层直接判。

        判定依据是**实际位置变化**，不是 `{移动}` 标记——玩家完全可能
        在自由文本里直接说"我来到青木山脉"，此时位置由 N6 的玩家增量改动。

        :param from_loc: 回合开始时的位置（全路径）
        :param to_loc: 回合结束时的位置（全路径，调用方负责归一）
        :param world_layer: B1WorldLayer，用于算 LCA 距离段数和读每段分钟配置
        :param declared_minutes: 玩家明确声明的移动耗时，0 表示未声明
        :param judged_minutes: N2 判定的这段路耗时，0 表示没给
        :return: 分钟数，未移动返回 0
        """
        for raw, label in ((declared_minutes, '玩家声明'), (judged_minutes, 'LLM 判定')):
            minutes = self._parse_declared(raw)
            if minutes <= 0:
                continue
            capped = min(minutes, self.MAX_DECLARED_MINUTES)
            if capped != minutes:
                self.logger.warn(f'{label}耗时 {minutes} 分超出上限，截断为 {capped} 分')
            self.logger.info(f'🚶 移动耗时：{label} {capped} 分')
            return capped

        # 没动地方就不用算
        if not from_loc or not to_loc or from_loc == to_loc:
            return 0

        seg = 0
        if world_layer is not None:
            try:
                seg = world_layer.location_distance(from_loc, to_loc)
            except Exception as e:
                self.logger.warn(f'移动距离计算失败：{from_loc} → {to_loc}：{e}')
                seg = 0

        per_seg = self.DEFAULT_MINUTES_PER_SEGMENT
        if world_layer is not None:
            per_seg = world_layer.get_config('移动耗时_每段分钟', self.DEFAULT_MINUTES_PER_SEGMENT)

        minutes = max(1, seg * int(per_seg))
        self.logger.info(
            f'🚶 移动耗时（兜底）：{from_loc} → {to_loc} | {seg}段 × {per_seg}分 = {minutes}分'
            f'（N2 没给 move_minutes，这是保守估值）'
        )
        return minutes

    # ===================== 聚合入口 =====================

    def _cap_span(self, minutes) -> int:
        """玩家声明的**时间跳过**截断到 100 年（防 LLM 吐出天文数字）。"""
        try:
            v = int(float(minutes or 0))
        except (TypeError, ValueError):
            return 0
        if v <= 0:
            return 0
        if v > self.MAX_DECLARED_MINUTES:
            self.logger.warn(f'⏭ 声明的时间跳过 {v} 分超出上限，'
                            f'截断为 {self.MAX_DECLARED_MINUTES} 分')
            return self.MAX_DECLARED_MINUTES
        return v

    def aggregate(self, npc_minutes: list = None, player_minutes: int = 0,
                  from_loc: str = '', to_loc: str = '', world_layer=None,
                  declared_minutes=0, judged_minutes=0,
                  declared_span_minutes=0) -> int:
        """
        聚合本轮游戏时间跨度。

        活动耗时（NPC/玩家取大）与移动耗时（先后发生）相加。
        **玩家明确声明的时间跳过**在此之上取 max（见下）。

        :param npc_minutes: 各NPC的耗时估计列表（并行，取最大）
        :param player_minutes: 玩家自主行动耗时
        :param from_loc: 回合开始位置
        :param to_loc: 回合结束位置
        :param world_layer: 世界层
        :param declared_minutes: 玩家声明的移动耗时
        :param judged_minutes: LLM 判定的这段路耗时（N2 的 `move_minutes`）
        :param declared_span_minutes: 玩家**明确声明的时间跳过**（"（一个月后）"、
            "修炼一年"）。N2 判为"提及时间"时这里恒为 0。
        :return: 整数分钟，≥1
        """
        npc_list = [m for m in (npc_minutes or []) if m]
        activity = max(npc_list + [int(player_minutes or 0)], default=0)
        move = self.estimate_move_minutes(from_loc, to_loc, world_layer,
                                          declared_minutes, judged_minutes)

        spent = max(1, int(activity + move))
        self.logger.info(
            f'⏱ 耗时聚合：活动{activity}分（NPC{len(npc_list)}个取大 / 玩家{player_minutes}分）'
            f' + 移动{move}分 = {spent}分'
        )

        # 时间跳过描述的不是"这轮干了多久"，而是"直接跳到之后某一刻"，所以取 max：
        # 跳一年不可能比一轮对话还短；反过来，声明了跳跃时也不该再加上对话的几分钟。
        span = self._cap_span(declared_span_minutes)
        if span > spent:
            self.logger.info(f'⏭ 时间跳过：{span}分 > 本轮实际消耗 {spent}分，'
                            f'时钟按 {span}分推进')
            return span
        return spent

    def process(self, game_clock, npc_minutes: list = None, player_minutes: int = 0,
                from_loc: str = '', to_loc: str = '', world_layer=None,
                declared_minutes=0, judged_minutes=0,
                declared_span_minutes=0) -> int:
        """
        聚合耗时并推进游戏时钟。

        :return: 耗时分钟数
        """
        minutes = self.aggregate(
            npc_minutes=npc_minutes,
            player_minutes=player_minutes,
            from_loc=from_loc,
            to_loc=to_loc,
            world_layer=world_layer,
            declared_minutes=declared_minutes,
            judged_minutes=judged_minutes,
            declared_span_minutes=declared_span_minutes,
        )
        game_clock.advance(minutes)
        self.logger.info(f'⏰ 游戏时间 → {game_clock.now_str()}（+{minutes:.0f}分钟）')
        return minutes
