"""
地段功能 —— 地点树归一化与属性查询（**不消耗 API 额度**）

要防的失败模式只有一个，但后果很重：

    地点节点升级后每个节点都能带 `_属性`。只要**有一个**遍历器漏掉保留键过滤，
    `_属性` 就会被当成一个叫"_属性"的地点，`苍云大陆·东域·_属性` 进而进入
    L3 的「已知地点」参考列表——而 L3 完全可能把玩家的位置写成这串。
    位置一漂，之后所有距离计算都跟着漂，**且不会报任何错**。

所以下面第 2、3、6 组断言的核心都是同一条：**保留键不能出现在任何遍历结果里**。

第四组是回归对照：往真实地点树的**每一层**注入 `_属性`，所有遍历器的输出
必须与注入前逐字一致（只有 attrs 是新东西）。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

from common.game_controller import GameController          # noqa: E402
from Schema.B1_worldState_sch import B1WorldLayer           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    mark = '  ✓' if cond else '  ✗'
    print(f'{mark} {name}' + (f'   [{detail}]' if detail and not cond else ''))


def has_reserved(path):
    """路径里有没有保留字段段（`_` 开头）。"""
    return any(seg.startswith('_') for seg in path.split('·'))


print('=' * 70)
print('1. normalize_node —— 唯一形状判定点')
print('=' * 70)

# 老写法：叶节点是字符串
a, d, k = B1WorldLayer.normalize_node('炼气期弟子修炼之所')
check('字符串叶 → 无属性', a == {}, a)
check('字符串叶 → 描述即该字符串', d == '炼气期弟子修炼之所', d)
check('字符串叶 → 无子地点', k == {}, k)

# 新写法：属性 + 描述 + 子地点
a, d, k = B1WorldLayer.normalize_node(
    {'_属性': {'灵气': '浓郁'}, '描述': '主城', '子地': {'a': 'x'}})
check('对象 → 取出 _属性', a == {'灵气': '浓郁'}, a)
check('对象 → 取出 描述', d == '主城', d)
check('对象 → 子地点不含 _属性/描述', set(k) == {'子地'}, list(k))

# 只有属性、没有子地点 —— 这是个**叶节点**，不是枝节点
a, d, k = B1WorldLayer.normalize_node({'_属性': {'灵气': '稀薄'}})
check('只有属性 → 仍是叶（无子地点）', k == {}, k)
check('只有属性 → 属性保留', a == {'灵气': '稀薄'}, a)

# 属性写坏了（不是 dict）不该炸
a, d, k = B1WorldLayer.normalize_node({'_属性': '这是坏数据', '甲': 'x'})
check('_属性 非 dict → 当空处理，不炸', a == {}, a)
check('_属性 非 dict → 子地点照常', set(k) == {'甲'}, list(k))

print()
print('=' * 70)
print('2. 合成树：保留键绝不进入路径')
print('=' * 70)

SYNTH = {'世界地点列表': {
    '根': {
        '_属性': {'灵气': '浓'},
        '甲': {'_属性': {'危险': '低'}, '甲1': '叶描述'},
        '乙': '叶',
        '丙': {'_属性': {'只有': '属性'}},
    }
}}
sw = B1WorldLayer(SYNTH)
paths = sw.list_location_paths(max_depth=9)

check('路径数正确（根/甲/甲1/乙/丙）', len(paths) == 5, paths)
check('路径里没有保留字段段', not any(has_reserved(p) for p in paths), paths)
check('没有"_属性"这个地点', not any('_属性' in p for p in paths), paths)
check('只有属性的节点算叶（丙不展开）',
      '根·丙' in paths and not any(p.startswith('根·丙·') for p in paths), paths)

print()
print('=' * 70)
print('3. get_location_children 排除保留键')
print('=' * 70)

check('根的子地点 = 甲乙丙（无 _属性）',
      set(sw.get_location_children(['根']) or {}) == {'甲', '乙', '丙'},
      list(sw.get_location_children(['根']) or {}))
check('甲的子地点 = 甲1', set(sw.get_location_children(['根', '甲']) or {}) == {'甲1'},
      list(sw.get_location_children(['根', '甲']) or {}))
check('老写法字符串叶 → 无子地点（None）',
      sw.get_location_children(['根', '乙']) is None)
check('只有属性的叶 → 空字典而非 None',
      sw.get_location_children(['根', '丙']) == {},
      sw.get_location_children(['根', '丙']))
check('路径不存在 → None', sw.get_location_children(['不存在']) is None)
check('空路径 → 根层级', set(sw.get_location_children([]) or {}) == {'根'},
      list(sw.get_location_children([]) or {}))

print()
print('=' * 70)
print('4. 属性查询')
print('=' * 70)

check('get_location_attrs 只给自身属性',
      sw.get_location_attrs('根·甲') == {'危险': '低'},
      sw.get_location_attrs('根·甲'))
check('get_location_attrs 老叶 → 空', sw.get_location_attrs('根·乙') == {})
check('get_location_attrs 不存在 → 空', sw.get_location_attrs('没有·这个') == {})

chain = sw.get_location_context('根·甲·甲1')
check('属性链层级数 = 3', len(chain) == 3, chain)
check('属性链按根→叶排列', [c['层级'] for c in chain] == ['根', '甲', '甲1'],
      [c['层级'] for c in chain])
check('属性链第 2 层带属性', chain[1]['属性'] == {'危险': '低'}, chain[1])
check('没有属性的层也保留（叶层）', chain[2]['属性'] == {}, chain[2])
check('**不做继承**：叶层不继承父层属性',
      '危险' not in chain[2]['属性'], chain[2])

print()
print('=' * 70)
print('5. 真实世界数据：老写法零回归')
print('=' * 70)

gc = GameController('loctest')
gc.init_world()
w = gc.b1WorldLayer

real_paths = w.list_location_paths(max_depth=9)
real_root = set(w.get_location_children([]) or {})
check(f'真实地点树路径非空（{len(real_paths)} 条）', len(real_paths) > 20, len(real_paths))
check('真实数据里没有保留字段段', not any(has_reserved(p) for p in real_paths),
      [p for p in real_paths if has_reserved(p)])
_with_attrs = sum(1 for p in real_paths if w.get_location_attrs(p))
check(f'真实数据已写 _属性（{_with_attrs}/{len(real_paths)} 个节点）',
      _with_attrs == len(real_paths),
      [p for p in real_paths if not w.get_location_attrs(p)])
check('叶节点属性可取到且含「功能」',
      '功能' in w.get_location_attrs('苍云大陆·东域·天道宗·外门弟子修炼区·落霞坪'),
      w.get_location_attrs('苍云大陆·东域·天道宗·外门弟子修炼区·落霞坪'))
check('资源型地段带「产出」表',
      len(w.get_location_attrs('苍云大陆·东域·青木山脉').get('产出') or []) > 0,
      w.get_location_attrs('苍云大陆·东域·青木山脉'))
check('产出条目形如 {物品, 概率}',
      all('物品' in e and '概率' in e
          for p in real_paths
          for e in (w.get_location_attrs(p).get('产出') or [])),
      [(p, e) for p in real_paths
       for e in (w.get_location_attrs(p).get('产出') or [])
       if '物品' not in e or '概率' not in e][:3])
check('产出概率都在 0~1 之间',
      all(0.0 < e['概率'] <= 1.0
          for p in real_paths
          for e in (w.get_location_attrs(p).get('产出') or [])),
      [(p, e) for p in real_paths
       for e in (w.get_location_attrs(p).get('产出') or [])
       if not (0.0 < e['概率'] <= 1.0)][:3])

print()
print('=' * 70)
print('6. 回归对照：注入 _属性 到每一层，遍历输出必须逐字不变')
print('=' * 70)


def inject_attrs(node):
    """把字符串叶升级成带属性的对象，并给每个枝节点挂 _属性。"""
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        out[k] = inject_attrs(v) if isinstance(v, dict) else {
            '描述': v, '_属性': {'注入': 'X'}}
    out['_属性'] = {'注入': 'X'}
    return out


tree = w.get_location_tree()
injected = {root: inject_attrs(sub) for root, sub in tree.items()}
w2 = B1WorldLayer({'世界地点列表': injected})

after_paths = w2.list_location_paths(max_depth=9)
check('注入后路径与注入前逐字一致', after_paths == real_paths,
      f'前{len(real_paths)}条 后{len(after_paths)}条')
check('注入后路径仍无保留字段段',
      not any(has_reserved(p) for p in after_paths),
      [p for p in after_paths if has_reserved(p)])
check('注入后根层子地点一致',
      set(w2.get_location_children([]) or {}) == real_root,
      list(w2.get_location_children([]) or {}))
check('注入后属性可读出', w2.get_location_attrs('苍云大陆·东域') == {'注入': 'X'},
      w2.get_location_attrs('苍云大陆·东域'))

# ⚠️ 这条是**往返一致性**：键名本身含 `·` 的叶节点，`list_location_paths`
# 吐出来的路径，`get_location_attrs` 必须解得回去。按 `·` 逐段匹配会够不着它们。
DOTTED = '苍云大陆·中州·上古试炼秘境·万象洞天'
check('含 · 的键：list 吐得出', DOTTED in after_paths,
      [p for p in after_paths if '万象洞天' in p])
check('含 · 的键：get_attrs 解得回（往返一致）',
      w2.get_location_attrs(DOTTED) == {'注入': 'X'},
      w2.get_location_attrs(DOTTED))
check('含 · 的键：属性链按整键算一层',
      [c['层级'] for c in w2.get_location_context(DOTTED)][-1] == '上古试炼秘境·万象洞天',
      [c['层级'] for c in w2.get_location_context(DOTTED)])
check('含 · 的键：链长 = 3（苍云大陆/中州/上古试炼秘境·万象洞天）',
      len(w2.get_location_context(DOTTED)) == 3,
      len(w2.get_location_context(DOTTED)))

print()
print('=' * 70)
print('7. 引擎视图：attrs 到位，节点数不因属性暴涨')
print('=' * 70)

from common.engine import GameEngine                        # noqa: E402

engine = GameEngine(session_id='loctest')
lt = engine.location_tree()


def flatten(nodes):
    for n in nodes:
        yield n
        yield from flatten(n['children'])


before_nodes = list(flatten(lt['tree']))
check(f'节点数合理（{len(before_nodes)}）', 20 < len(before_nodes) < 60, len(before_nodes))
check('每个节点都有 attrs 字段',
      all(isinstance(n.get('attrs'), dict) for n in before_nodes),
      [n['name'] for n in before_nodes if 'attrs' not in n][:3])
check('没有节点名叫 _属性（否则就是遍历器漏了）',
      not any(n['name'].startswith('_') for n in before_nodes),
      [n['name'] for n in before_nodes if n['name'].startswith('_')])
check('叶节点仍带描述',
      sum(1 for n in before_nodes if n['desc']) == sum(1 for n in before_nodes if n['leaf']),
      f"有描述{sum(1 for n in before_nodes if n['desc'])} 叶{sum(1 for n in before_nodes if n['leaf'])}")

# 换一棵带属性的树再建引擎视图，节点数必须一模一样
# ⚠️ 要改的是 **engine 自己的** gc——上面那个 gc 是另一个对象
engine.gc.b1WorldLayer = w2
lt2 = engine.location_tree()
after_nodes = list(flatten(lt2['tree']))
check('注入属性后引擎视图节点数不变',
      len(after_nodes) == len(before_nodes),
      f'{len(before_nodes)} → {len(after_nodes)}')
check('注入属性后视图里也没冒出 _属性 节点',
      not any(n['name'].startswith('_') for n in after_nodes))
check('视图里 attrs 拿到了值',
      any(n['attrs'] for n in after_nodes),
      sum(1 for n in after_nodes if n['attrs']))

print()
print('=' * 70)
print('8. 属性真正进了 L1 / L3 的输入（不然就是死数据）')
print('=' * 70)

from common.L1_perception import L1_Perception               # noqa: E402
from common.L3_settlement import L3_Settlement               # noqa: E402

_cap = {}


class _CaptureCLI:
    """把该层发给 LLM 的 user payload 抄下来（契约同 dry_run 的 FakeCLI）。"""

    def __init__(self, key):
        self.k = key
        self.cfg = type('C', (), {'layer': key, 'model': 'fake', 'thinking': True,
                                  'timeout': 0, 'max_retries': 1,
                                  'temperature': None, 'describe': lambda s: 'fake'})()

    def chat_by_context(self, context, label='', temperature=None):
        try:
            _cap[self.k] = json.loads(context[-1]['content'])
        except Exception:
            _cap[self.k] = {}
        return '{}'


# 单独建一个**全量初始化**的控制器——上面那个 gc 只 init_world，
# 没有 player / game_clock；而且 section 7 把 engine 的世界层换成了注入过的树。
_gc = GameController('loctest2')
_gc.init_world()
_gc.init_clock()
_gc.init_all_npcs()
_gc.init_player()

loc = _gc.player_location
check('探针玩家的位置就是落霞坪',
      loc.endswith('落霞坪'), loc)          # 前提：世界数据的默认出生点

_l1 = L1_Perception()
_l1.set_world(_gc.b1WorldLayer)
_l1.cli = _CaptureCLI('L1')
_l1.judge(event_context=_gc.event_context, event_layer=_gc.event_layer,
          all_npc_list=_gc.all_npc_list, player=_gc.player,
          director_context=_gc.director_context, world_layer=_gc.b1WorldLayer,
          player_input='我在落霞坪上喊了一声')

check('L1 输入带「当前地段」', '当前地段' in _cap.get('L1', {}), list(_cap.get('L1', {})))
_l1_chain = _cap.get('L1', {}).get('当前地段') or []
check('L1 的地段链从根排到当前地点',
      [c['层级'] for c in _l1_chain][-1] == '外门弟子修炼区·落霞坪',
      [c['层级'] for c in _l1_chain])
check('L1 的地段链带上了真实属性',
      _l1_chain and _l1_chain[-1]['属性'].get('功能'),
      _l1_chain[-1] if _l1_chain else None)

_l3 = L3_Settlement()
_l3.set_world(_gc.b1WorldLayer)
_l3.cli = _CaptureCLI('L3')
_l3.settle_solo(player=_gc.player, player_input='我在此打坐', location=loc,
                game_time=_gc.game_clock.now_str())
check('L3 独处输入带「当前地段」', '当前地段' in _cap.get('L3', {}), list(_cap.get('L3', {})))
check('L3 的地段链层数与 L1 一致',
      len(_cap.get('L3', {}).get('当前地段') or []) == len(_l1_chain),
      f"L3={len(_cap.get('L3', {}).get('当前地段') or [])} L1={len(_l1_chain)}")

# 世界层没绑定时必须不炸（ui_test 的 _FakeWorld 只有 get_config）
_l3_bare = L3_Settlement()
check('L3 未绑世界时地段链退化为空，不炸',
      _l3_bare._location_context('苍云大陆·东域') == [])


class _BareWorld:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_config(self, key, default=None):
        return self._cfg.get(key, default)


_l3_bare.set_world(_BareWorld({}))
check('L3 绑了没有地段接口的假世界也不炸',
      _l3_bare._location_context('苍云大陆·东域') == [])

print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
if FAIL:
    print('失败项：')
    for f in FAIL:
        print(f'  ✗ {f}')
print('=' * 70)
sys.exit(1 if FAIL else 0)
