"""
gushi4 Web 集成测试 —— **会真实调用 LLM**（跑两个回合，约 20~40 秒）。

覆盖 `web/server.py` 的全部路由 + SSE 事件流的顺序与形状。

跑法（需要先起服务）：

    cd E:/A.gushi/gushi4
    python web/server.py --port 8765          # 另开一个终端
    python src/test/web_test.py

    # 或指定地址
    python src/test/web_test.py http://127.0.0.1:9000

为什么用 Python 而不是 curl：**Windows 的 Git Bash 是 GBK**，
中文写在 `curl -d '...'` 里会被转坏，服务端收到的 JSON 直接解析失败，
表现为 `{"action":"empty"}`——一个和被测代码毫无关系的假故障。
"""

import io
import json
import os
import sys
import threading
import time

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8765').rstrip('/')

# 本脚本刻意只当**外部 HTTP 客户端**（不 import 引擎）——所以地图文件路径
# 也从自身位置推，不去 import `base_cnf`。验"没写盘"只需要这个路径。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAP_FILE = os.path.join(_ROOT, 'data', 'B1_worldState', '世界.map')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"{'  ✓' if cond else '  ✗'} {name}" + (f'   [{detail}]' if detail and not cond else ''))


# ══════════════════════════════════════════════════════════════
# SSE 订阅（后台线程，一直收）
# ══════════════════════════════════════════════════════════════

events = []
_sse_err = []


def reader():
    try:
        with requests.get(BASE + '/api/stream', stream=True, timeout=300) as r:
            r.raise_for_status()
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith('data: '):
                    continue          # ': ping' 心跳也会走到这里，跳过
                events.append(json.loads(raw[6:]))
    except Exception as e:
        _sse_err.append(f'{type(e).__name__}: {e}')


def wait_event(kind, timeout=180, since=0):
    """等一个指定类型的事件出现，返回它（或 None）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in events[since:]:
            if e.get('type') == kind:
                return e
        time.sleep(0.25)
    return None


def wait_idle(timeout=180):
    """等本轮跑完（round_done 之后 busy 变回 false）。"""
    e = wait_event('round_done', timeout=timeout, since=len(events))
    return e is not None


print('=' * 70)
print(f'gushi4 Web 集成测试 → {BASE}')
print('=' * 70)

# ── 服务可达？ ──
try:
    requests.get(BASE + '/api/state', timeout=10).raise_for_status()
except Exception as e:
    print(f'\n✗ 连不上 {BASE}：{e}')
    print('  先起服务：python web/server.py --port 8765')
    sys.exit(2)

threading.Thread(target=reader, daemon=True).start()
time.sleep(1.5)
check('SSE 已连上（收到 hello）', any(e.get('type') == 'hello' for e in events),
      _sse_err or events[:3])


# ══════════════════════════════════════════════════════════════
print()
print('=' * 70)
print('1. 只读接口')
print('=' * 70)

st = requests.get(BASE + '/api/state', timeout=10).json()
for k in ('player', 'clock', 'present_npcs', 'event', 'busy', 'can_undo'):
    check(f'/api/state 有 {k}', k in st, list(st))
check('/api/state 玩家有名有位置',
      bool(st['player']['name']) and bool(st['player']['location']), st['player'])

loc = requests.get(BASE + '/api/locations', timeout=10).json()
check('/api/locations 有 tree', isinstance(loc.get('tree'), list) and loc['tree'], list(loc))
check('/api/locations 当前位置在树里', loc.get('in_tree') is True, loc.get('current_path'))


def walk(nodes):
    for n in nodes:
        yield n
        yield from walk(n.get('children') or [])


nodes = list(walk(loc['tree']))
check('/api/locations 节点字段齐全',
      all(all(k in n for k in ('name', 'path', 'leaf', 'desc', 'children')) for n in nodes),
      nodes[0] if nodes else None)
check(f'/api/locations 节点数 {len(nodes)} > 20', len(nodes) > 20, len(nodes))

npcs = requests.get(BASE + '/api/npcs', timeout=10).json()
check('/api/npcs 有列表', isinstance(npcs.get('npcs'), list) and npcs['npcs'], list(npcs))
check('/api/npcs 每项有 short/level/location',
      all(all(k in n for k in ('short', 'level', 'location')) for n in npcs['npcs']),
      npcs['npcs'][0] if npcs['npcs'] else None)

first = npcs['npcs'][0]['name']
det = requests.get(BASE + '/api/npc/' + requests.utils.quote(first), timeout=10).json()
check('/api/npc/<name> 找得到', det.get('found') is True, det)
check('/api/npc/<name> 含 11 张表', len(det.get('tables') or {}) >= 11,
      len(det.get('tables') or {}))

miss = requests.get(BASE + '/api/npc/' + requests.utils.quote('不存在的人'), timeout=10).json()
check('/api/npc/<不存在> → found=false', miss.get('found') is False, miss)

ctx = requests.get(BASE + '/api/context/global', timeout=10).json()
check('/api/context/global 有 lines', isinstance(ctx.get('lines'), list), list(ctx))

check('/api/saves 可读', isinstance(requests.get(BASE + '/api/saves', timeout=10).json().get('saves'), list))


# ══════════════════════════════════════════════════════════════
print()
print('=' * 70)
print('2. 命令路由（不发 LLM）')
print('=' * 70)

r = requests.post(BASE + '/api/input', json={'text': ''}, timeout=10).json()
check('空输入 → empty', r['action'] == 'empty', r)

r = requests.post(BASE + '/api/input', json={'text': '/help'}, timeout=10).json()
check('/help → skip', r['action'] == 'skip', r)

r = requests.post(BASE + '/api/input', json={'text': '/npcs'}, timeout=10).json()
check('/npcs → skip', r['action'] == 'skip', r)
check('/npcs 的输出进了 SSE 事件流',
      any(e.get('type') == 'info' and '全部 NPC' in str(e.get('text')) for e in events),
      [e for e in events if e.get('type') == 'info'][-3:])

# 移动标记已退役：`{移动}` 不再是特殊语法（会当普通文本跑一轮，这里**不发**它，
# 免得白烧额度）；重点是那个端点没了。
check('★ /api/move 已退役（404）',
      requests.post(BASE + '/api/move', json={'path': 'x'}, timeout=10).status_code == 404)


# ══════════════════════════════════════════════════════════════
print()
print('=' * 70)
print('3. 一个完整回合（真实 LLM，可能要 10~30 秒）')
print('=' * 70)

mark = len(events)
r = requests.post(BASE + '/api/input',
                  json={'text': '我把落霞坪的石阶扫干净，然后抬头看天'}, timeout=15).json()
check('普通输入 → started', r['action'] == 'started', r)

done = wait_event('round_done', timeout=200, since=mark)
check('收到 round_done', done is not None, f'超时。已收：{sorted({e.get("type") for e in events[mark:]})}')

mine = events[mark:]
kinds = [e.get('type') for e in mine]
check('收到 round_start', 'round_start' in kinds, kinds[:8])
check('收到 scene（N0）', 'scene' in kinds, kinds[:8])
check('收到 narration（L3 旁白）', 'narration' in kinds, kinds)
check('收到 time（时钟推进）', 'time' in kinds, kinds)

stages = [e for e in mine if e.get('type') == 'stage' and e.get('phase') == 'end']
check(f'阶段完成事件 >= 3（实收 {len(stages)}）', len(stages) >= 3,
      [e.get('name') for e in stages])
check('每个阶段都有耗时秒数',
      all(isinstance(e.get('seconds'), (int, float)) for e in stages),
      [(e.get('name'), e.get('seconds')) for e in stages])

ticks = [e for e in mine if e.get('type') == 'stage' and e.get('phase') == 'tick']
check('有 tick 事件（进度条"还在动"的来源）', len(ticks) > 0, len(ticks))

# 事件顺序：round_start 必须排在第一个 stage 之前
check('round_start 在首个 stage 之前',
      kinds.index('round_start') < min([i for i, k in enumerate(kinds) if k == 'stage'] or [99]),
      kinds[:6])

t_ev = next((e for e in mine if e.get('type') == 'time'), None)
check('time 事件带 minutes', t_ev and isinstance(t_ev.get('minutes'), (int, float)),
      t_ev)

# 回合跑完后状态应当变了（至少 round 号 +1）
st2 = requests.get(BASE + '/api/state', timeout=10).json()
check('回合后 round 号增加', st2['round'] > st['round'], f"{st['round']} → {st2['round']}")
check('回合后可以撤回了', st2['can_undo'] is True, st2)
check('回合后 busy=false', st2['busy'] is False, st2)


# ══════════════════════════════════════════════════════════════
print()
print('=' * 70)
print('3.5 世界编辑器接口（**不调 LLM**，只走读接口与"拒绝"路径）')
print('=' * 70)
# ══════════════════════════════════════════════════════════════
# 保存的**成功**路径在 `map_edit_test.py` 里对着临时副本测（不碰真文件）。
# 这里只测两件在生产上更要紧的事：
#   1. `/api/map/edit` 给的是一份**能原样存回去**的表单（含被前端隐藏的 `产出`）；
#   2. **非法表单一个字节都不许写**——错的东西必须停在接口这里。

ed = requests.get(BASE + '/api/map/edit', timeout=15).json()
check('GET /api/map/edit 成功', ed.get('ok') is True, ed.get('reason'))
check('带上了扁平表单', isinstance(ed.get('form', {}).get('地块'), list))
check('带上了地图视图（编辑器左侧复用同一组件）',
      ed.get('view', {}).get('ok') is True)
check('★ 表单里有 `产出`（编辑器要原样，不能拿玩家视角的剥过数据回写）',
      any(f['k'] == '产出' for p in ed['form']['地块'] for f in p['字段']),
      '一条产出都没给出来')
check('开局表单自身无问题', ed.get('problems') == [], ed.get('problems'))
_nt = ed.get('notices') or []
check('带上了"键写歪了"的提醒（开局没有拼错的键）',
      all(('很像保留键' not in x and '没人读' not in x) for x in _nt), _nt)
# ⚠️ 这里原来断言"会提醒：带子地块却没写 `类型`"——**那条提醒 2026-10-10 删了**
# （地点完全可以有子节点：天道宗有 7 个）。改成盯**新接口字段**的契约：
#   `regions`（哪些节点是地区）与 `region_types`（能填哪些值）**成对出现**，
#   且 `regions` 必须正好等于"字段里写了地区类型词"的那些。
# 两个都没给＝服务端还是旧的（这套测试打的是**正在跑的那个服务**），不硬失败。
# 两个都没给＝服务端还是旧的（这套测试打的是**正在跑的那个服务**）；只有一个也算过
# （过渡期：前端两处是各自独立判的，缺哪个都不至于出错）。不硬失败，值合法才查。
_regs, _rts = ed.get('regions'), ed.get('region_types')
check('地区那两项（`regions` / `region_types`）出现的话都得是表',
      (_regs is None or isinstance(_regs, list))
      and (_rts is None or isinstance(_rts, list)), (_regs, _rts))
if isinstance(_regs, list) and isinstance(_rts, list):
    _names = {p['名'] for p in ed['form']['地块']}
    check('  值合法：`regions` 都是地块名、`region_types` 都是字符串',
          all(n in _names for n in _regs) and all(isinstance(x, str) for x in _rts),
          (_regs, _rts))
    check('★ `regions` 正好等于"字段里写了地区类型词"的那些（一个不多一个不少）',
          {p['名'] for p in ed['form']['地块']
           if any(f['k'] == '类型' and str(f['v']).strip() in set(_rts) for f in p['字段'])}
          == set(_regs), _regs)
check('表单里**路与段都有说明格**（说明是行尾 `|`，编辑器要能改）',
      all('说明' in r for r in ed['form']['路']))

before_bytes = io.open(MAP_FILE, 'rb').read()

bad = json.loads(json.dumps(ed['form'], ensure_ascii=False))
bad['地块'][0]['属于'] = '这个地不存在'
r = requests.post(BASE + '/api/map/save', json={'form': bad}, timeout=20).json()
check('非法表单被拒', r.get('ok') is False, r)
check('★ 非法表单**没有写盘**', io.open(MAP_FILE, 'rb').read() == before_bytes,
      '世界.map 被改动了！')
check('拒绝理由说得清是哪个字段',
      any('不是已知地块' in x or '不存在' in x for x in (r.get('problems') or [])),
      r.get('problems'))

r2 = requests.post(BASE + '/api/map/save', json={}, timeout=15).json()
check('没有表单也拒绝', r2.get('ok') is False)
check('空请求同样没写盘', io.open(MAP_FILE, 'rb').read() == before_bytes)

# 预览接口（**只看不写**）：编辑器左侧那张图是"开编辑器时拍的快照"，
# 表单里删掉的东西它不知道，于是删过的线还留在图上（用户报的幽灵线）。
# 这里盯两条：它是同一张图（不是另算一套），以及它**绝不写盘**。
pv = requests.post(BASE + '/api/map/preview',
                   json={'form': ed['form']}, timeout=20).json()
check('POST /api/map/preview 成功', pv.get('ok') is True, pv.get('reason'))
check('★ 原样预览与 /api/map/edit 的视图逐块一致（同一条流水线）',
      [(p['name'], p['x'], p['y']) for p in pv['view']['places']]
      == [(p['name'], p['x'], p['y']) for p in ed['view']['places']]
      and sorted(r['name'] for r in pv['view']['roads'])
      == sorted(r['name'] for r in ed['view']['roads']))
check('★ 预览**没有写盘**', io.open(MAP_FILE, 'rb').read() == before_bytes,
      '预览把世界.map 改了！')

_no_road = json.loads(json.dumps(ed['form'], ensure_ascii=False))
_gone = _no_road['路'].pop(0)['名']
pv2 = requests.post(BASE + '/api/map/preview',
                    json={'form': _no_road}, timeout=20).json()
check('★ 表单里删掉的路，预览里当场就没了',
      _gone not in [r['name'] for r in pv2['view']['roads']],
      f'{_gone} 还画在图上')
check('  删完之后仍然没写盘', io.open(MAP_FILE, 'rb').read() == before_bytes)

_ren = json.loads(json.dumps(ed['form'], ensure_ascii=False))
_old = _ren['地块'][3]['名']
_ren['地块'][3]['名'] = '改名试·接口'
pv3 = requests.post(BASE + '/api/map/preview',
                    json={'form': _ren}, timeout=20).json()
check('★ 改名：预览按新名字画',
      pv3.get('ok') and (pv3['renames'].get('地块') or {}).get(_old) == '改名试·接口',
      pv3.get('renames'))

# 几何有一行**还没写完**时：宁可不画，也不能少画一块地
# （`from_edit_form` 会把解析不出的那一条丢掉——照丢掉的画，就是"某块地凭空不见了"）
_broken = json.loads(json.dumps(ed['form'], ensure_ascii=False))
_k = list(_broken['几何']['坐标'])[0]
_broken['几何']['坐标'][_k] = '(41'
pv4 = requests.post(BASE + '/api/map/preview',
                    json={'form': _broken}, timeout=20).json()
check('★ 几何写坏一行 → 预览拒答（不让地块凭空消失）',
      pv4.get('ok') is False and '坐标' in (pv4.get('reason') or ''), pv4)

_half = json.loads(json.dumps(ed['form'], ensure_ascii=False))
_half['地块'][0]['字段'].append({'k': '', 'v': '1'})
pv5 = requests.post(BASE + '/api/map/preview',
                    json={'form': _half}, timeout=20).json()
check('  字段行没写完**不拦**（那不影响画图）', pv5.get('ok') is True, pv5.get('reason'))

check('旧接口 /api/move 已退役',
      requests.post(BASE + '/api/move', json={}, timeout=10).status_code == 404)

# 保存成功时的**回执要诚实**：以前一律回一句"已保存并热重载"，而配置里的
# `NPC_上限_*` 与 `游戏开始时间` 其实根本不生效——那是"假信心"，比报错更糟。
ed2 = requests.get(BASE + '/api/map/edit', timeout=15).json()['form']
_orig_start = ed2['世界']['游戏开始时间']
ed2['世界']['游戏开始时间'] = '太初历三千七百四十三年·孟春·卯时'
rr = requests.post(BASE + '/api/map/save', json={'form': ed2}, timeout=30).json()
check('改配置存得下去', rr.get('ok'), rr.get('problems'))
_rl = rr.get('reloaded') or {}
check('★ 回执分三类（不再是一句"已热重载"）',
      ('立即生效' in _rl) and ('需重启' in _rl), list(_rl))
check('★ 改了开局时刻要报成"需重启"',
      any('游戏开始时间' in x for x in _rl.get('需重启', [])), _rl)
check('地点/路/几何 报成"立即生效"',
      any('地点' in x for x in _rl.get('立即生效', [])), _rl)

# 改回去，别把测试的痕迹留在世界里
ed3 = requests.get(BASE + '/api/map/edit', timeout=15).json()['form']
ed3['世界']['游戏开始时间'] = _orig_start
r3 = requests.post(BASE + '/api/map/save', json={'form': ed3}, timeout=30).json()
check('改回原值', r3.get('ok'), r3.get('problems'))
check('★ 世界.map 复原（测试不留痕）',
      '游戏开始时间=' + _orig_start in io.open(MAP_FILE, encoding='utf-8').read())


# ══════════════════════════════════════════════════════════════
print()
print('=' * 70)
print('4. 移动：**打字下达去向**（真实 LLM，新机制）')
print('=' * 70)
# ══════════════════════════════════════════════════════════════
# 旧机制是"点地点树 → /api/move → 引擎直接写位置"，那条路已退役。
# 新机制：玩家打字说去哪 → L3 判 `去向`（地名）→ **代码**把它落成坐标。
# 这里验的不是"LLM 一定会照做"（那是模型行为，不能当断言），
# 而是**位置永远是一个合法地块**——不再可能出现"往真路径后面接话"那种串。

here = loc['current_path']
known = {n['path'] for n in nodes}
target = next((n for n in nodes if n['leaf'] and n['path'] != here), None)
check('找到一个可去的目标', target is not None)

if target:
    mark = len(events)
    requests.post(BASE + '/api/input',
                  json={'text': f'我动身前往{target["name"]}。'}, timeout=15)
    ok = wait_event('round_done', timeout=240, since=mark)
    check('移动回合跑完', ok is not None)
    st3 = requests.get(BASE + '/api/state', timeout=10).json()
    now = st3['player']['location']
    print(f"    从 {here}")
    print(f"    到 {now}")
    check('★ 位置仍是一个**已知地块**（漂了就说明解析出了编造的路径）', now in known, now)
    check('位置非空', bool(now), now)

print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
print('=' * 70)
if FAIL:
    print('失败项：')
    for f in FAIL:
        print(f'  ✗ {f}')
    sys.exit(1)
