"""
事件结构 —— gushi4 新增。

gushi3 的事件状态散落在 `event_context`（文本历史）+ `event_npc`/`related_npc`
（两个 NPC 列表）里，**没有"这个事件有多公开"这个维度**——公开程度靠 N3 输出的
`地段传播.mask` 概率表达。

gushi4 把 `scope` 收回来（设计方案 D8）：**可见性用枚举声明，不用概率暗示**。
理由：枚举是确定性判断，可以放在纯代码层（R3）强制执行；概率得靠 LLM 每轮自觉。

    public   公开      —— 传播范围内的任何人都可能知道
    area     区域      —— 同一地段（最近公共祖先）内可感知
    group    群体      —— 只有 involved 名单里的人/势力可感知
    private  私密      —— 只有直接互动对象可感知（一对一密谈）
    hidden   隐藏      —— 无人感知（幕后推演、纯心理活动）

**scope 与 involved 的关系**：scope 决定"名单外的人能不能知道"，
`involved` 决定"名单内的人无论如何都知道"。两者是"或"的关系，由 R3 执行：

    R3 判定顺序（硬约束，LLM 不可违反）：
      1. scope == hidden            → 谁都不给
      2. npc 在 involved 里          → 无条件给（无论多远、scope 多严）
      3. scope == private           → 只给 direct_target
      4. scope == group             → 只给 involved 里同势力的
      5. scope in (public, area)    → 走距离判定（area 额外要求同地段）
"""

from enum import Enum

from common.log import get_logger

logger = get_logger(__name__)


class Scope(str, Enum):
    """事件可见性。用 str 混入，方便直接与 JSON 字符串比较、直接落盘。"""

    PUBLIC = 'public'
    AREA = 'area'
    GROUP = 'group'
    PRIVATE = 'private'
    HIDDEN = 'hidden'

    #: 认不出模型给的 scope 时的回落值。
    #:
    #: **必须是 GROUP，不能是 PUBLIC。**
    #:
    #: 原来的注释说回落 public 是"宽进严出"——理由是"整轮无人生成感知比误公开更难排查"。
    #: 这个理由**不成立**：R3 里 `involved`（锁定名单）是无条件豁免的，还会用事实文本
    #: 给漏写的人补齐。所以**缩窄 scope 饿不死 L1 明确点名的人**，只会挡掉没被点名的旁观者。
    #:
    #: 代价是不对称的：
    #:   - 误判**过窄** → 少几个旁观者收到感知。可恢复，玩家重说一遍就行。
    #:   - 误判**过宽** → 秘密进了别人的观察表/记忆表，**不可逆**，且不会报错。
    #:
    #: GROUP 的语义正好是"只有明确列进名单的人知道"，是"非空且最保守"的那一档。
    SAFE_FALLBACK = 'group'

    @classmethod
    def parse(cls, raw, default: 'Scope' = None) -> 'Scope':
        """
        容错解析 LLM 给的 scope。

        模型经常输出中文、大小写混杂、近义词，甚至**枚举里根本不存在的值**
        （实测见过 `"open"`）。三级查找：精确英文 → 中文别名 → 保守回落。

        :param default: 模型**没给**这个字段时的沿用值（通常是上一轮的 scope）。
                        注意它**不用于**"给了但认不出"——那种情况一律走 `SAFE_FALLBACK`。
        """
        if isinstance(raw, cls):
            return raw

        # 字段缺失 ≠ 字段乱写：缺失就沿用上一轮，乱写才走保守回落
        if not raw:
            return default if default is not None else cls.PUBLIC

        key = str(raw).strip().lower()

        # ---- 1. 精确命中英文 ----
        for member in cls:
            if key == member.value:
                return member

        # ---- 2. 英文变体/近义（**精确匹配**，不做子串）----
        # 子串会误伤：'all' 命中 'small'、'sect' 命中 'section'。
        en_alias = {
            'open': cls.PUBLIC, 'openly': cls.PUBLIC, 'publicly': cls.PUBLIC,
            'overt': cls.PUBLIC, 'everyone': cls.PUBLIC, 'all': cls.PUBLIC,
            'local': cls.AREA, 'regional': cls.AREA, 'nearby': cls.AREA, 'zone': cls.AREA,
            'internal': cls.GROUP, 'faction': cls.GROUP, 'clan': cls.GROUP, 'sect': cls.GROUP,
            'secret': cls.PRIVATE, 'whisper': cls.PRIVATE, 'confidential': cls.PRIVATE,
            'duo': cls.PRIVATE, 'one_on_one': cls.PRIVATE, 'one-to-one': cls.PRIVATE,
            'none': cls.HIDDEN, 'invisible': cls.HIDDEN, 'solo': cls.HIDDEN,
            'inner': cls.HIDDEN, 'thought': cls.HIDDEN,
        }
        if key in en_alias:
            return en_alias[key]

        # ---- 3. 中文与近义词（含模型爱造的说法）----
        # 中文用子串是安全的：中文字符不会像英文那样拼在别的词里
        alias = {
            '公开': cls.PUBLIC, '众所周知': cls.PUBLIC, '全场': cls.PUBLIC,
            '公共': cls.PUBLIC, '所有人': cls.PUBLIC, '众目': cls.PUBLIC,
            '区域': cls.AREA, '地段': cls.AREA, '本地': cls.AREA, '附近': cls.AREA,
            '局部': cls.AREA, '一地': cls.AREA, '周边': cls.AREA,
            '群体': cls.GROUP, '组织': cls.GROUP, '势力': cls.GROUP, '内部': cls.GROUP,
            '圈内': cls.GROUP, '宗门': cls.GROUP, '门派': cls.GROUP, '同门': cls.GROUP,
            '私密': cls.PRIVATE, '私下': cls.PRIVATE, '一对一': cls.PRIVATE,
            '单独': cls.PRIVATE, '密谈': cls.PRIVATE, '两人': cls.PRIVATE,
            '悄悄': cls.PRIVATE, '低声': cls.PRIVATE, '耳语': cls.PRIVATE,
            '隐藏': cls.HIDDEN, '幕后': cls.HIDDEN, '无人知晓': cls.HIDDEN, '内心': cls.HIDDEN,
            '独自': cls.HIDDEN, '暗自': cls.HIDDEN, '心理': cls.HIDDEN,
        }
        for k, v in alias.items():
            if k in key:
                return v

        # ---- 4. 认不出 → 保守回落 ----
        # 打 WARN 且带上原值：这类值成批出现就说明提示词该改了，不能让它悄悄过去
        fallback = cls(cls.SAFE_FALLBACK)
        logger.warn(f'⚠️ scope 无法识别：{raw!r} → 保守回落 {fallback.value}'
                    f'（只给明确点名的人；模型在造枚举外的值）')
        return fallback


class EventLayer:
    """
    当前事件的状态容器。

    生命周期：L1 判定 `is_new_event=True` 时调用 `reset()`，
    LLM 给出的 scope / involved 通过 `apply()` 写入。

    :param game_clock: GameClock 实例（取起始时间用），可为 None
    """

    def __init__(self, game_clock=None):
        self.scope: Scope = Scope.PUBLIC
        self.involved: list = []            # 名单内的人（NPC 完整名）无条件获得感知
        self.direct_target: list = []       # 被直接对话/作用的对象
        self.title: str = ''
        self.started_at: str = ''
        self.rounds: int = 0                # 本事件已持续轮数
        self.game_clock = game_clock

    # ---------- 生命周期 ----------

    def reset(self, title: str = ''):
        """开启新事件。"""
        self.scope = Scope.PUBLIC
        self.involved = []
        self.direct_target = []
        self.title = title or ''
        self.rounds = 0
        self.started_at = self.game_clock.now_str() if self.game_clock else ''

    def apply(self, scope_raw=None, involved=None, direct_target=None, title: str = ''):
        """
        写入 L1 判定结果。

        `involved` 是**整份替换**而不是累加——因为调用方传进来的是
        「上一轮名单 + add − del」的结果，它本身就是权威的全量名单。
        这里要是做并集，被 `del_npc` 移出的人下一轮会自己走回来。

        `involved=None` 表示"本轮不更新名单"（区别于传空列表 = 清空）。
        """
        if scope_raw is not None:
            self.scope = Scope.parse(scope_raw, default=self.scope)
        if involved is not None:
            seen = []
            for nm in involved:
                if isinstance(nm, str) and nm.strip() and nm.strip() not in seen:
                    seen.append(nm.strip())
            self.involved = seen
        if direct_target is not None:
            self.direct_target = [n for n in direct_target if n]
        if title:
            self.title = title

    def tick(self):
        self.rounds += 1

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            'scope': self.scope.value,
            'involved': list(self.involved),
            'direct_target': list(self.direct_target),
            'title': self.title,
            'started_at': self.started_at,
            'rounds': self.rounds,
        }

    @classmethod
    def from_dict(cls, d: dict, game_clock=None) -> 'EventLayer':
        ev = cls(game_clock=game_clock)
        d = d or {}
        ev.scope = Scope.parse(d.get('scope'), default=Scope.PUBLIC)
        ev.involved = list(d.get('involved') or [])
        ev.direct_target = list(d.get('direct_target') or [])
        ev.title = d.get('title', '')
        ev.started_at = d.get('started_at', '')
        ev.rounds = int(d.get('rounds') or 0)
        return ev

    def __repr__(self):
        return f'<Event {self.scope.value} involved={len(self.involved)} rounds={self.rounds}>'
