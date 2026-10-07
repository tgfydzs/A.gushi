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
    cd E:/A.gushi/gushi4 && python src/test/ui_test.py

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
from common.small_tool import find_move_marker, apply_move_target


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
print('2. 移动标记：检测与换算拆成两步后行为不变')
print('=' * 70)
# ======================================================================

m = find_move_marker('我去{移动}看看')
check('检测 {移动} 占位符', m and m['kind'] == 'placeholder', m)

m = find_move_marker('/move 顺便买点药')
check('检测 /move 带后缀', m and m['kind'] == 'command' and m['suffix'] == '顺便买点药', m)

m = find_move_marker('{MOVE}')
check('检测大小写不敏感', m and m['kind'] == 'placeholder', m)

check('无标记返回 None', find_move_marker('我就站着不动') is None)
check('空输入返回 None', find_move_marker('') is None)

# 填法：占位符是原地替换，/move 是整条重写 —— 这是**原始行为**，不是新加的
mk = find_move_marker('我去{移动}看看')
text, info = apply_move_target('我去{移动}看看', mk, '苍云大陆·东域', None)
check('占位符：原地替换，前后文保留', text == '我去苍云大陆·东域看看', text)
check('占位符：move_info 的 to 正确', info['to'] == '苍云大陆·东域', info)

mk = find_move_marker('/move 买点药')
text, info = apply_move_target('/move 买点药', mk, '北域坊市', None)
check('/move：整条重写成「前往X，后缀」', text == '前往北域坊市，买点药', text)

mk = find_move_marker('/move')
text, _ = apply_move_target('/move', mk, '北域坊市', None)
check('/move 无后缀：「前往X」', text == '前往北域坊市', text)


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
check('{移动} → need_move', r['action'] == 'need_move', r)
check('need_move 带回 marker', r.get('marker', {}).get('kind') == 'placeholder', r)

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
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
print('=' * 70)
if FAIL:
    print('失败项：')
    for f in FAIL:
        print(f'  ✗ {f}')
    sys.exit(1)
