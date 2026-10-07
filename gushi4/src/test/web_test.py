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

import json
import sys
import threading
import time

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8765').rstrip('/')

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

r = requests.post(BASE + '/api/input', json={'text': '我去{移动}看看'}, timeout=10).json()
check('{移动} → need_move', r['action'] == 'need_move', r)
check('need_move 带回 raw 和 marker', bool(r.get('raw')) and r.get('marker'), r)


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
print('4. 地点树移动（真实 LLM）')
print('=' * 70)

# 挑一个和当前位置不同的叶节点
here = loc['current_path']
target = next((n for n in nodes if n['leaf'] and n['path'] != here), None)
check('找到一个可移动的目标', target is not None)

if target:
    mark = len(events)
    r = requests.post(BASE + '/api/move',
                      json={'path': target['path']}, timeout=15).json()
    check('/api/move 启动回合', r.get('ok') is True, r)
    check('move 返回换算好的文本', '前往' in (r.get('text') or ''), r)
    check('move_info 的 to 是目标路径',
          (r.get('move') or {}).get('to') == target['path'], r.get('move'))

    ok = wait_event('round_done', timeout=200, since=mark)
    check('移动回合跑完', ok is not None)

    st3 = requests.get(BASE + '/api/state', timeout=10).json()
    print(f"    位置：{st3['player']['location']}")
    print(f"    目标：{target['path']}")
    check('位置确实动了',
          st3['player']['location'] != here, st3['player']['location'])


# ══════════════════════════════════════════════════════════════
print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
print('=' * 70)
if FAIL:
    print('失败项：')
    for f in FAIL:
        print(f'  ✗ {f}')
    sys.exit(1)
