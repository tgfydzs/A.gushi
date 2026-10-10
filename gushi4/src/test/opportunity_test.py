"""
机缘导演（R4）测试 —— **不消耗 API 额度**。

覆盖三层：

  1. **纯逻辑**：地点 → 产出表定位、清洗、输出契约
  2. **节流与上限**：首次只记时间、窗口消耗、每日件数上限、配置坏值回落
  3. **接线闭合**：用假 LLM 跑**完整一轮**，验机缘真的进了 L3 的输入与条款，
     且 R4 自己**不写任何状态**（客观写入口只有 L3，D5/D12）

为什么要单独一套：R4 是唯一"世界主动给玩家东西"的通道。它一旦越权写状态，
交易一致性（`apply_l3_settlement` 的双方一起变或一起不变）就破了，
而那种破坏**不会报错**，只会让存档慢慢漂——所以这条得由测试盯住。

跑法：
    cd <项目根> && python src/test/opportunity_test.py
"""

import os
import sys
import json
from copy import deepcopy

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"  ✓" if cond else "  ✗"} {name}' + (f'   [{detail}]' if detail and not cond else ''))


# ═══════════════════ 测试替身 ═══════════════════

class Clock:
    """最小时钟：R4 只读 `_elapsed`。"""
    def __init__(self, minutes=0):
        self._elapsed = minutes


class Rng:
    """确定性掷骰源：`random()` 恒返一个定值 → 全中（0.0）或全不中（0.99）。"""
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.value


class FakeWorld:
    """只实现 R4 用到的那两个接口，用来构造真实世界数据里没有的形状。"""
    def __init__(self, chain=None, config=None):
        self._chain = chain or []
        self._config = config or {}
        self.chain_calls = 0

    def get_location_context(self, location):
        self.chain_calls += 1
        return self._chain

    def get_config(self, key, default=None):
        return self._config.get(key, default)


class FakeCLI:
    """按 layer 返回预设 JSON，并抄下 L3 收到的 user 输入与 system prompt。"""

    def __init__(self, layer, canned, calls=None):
        self.layer = layer
        self.canned = canned
        self.calls = calls if calls is not None else {}
        self.cfg = type('C', (), {'layer': layer, 'model': 'fake', 'thinking': False,
                                  'timeout': 0, 'max_retries': 1, 'temperature': None,
                                  'describe': lambda s: 'fake'})()

    def chat_by_context(self, context, label='', temperature=None):
        key = self.layer
        self.calls[key] = self.calls.get(key, 0) + 1
        try:
            payload = json.loads(context[-1]['content'])
        except Exception:
            payload = {}
        if key == 'L3':
            self.calls.setdefault('L3_input', []).append(payload)
            self.calls.setdefault('L3_sys', []).append(
                context[0].get('content', '') if context else '')
        return json.dumps(self.canned, ensure_ascii=False)

    def chat(self, *a, **k):
        return self.chat_by_context([], self.layer)


# ═══════════════════ 真实世界数据 ═══════════════════

from common.R4_opportunity import R4_Opportunity, DEFAULT_INTERVAL_HOURS
from common.game_controller import GameController
from common import log as log_cfg

log_cfg.setup(session_id='opttest', console_min_score=40)

gc = GameController('opttest')
gc.init_world()
gc.init_clock()
gc.init_player()
W = gc.b1WorldLayer

MTN = '苍云大陆·东域·青木山脉'                       # 有产出（4 条，含一条带条件）
MARKET = '苍云大陆·东域·东域坊市·灵虚集'             # 有产出（5 条，无条件）
MEADOW = '苍云大陆·东域·天道宗·外门弟子修炼区·落霞坪'  # 无产出
DOTTED = '苍云大陆·东域·星辰阁·跨界传送阵·星枢殿'     # 键名含 "·"，只有一条产出

print('=' * 70)
print('机缘导演（R4）')
print('=' * 70)

# ═══════════════════ 1. 地点 → 产出表 ═══════════════════
print('\n1. 产出表定位与清洗')

r4 = R4_Opportunity(W, rng=Rng(0.99))

t_mtn = r4.loot_table(MTN)
check('青木山脉取到 4 条产出', len(t_mtn) == 4, str(t_mtn))
check('其中含 灵草', any(x['物品'] == '灵草' for x in t_mtn))
check('带条件的条目保留了 条件',
      any(x.get('条件') == '深入山脉腹地' for x in t_mtn), str(t_mtn))
check('loot_table 是**内部表**，带着概率（roll() 才是对外的唯一出口）',
      all('概率' in x for x in t_mtn), str(t_mtn))

check('灵虚集取到 5 条产出', len(r4.loot_table(MARKET)) == 5)
check('落霞坪（无产出）→ 空表', r4.loot_table(MEADOW) == [])
check('不存在的路径 → 空表', r4.loot_table('苍云大陆·根本没有这个地') == [])
check('空路径 → 空表', r4.loot_table('') == [])
check('键名含 "·" 的节点也能取到（最长匹配）',
      [x['物品'] for x in r4.loot_table(DOTTED)] == ['传送符引'],
      str(r4.loot_table(DOTTED)))
check('没绑世界层 → 空表，不炸', R4_Opportunity(None).loot_table(MTN) == [])

# —— 链上取最深的带产出节点 ——
deep_chain = [{'层级': '父', '属性': {'产出': [{'物品': '父之物', '概率': 1}]}},
              {'层级': '子', '属性': {'产出': [{'物品': '子之物', '概率': 1}]}}]
shallow_chain = [{'层级': '父', '属性': {'产出': [{'物品': '父之物', '概率': 1}]}},
                 {'层级': '子', '属性': {}}]
nothing_chain = [{'层级': '父', '属性': {}}, {'层级': '子', '属性': {}}]
check('父子都有产出 → 取子（最深）',
      R4_Opportunity(FakeWorld(deep_chain)).loot_table('x')[0]['物品'] == '子之物')
check('只有父有产出 → 取父（产出对子地点成立）',
      R4_Opportunity(FakeWorld(shallow_chain)).loot_table('x')[0]['物品'] == '父之物')
check('整条链都没有产出 → 空表',
      R4_Opportunity(FakeWorld(nothing_chain)).loot_table('x') == [])
check('坏链（元素不是 dict / 属性不是 dict）不炸',
      R4_Opportunity(FakeWorld([None, {'层级': 'a'}, {'层级': 'b', '属性': '不是dict'}])
                     ).loot_table('x') == [])

# —— 清洗 ——
dirty = [{'物品': '好的', '概率': 0.5},
         {'物品': '概率为0', '概率': 0},
         {'物品': '概率超1', '概率': 9},
         {'物品': '', '概率': 0.5},
         {'物品': '非数字概率', '概率': 'abc'},
         {'概率': 0.5},
         '不是dict',
         {'物品': ' 带空格的 ', '概率': 0.25, '条件': ' 深处的 '}]
clean = R4_Opportunity._clean(dirty)
check('清洗：丢弃 概率为0 / 超范围外的无效条目',
      [x['物品'] for x in clean] == ['好的', '概率超1', '带空格的'], str(clean))
check('清洗：概率夹到 ≤1', clean[1]['概率'] == 1.0, str(clean[1]))
check('清洗：物品名 strip 后仍保留', clean[2]['物品'] == '带空格的', str(clean[2]))
check('清洗：条件 strip', clean[2]['条件'] == '深处的', str(clean[2]))

# ═══════════════════ 2. 节流与上限 ═══════════════════
print('\n2. 节流与每日上限')

# —— 首次只记时间（防"读档即爆"）——
# 间隔一律从常量推，不写死 6 小时：默认值是按实测改过的（见 R4 模块顶部），
# 写死数字的测试会在改默认值时集体翻车——那正是它该防的"漂"。
r4 = R4_Opportunity(W, rng=Rng(0.0))          # 必中
IV = r4._interval_minutes()
c = Clock(0)
check('首次调用不掷骰（只记时间）', r4.roll(MTN, c) == [])
check('首次确实记下了时间', r4._last_elapsed == 0, str(r4._last_elapsed))
c._elapsed = IV - 1
check('差一分钟到间隔 → 空', r4.roll(MTN, c) == [], f'IV={IV}')
c._elapsed = IV
hits = r4.roll(MTN, c)
check('到间隔 → 出东西', bool(hits), str(hits))
check('★ 单次窗口受每日额度封顶（默认 2 件，不是表里的 4 条）',
      len(hits) == 2, str(hits))
check('★ roll() 的产出只有 物品/条件 —— 概率读完就丢',
      all(set(x) <= {'物品', '条件'} for x in hits), str(hits))

# —— 中与不中都消耗窗口 ——
# 注意：一次掷骰会对表里**每一条**各调一次 rng（逐条独立判定），
# 所以"掷了几个窗口"要按 rng.calls / len(表) 算，不是看调用次数本身。
PER_ROLL = len(t_mtn)
rng = Rng(0.99)                               # 必不中
r4 = R4_Opportunity(W, rng=rng)
IV = r4._interval_minutes()
c = Clock(0)
r4.roll(MTN, c)                               # 首次记时间，不掷
check('首次不掷骰（一次都没掷）', rng.calls == 0, str(rng.calls))
c._elapsed = IV
check('必不中 → 空', r4.roll(MTN, c) == [])
check('窗口确实掷了一遍（没中也要消耗）', rng.calls == PER_ROLL, str(rng.calls))
c._elapsed = IV + 1
check('没到下一个间隔 → 不再掷',
      r4.roll(MTN, c) == [] and rng.calls == PER_ROLL, str(rng.calls))
c._elapsed = IV * 2
r4.roll(MTN, c)
check('下一个窗口到了 → 又掷一遍', rng.calls == PER_ROLL * 2, str(rng.calls))

# —— 每日上限按件数算，跨日清零 ——
r4 = R4_Opportunity(W, rng=Rng(0.0))
IV = r4._interval_minutes()
c = Clock(0)
r4.roll(MTN, c)                               # 首次
c._elapsed = IV
first = r4.roll(MTN, c)
c._elapsed = 12 * 60
second = r4.roll(MTN, c)
check('今日两件之后额度用尽', (len(first), second) == (2, []), f'{first} | {second}')
check('今日计数 = 2', r4._day_hits == 2, str(r4._day_hits))
c._elapsed = 24 * 60
check('★ 次日额度恢复', len(r4.roll(MTN, c)) == 2, str(r4._day_hits))

# —— 上限 0 = 关掉机缘 ——
off = R4_Opportunity(FakeWorld([{'层级': 'x', '属性': {'产出': [{'物品': '甲', '概率': 1}]}}],
                               config={'机缘_每日上限': 0}), rng=Rng(0.0))
cc = Clock(0)
off.roll('x', cc)
cc._elapsed = 24 * 60
check('机缘_每日上限 = 0 → 永远不出东西', off.roll('x', cc) == [])

# —— 配置坏值回落 ——
bad = R4_Opportunity(FakeWorld(config={'机缘_间隔_小时': '不是数字',
                                       '机缘_每日上限': None}))
check('间隔坏值回落默认值（跟着常量走，不写死数字）',
      bad._interval_minutes() == int(DEFAULT_INTERVAL_HOURS * 60), str(bad._interval_minutes()))
check('上限坏值回落 2', bad._daily_cap() == 2, str(bad._daily_cap()))
# 下限是 1 分钟（不是 1 小时）：设成 0 等于"每轮都掷"，那时唯一的闸门是每日上限。
# 这个是刻意的可调性，不是漏保护——但必须有下限，否则 0/负数会退化成无节流。
check('间隔下限 1 分钟（0 小时 → 每轮都掷，闸门交给每日上限）',
      R4_Opportunity(FakeWorld(config={'机缘_间隔_小时': 0}))._interval_minutes() == 1)
check('间隔负值也落在下限上',
      R4_Opportunity(FakeWorld(config={'机缘_间隔_小时': -5}))._interval_minutes() == 1)
check('间隔 0.5 小时 = 30 分钟',
      R4_Opportunity(FakeWorld(config={'机缘_间隔_小时': 0.5}))._interval_minutes() == 30)

# —— 无产出地点不建状态 ——
r4 = R4_Opportunity(W, rng=Rng(0.0))
c = Clock(5 * 60)
check('无产出地点掷不出东西', r4.roll(MEADOW, c) == [])
check('★ 无产出地点也不留节流状态（到了有产出的地方应当从头计时）',
      r4._last_elapsed is None, str(r4._last_elapsed))

# —— 逐条独立判定 ——
ind = R4_Opportunity(FakeWorld([{'层级': 'x', '属性': {'产出': [
    {'物品': '必得', '概率': 1.0}, {'物品': '必不得', '概率': 0.0}]}}]),
    rng=Rng(0.5))
cc = Clock(0)
ind.roll('x', cc)
cc._elapsed = 24 * 60
check('逐条独立：p=1 出、p=0 不出',
      [x['物品'] for x in ind.roll('x', cc)] == ['必得'],
      str(ind.roll('x', cc)))

# —— R4 不碰任何状态 ——
r4 = R4_Opportunity(W, rng=Rng(0.0))
c = Clock(0)
r4.roll(MTN, c)
before = deepcopy(gc.player.player_dict)
c._elapsed = 6 * 60
r4.roll(MTN, c, gc.player)
check('★ R4 掷骰后玩家状态一字未动（它无权写客观状态）',
      gc.player.player_dict == before, '玩家字典被改了')

# ═══════════════════ 3. 接线闭合（假 LLM 跑一整轮）═══════════════════
print('\n3. 接线闭合（GameEngine + 假 LLM）')

from common.engine import GameEngine
from common.L3_settlement import L3_Settlement

engine = GameEngine(session_id='opttest')
# 心跳会另起线程真调 API——测试里直接掐掉
engine.bg_mgr.try_trigger = lambda **kw: False

check('引擎挂上了机缘导演', isinstance(getattr(engine, 'r4_opp', None), R4_Opportunity))
check('机缘导演绑到了世界层', engine.r4_opp.world_layer is engine.gc.b1WorldLayer)

# —— L1：无事件、无 NPC（走到 L3 独处分支）——
l1_canned = {'is_new_event': False, 'scope': 'private', '感知分发': {},
             '地段传播': {}, 'add_npc': [], 'del_npc': [],
             'add_related_npc': [], 'del_related_npc': []}
# —— L3：什么都不改（只为看输入）——
l3_noop = {'玩家实例': {'增加': {}, '删减': {}}, 'NPC实例': {}, '旁白': [],
           '本轮耗时分钟': 3, '破裂观察': {}}

CALLS = {}
engine.l1_layer.cli = FakeCLI('L1', l1_canned, CALLS)
engine.l3_layer.cli = FakeCLI('L3', l3_noop, CALLS)

# 把玩家挪到一个有产出的地方（落霞坪没有产出表）。
# ⚠️ 必须走 `set_position`——`位置` 是坐标（真源），直接写 `当前所处位置`
# 这句**不再决定位置**（它只是派生），而且绕过唯一写入点。
engine.gc.set_position(engine.gc.player.player_dict['当前状态CurrentState'], name=MTN)
engine.r4_opp._rng = Rng(0.0)                 # 必中

inv_before = deepcopy(engine.gc.player.player_dict['持有物Inventory'])

# 第 1 轮：首次，只记时间
engine.run_round('我在山里转了转。')
check('第 1 轮没有机缘 → L3 输入里**没有** 本轮机缘 字段',
      '本轮机缘' not in CALLS['L3_input'][0], str(list(CALLS['L3_input'][0])))
check('第 1 轮 system prompt 里**没有**机缘条款',
      '本轮机缘（`本轮机缘` 非空时才出现）' not in CALLS['L3_sys'][0])
check('第 1 轮持有物不变（R4 没写东西）',
      engine.gc.player.player_dict['持有物Inventory'] == inv_before)

# 第 2 轮：把时钟推过间隔
engine.gc.game_clock._elapsed = 7 * 60
engine.run_round('我继续在溪边找。')
l3_in = CALLS['L3_input'][1]
check('★ 命中后 本轮机缘 进了 L3 输入',
      isinstance(l3_in.get('本轮机缘'), list) and len(l3_in['本轮机缘']) == 2,
      str(l3_in.get('本轮机缘')))
check('★ 机缘条目只有 物品/条件，没有 概率',
      all(set(x) <= {'物品', '条件'} for x in l3_in.get('本轮机缘', [])),
      str(l3_in.get('本轮机缘')))
check('★ system prompt 里出现了机缘条款（且只在有货时出现）',
      '本轮机缘（`本轮机缘` 非空时才出现）' in CALLS['L3_sys'][1])
check('★ 条款把「条件」写成硬门槛',
      '硬门槛' in CALLS['L3_sys'][1] and '不得落地' in CALLS['L3_sys'][1])
check('R4 命中本身不写状态（L3 交白卷 → 持有物仍不变）',
      engine.gc.player.player_dict['持有物Inventory'] == inv_before,
      str(list(engine.gc.player.player_dict['持有物Inventory'])))

# —— L3 若照条款落地，链路闭合 ——
# 让额度恢复（等价于"次日"）并把时钟推过间隔，这一轮必须真的带机缘——
# 否则"闭合"这条根本没被验到，只是看假 L3 白送了一件东西。
engine.r4_opp._day_hits = 0
engine.gc.game_clock._elapsed += 6 * 60
grant = {'玩家实例': {'增加': {'持有物Inventory': {'灵草': '三株，叶片还带着露水'}},
                      '删减': {}}, 'NPC实例': {}, '旁白': ['你在背阴的石缝里看到几株灵草。'],
         '本轮耗时分钟': 3, '破裂观察': {}}
engine.l3_layer.cli = FakeCLI('L3', grant, CALLS)
res = engine.run_round('我把那几株灵草采下来。')
check('★ 结算前 L3 确实收到了机缘（这条闭合才算验过）',
      bool(CALLS['L3_input'][-1].get('本轮机缘')),
      str(CALLS['L3_input'][-1].get('本轮机缘')))
inv = engine.gc.player.player_dict['持有物Inventory']
check('★ 机缘经 L3 落地：玩家持有物里真的多了那件东西',
      inv.get('灵草') == '三株，叶片还带着露水', str(inv.get('灵草')))
check('旁白也随结算出来了',
      any('灵草' in n for n in res.get('narration', [])), str(res.get('narration')))

# —— 有 NPC 的分支同样吃 opportunities ——
l3_direct = L3_Settlement()
CAPTURE = {}
l3_direct.cli = FakeCLI('L3', l3_noop, CAPTURE)
l3_direct.settle_with_npcs(gc.player, [], [], '说话',
                           location=MTN,
                           opportunities=[{'物品': '灵草', '条件': '深入山脉腹地'}])
check('settle_with_npcs 也把机缘写进输入',
      CAPTURE['L3_input'][0].get('本轮机缘') ==
      [{'物品': '灵草', '条件': '深入山脉腹地'}], str(CAPTURE['L3_input'][0].get('本轮机缘')))
check('settle_with_npcs 的 system prompt 带了条款',
      '本轮机缘（`本轮机缘` 非空时才出现）' in CAPTURE['L3_sys'][0])

# —— 概率不进前端：出口（不是浏览器）就该剥掉 ——
# `app.js` 的 ATTR_HIDDEN 只拦得住渲染，拦不住任何人直接 GET /api/locations。
# 所以后端出口也要剥——但剥的只是**出口**，世界层照旧持有数据（R4 要靠它掷骰）。
def _node_by_path(nodes, path):
    for n in nodes:
        if n.get('path') == path:
            return n
        hit = _node_by_path(n.get('children') or [], path)
        if hit:
            return hit
    return None


mtn_node = _node_by_path(engine.location_tree()['tree'], MTN)
check('前端视角的地点树能取到青木山脉', mtn_node is not None)
check('★ 前端出口里没有 `产出`（概率不给玩家看，也不给好奇的人）',
      '产出' not in (mtn_node.get('attrs') or {}), str(mtn_node.get('attrs')))
check('但正常属性照常出去（灵气/危险/功能…）',
      bool(mtn_node.get('attrs')), str(mtn_node.get('attrs')))
check('★ 世界层仍持有 `产出`（剥的是出口，不是数据）',
      bool(W.get_location_attrs(MTN).get('产出')))

# ═══════════════════ 结果 ═══════════════════
print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
if FAIL:
    print('失败项：')
    for n in FAIL:
        print(f'  ✗ {n}')
print('=' * 70)
sys.exit(1 if FAIL else 0)
