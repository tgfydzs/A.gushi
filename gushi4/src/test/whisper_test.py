"""
悄悄话 / scope 稳定性探针 —— **真实调用 LLM**（每次 L1 约 2~5 秒）。

# 为什么测这个

`Schema/Event_sch.py` 定了五档 scope：

    public · area · group · private · hidden

但翻遍过去所有运行记录，**实际出现过的只有 `area`(8) 和 `public`(2)** ——
`private` / `group` / `hidden` **一次都没被触发过**。

悄悄话正是 `private` 最常见的入口，也是**信息隔离最该守住的场景**：
玩家贴着白鹤耳边说一件事，两段路外的陈守贵和柳明溪**绝不能收到**。
真漏了的话整个多 NPC 设计的地基就塌一角，而且**不会报错**——只会安静地泄漏。

# 为什么要重复 N 次

L1 是不确定的。同一个"耳边低语"输入，实测一次给 `private`、一次给 `area`——
**单跑一遍的结论就是噪声**。所以这里把同一个输入重复 N 次，报**分布**。

# 判据

`area` 对悄悄话来说是**危险的误判**——它意味着"这块地界的人都可能知道"。
本探针把每次的 scope 和 `感知分发` 的名单都打出来，供人工核对。

跑法：
    cd E:/A.gushi/gushi4 && python src/test/whisper_test.py [次数，默认 5]
"""

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

from common.engine import GameEngine
from Schema.Context_sch import ContextHistory

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5

SECRET = '令牌'
WHISPER = f'我凑到白鹤长老耳边，用只有两个人能听见的声音说：弟子昨夜在后山捡到一枚{ SECRET }，没敢声张。'
LOUD = f'我站在落霞坪中央，运足气力高声宣布：后山有一枚{ SECRET }！'

SHORT = lambda n: (n or '').split('、')[0].split('（')[0]


def probe(engine, text):
    """
    只跑 L1。

    ⚠️ **必须同时重置 event_context 和 event_layer**——只重置前者的话，
    上一轮的 `event_layer.scope` 会作为 `Scope.parse` 的 default 留下来，
    污染下一次的判定。第一次写这个测试时就踩了这个坑。
    """
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


def summarize(tag, label, results):
    scopes = collections.Counter(str(r['scope'].value) for r in results)
    print(f'\n--- {tag}：{label} ---')
    print(f'  出现 {N} 次的 scope 分布：{dict(scopes)}')
    for r in results:
        who = [SHORT(k) for k in (r['感知分发'] or {})]
        print(f'    scope={str(r["scope"].value):8} 感知分发={who}')
    return scopes


print('=' * 70)
print(f'scope 稳定性探针（每个输入重复 {N} 次）')
print('=' * 70)

engine = GameEngine(session_id='whisperprobe')
here = [SHORT(n.name) for n in engine.gc.b1WorldLayer.find_npcs_at_location(
    engine.gc.b1WorldLayer.parse_path(engine.gc.player_location), engine.gc.all_npc_list)]
pool = [SHORT(n.name) for n in engine.gc.all_npc_list.npc_list]
print(f'玩家位置：{engine.gc.player_location}')
print(f'在场：{here}')
print(f'候选池（NPC_附近距离_段=3）：白鹤 + 陈守贵 + 柳明溪')
print(f'输入：{WHISPER}')

wh = [probe(engine, WHISPER) for _ in range(N)]
sc = summarize('悄悄话', '耳边低语给白鹤', wh)

ld = [probe(engine, LOUD) for _ in range(N)]
sl = summarize('对照', '当众高声宣布', ld)

# ══════════════════════════════════════════════════════════════
print()
print('=' * 70)
print('统计')
print('=' * 70)

priv = sc.get('private', 0)
print(f'悄悄话被判为 private：{priv}/{N}')
print(f'悄悄话被判为 area   ：{sc.get("area", 0)}/{N}   ← area 会放行整块地界的人')
print(f'悄悄话被判为 public ：{sc.get("public", 0)}/{N}   ← 更宽')
print(f'悄悄话被判为 hidden ：{sc.get("hidden", 0)}/{N}   ← 等于没人听见，玩家白说')

# 有没有泄漏：悄悄话里，除白鹤外还有谁收到了含"令牌"的感知
leaks = []
for r in wh:
    for name, perc in (r['感知分发'] or {}).items():
        if SHORT(name) != '白鹤' and SECRET in str(perc):
            leaks.append((SHORT(name), str(perc)[:60]))
print(f'\n实际泄漏（非白鹤收到了保密内容）：{leaks if leaks else "无"}')

# 悄悄话有没有整个"掉"掉
dropped = sum(1 for r in wh if not (r['感知分发'] or {}))
print(f'整轮无人收到感知（悄悄话凭空消失）：{dropped}/{N}')

empty_invol = sum(1 for r in wh if not (r['锁定名单'] or []))
print(f'锁定名单为空：{empty_invol}/{N}')

# 锁定名单 与 感知分发 是否一致 —— 两者不一致会让 L2 和 L3 对"谁在场"有分歧
mismatch = 0
for r in wh + ld:
    if set(r['锁定名单'] or []) != set((r['感知分发'] or {}).keys()):
        mismatch += 1
print(f'锁定名单 ≠ 感知分发 的次数：{mismatch}/{2 * N}')
print('  （L2 用 感知分发，L3 用 锁定名单——不一致意味着两边对"谁参与"有分歧）')

print()
print('=' * 70)
print('观察到的 scope 全集：', sorted(set(sc) | set(sl)))
print('五档定义：public / area / group / private / hidden')
missing = {'public', 'area', 'group', 'private', 'hidden'} - (set(sc) | set(sl))
if missing:
    print('本次仍未覆盖：', sorted(missing))
print('=' * 70)
