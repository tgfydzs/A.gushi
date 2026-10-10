"""
N8 时间层 — 纯规则聚合，不再调用 LLM。

耗时来源（都已在上游产生，本层只做聚合）：
  1. NPC互动：N6 收尾时顺带估出的「本轮耗时分钟」。同一轮多个NPC是**并行**的，
     互相不叠加，取最大值。
  2. 玩家自主行动：N6-Player 估出的「本轮耗时分钟」。
  3. 移动：**客观距离**（地点树 LCA 段数 × 每段分钟）÷ **玩家输入给出的方式系数**
     （N2 判断的 move_style：步行/疾行/御剑飞行…）。

移动 = 客观位置 + 玩家输入的综合：
  - 客观位置决定**基准耗时**（走多远）
  - 玩家输入的移动方式/紧急程度决定**折算系数**（怎么走的，急不急）
  - 玩家自己声明的耗时优先级最高，直接采信

聚合规则：活动耗时（NPC/玩家取大） + 移动耗时（先后发生，叠加），下限 1 分钟。

原 N8 的 LLM 估算职责已并入 N6，本层因此从关键路径上消失（少一次串行往返）。
"""

from common.log import get_logger

logger = get_logger('N8_Time')


class N8_Time:
    """N8 时间层 — 纯规则聚合器"""

    # 移动每段（地点树相距一级）的默认分钟数，可由世界配置覆写
    DEFAULT_MINUTES_PER_SEGMENT = 10

    # 玩家声明耗时的上限（100年），防止 LLM 产出天文数字
    MAX_DECLARED_MINUTES = 52_560_000

    # 移动方式 → 相对【步行】的速度倍率（越大越快，耗时 = 基准 ÷ 倍率）
    # 可由世界配置 "移动方式系数" 整体覆写，以便不同世界观自定义（修仙/都市/…）
    #
    # 匹配是"子串"式的，所以表里要放足够多的近义写法——LLM 输出的方式是自由文本
    # （提示词给了参考词，但模型经常会写成"骑着快马疾驰"这种），
    # 覆盖不到就会静默回落成步行。未知写法的兜底是安全的，只是会偏慢。
    DEFAULT_STYLE_FACTORS = {
        '缓步': 0.6, '散步': 0.6, '踱步': 0.6, '漫步': 0.6,
        '步行': 1.0, '走路': 1.0, '赶路': 1.2, '跋涉': 0.8, '远足': 0.8,
        '急行': 1.6, '疾行': 1.6, '快走': 1.6, '快步': 1.6, '疾步': 1.6, '匆忙': 1.5,
        '奔跑': 2.5, '狂奔': 3.0, '冲刺': 3.0, '疾驰': 3.0, '飞奔': 3.0,
        '骑马': 4.0, '策马': 4.0, '纵马': 4.0, '快马': 4.0, '骑行': 3.0, '马匹': 4.0,
        '乘车': 4.0, '驾车': 4.0, '车马': 4.0, '赶车': 4.0, '乘舟': 3.0, '乘船': 3.0, '舟船': 3.0,
        '御剑飞行': 20.0, '御剑': 20.0, '飞行': 15.0, '御空': 15.0, '腾空': 15.0, '遁术': 20.0,
        '传送': 100.0, '瞬移': 100.0, '传送阵': 100.0, '空间移动': 100.0,
    }

    # 未识别方式时的兜底倍率（按步行算）
    DEFAULT_STYLE_FACTOR = 1.0

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N8_Time')

    # ===================== 移动方式系数 =====================

    def get_style_factor(self, style: str, world_layer=None) -> float:
        """
        查移动方式的速度倍率。未识别回落 DEFAULT_STYLE_FACTOR。

        匹配顺序：配置表 → 内置表；都是先精确匹配，再按 key 长度降序做子串匹配
        （保证"御剑飞行"优先于"飞行"命中）。
        """
        if not style or not style.strip():
            return self.DEFAULT_STYLE_FACTOR

        table = self.DEFAULT_STYLE_FACTORS
        if world_layer is not None:
            table = world_layer.get_config('移动方式系数', table) or table

        style = style.strip()
        if style in table:
            return float(table[style])

        # 子串匹配：长的 key 优先，避免 "飞行" 抢在 "御剑飞行" 前面
        for key in sorted(table, key=len, reverse=True):
            if key in style:
                return float(table[key])

        self.logger.info(f'未识别的移动方式「{style}」，按步行（×{self.DEFAULT_STYLE_FACTOR}）折算')
        return self.DEFAULT_STYLE_FACTOR

    @staticmethod
    def _parse_declared(raw) -> int:
        """解析玩家声明的移动耗时，非法值返回 0（= 没声明）。"""
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return 0
        return v if v > 0 else 0

    # ===================== 移动耗时（规则） =====================

    def estimate_move_minutes(self, from_loc: str, to_loc: str, world_layer=None,
                              style: str = '', declared_minutes=0) -> int:
        """
        估算移动耗时 = 客观距离基准 ÷ 移动方式系数。

        判定依据是**实际位置变化**，而不是 {移动} 标记——因为玩家完全可能
        在自由文本里直接说"我来到青木山脉"，此时位置由 N6 的玩家增量改动，
        标记路径拿不到。用「回合前位置 → 回合后位置」两条路都能覆盖。

        优先级：玩家声明的耗时 > 规则折算（距离 × 每段分钟 ÷ 方式系数）

        :param from_loc: 回合开始时的位置
        :param to_loc: 回合结束时的位置
        :param world_layer: B1WorldLayer，用于计算 LCA 距离段数和读取配置
        :param style: N2 判断的移动方式（"步行"/"御剑飞行"…）
        :param declared_minutes: 玩家明确声明的移动耗时，0 表示未声明
        :return: 分钟数，未移动返回 0
        """
        declared = self._parse_declared(declared_minutes)

        # 玩家自己说了多久就按他说的算（不压缩、不改写）
        if declared > 0:
            capped = min(declared, self.MAX_DECLARED_MINUTES)
            if capped != declared:
                self.logger.warn(f'声明耗时 {declared} 分超出上限，截断为 {capped} 分')
            self.logger.info(f'🚶 移动耗时：玩家声明 {capped} 分（方式「{style or "未提"}」）')
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

        factor = self.get_style_factor(style, world_layer)
        base = seg * per_seg
        # 四舍五入（不用 round()：它是银行家舍入，12.5→12 反直觉）
        minutes = max(1, int(base / factor + 0.5))

        self.logger.info(
            f'🚶 移动计费：{from_loc} → {to_loc} | {seg}段 × {per_seg}分 = {base}分'
            f' ÷ 方式「{style or "未提"}」×{factor} = {minutes}分'
        )
        return minutes

    # ===================== 聚合入口 =====================

    def aggregate(self, npc_minutes: list = None, player_minutes: int = 0,
                  from_loc: str = '', to_loc: str = '', world_layer=None,
                  style: str = '', declared_minutes=0) -> int:
        """
        聚合本轮游戏时间跨度。

        活动耗时（NPC/玩家取大）与移动耗时（先后发生）相加。

        :param npc_minutes: 各NPC的耗时估计列表（并行，取最大）
        :param player_minutes: 玩家自主行动耗时
        :param from_loc: 回合开始位置
        :param to_loc: 回合结束位置
        :param world_layer: 世界层
        :param style: N2 判断的移动方式
        :param declared_minutes: 玩家声明的移动耗时
        :return: 整数分钟，≥1
        """
        npc_list = [m for m in (npc_minutes or []) if m]
        activity = max(npc_list + [int(player_minutes or 0)], default=0)
        move = self.estimate_move_minutes(from_loc, to_loc, world_layer, style, declared_minutes)

        total = max(1, int(activity + move))
        self.logger.info(
            f'⏱ 耗时聚合：活动{activity}分（NPC{len(npc_list)}个取大 / 玩家{player_minutes}分）'
            f' + 移动{move}分 = {total}分'
        )
        return total

    def process(self, game_clock, npc_minutes: list = None, player_minutes: int = 0,
                from_loc: str = '', to_loc: str = '', world_layer=None,
                style: str = '', declared_minutes=0) -> int:
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
            style=style,
            declared_minutes=declared_minutes,
        )
        game_clock.advance(minutes)
        self.logger.info(f'⏰ 游戏时间 → {game_clock.now_str()}（+{minutes:.0f}分钟）')
        return minutes
