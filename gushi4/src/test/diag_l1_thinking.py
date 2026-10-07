"""
对照实验：L1 **开推理 vs 关推理**，对 scope 判定的影响 —— **真实调用 LLM**。

# 为什么要专门测这个

我之前说过"L1 判不准大概是因为关着推理"——**那是推测，不是实测**。
它来自另一组数据（消融实验里 L1 关推理后「看不透」率从 92% 掉到 58%），
那是**另一个指标**。scope 判定到底受不受推理影响，没人测过。

本脚本就是补这个对照：同一个输入、同一套提示词，只改 L1 的 thinking 开关，
各跑 N 次，比 scope 分布。

# 顺带回答的第二个问题

开推理值不值？这个脚本会打出两组的耗时，直接对照关键路径的代价。

跑法：
    cd E:/A.gushi/gushi4 && python src/test/diag_l1_thinking.py [次数，默认 5]
"""

import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

from common import base_cnf
from common.engine import GameEngine
from Schema.Context_sch import ContextHistory

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5

CASES = [
    ('悄悄话', '我凑到白鹤长老耳边，用只有两个人能听见的声音说：弟子昨夜在后山捡到一枚令牌，没敢声张。'),
    ('当众喊', '我站在落霞坪中央，运足气力高声宣布：后山有一枚令牌！'),
]

SHORT = lambda n: (n or '').split('、')[0].split('（')[0]


def probe(engine, text):
    engine.gc.event_context = ContextHistory(game_clock=engine.gc.game_clock)
    engine.gc.event_layer.reset(title='')
    return engine.l1_layer.judge(
        event_context=engine.gc.event_context,
        event_layer=engine.gc.event_layer,
        all_npc_list=engine.gc.all_npc_list,
        player=engine.gc.player,
        director_context=engine.gc.director_context,
        world_layer=engine.gc.b1WorldLayer,
        player_input=text,
    )


def run_case(thinking: bool, label: str, text: str):
    # 改配置必须在 GameEngine 构造**之前**——LLMClient 在构造时就把 LayerCfg 定下来了
    base_cnf.LAYER_MODEL_OVERRIDE['L1'] = {'thinking': thinking}
    engine = GameEngine(session_id=f'l1think_{int(thinking)}')

    scopes = collections.Counter()
    who = []
    t0 = time.time()
    for _ in range(N):
        r = probe(engine, text)
        scopes[r['scope'].value] += 1
        who.append([SHORT(k) for k in (r['感知分发'] or {})])
    dt = time.time() - t0
    return scopes, who, dt


print('=' * 70)
print(f'L1 推理开关对照（每格 {N} 次）')
print('=' * 70)

rows = []
for case_label, text in CASES:
    for thinking, tag in ((False, '关推理'), (True, '开推理')):
        print(f'\n>>> {case_label} · L1 {tag} …')
        scopes, who, dt = run_case(thinking, tag, text)
        rows.append((case_label, tag, scopes, who, dt))
        print(f'    scope 分布：{dict(scopes)}')
        print(f'    感知分发人数：{sorted(collections.Counter(len(w) for w in who).items())}')
        print(f'    总耗时 {dt:.1f}s（{dt / N:.1f}s/次）')

print()
print('=' * 70)
print('对照表')
print('=' * 70)
print(f'{"用例":<8}{"配置":<10}{"scope 分布":<34}{"秒/次":>8}')
for case_label, tag, scopes, who, dt in rows:
    dist = ' '.join(f'{k}:{v}' for k, v in sorted(scopes.items()))
    print(f'{case_label:<8}{tag:<10}{dist:<34}{dt / N:>8.1f}')

print()
print('=' * 70)
print('怎么读')
print('=' * 70)
print('· 悄悄话那一行，`private` 出现得多 = 判得准；开/关推理对比就是答案。')
print('· 当众喊那一行，`public/area` 出现得多 = 判得准。')
print('· "秒/次"是 L1 单次调用的墙钟。它在关键路径上，这个数字直接加到每轮耗时里。')
