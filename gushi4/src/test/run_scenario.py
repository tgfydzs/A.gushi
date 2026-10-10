"""
阶段 0 验收跑 —— 按设计方案 §八 的实验协议跑一组固定输入，然后出报告。

覆盖设计文档要求的五类输入：**普通对话 / 移动 / 交易 / 战斗 / 无NPC独处**，
外加两条专门压测边界的：**点名远处的NPC**（测"点名必达"）、
**连续两笔交易一败一成**（测 L3 的客观一致性）。

跑法：
    cd <项目根>
    python src/test/run_scenario.py                       # 跑默认剧本
    python src/test/run_scenario.py 路径/剧本.txt          # 跑指定剧本

产出：
    - 控制台：逐层耗时/token 汇总表
    - log/gushi4.log：完整日志
    - debug_rounds/round_XXXX/：逐轮快照 + **perception_audit.md**（信息隔离审计）

⚠️ 这个脚本会**真实调用 API**（花钱、花时间）。
"""

import os
import re
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SCENARIO = os.path.join(ROOT, 'src', 'test', 'scenarios', '阶段0剧本.txt')
LOG_PATH = os.path.join(ROOT, 'log', 'gushi4.log')


def main():
    """--report-only：只从现有日志出报告，不跑游戏（调 prompt 反复看数据时省时间省钱）"""
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--report-only' in sys.argv:
        return _report()

    scenario = args[0] if args else DEFAULT_SCENARIO
    if not os.path.isfile(scenario):
        print(f'剧本不存在：{scenario}')
        return 1

    print('=' * 78)
    print(f'阶段 0 验收跑 —— 剧本：{os.path.basename(scenario)}')
    print('=' * 78)
    lines = [l.strip() for l in open(scenario, encoding='utf-8') if l.strip()]
    for i, l in enumerate(lines, 1):
        print(f'  {i:2d}. {l[:70]}')
    print()

    # 每轮重跑前清日志，报告只统计本次
    if os.path.isfile(LOG_PATH):
        os.remove(LOG_PATH)

    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    with open(scenario, 'r', encoding='utf-8') as fin:
        proc = subprocess.run([sys.executable, os.path.join(ROOT, 'main.py')],
                              stdin=fin, cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding='utf-8',
                              errors='replace')

    print('=' * 78)
    print('控制台输出')
    print('=' * 78)
    out = re.sub(r'^\d{4}-\d{2}-\d{2} .*?\[(WARN|ERROR)\s*\] .*$', '', proc.stdout, flags=re.M)
    print('\n'.join(l for l in out.splitlines() if l.strip()))

    if proc.stderr.strip():
        print('\n--- stderr ---')
        print(proc.stderr[-2000:])

    return _report()


def _report():
    # ---------- 逐层统计 ----------
    print()
    print('=' * 78)
    print('逐层耗时与 token')
    print('=' * 78)
    if not os.path.isfile(LOG_PATH):
        print('没有日志文件，无法统计')
        return 1

    pat = re.compile(
        r'\[LLM\] (?P<layer>\S+?) \[(?P<label>[^\]]*)\] 完成 \| '
        r'(?P<sec>[\d.]+)s \| tokens: (?P<tok>.+)$')
    stats = defaultdict(lambda: {'n': 0, 'sec': 0.0, 'out': 0, 'reason': 0, 'in': 0})
    total_sec = 0.0

    for line in open(LOG_PATH, encoding='utf-8', errors='replace'):
        m = pat.search(line)
        if not m:
            continue
        s = stats[m.group('layer')]
        s['n'] += 1
        sec = float(m.group('sec'))
        s['sec'] += sec
        total_sec += sec
        for k, key in (('in', 'in'), ('out', 'out'), ('reason', 'reason')):
            mm = re.search(rf'{k}=(\d+)', m.group('tok'))
            if mm:
                s[key] += int(mm.group(1))

    if not stats:
        print('没抓到任何 LLM 调用记录——日志格式可能变了，检查 LLMclient 的日志行')
        return 1

    def _layer_key(name):
        m = re.match(r'([A-Z]+\d*[a-c]?)', name)
        return m.group(1) if m else name

    agg = defaultdict(lambda: {'n': 0, 'sec': 0.0, 'out': 0, 'reason': 0, 'in': 0})
    for name, s in stats.items():
        a = agg[_layer_key(name)]
        for k in ('n', 'sec', 'out', 'reason', 'in'):
            a[k] += s[k]

    print(f'{"层":<8}{"次数":>5}{"总耗时":>10}{"均耗时":>9}'
          f'{"输入tok":>10}{"输出tok":>10}{"推理tok":>10}{"推理占比":>9}')
    print('-' * 72)
    for name in sorted(agg, key=lambda k: -agg[k]['sec']):
        a = agg[name]
        ratio = f'{a["reason"] / a["out"] * 100:.0f}%' if a['out'] else '-'
        print(f'{name:<8}{a["n"]:>5}{a["sec"]:>9.1f}s{a["sec"] / a["n"]:>8.1f}s'
              f'{a["in"]:>10}{a["out"]:>10}{a["reason"]:>10}{ratio:>9}')
    print('-' * 60)
    print(f'{"合计":<8}{sum(a["n"] for a in agg.values()):>5}{total_sec:>9.1f}s')

    # ---------- 审计提示 ----------
    rounds = sorted(d for d in os.listdir(os.path.join(ROOT, 'debug_rounds'))
                    if d.startswith('round_')) if os.path.isdir(
        os.path.join(ROOT, 'debug_rounds')) else []
    print()
    print('=' * 78)
    print(f'逐轮审计（{len(rounds)} 轮）')
    print('=' * 78)
    print('每轮的 perception_audit.md 把「NPC 收到的感知」和「它说出口的对白」并排摆着。')
    print('检查重点（设计方案 §八）：')
    print('  1. ★ NPC 有没有说出它不该知道的？（最关键）')
    print('  2. 回应有没有套路化？（"深不可测""蝼蚁"刷屏）')
    print('  3. 状态变更自不自洽？（交易、伤害、物品转移对不对得上）')
    print()
    for d in rounds:
        p = os.path.join(ROOT, 'debug_rounds', d, 'perception_audit.md')
        print(f'  {p}' if os.path.isfile(p) else f'  {d}/(无审计文件)')

    print()
    print('关键日志行（越权拦截 / 感知复述 / 交易破裂）：')
    for line in open(LOG_PATH, encoding='utf-8', errors='replace'):
        if any(k in line for k in ('越权字段被丢弃', '感知分发里逐字出现', '破裂观察注入',
                                   '破裂', 'R3：', 'R1：')):
            print('  ' + line.strip()[:150])

    return 0


if __name__ == '__main__':
    sys.exit(main())
