"""
设计方案 §八 · 阶段 1 推理消融 —— 逐层关推理，一次只关一层。

    组       L1    L2    L3
    基线     开    开    开
    实验1    关    开    开
    实验2    开    关    开
    实验3    开    开    关
    全关     关    关    关

跑法：
    python src/test/run_ablation.py                 # 默认每组 1 遍
    python src/test/run_ablation.py --repeat 3      # 每组 3 遍，报告取中位数
    python src/test/run_ablation.py --report-only   # 只从缓存重新出报告

产出：`src/test/ablation_report.md`
逐轮产物留档：`src/test/ablation_runs/<组>/rep<N>/round_XXXX/`

# 为什么必须 --repeat ≥ 3

第一轮实验跑了 5 组 × 5 轮，同配置两遍的墙钟是 **181s 和 321s（差 77%）**。
把单层耗时按 (均, 最慢, 次数) 反推，长尾形状是：

    基线 L3：4 次约 11s + **1 次 101.7s（9.4×）**     ← 一次独飙，不是整体慢
    基线 L1：4 次约 15.8s + 1 次 39.3s（2.5×）

**推理开着时，墙钟由一两次长尾调用主导。** 所以：

  · 均值没意义，要看**中位数**和**最慢值**
  · n=1 的结论就是噪声

# 质量怎么量（§八 要求，按重要性排序）

| # | 检查项 | 怎么自动量 |
|---|--------|-----------|
| 1 | **NPC 有没有说出它不该知道的** | 把 NPC **说出口的话**与它**收到的感知**比对：话里的"隐藏状态词"（玩家持有物名）不在感知里，就算一次越界嫌疑。对白越界比内心越界严重 |
| 2 | **回应有没有套路化** | ① 套路词频 ② 组内两两 Jaccard 相似度 ③ 平均长度 |
| 3 | **状态变更自不自洽** | 交易破裂时双方持有物有没有变 |

另外单列一条 **L1 失真 vs 复述**（见 report 第二节补）——它比越界探针灵敏，
但**只有在场观察者与玩家修为相近时才有判别力**。

⚠️ 这些都是**代理指标**，有误报也有漏报。真判断要看
`ablation_runs/<组>/rep<N>/round_*/perception_audit.md`。
"""

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENARIO = os.path.join(ROOT, 'src', 'test', 'scenarios', '消融剧本.txt')
SCENARIO_PEER = os.path.join(ROOT, 'src', 'test', 'scenarios', '近修为剧本.txt')
LOG_PATH = os.path.join(ROOT, 'log', 'gushi4.log')
DEBUG_DIR = os.path.join(ROOT, 'debug_rounds')
REPORT = os.path.join(ROOT, 'src', 'test', 'ablation_report.md')
RUNS_DIR = os.path.join(ROOT, 'src', 'test', 'ablation_runs')
CACHE = os.path.join(ROOT, 'src', 'test', 'ablation_raw.json')

GROUPS = [
    ('基线', ''),
    ('实验1-L1关', 'L1'),
    ('实验2-L2关', 'L2'),
    ('实验3-L3关', 'L3'),
    ('全关', 'ALL'),
]

CLICHES = ['深不可测', '蝼蚁', '不知死活', '聒噪', '找死', '不自量力', '米粒之珠',
           '萤火之光', '井底之蛙', '班门弄斧', '不知天高地厚', '蚍蜉撼树',
           '狂妄', '放肆', '孽障', '竖子', '可笑', '有点意思']


# ═══════════════════ 跑一遍 ═══════════════════

_LOG_OFFSET = 0


def _clear_debug():
    """
    重置本轮统计的起点。

    **不删日志文件。** 原来用 `os.remove`，结果在 Windows 上撞过一次
    `PermissionError: [WinError 32]`——上一次实验的残留进程还开着这个文件，
    整个 30 分钟的实验直接崩在第一行。

    删文件本来也不是必须的：**main.py 启动时会 `log.setup(file_append=False)`，
    自己就把日志截断重写了**，所以跑完之后整份日志就是本轮的内容，从头读即可。
    （删除需要没有进程持有句柄，截断不需要——这就是 `os.remove` 会炸而跑起来没事的原因。）

    ⚠️ 曾经试过"记住文件大小、只读新增部分"，那是错的：main.py 会先把文件
    截断成 0 再从 0 写起，于是"偏移量"永远大于新文件大小，**一行都读不到**，
    所有层都显示 0.0s。所以这里老老实实从头读。
    """
    global _LOG_OFFSET
    _LOG_OFFSET = 0

    for d in os.listdir(DEBUG_DIR) if os.path.isdir(DEBUG_DIR) else []:
        p = os.path.join(DEBUG_DIR, d)
        if os.path.isdir(p):
            for f in os.listdir(p):
                try:
                    os.remove(os.path.join(p, f))
                except OSError:
                    pass
            try:
                os.rmdir(p)
            except OSError:
                pass


def run_once(name: str, thinking_off: str, rep: int, scenario: str) -> dict:
    label = f'{name} 第{rep}遍'
    print(f'\n{"=" * 70}\n▶ {label}   G4_THINKING_OFF={thinking_off or "(全开)"}'
          f'   剧本={os.path.basename(scenario)}\n{"=" * 70}')

    _clear_debug()
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    if thinking_off:
        env['G4_THINKING_OFF'] = thinking_off

    t0 = time.time()
    with open(scenario, 'r', encoding='utf-8') as fin:
        proc = subprocess.run([sys.executable, os.path.join(ROOT, 'main.py')],
                              stdin=fin, cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding='utf-8',
                              errors='replace')
    wall = time.time() - t0

    keep = os.path.join(RUNS_DIR, name, f'rep{rep}')
    shutil.rmtree(keep, ignore_errors=True)
    if os.path.isdir(DEBUG_DIR):
        shutil.copytree(DEBUG_DIR, keep)

    result = {
        'name': name,
        'rep': rep,
        'thinking_off': thinking_off or '(全开)',
        'scenario': os.path.basename(scenario),
        'wall_sec': wall,
        'returncode': proc.returncode,
        'layers': parse_log(),
        'quality': quality_metrics(keep),
    }
    if proc.returncode != 0:
        print(f'⚠️ 退出码 {proc.returncode}')
        print(proc.stderr[-1500:])
    L = result['layers']
    print(f'✔ {label}：墙钟 {wall:.0f}s | L1 {L.get("L1", {}).get("avg_sec", 0):.1f}s '
          f'| L3 {L.get("L3", {}).get("avg_sec", 0):.1f}s '
          f'| L3最慢 {L.get("L3", {}).get("max_sec", 0):.1f}s')
    return result


def parse_log() -> dict:
    """从日志里抓本轮新增部分的每层耗时/token。

    只读 `_LOG_OFFSET` 之后的内容——见 `_clear_debug` 为什么不删日志。
    """
    pat = re.compile(r'\[LLM\] (?P<layer>\S+?) \[[^\]]*\] 完成 \| '
                     r'(?P<sec>[\d.]+)s \| tokens: (?P<tok>.+)$')
    agg = defaultdict(lambda: {'n': 0, 'sec': 0.0, 'in': 0, 'out': 0, 'reason': 0,
                               'max_sec': 0.0, 'calls': []})
    fails = 0

    if not os.path.isfile(LOG_PATH):
        agg['_fails'] = 0
        return dict(agg)

    with open(LOG_PATH, encoding='utf-8', errors='replace') as f:
        f.seek(_LOG_OFFSET)
        for line in f:
            if '请求失败' in line:
                fails += 1
            m = pat.search(line)
            if not m:
                continue
            k = re.match(r'([A-Z]+\d*[a-c]?)', m.group('layer'))
            a = agg[k.group(1) if k else m.group('layer')]
            sec = float(m.group('sec'))
            a['n'] += 1
            a['sec'] += sec
            a['max_sec'] = max(a['max_sec'], sec)
            a['calls'].append(sec)
            for fld in ('in', 'out', 'reason'):
                mm = re.search(rf'{fld}=(\d+)', m.group('tok'))
                if mm:
                    a[fld] += int(mm.group(1))

    for a in agg.values():
        a['avg_sec'] = a['sec'] / a['n'] if a['n'] else 0.0
    agg['_fails'] = fails

    # 兜底自检：一轮里 L1/L2/L3 都必然被调用过。哪个是 0 就说明日志没读到，
    # 数字全是 0.0s——**这种"假绿"比报错更危险**，所以直接喊出来。
    silent = [k for k in ('L1', 'L2', 'L3') if agg.get(k, {}).get('n', 0) == 0]
    if silent:
        print(f'⚠️⚠️ 日志里没读到 {silent} 的调用记录——本次耗时数据无效，'
              f'（别拿去比较，先查日志是不是被别的东西截断了）')

    return dict(agg)


# ═══════════════════ 质量指标 ═══════════════════

def _load_player() -> dict:
    world = json.load(open(os.path.join(ROOT, 'data', 'B1_worldState', 'global.json'),
                           encoding='utf-8'))
    sel = None
    with open(os.path.join(ROOT, 'common', 'base_cnf.py'), encoding='utf-8') as f:
        for line in f:
            m = re.match(r"\s*WORLD_DATA_SELECT\s*=\s*['\"](.+?)['\"]", line)
            if m:
                sel = m.group(1)
                break
    pf = world[sel].get('世界配置', {}).get('玩家初始数据')
    return json.load(open(os.path.join(ROOT, pf), encoding='utf-8'))


def quality_metrics(run_dir: str) -> dict:
    l2_all, l1_all = [], []
    for d in sorted(os.listdir(run_dir) if os.path.isdir(run_dir) else []):
        rdir = os.path.join(run_dir, d)
        for fn, bucket in (('l2_output.json', l2_all), ('l1_output.json', l1_all)):
            p = os.path.join(rdir, fn)
            if not os.path.isfile(p):
                continue
            try:
                v = json.load(open(p, encoding='utf-8'))
                bucket.extend(v if isinstance(v, list) else [v])
            except Exception:
                pass

    # ---- #2 套路化 ----
    texts = [(r.get('回应Response') or '').strip() for r in l2_all]
    texts = [t for t in texts if t]
    cliche_hits = defaultdict(int)
    for t in texts:
        for c in CLICHES:
            if c in t:
                cliche_hits[c] += 1
    sims = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a, b = set(texts[i]), set(texts[j])
            if a and b:
                sims.append(len(a & b) / len(a | b))

    # ---- #1 信息隔离（代理） ----
    probes, probe_err = set(), ''
    try:
        pd = _load_player()
        for k in (pd.get('持有物Inventory') or {}):
            probes.add(k)
            base = re.split(r'[（(]', k)[0].strip()
            if len(base) >= 2:
                probes.add(base)
    except Exception as e:
        probe_err = f'{type(e).__name__}: {e}'
        print(f'⚠️ 探针构建失败——**越界指标无效**：{probe_err}')

    leaks_said, leaks_thought = [], []
    for r in l2_all:
        seen = r.get('感知输入') or ''
        for probe in probes:
            if not probe:
                continue
            if probe in (r.get('回应Response') or '') and probe not in seen:
                leaks_said.append({'npc': r.get('name', ''), '词': probe,
                                   '文本': (r.get('回应Response') or '')[:100]})
            if probe in (r.get('内心想法InnerThought') or '') and probe not in seen:
                leaks_thought.append({'npc': r.get('name', ''), '词': probe,
                                      '文本': (r.get('内心想法InnerThought') or '')[:100]})

    # ---- L1 失真 vs 复述 ----
    truth, l1_total, l1_echo = '', 0, 0
    try:
        truth = (_load_player().get('当前状态CurrentState') or {}).get('实力等级', '')
    except Exception:
        pass
    if truth:
        for l1 in l1_all:
            for _, txt in ((l1 or {}).get('感知分发') or {}).items():
                l1_total += 1
                if truth in str(txt):
                    l1_echo += 1

    # ---- #3 状态自洽 ----
    broken, consistent = 0, 0
    for d in sorted(os.listdir(run_dir) if os.path.isdir(run_dir) else []):
        p = os.path.join(run_dir, d, 'l3_output.json')
        if not os.path.isfile(p):
            continue
        try:
            d3 = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        if d3.get('破裂观察'):
            broken += 1
            pinc = (d3.get('玩家实例') or {}).get('增加', {}) or {}
            npcinc = d3.get('NPC实例') or {}
            if '持有物Inventory' not in pinc and not any(
                    '持有物Inventory' in (v.get('增加', {}) or {})
                    for v in npcinc.values()):
                consistent += 1

    return {
        'probe_count': len(probes), 'probe_error': probe_err,
        'n_responses': len(texts),
        'avg_len': sum(len(t) for t in texts) / len(texts) if texts else 0,
        'cliche_total': sum(cliche_hits.values()),
        'cliche_detail': dict(cliche_hits),
        'avg_similarity': sum(sims) / len(sims) if sims else 0,
        'leaks_said': leaks_said, 'leaks_thought': leaks_thought,
        'broken_trades': broken, 'broken_consistent': consistent,
        'truth_word': truth, 'l1_perceptions': l1_total, 'l1_echo_truth': l1_echo,
    }


# ═══════════════════ 多遍聚合 ═══════════════════

LAYERS = ('L1', 'L2', 'L3', 'S1', 'B1a', 'B1b', 'MEM')


def aggregate(runs: list) -> dict:
    """把 N 遍压成一行统计。**用中位数，不用均值**——长尾会把均值带跑。"""
    walls = sorted(r['wall_sec'] for r in runs)

    def med(vals):
        return statistics.median(vals) if vals else 0.0

    layer_stat = {}
    for k in LAYERS:
        items = [r['layers'].get(k) for r in runs]
        items = [i for i in items if i]
        if not items:
            continue
        layer_stat[k] = {
            'n_runs': len(items),
            'avg_median': med([i['avg_sec'] for i in items]),
            'avg_min': min(i['avg_sec'] for i in items),
            'avg_max': max(i['avg_sec'] for i in items),
            'worst_call': max(i['max_sec'] for i in items),
            'reason_pct': (sum(i['reason'] for i in items) /
                           max(1, sum(i['out'] for i in items)) * 100),
        }

    q = {
        'n_responses': sum(r['quality']['n_responses'] for r in runs),
        'avg_len': med([r['quality']['avg_len'] for r in runs]),
        'cliche_total': sum(r['quality']['cliche_total'] for r in runs),
        'avg_similarity': med([r['quality']['avg_similarity'] for r in runs]),
        'leaks_said': [x for r in runs for x in r['quality']['leaks_said']],
        'leaks_thought': [x for r in runs for x in r['quality']['leaks_thought']],
        'broken_trades': sum(r['quality']['broken_trades'] for r in runs),
        'broken_consistent': sum(r['quality']['broken_consistent'] for r in runs),
        'l1_perceptions': sum(r['quality']['l1_perceptions'] for r in runs),
        'l1_echo_truth': sum(r['quality']['l1_echo_truth'] for r in runs),
        'truth_word': runs[0]['quality'].get('truth_word', ''),
        'probe_count': runs[0]['quality'].get('probe_count', 0),
        'probe_error': runs[0]['quality'].get('probe_error', ''),
    }

    return {
        'name': runs[0]['name'],
        'thinking_off': runs[0]['thinking_off'],
        'scenario': runs[0]['scenario'],
        'n_reps': len(runs),
        'wall_median': med(walls), 'wall_min': min(walls), 'wall_max': max(walls),
        'rc_bad': sum(1 for r in runs if r['returncode'] != 0),
        'layers': layer_stat,
        'quality': q,
        'calls': {k: [c for r in runs for c in r['layers'].get(k, {}).get('calls', [])]
                  for k in LAYERS},
    }


def critical_path(agg: dict) -> float:
    """L2 并行 → 串行长度 ≈ L1 + L2最慢 + L3"""
    L = agg['layers']
    return (L.get('L1', {}).get('avg_median', 0)
            + L.get('L2', {}).get('worst_call', 0)
            + L.get('L3', {}).get('avg_median', 0))


# ═══════════════════ 报告 ═══════════════════

def write_report(aggs: list):
    ok = [a for a in aggs if a['n_reps']]
    if not ok:
        return
    reps = ok[0]['n_reps']
    base = next((a for a in ok if a['name'] == '基线'), None)
    base_cp = critical_path(base) if base else None

    lines = ['# 阶段 1 · 推理消融实验报告', '',
             f'剧本：`{ok[0]["scenario"]}`　每组 **{reps} 遍**。',
             '逐轮产物留档：`src/test/ablation_runs/<组>/rep<N>/round_XXXX/`。', '']

    if reps < 3:
        lines += ['> ⚠️ **每组只跑了 %d 遍。** 实测同配置两遍墙钟能差 77%%，'
                  'n<%d 的结论是噪声。跑 `--repeat 3` 再来。' % (reps, 3), '']

    # ---- 一、墙钟（中位数 + 极差） ----
    lines += ['## 一、墙钟：中位数与极差', '',
              '**看中位数，不看均值。** 推理开着时墙钟由一两次长尾调用主导，'
              '均值会被带跑。极差说明这个数字有多不稳。', '',
              '| 组 | 配置 | 中位数 | 最快 | 最慢 | 极差 | 关键路径(中位) | vs 基线 | 失败 |',
              '|----|------|------:|-----:|-----:|-----:|--------------:|--------:|-----:|']
    for a in ok:
        cp = critical_path(a)
        spread = a['wall_max'] / a['wall_min'] if a['wall_min'] else 0
        rel = ('基准' if a is base else
               (f'{base_cp / cp:.1f}× 快' if base_cp and cp else '—'))
        lines.append(f"| {a['name']} | `{a['thinking_off']}` | **{a['wall_median']:.0f}s** "
                     f"| {a['wall_min']:.0f}s | {a['wall_max']:.0f}s | {spread:.2f}× "
                     f"| {cp:.1f}s | {rel} | {a['rc_bad']} |")

    # ---- 二、关键路径拆解 ----
    lines += ['', '## 二、关键路径拆解（一轮实际要等多久）', '',
              '`L2` 并行，串行长度 ≈ `L1 + L2最慢 + L3`，不是三者相加。', '',
              '| 组 | L1 中位 | L2 最慢 | L3 中位 | 关键路径 |',
              '|----|--------:|--------:|--------:|--------:|']
    for a in ok:
        L = a['layers']
        lines.append(f"| {a['name']} | {L.get('L1', {}).get('avg_median', 0):.1f}s "
                     f"| {L.get('L2', {}).get('worst_call', 0):.1f}s "
                     f"| {L.get('L3', {}).get('avg_median', 0):.1f}s "
                     f"| **{critical_path(a):.1f}s** |")

    # ---- 三、单次调用的最慢值（长尾在哪） ----
    lines += ['', '## 三、长尾在哪（单次调用最慢值）', '',
              '这一栏是**体感**的来源——你感觉"卡住"的那一刻，就是这里出现大数的时候。', '',
              '| 组 | L1 最慢单次 | L2 最慢单次 | L3 最慢单次 |',
              '|----|-----------:|-----------:|-----------:|']
    for a in ok:
        L = a['layers']
        lines.append(f"| {a['name']} "
                     f"| {L.get('L1', {}).get('worst_call', 0):.1f}s "
                     f"| {L.get('L2', {}).get('worst_call', 0):.1f}s "
                     f"| {L.get('L3', {}).get('worst_call', 0):.1f}s |")

    # ---- 四、推理占比 ----
    lines += ['', '## 四、推理 token 占比', '',
              '| 组 | L1 | L2 | L3 |', '|----|---:|---:|---:|']
    for a in ok:
        cells = [f"{a['layers'][k]['reason_pct']:.0f}%" if k in a['layers'] else '—'
                 for k in ('L1', 'L2', 'L3')]
        lines.append(f"| {a['name']} | " + ' | '.join(cells) + ' |')

    # ---- 五、L1 失真 vs 复述 ----
    tw = ok[0]['quality'].get('truth_word', '')
    lines += ['', f'## 五、L1 是"失真"还是"复述"（玩家真实实力=`{tw}`）', '',
              '失真感知是本设计的枢纽。**"没在失真"最直接的症状**，'
              '就是 L1 把玩家的真实实力值逐字抄进感知里。', '',
              '| 组 | L1 感知份数 | 逐字复述真值 | 复述率 |',
              '|----|-----------:|-------------:|-------:|']
    for a in ok:
        q = a['quality']
        tot, echo = q['l1_perceptions'], q['l1_echo_truth']
        lines.append(f"| {a['name']} | {tot} | {echo} | "
                     f"{echo / tot * 100:.0f}% |" if tot else f"| {a['name']} | 0 | 0 | — |")
    lines += ['', '⚠️ **只有在场观察者与玩家修为相近时，这条才有判别力。**'
              '若剧本里只有远高于玩家的观察者，"照实看穿"是正确行为，复述率高不代表退化。']

    # ---- 六、质量 ----
    lines += ['', '## 六、质量指标（§八 要求的三条）', '',
              '| 组 | 回应数 | 均长 | 套路词 | 相似度 | **对白越界** | 内心越界 '
              '| 破裂交易 | 破裂时一致 |',
              '|----|------:|-----:|------:|------:|-----------:|--------:'
              '|--------:|----------:|']
    for a in ok:
        q = a['quality']
        lines.append(f"| {a['name']} | {q['n_responses']} | {q['avg_len']:.0f}字 "
                     f"| {q['cliche_total']} | {q['avg_similarity']:.2f} "
                     f"| **{len(q['leaks_said'])}** | {len(q['leaks_thought'])} "
                     f"| {q['broken_trades']} | {q['broken_consistent']}/{q['broken_trades']} |")
    if any(not a['quality'].get('probe_count') for a in ok):
        lines += ['', '⚠️ **有组的探针没建起来，越界列是空的、不代表干净。**']

    # ---- 七、逐组细节 ----
    lines += ['', '## 七、逐组细节', '']
    for a in ok:
        q = a['quality']
        lines += [f"### {a['name']}（`G4_THINKING_OFF={a['thinking_off']}`，{a['n_reps']} 遍）", '',
                  f"- 墙钟中位 **{a['wall_median']:.0f}s**"
                  f"（{a['wall_min']:.0f}~{a['wall_max']:.0f}s），关键路径 {critical_path(a):.1f}s"]
        for k in LAYERS:
            if k not in a['layers']:
                continue
            s = a['layers'][k]
            lines.append(f"- **{k}**：中位 {s['avg_median']:.1f}s"
                         f"（{s['avg_min']:.1f}~{s['avg_max']:.1f}s），"
                         f"最慢单次 {s['worst_call']:.1f}s，推理 {s['reason_pct']:.0f}%")
        if q['cliche_total']:
            lines.append(f"- 套路词命中 {q['cliche_total']} 次")
        if q['probe_error']:
            lines.append(f"- ⚠️ **探针构建失败，越界指标无效**：{q['probe_error']}")
        if q['leaks_said']:
            lines.append(f"- 🔴 **对白越界 {len(q['leaks_said'])} 处**（玩家能直接看到）：")
            for lk in q['leaks_said'][:5]:
                lines.append(f"    - `{lk['词']}` @ {lk['npc'].split('、')[0]}：{lk['文本']}")
        if q['leaks_thought']:
            lines.append(f"- 🟡 内心想法越界 {len(q['leaks_thought'])} 处：")
            for lk in q['leaks_thought'][:5]:
                lines.append(f"    - `{lk['词']}` @ {lk['npc'].split('、')[0]}：{lk['文本']}")
        lines.append('')

    # ---- 八、怎么读 ----
    lines += ['', '## 八、怎么读这张表', '',
              '- **只有中位数降了、且极差没有离谱地大**，才算真的快',
              '- 第三节的"最慢单次"才是**体感** —— 它大，玩家就会觉得卡住了',
              '- 越界是代理指标，有误报也有漏报。真判断看 '
              '`ablation_runs/<组>/rep<N>/round_*/perception_audit.md`', '']

    open(REPORT, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'\n📄 报告已写入：{REPORT}')


# ═══════════════════ 主流程 ═══════════════════

def load_cache() -> dict:
    if os.path.isfile(CACHE):
        try:
            return json.load(open(CACHE, encoding='utf-8'))
        except Exception:
            pass
    return {}


def main():
    argv = sys.argv[1:]
    report_only = '--report-only' in argv
    peer = '--peer' in argv
    scenario = SCENARIO_PEER if peer else SCENARIO
    repeat = 1
    for i, a in enumerate(argv):
        if a == '--repeat' and i + 1 < len(argv):
            repeat = max(1, int(argv[i + 1]))
    only = {a for a in argv if not a.startswith('--') and not a.isdigit()}

    # 两套剧本分开缓存。共用一份的话，跑 --peer 会把原剧本的结果冲掉
    # （缓存按组名存，同名就覆盖）。
    global CACHE, REPORT
    if peer:
        CACHE = CACHE.replace('ablation_raw.json', 'ablation_raw_peer.json')
        REPORT = REPORT.replace('ablation_report.md', 'ablation_report_peer.md')

    cache = load_cache()
    for name, off in GROUPS:
        if only and name not in only:
            continue
        runs = cache.get(name, []) if report_only else []
        if report_only:
            continue
        keep = [r for r in runs if r.get('scenario') == os.path.basename(scenario)]
        for rep in range(len(keep) + 1, repeat + 1):
            keep.append(run_once(name, off, rep, scenario))
            cache[name] = keep
            json.dump(cache, open(CACHE, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
            write_report([aggregate(v) for k, v in cache.items() if v])

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    aggs = [aggregate(cache[n]) for n, _ in GROUPS if cache.get(n)]
    write_report(aggs)

    print('\n' + '=' * 74)
    print(f'{"组":<12}{"中位(极差)":>16}{"L1":>8}{"L2最慢":>9}{"L3":>8}'
          f'{"L3最慢":>9}{"对白越界":>9}')
    print('-' * 74)
    for a in aggs:
        L = a['layers']
        print(f"{a['name']:<12}"
              f"{a['wall_median']:>9.0f}s({a['wall_max'] / a['wall_min']:.1f}x)"
              f"{L.get('L1', {}).get('avg_median', 0):>7.1f}s"
              f"{L.get('L2', {}).get('worst_call', 0):>8.1f}s"
              f"{L.get('L3', {}).get('avg_median', 0):>7.1f}s"
              f"{L.get('L3', {}).get('worst_call', 0):>8.1f}s"
              f"{len(a['quality']['leaks_said']):>9}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
