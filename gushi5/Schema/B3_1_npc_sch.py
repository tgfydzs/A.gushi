from enum import Enum, unique

from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid
from common.log import get_logger

logger = get_logger(__name__)



# ===================== 数值型表：`{"名": 数值}` =====================
#
# 历史上 `性格Personality` / `好感度Relationship` 是 `["对象名：数值", ...]` 的**字符串列表**。
# 那样"改值"必须同时做两件事（删旧串 + 加新串）——只加不删就会同时存在
# `天道宗：10` 与 `天道宗：8` 两条互相矛盾的记录，而 `cap_fields` 截断时还会把别家的关系挤掉。
# 实测模型就是"只加不删"（`删减` 里放的是改写过的句子，精确匹配不上）。
# 改成 dict 之后，"改值"就是覆盖这个键，语义唯一、可去重、可截断。
NUMERIC_TABLES = ('性格Personality', '好感度Relationship')

#: 数值型表的合法区间。越界**只告警不改值**——数据问题不该由引擎悄悄修正。
NUMERIC_RANGE = {
    '性格Personality': (0, 10),
    '好感度Relationship': (-10, 10),
}


def _split_numeric(text):
    """`"天道宗：10"` → `("天道宗", 10)`；解析不出数值时返回 `(原文, None)`。"""
    s = str(text or '').strip()
    if not s:
        return '', None
    for sep in ('：', ':'):
        if sep in s:
            name, _, raw = s.rpartition(sep)
            try:
                return name.strip(), int(float(raw.strip()))
            except (TypeError, ValueError):
                return s, None
    return s, None


def normalize_numeric_table(field: str, value):
    """
    把数值型表规范成 `{名: 数值}`，**幂等**（已经是 dict 就原样返回）。

    放在 `B3NPCLayer.__init__` 里调用——那是所有构造路径的**唯一咽喉**
    （加载 JSON / 增量重建 / 读档还原都过它），所以旧档旧数据不需要单独的迁移脚本。
    """
    if isinstance(value, dict):
        return value
    lo, hi = NUMERIC_RANGE.get(field, (None, None))
    out = {}
    for item in (value or []):
        name, num = _split_numeric(item)
        if not name:
            continue
        if num is None:
            logger.warn(f'{field} 条目解析不出数值，按原文保留：{item!r}')
            out[name] = item
            continue
        if lo is not None and not (lo <= num <= hi):
            logger.warn(f'{field}「{name}」={num} 超出 {lo}~{hi}（保留原值）')
        out[name] = num
    return out


#: `表更新成本UpdateCost` 的**固定顺序**：列表第 i 个数 = `COST_TABLE_ORDER[i]` 这张表的成本。
#: 口径见 `data_异能都市/B3_npc/npc层数据结构.md`：以 -10~10 标记"是否更容易写入表"，
#: **正数 = 越难改**（散修凡人的记忆/信念是 9~10，化神宗主的只有 3~4——凡人迟钝、修士敏锐）。
COST_TABLE_ORDER = (
    '知识记忆Memory',
    '信念Belief',
    '性格Personality',
    '生活目标Goals',
    '好感度Relationship',
    '当前状态CurrentState',
    '观察Observation',
    '对玩家观察PlayerObservation',
    '持有物Inventory',
    '待解释事件PendingReasoning',
    '表更新成本UpdateCost',
)

#: 一次结算里**受"可塑性"约束**的表：只有身份类的四张。
#: 为什么不是全部：观察/记忆/待解释是工作台，一轮写三五条是常态，用成本去掐只会
#: 把正常结算掐掉；而"这个人是否易变"说的本来就是**信念/性格/目标/好恶**。
MALLEABLE_TABLES = ('信念Belief', '性格Personality', '生活目标Goals', '好感度Relationship')


def allowed_items_by_cost(cost):
    """
    成本 → 一次结算里该表最多能改几条。`None` 表示不约束。

    分档刻意偏松（3/2/1）：这是"可塑性"的粗档位，不是精确计费。
    真把工作台也一起限流会明显削掉正常结算，所以只作用于 `MALLEABLE_TABLES`。
    """
    if cost is None:
        return None
    try:
        c = int(cost)
    except (TypeError, ValueError):
        return None
    if c < 0:
        return None          # 负数 = 特别易变，不额外限制
    if c <= 3:
        return 3
    if c <= 6:
        return 2
    return 1


class B3NPCLayer:
    """
    base3.1 单个npc层
    职能：每一个npc为一个对象，对象内部维护多个表
    表：知识记忆Memory，信念Belief， 性格Personality， 生活目标Goals， 好感度Relationship， 当前状态CurrentState， 观察Observation， 对玩家观察PlayerObservation， 持有物Inventory， 待解释事件PendingReasoning， 表更新成本UpdateCost

    注意：此类的实例由 B3NPCListLayer 统一管理，不直接对外暴露。
    """

    def __init__(self, dict: dict):
        self.name = dict['名字Name']
        self.memory = dict['知识记忆Memory']
        self.belief = dict['信念Belief']
        self.personality = normalize_numeric_table('性格Personality', dict['性格Personality'])
        self.goals = dict['生活目标Goals']
        self.relationship = normalize_numeric_table('好感度Relationship', dict['好感度Relationship'])
        self.current_state = dict['当前状态CurrentState']
        self.observation = dict['观察Observation']
        self.player_observation = dict['对玩家观察PlayerObservation']
        self.inventory = dict['持有物Inventory']
        self.pending_reasoning = dict['待解释事件PendingReasoning']
        self.update_cost = dict['表更新成本UpdateCost']

    MAX_OBSERVATION = 20
    #: **0 = 不截断**（`cap_fields` 里 `if not limit: continue`）。
    #: 知识记忆是角色的**库存**：只收"转化后的定论"，增长本来就慢。
    #: 给它设上限 = 给角色设遗忘上限——被 FIFO 挤掉的往往正是"第一次见到某人"那种根。
    #: 工作台（观察 / 待解释 / 对玩家观察）才该有上限，它们靠"查重 + 转成记忆"自净。
    MAX_MEMORY = 0
    MAX_PENDING = 10
    # 以下 4 项 gushi3 当年**没有上限**：表会无限增长，而且每轮随 to_dict() 全量喂给 LLM。
    # 上限值取自世界配置 `NPC_上限_<属性名>`，键名拼法见 CAP_MAP。
    MAX_PLAYER_OBSERVATION = 20
    MAX_BELIEF = 12
    MAX_GOALS = 10
    MAX_RELATIONSHIP = 15
    #: 性格改成 dict 之后它也能长（新增特质），给个闸。它不在世界配置里，
    #: 所以只取类默认值；以后要调，往 `世界.map` 的 `配置` 行加 `NPC_上限_MAX_PERSONALITY` 即可。
    MAX_PERSONALITY = 20

    #: 世界配置键名 `NPC_上限_<属性名>` 的拼法表（字段名 → 类属性名）。
    #: `init_all_npcs` 按它逐个覆写；`cap_fields` 按它逐个截断。
    #: ⚠️ 键名是 `NPC_上限_MAX_MEMORY` 这种形式，**不是** `NPC_记忆上限`——
    #: 旧名与代码查的键零交集，那三项从来没生效过，只因值恰好等于类默认值才没暴露。
    CAP_MAP = {
        '观察Observation': 'MAX_OBSERVATION',
        '知识记忆Memory': 'MAX_MEMORY',
        '待解释事件PendingReasoning': 'MAX_PENDING',
        '对玩家观察PlayerObservation': 'MAX_PLAYER_OBSERVATION',
        '信念Belief': 'MAX_BELIEF',
        '生活目标Goals': 'MAX_GOALS',
        '好感度Relationship': 'MAX_RELATIONSHIP',
        '性格Personality': 'MAX_PERSONALITY',
    }

    def cap_fields(self):
        """
        截断所有列表类型字段，防止无限增长。
        在每个修改入口（sync_npc_to_lists、N7传播等）后调用。

        gushi3 只截 3 张表（observation/memory/pending），另 4 张无上限无限增长——
        这里 7 张全截。上限是**类属性**，由 `init_all_npcs` 从世界配置覆写。
        """
        for attr, field, label in (
            ('MAX_OBSERVATION', 'observation', '观察'),
            ('MAX_MEMORY', 'memory', '知识记忆'),
            ('MAX_PENDING', 'pending_reasoning', '待解释事件'),
            ('MAX_PLAYER_OBSERVATION', 'player_observation', '对玩家观察'),
            ('MAX_BELIEF', 'belief', '信念'),
            ('MAX_GOALS', 'goals', '生活目标'),
            ('MAX_RELATIONSHIP', 'relationship', '好感度'),
            ('MAX_PERSONALITY', 'personality', '性格'),
        ):
            limit = getattr(self, attr, None)
            value = getattr(self, field, None)
            if not limit:
                continue
            if isinstance(value, list) and len(value) > limit:
                dropped = len(value) - limit
                setattr(self, field, value[-limit:])
            elif isinstance(value, dict) and len(value) > limit:
                # dict 型（好感度/性格）按**插入序**留后 limit 个：后加的是后认识的关系
                dropped = len(value) - limit
                setattr(self, field, dict(list(value.items())[-limit:]))
            else:
                continue
            logger.debug(f'🧹 [{self.name}] {label} 截断：丢弃{dropped}条')

    def table_cost(self, field: str):
        """
        这张表对这个 NPC 的**更新成本**（越大越难改）。取不到返回 `None`（= 不约束）。

        数据在 `表更新成本UpdateCost`（11 个数字，顺序见 `COST_TABLE_ORDER`）。
        这个字段 2026-08 设计之后一直没接消费者，`层级职能梳理.md` 里标的是"无代码读取或使用"
        ——现在由 `small_tool.apply_npc_delta` 的限流闸和 N6 的输入摘要共同消费。
        """
        cost = self.update_cost
        if not isinstance(cost, list) or field not in COST_TABLE_ORDER:
            return None
        i = COST_TABLE_ORDER.index(field)
        if i >= len(cost):
            return None
        try:
            return int(cost[i])
        except (TypeError, ValueError):
            return None

    def cost_summary(self) -> dict:
        """`{表名: 成本}`，只给**受约束的身份四表**——喂给 N6 让它知道这人有多难说动。"""
        out = {}
        for field in MALLEABLE_TABLES:
            c = self.table_cost(field)
            if c is not None:
                out[field] = c
        return out

    def to_dict(self) -> dict:
        return {
            '名字Name': self.name,
            '知识记忆Memory': self.memory,
            '信念Belief': self.belief,
            '性格Personality': self.personality,
            '生活目标Goals': self.goals,
            '好感度Relationship': self.relationship,
            '当前状态CurrentState': self.current_state,
            '观察Observation': self.observation,
            '对玩家观察PlayerObservation': self.player_observation,
            '持有物Inventory': self.inventory,
            '待解释事件PendingReasoning': self.pending_reasoning,
            '表更新成本UpdateCost': self.update_cost
        }
