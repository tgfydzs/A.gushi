"""
UI/引擎层离线测试 —— **不发起任何 LLM 调用**。

覆盖引擎抽取（`common/engine.py`）的验收点：

  1. **终端渲染保真**——`terminal_renderer` / `terminal_output` 输出的字节，
     必须与重构前 `main.py` / `game_controller.py` 里那些 `print` **完全一致**。
     这是"引擎抽取"那一步唯一的验收标准。
  2. 事件流机制（`_emit` / `subscribe`）
  3. 移动标记的检测与换算（拆成两步后行为不变）
  4. `handle_input` 的路由
  5. 只读视图（`scene_view` / `location_tree` / `npc_detail`）的形状

跑法：
    cd <项目根> && python src/test/ui_test.py

⚠️ 与 `smoke_test.py` 同类：构造 `GameEngine` 会加载世界与 NPC 数据，但**不发请求**。
"""

import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    mark = '  ✓' if cond else '  ✗'
    print(f'{mark} {name}' + (f'   [{detail}]' if detail and not cond else ''))


def capture(fn, *a, **kw):
    """跑 fn，返回它打到 stdout 的内容。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


from common.engine import terminal_renderer
from common.progress import terminal_output, Stage


# ======================================================================
print('=' * 70)
print('1. 终端渲染保真 —— 与重构前的 print 逐字节对比')
print('=' * 70)
# ======================================================================
#
# 右列是重构前 main.py / game_controller.py 里的原始语句，逐条对照。

# 场景（原 N0_显示场景，main.py:74-81）
got = capture(terminal_renderer, {'type': 'scene', 'location': '苍云大陆·东域·落霞坪',
                                  'present': ['白鹤（天道宗外门长老，道号）'], 'clock': '午时'})
want = '\n📍 苍云大陆·东域·落霞坪\n👥 在场：白鹤（天道宗外门长老，道号）\n⏰ 午时\n'
check('scene 有人：逐字节一致', got == want, repr(got))

got = capture(terminal_renderer, {'type': 'scene', 'location': 'X', 'present': [], 'clock': 'T'})
want = '\n📍 X\n👥 （周围无人）\n⏰ T\n'
check('scene 无人：逐字节一致', got == want, repr(got))

# 对白（原 main.py:211 `print(f'\n{tag}：{text}')`）
got = capture(terminal_renderer, {'type': 'dialogue', 'speaker': '白鹤', 'text': '嗯。'})
check('dialogue：逐字节一致', got == '\n白鹤：嗯。\n', repr(got))

# 旁白（原 main.py:311 与 game_controller.py:269，同一条语句）
got = capture(terminal_renderer, {'type': 'narration', 'text': '风起。'})
check('narration：逐字节一致', got == '\033[3m  风起。\033[0m\n', repr(got))

# 幕后简讯（原 game_controller.py:286 `print(f'  \033[2m· {joined}\033[0m')`）
got = capture(terminal_renderer, {'type': 'dim', 'text': '· 🌍 某事  |  🎬 甲有所行动'})
check('dim：逐字节一致', got == '  \033[2m· 🌍 某事  |  🎬 甲有所行动\033[0m\n', repr(got))

# 时间（原 main.py:353，注意 `:.0f` 取整）
got = capture(terminal_renderer, {'type': 'time', 'text': '未时', 'minutes': 50.4})
check('time：逐字节一致（含 .0f 取整）', got == '\n⏰ [游戏时间 未时（+50分）]\n', repr(got))

# 普通信息
got = capture(terminal_renderer, {'type': 'info', 'text': '↩ 已撤回上一条'})
check('info：逐字节一致', got == '↩ 已撤回上一条\n', repr(got))

# stage 事件**不该**被渲染器重复打印（Stage 自己负责终端那一行）
got = capture(terminal_renderer, {'type': 'stage', 'phase': 'end', 'name': 'L1', 'seconds': 2.5})
check('stage 事件不被渲染器打印（避免重复）', got == '', repr(got))

# sink 侧的三件套（GameController._out 的默认渲染）
check('terminal_output narration 一致', capture(terminal_output, 'narration', '风起。')
      == '\033[3m  风起。\033[0m\n')
check('terminal_output dim 一致', capture(terminal_output, 'dim', '· 甲')
      == '  \033[2m· 甲\033[0m\n')
check('terminal_output info 一致', capture(terminal_output, 'info', '你好') == '你好\n')


# ======================================================================
print()
print('=' * 70)
print('2. 移动标记已退役（地图成为主输入）')
print('=' * 70)
# ======================================================================
# 原来这一节验 `find_move_marker` / `apply_move_target` 的检测与换算。
# 移动标记存在的理由是"让前端给引擎一个树上的合法路径"；位置改由
# **L3 出 `去向`（地名）+ 代码落坐标**之后，那个理由没有了 → 三个函数一并删除。

from common import small_tool

check('★ find_move_marker 已删除', not hasattr(small_tool, 'find_move_marker'))
check('★ apply_move_target 已删除', not hasattr(small_tool, 'apply_move_target'))
check('★ resolve_move_markers 已删除', not hasattr(small_tool, 'resolve_move_markers'))
check('引擎模块里不再有移动换算的名字',
      'find_move_marker' not in dir(__import__('common.engine', fromlist=['x'])))

# ======================================================================
print()
print('=' * 70)
print('3. 事件流机制')
print('=' * 70)
# ======================================================================

from common.engine import GameEngine

engine = GameEngine(session_id='uitest')

q = engine.subscribe()
seen_sync = []
engine.attach(seen_sync.append)

terminal_renderer_events = []
engine._emit({'type': 'info', 'text': '甲'})

ev = q.get_nowait()
check('subscribe 收到事件', ev['text'] == '甲', ev)
check('事件带 round 字段', 'round' in ev, ev)
check('attach 的同步渲染器也收到', len(seen_sync) == 1, seen_sync)

engine.unsubscribe(q)
engine._emit({'type': 'info', 'text': '乙'})
check('退订后不再收到', q.empty())

# GameController 的 print 走 sink 进到事件流
sink_events = []
engine.attach(sink_events.append)
engine.gc._out('narration', '旁白测试')
check('GameController._out 并入事件流',
      any(e.get('text') == '旁白测试' and e['type'] == 'narration' for e in sink_events),
      sink_events)

engine.attach(None)


# ======================================================================
print()
print('=' * 70)
print('4. handle_input 路由')
print('=' * 70)
# ======================================================================

check('空输入 → empty', engine.handle_input('   ')['action'] == 'empty')
check('普通输入 → run', engine.handle_input('我看看四周')['action'] == 'run')
check('run 的 text 是原文', engine.handle_input('我看看四周')['text'] == '我看看四周')

check('exit → break', engine.handle_input('exit')['action'] == 'break')

r = engine.handle_input('!!')
check('!!（无快照）→ skip', r['action'] == 'skip')

r = engine.handle_input('/help')
check('/help → skip', r['action'] == 'skip')

r = engine.handle_input('/player')
check('/player → skip', r['action'] == 'skip')

r = engine.handle_input('/npcs')
check('/npcs → skip', r['action'] == 'skip')

r = engine.handle_input('我去{移动}看看')
# 移动标记退役后：`{移动}` 不再是特殊语法，当普通文本走一轮（见第 2 节）
r = engine.handle_input('我去{移动}看看')
check('★ {移动} 已退役 → 按普通输入 run', r['action'] == 'run', r)
check('handle_input 不再吐 move_info / marker', 'move_info' not in r and 'marker' not in r, r)

# ⚠️ 记录一个**既有 bug**（重构前就存在，本次刻意原样保留）：
# `/move` 会先被 gc.handle_command 的 else 分支当成"未知命令"接走并返回 'continue'，
# 于是永远走不到移动逻辑。`{移动}`（不以 / 开头）才有效。
r = engine.handle_input('/move')
check('已知 bug：/move 被当成未知命令吃掉（重构前即如此）',
      r['action'] == 'skip', r)


# ======================================================================
print()
print('=' * 70)
print('5. 只读视图（UI 的数据来源）')
print('=' * 70)
# ======================================================================

sv = engine.scene_view()
for key in ('round', 'player', 'clock', 'present_npcs', 'event', 'can_undo'):
    check(f'scene_view 有 {key}', key in sv, list(sv))

check('scene_view.player 有 status/inventory',
      'status' in sv['player'] and 'inventory' in sv['player'], sv['player'].keys())
check('scene_view 玩家名字正确',
      sv['player']['name'] == engine.player_name, sv['player']['name'])
check('scene_view 位置非空', bool(sv['player']['location']), sv['player']['location'])
check('scene_view 时钟是字符串', isinstance(sv['clock']['text'], str), sv['clock'])
check('scene_view present_npcs 是 list', isinstance(sv['present_npcs'], list))
check('scene_view npc_total 与列表一致',
      sv['npc_total'] == len(engine.gc.all_npc_list), sv['npc_total'])

lt = engine.location_tree()
check('location_tree 有 tree/current_path', 'tree' in lt and 'current_path' in lt, list(lt))
check('location_tree 根节点非空', len(lt['tree']) > 0)
check('location_tree 节点字段齐全',
      all(k in lt['tree'][0] for k in ('name', 'path', 'leaf', 'desc', 'children')),
      lt['tree'][0])

def _count(nodes):
    return sum(1 + _count(n['children']) for n in nodes)

total_nodes = _count(lt['tree'])
check('location_tree 节点数 > 20', total_nodes > 20, total_nodes)

# 当前位置在树里吗（预设世界应当在内）
check('当前位置能在树里找到', lt['in_tree'], lt['current_path'])

first_npc = engine.gc.all_npc_list.npc_list[0]
nd = engine.npc_detail(first_npc.name)
check('npc_detail 找得到', nd['found'] is True, nd)
check('npc_detail 含 11 张表', len(nd.get('tables', {})) >= 11, len(nd.get('tables', {})))
check('npc_detail 不存在的名字返回 found=False',
      engine.npc_detail('这个人不存在').get('found') is False)

check('list_npcs 数量与 NPC 列表一致',
      len(engine.list_npcs()) == len(engine.gc.all_npc_list))

ctx = engine.contexts('global')
check('contexts 返回 lines 列表', isinstance(ctx.get('lines'), list), ctx.keys())

check('list_saves 返回 list', isinstance(engine.list_saves(), list))


# ======================================================================
print()
print('=' * 70)
print('6. 生命值体系：数值 / 文本 可切')
print('=' * 70)

from common.L3_settlement import L3_Settlement, _HEALTH_CLAUSE_NUMERIC


class _FakeWorld:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_config(self, key, default=None):
        return self._cfg.get(key, default)


l3 = L3_Settlement()

check('未绑世界时默认走数值',
      l3._health_clause() == _HEALTH_CLAUSE_NUMERIC and '生命' in l3._health_clause())

l3.set_world(_FakeWorld({'生命值体系': '数值'}))
check('配置=数值 → 追加条款', l3._health_clause() == _HEALTH_CLAUSE_NUMERIC)

l3.set_world(_FakeWorld({'生命值体系': '文本'}))
check('配置=文本 → 不追加（退回原行为）', l3._health_clause() == '')

l3.set_world(_FakeWorld({}))
check('配置缺失 → 兜底走数值', l3._health_clause() == _HEALTH_CLAUSE_NUMERIC)

# 条款里提到"等级差就打不动"只能是**为了禁止它**——否则越级战斗就被程序化了
clause = _HEALTH_CLAUSE_NUMERIC
check('条款明确禁止"等级差"式判定规则',
      '这类规则' in clause and '不要' in clause)
check('条款写明数值只记录结果、不参与判定', '只记录结果' in clause)
check('条款保留了越级战斗的可能', '修为低的人完全可以打伤修为高的人' in clause)

# 真实世界配置已启用
engine.l3_layer.set_world(engine.gc.b1WorldLayer)
check('引擎已给 L3 注入世界层', engine.l3_layer.world_layer is not None)
check('当前世界配置=数值',
      engine.gc.b1WorldLayer.get_config('生命值体系', '数值') == '数值',
      engine.gc.b1WorldLayer.get_config('生命值体系', '默认缺省'))

# 玩家初始数据里有 生命/灵力
pst = engine.gc.player.player_dict.get('当前状态CurrentState', {})
check('玩家初始数据含 生命', isinstance(pst.get('生命'), int), pst.get('生命'))
check('玩家初始数据含 灵力', isinstance(pst.get('灵力'), int), pst.get('灵力'))
check('玩家初始数据含 体力状态', bool(pst.get('体力状态')), pst.get('体力状态'))

sv2 = engine.scene_view()
check('scene_view 透出 生命/灵力',
      sv2['player']['status'].get('生命') == 100 and sv2['player']['status'].get('灵力') == 100,
      sv2['player']['status'])


# ======================================================================
print()
print('=' * 70)
print('7. 叙事流历史（页面 F5 之后靠它恢复）')
print('=' * 70)

from common.engine import parse_context_line, STREAM_EVENT_TYPES, HISTORY_LIMIT

p = parse_context_line('3 | 太初历·午时 | 旁白：风起了')
check('旁白行 → narration', p and p['type'] == 'narration' and p['text'] == '风起了', p)

p = parse_context_line('1 | T | 叶尘（玩家）：我走过去')
check('玩家行 → round_start', p and p['type'] == 'round_start' and p['input'] == '我走过去', p)
check('玩家行带回 who', p and p.get('who') == '叶尘', p)

p = parse_context_line('2 | T | 白鹤（天道宗外门长老，道号）：嗯。')
check('他人行 → dialogue', p and p['type'] == 'dialogue' and p['speaker'].startswith('白鹤'), p)

p = parse_context_line('4 | T | 叶尘（移动）：从A前往B')
check('移动行 → info', p and p['type'] == 'info' and '移动' in p['text'], p)

p = parse_context_line('5 | T | 世界事件（初期）：某某')
check('世界事件 → info', p and p['type'] == 'info', p)

check('格式不对 → None', parse_context_line('没有分隔符') is None)
check('空内容 → None', parse_context_line('1 | T | ') is None)

check('stage 不在入史类型里', 'stage' not in STREAM_EVENT_TYPES)
check('round_done 不在入史类型里', 'round_done' not in STREAM_EVENT_TYPES)
check('dialogue/narration/scene 在', {'dialogue', 'narration', 'scene'} <= STREAM_EVENT_TYPES)

engine.gc.global_context.context[:] = [
    '1 | T | 叶尘（玩家）：你好',
    '2 | T | 白鹤（天道宗外门长老，道号）：嗯。',
    '3 | T | 旁白：风起了',
]
engine.hydrate_history()
types = [e['type'] for e in engine.history()]
# 有历史时**不**补 scene（真跑过一轮的记录里本来就有，补了会重复）
check('有历史时不补 scene', 'scene' not in types, types)
check('history 重建出三条', types == ['round_start', 'dialogue', 'narration'], types)

before = len(engine._history)
engine.emit({'type': 'stage', 'phase': 'tick', 'name': 'x', 'seconds': 1})
check('stage 不入史（进度条是瞬时的）', len(engine._history) == before)
engine.emit({'type': 'dialogue', 'speaker': '甲', 'text': '乙'})
check('dialogue 入史', len(engine._history) == before + 1)
check('入史事件带 ts', engine._history[-1].get('ts') is not None, engine._history[-1])

engine.gc.global_context.context[:] = []
engine.hydrate_history()
check('清空后 history 仍有 scene（空局也显示你在哪）',
      [e['type'] for e in engine.history()] == ['scene'])


# ======================================================================
print()
print('=' * 70)
print('8. 位置：坐标是真源、路径是派生；L3 只写「去向」')
print('=' * 70)
# ======================================================================

from common.L3_settlement import sanitize_objective_delta

_w = engine.gc.b1WorldLayer
_st = engine.gc.player.player_dict['当前状态CurrentState']
_npcs = engine.gc.all_npc_list.npc_list

check('玩家状态里有坐标 `位置`', isinstance(_st.get('位置'), list) and len(_st['位置']) == 2, _st.get('位置'))
check('★ 派生路径与坐标一致（不会各说各话）',
      _st.get('当前所处位置') == _w.path_at(_st),
      f"{_st.get('当前所处位置')} vs {_w.path_at(_st)}")
check('NPC 也迁到了坐标',
      bool(_npcs) and all(isinstance((n.current_state or {}).get('位置'), list) for n in _npcs),
      [n.name for n in _npcs if not isinstance((n.current_state or {}).get('位置'), list)][:3])

_ok = engine.gc.set_position(_st, name='青木山脉')
check('set_position(名字) 能落坐标', _ok and _st['当前所处位置'].endswith('青木山脉'), _st.get('当前所处位置'))
check('落点后派生路径同步更新', _st['当前所处位置'] == _w.path_at(_st))
check('不存在的名字 → 不动（宁可原地，也不要假位置）',
      engine.gc.set_position(_st, name='地图上不存在的地方') is False)

_d = sanitize_objective_delta({'增加': {'当前状态CurrentState': {
    '位置': [1, 2], '当前所处位置': '某个编出来的路径', '生命': 90}}, '删减': {}})
_inc = _d['增加'].get('当前状态CurrentState', {})
check('★ L3 写 `位置` 被丢弃', '位置' not in _inc, _inc)
check('★ L3 写 `当前所处位置` 被丢弃', '当前所处位置' not in _inc, _inc)
check('同一增量里的合法字段照常放行', _inc.get('生命') == 90, _inc)

engine.gc.apply_l3_settlement({'去向': '东域坊市·灵虚集'})
check('★ 去向是地名 → 代码把它落成坐标',
      engine.gc.player_location.endswith('灵虚集'), engine.gc.player_location)
engine.gc.apply_l3_settlement({'去向': '一个不存在的地方'})
check('去向解析不出 → 位置不动（不编造）',
      engine.gc.player_location.endswith('灵虚集'), engine.gc.player_location)
engine.gc.apply_l3_settlement({'去向': ''})
check('去向为空 → 也不动', engine.gc.player_location.endswith('灵虚集'))
engine.gc.set_position(_st, name='外门弟子修炼区·落霞坪')
check('复原出生点', engine.gc.player_location.endswith('落霞坪'), engine.gc.player_location)

# ======================================================================
print()
print('=' * 70)
print('9. 热重载：真生效 + 说清楚哪一类（原来是一句笼统的"已重载"）')
print('=' * 70)
# ======================================================================
from Schema.B3_npclist_sch import B3NPCLayer
from common import map_io as M

_real_cfg = dict(engine.gc.b1WorldLayer.dict.get('世界配置') or {})
_key = 'NPC_上限_MAX_MEMORY'
_before_cap = B3NPCLayer.MAX_MEMORY

# 模拟"编辑器把上限从 30 改成 7 并保存"
engine.gc.b1WorldLayer.dict['世界配置'][_key] = 7
engine.gc.apply_config_caps()
check('★ 上限是**类属性**，改配置后必须重跑覆写才生效',
      B3NPCLayer.MAX_MEMORY == 7, f'还是 {B3NPCLayer.MAX_MEMORY}')

# 还原类属性，免得影响后面的用例
engine.gc.b1WorldLayer.dict['世界配置'] = _real_cfg
engine.gc.apply_config_caps()
check('还原后类属性也回来了', B3NPCLayer.MAX_MEMORY == _before_cap, B3NPCLayer.MAX_MEMORY)

# `reload_world` 的三分类：说清"哪些立刻生效、哪些要重启"
_r = engine.gc.reload_world()
check('reload_world 给的是三分类', '立即生效' in _r and '需重启' in _r, list(_r))
check('地点/路/几何 归"立即生效"',
      any('地点' in x for x in _r['立即生效']), _r['立即生效'])
# 这两个是顶层键、**不在 `世界配置` 里**——只比配置的话会被算成"没有变动"
_d = engine.gc.b1WorldLayer.dict
_saved_start = _d.get('游戏开始时间')
_d['游戏开始时间'] = '某个别的时刻'
_r2 = engine.gc.reload_world()
check('★ 改了开局时刻会被报成"需重启"（它是顶层键，不在世界配置里）',
      any('游戏开始时间' in x for x in _r2['需重启']), _r2)
_d['游戏开始时间'] = _saved_start

# ======================================================================
print()
print('=' * 70)
print('10. 开新游戏 / 手动重载（顶栏那两个按钮走的就是这两条命令）')
print('=' * 70)
# ======================================================================
# ⚠️ 放在**最后**：`/new` 把这一局的运行期状态整个换掉，跑在中间会污染后面所有断言。

r = engine.handle_input('/reload')
check('/reload → skip', r['action'] == 'skip')
check('★ /reload 之后世界还在（不是空地图）',
      len(engine.gc.b1WorldLayer.list_location_paths()) > 0)

# 先把状态弄"脏"，再看 `/new` 是不是真回到开局
engine.round_no = 7
engine.gc.all_npc_list.new_list()                      # 故意清空名册
_npcs_before = len(engine.gc.all_npc_list)
engine.gc.deferred_queue.put({'source': 'test', 'NPC自主行为': {}})

r = engine.handle_input('/new')
check('/new → skip', r['action'] == 'skip')
check('★ 轮次归零', engine.round_no == 0, engine.round_no)
check('★ 撤回快照清掉（新局不该还能撤回上一局）', engine.last_snap is None)
check('★ NPC 名册**重建**（不是两局叠一起，也不是空）',
      len(engine.gc.all_npc_list) > 0 and len(engine.gc.all_npc_list) != _npcs_before,
      f'{_npcs_before} → {len(engine.gc.all_npc_list)}')
check('★ 后台产出队列倒掉（上一局的产出与新的世界不兼容）',
      engine.gc.deferred_queue.size == 0, engine.gc.deferred_queue.size)

# 这一条是 `/new` 最容易漏的地方：`init_world` 建的是**新对象**，
# 各层把世界层存进了自己的字段——不重新接线就会拿着上一局的世界跑，且不报错。
check('★ 各层接到了**新的**世界层上（这条漏了就是"地图改了没生效"）',
      engine.r2_time.world_layer is engine.gc.b1WorldLayer
      and engine.r4_opp.world_layer is engine.gc.b1WorldLayer)
check('★ 后台管理器接到的是**当前这个**队列',
      engine.bg_mgr.queue is engine.gc.deferred_queue)
check('★ 玩家的持有物回到初始档（不是上一局攒的）',
      engine.gc.player.player_dict.get('持有物Inventory') is not None)

# ======================================================================
print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
print('=' * 70)
if FAIL:
    print('失败项：')
    for f in FAIL:
        print(f'  ✗ {f}')
    sys.exit(1)
