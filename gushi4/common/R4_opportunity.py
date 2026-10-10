"""
R4 机缘导演（纯规则，不调用 LLM）

按玩家所在地的 `_属性.产出` 概率表掷骰，产出**"此地此刻出了什么"的事实**，
交给 L3 结算。名字沿用设计里的「机缘导演」；实现上是纯规则，与 R1/R2/R3 同级。

# 它解决的是什么

在它之前，世界**不会给玩家任何东西**：玩家能拿到物品的唯一方式是对话里被 NPC
"给"——那是小说逻辑，不是游戏逻辑。地点属性里那 15 张 `产出` 概率表（灵虚集的
低阶丹药、青木山脉的灵草、万蛇谷的蛇王内丹…）写得很完整，却没有任何代码读它。

# 三条边界（都不是实现细节，是设计约束）

| 约束 | 出处 | 这里怎么守 |
|------|------|-----------|
| **骰子只管"有没有"，LLM 管"是什么样"** | 设计方案 §一 原则 8 | Python 只掷骰，产出的是一行 `{物品, 条件}`；怎么被发现、能不能拿到、写成什么句子，全在 L3 |
| **客观状态只有 L3 能写** | 设计方案 D5/D12 | **本层不写任何状态**。它把结果放进 L3 的输入，由 L3 走 `sanitize_objective_delta` 落到 `持有物Inventory` |
| **概率不给玩家看见** | README「地段功能」 | 概率**只在这里读一次就丢掉**，不进 L3 输入、不进日志正文、不进前端——把投放率露出去等于把骰子给玩家看 |

# 为什么不做成"玩家动作触发"

直觉上更省的方案是"玩家说采药 → 掷骰"。但**判断"这句话是不是一次采集尝试"本身
就是语义判断**，纯规则做不了，只能再加一次 LLM 调用——那正是 S1 旁路被摘除的
原因（"旁路绝不能成为新的故障点"，见 `common/R2_time.py`）。

所以触发改成**时间门**：玩家所在地每 `机缘_间隔_小时` 到一次机会，中不中都消耗
这个窗口。骰子在玩家什么都没说的时候也照掷——**世界不围绕玩家转**（B1a 的同一
条原则）。玩家能拿到的只是"恰好在场"的那一份。

# 节流

    距上次掷骰 ≥ 机缘_间隔_小时  且  今日成功次数 < 机缘_每日上限

- **中与不中都消耗窗口**。否则站在青木山脉反复空掷，直到命中为止——等于把
  `概率` 变成"每次都能拿到"，只是多等几轮。
- 每日上限按**件数**算，不是按次数：`机缘_每日上限: 2` = 一天最多得手两件东西。
  产出表是**逐条独立判定**的（表内概率不归一），青木山脉四条全中会一次出四件——
  按次数算的话上限形同虚设。按游戏日（`_elapsed // 1440`）清零，**设 0 就是彻底
  关掉机缘**（世界数据里没有 `产出` 表时效果相同）。
- **首次调用只记时间、不掷骰**：读档后本层是新建的，`_last_elapsed` 归零，
  而时钟是存档里的大值——不设这道闸，一读档立刻爆一次机缘。
  （后台心跳那个 `_last_trigger_elapsed` 就有这个毛病，见 `common/background.py`；
  这里不重复它。）

# 已知取舍

- **只对玩家当前所在地掷骰**，不替别处的 NPC 掷。理由：v1 不想顺带引入"NPC 自己
  捡东西"的经济与库存问题；世界别处的机缘留给以后。
- 取**属性链上最深的带 `产出` 的节点**。父节点的产出对子地点同样成立
  （青木山脉产灵草，山脉里哪个角落都可能长灵草），而叶节点若有自己的表就优先它。
- `条件` 是自由文本（"深入山脉腹地" / "需破开血月光门"），**由 L3 判断是否满足**。
  这里只负责原样搬运，不解析——和"地段属性是描述不是规则"是同一条。
"""

import random

from common.log import get_logger

logger = get_logger('R4_机缘导演')

# ── 产出表的键名与默认闸门 ─────────────────────────────
ATTR_KEY = '产出'
ITEM_KEY = '物品'
CHANCE_KEY = '概率'
COND_KEY = '条件'

#: 同一地点两次机会之间至少隔这么多游戏时间。
#:
#: ⚠️ **这个数是按实测定的，不是拍脑袋**（2026-10-08，真实 API）：
#: L3 对普通一轮的耗时判断是**分钟级**——"转了一圈" +6 分、"逛了大半天" +3 分、
#: 当街拔剑 +3 分；只有显式时间跨度才给小时以上（"赶了三天的路" +4320 分）。
#: 而一个游戏日是 1440 分。所以间隔取 6 小时 = 60~100 轮才有一次机会，
#: **功能等于哑火**。取 0.5 小时 ≈ 每 6~10 轮一次，才在正常游玩里能被感觉到。
#:
#: 顺带一个反向结论：因为一轮平均才几分钟，**每日上限几乎永远不触发**，
#: 真正决定投放频率的是这个间隔。上限留着当长时间跨度（闭关/赶路）的兜底。
DEFAULT_INTERVAL_HOURS = 0.5
DEFAULT_DAILY_CAP = 2           # 每个游戏日最多得手几件（0 = 关掉机缘）

MINUTES_PER_DAY = 24 * 60


class R4_Opportunity:
    """
    机缘导演。用法：

        r4 = R4_Opportunity(world_layer)          # 需要世界层才能读地点属性
        hits = r4.roll(loc_before, game_clock)    # → [{'物品': '灵草'}] 或 []
        # 命中结果交给 L3：settle_*(..., opportunities=hits)
    """

    def __init__(self, world_layer=None, rng=None):
        self.world_layer = world_layer
        #: 掷骰源可注入——测试要确定性，生产用全局 random
        self._rng = rng if rng is not None else random
        self._last_elapsed = None   #: None = 还没掷过（首次只记时间）
        self._day = None
        self._day_hits = 0

    def set_world(self, world_layer):
        """绑定世界层（与 L1/L3 同一个装配模式：构造时不持有世界）。"""
        self.world_layer = world_layer

    # ===================== 闸门 =====================

    def _interval_minutes(self) -> int:
        """
        两次机会之间至少隔多少游戏时间。

        **坏值一律回落默认**：这个值直接决定玩家多久能拿一次东西，
        一个字符串或 null 不该让整层炸掉。
        """
        hours = DEFAULT_INTERVAL_HOURS
        if self.world_layer is not None:
            raw = self.world_layer.get_config('机缘_间隔_小时', DEFAULT_INTERVAL_HOURS)
            try:
                hours = float(raw)
            except (TypeError, ValueError):
                logger.warn(f'机缘_间隔_小时 = {raw!r} 不是数字，回落 {DEFAULT_INTERVAL_HOURS}')
                hours = DEFAULT_INTERVAL_HOURS
        return max(1, int(hours * 60))

    def _daily_cap(self) -> int:
        """每个游戏日最多得手**几件**东西。0 = 关掉机缘（比在世界数据里删干净 15 张表省事）。"""
        cap = DEFAULT_DAILY_CAP
        if self.world_layer is not None:
            raw = self.world_layer.get_config('机缘_每日上限', DEFAULT_DAILY_CAP)
            try:
                cap = int(raw)
            except (TypeError, ValueError):
                logger.warn(f'机缘_每日上限 = {raw!r} 不是整数，回落 {DEFAULT_DAILY_CAP}')
                cap = DEFAULT_DAILY_CAP
        return max(0, cap)

    # ===================== 地点 → 产出表 =====================

    def loot_table(self, location: str) -> list:
        """
        取该地**属性链上最深的带 `产出` 节点**的表（已清洗，可直接掷）。

        走 `get_location_context()` 而不是自己拆路径：地点键**本身可能含 "·"**
        （`跨界传送阵·星枢殿`），逐段下钻会错位——那正是 Phase A 修过的坑，
        见 `Schema/B1_worldState_sch.py` 的 `_walk_path` 最长匹配。

        ⚠️ 返回的表**带 `概率`**（掷骰要用），所以它是**内部表**：
        本模块对外的唯一出口是 `roll()`，它的产出只有 `物品`/`条件`。
        别把这里的返回值直接送往下游（L3 / 日志 / 前端）——那等于把骰子给玩家看。
        """
        if self.world_layer is None or not location:
            return []
        fn = getattr(self.world_layer, 'get_location_context', None)
        if fn is None:
            return []
        try:
            chain = fn(location) or []
        except Exception as e:
            logger.warn(f'取地段属性链失败（{location}）：{type(e).__name__}: {e}')
            return []

        for node in reversed(chain):            # 从最深往上找
            if not isinstance(node, dict):
                continue
            attrs = node.get('属性')
            table = attrs.get(ATTR_KEY) if isinstance(attrs, dict) else None
            if isinstance(table, list) and table:
                return self._clean(table)
        return []

    @staticmethod
    def _clean(table: list) -> list:
        """
        清洗产出表。**概率在这里读完就丢**（返回值只带 物品 / 条件），
        下游任何人——L3、日志、前端——都拿不到投放率。
        """
        out = []
        for e in table:
            if not isinstance(e, dict):
                continue
            item = str(e.get(ITEM_KEY, '') or '').strip()
            if not item:
                continue
            try:
                chance = float(e.get(CHANCE_KEY, 0) or 0)
            except (TypeError, ValueError):
                chance = 0.0
            if chance <= 0:
                continue
            rec = {'物品': item, '概率': min(1.0, chance)}
            cond = str(e.get(COND_KEY, '') or '').strip()
            if cond:
                rec['条件'] = cond
            out.append(rec)
        return out

    # ===================== 主入口 =====================

    def roll(self, location: str, game_clock=None, player=None) -> list:
        """
        掷一次骰。返回**本轮机缘**（可能为空）：`[{'物品': '灵草', '条件': '…'}]`

        :param location: 玩家本轮出发地（用 `loc_before`，不是移动后的位置——
                         机缘发生在"他这轮待着的地方"）
        :param game_clock: 取游戏时间做节流；缺省按 0 处理（沙盒/测试）
        :param player: 目前不用。留着是因为以后若要按修为/身份修正概率
                       （例如低修为者不易遇到高阶机缘），入口不必再改签名。
        """
        elapsed = int(getattr(game_clock, '_elapsed', 0) or 0)

        day = elapsed // MINUTES_PER_DAY
        if day != self._day:                    # 新的一天：清计数
            self._day = day
            self._day_hits = 0

        table = self.loot_table(location)
        if not table:                           # 此地没有产出表：不占窗口，也不建状态
            return []

        if self._last_elapsed is None:          # 首次：只记时间（防读档即爆）
            self._last_elapsed = elapsed
            # 说分钟不说小时：间隔默认 0.5 小时，写成"再推进 0 小时"是句废话
            logger.info(f'🎲 机缘导演就位 @ {location}：'
                        f'再推进 {self._interval_minutes()} 分钟才有第一次机会')
            return []

        if elapsed - self._last_elapsed < self._interval_minutes():
            return []

        self._last_elapsed = elapsed            # 窗口到了：中不中都消耗它

        cap = self._daily_cap()
        room = cap - self._day_hits            #: 今日还剩几件的额度
        if room <= 0:
            logger.info(f'🎲 机缘导演：今日已满 {self._day_hits}/{cap}，本窗口空过 @ {location}')
            return []

        hits = []
        for rec in table:                       # 逐条独立判定：表内概率不归一
            if len(hits) >= room:               # 额度用完就停（不改变逐条独立的语义，
                break                           #   只是封顶；顺序即表内顺序）
            if self._rng.random() < rec['概率']:
                hit = {'物品': rec['物品']}
                if '条件' in rec:
                    hit['条件'] = rec['条件']   # 条件原样搬运，由 L3 判是否满足
                hits.append(hit)

        if hits:
            self._day_hits += len(hits)
            logger.info(f'🎲 机缘导演 @ {location} → {[h["物品"] for h in hits]}'
                        f'（今日 {self._day_hits}/{cap} 件）')
        else:
            logger.info(f'🎲 机缘导演 @ {location} 未中（今日 {self._day_hits}/{cap} 件）')
        return hits
