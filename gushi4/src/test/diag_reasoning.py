"""
L1 为什么想那么久 —— **诊断脚本，不是实验**。

背景：L1 实测 17.5s/轮（3 遍中位数），可见输出只有 ~320 tok/轮，
推理 token 占 94%。也就是**慢在想，不在写**。

但**想法本身从来没被看过**——日志只记了 `reason=21342` 这样的字数。
所以"它是不是没找到停止条件"这句话，在跑这个脚本之前只是猜。

这个脚本做一件事：用真实输入跑几次 L1，把**推理正文**抓下来落盘，供人读。

判断标准（读完推理正文后对号入座）：

| 症状 | 长什么样 | 提示词能不能治 |
|------|---------|--------------|
| (a) 反复权衡同一决定 | "选 public 还是 area……不过 area 也……还是 public 吧，可是……" | ✅ |
| (b) 枚举所有可能性 | 把全部 9 个 NPC 逐个过一遍"他会不会听到" | ✅ |
| (c) 越权推演 | 开始模拟 NPC 会怎么反应、会说什么话 | ✅ |
| (d) 任务真的难 | 在认真权衡 scope 的边界情形 | ❌ 只能关推理 |

**(a)(b)(c) 改提示词有用，(d) 没用。** 不先分清是哪一种，改提示词就是瞎改。

跑法（**会真实调用 API，约 3~5 次，1 分钟内**）：
    cd <项目根>
    python src/test/diag_reasoning.py

产出：
    - 控制台：每次调用的耗时 + 推理长度 + **相对可见输出放大的倍数**
    - `debug_rounds/reasoning_dump.md`：推理正文全文（分段标注，方便读）
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from common import log as log_cfg
log_cfg.setup(session_id='diag', console_min_score=40)

from common.LLMclient import enable_reasoning_capture, drain_reasoning
from common.game_controller import GameController
from common.L1_perception import L1_Perception

DUMP = os.path.join(ROOT, 'debug_rounds', 'reasoning_dump.md')

# 三种输入，覆盖 L1 的不同负担：
#   ① 面对面普通对话（1 个近处 NPC）
#   ② 点名远处 NPC（要跨距离写失真感知）
#   ③ 公开宣告 + 移动（事件判断 + scope + 传播都在动）
CASES = [
    ('面对面·普通对话',
     '白鹤长老，弟子这几日打扫落霞坪，见灵草长势不如从前，想请长老指点一二。'),
    ('点名·远处的宗主',
     '我走到坪边，朝着天道宗后山的方向运足气力喊道：玄清宗主——弟子叶尘有要事求见！'),
    ('公开宣告·兼移动',
     '我站在落霞坪中央，当着所有人的面高声宣布：我叶尘，三日后要离开天道宗，独自去青木山脉闯一闯！'),
]


def summarize(reasoning: str, reply: str) -> dict:
    """
    给推理正文算几个粗略的分类信号。**只是给读的人一个入口，不是判定。**

    真判断还得读正文——正则数出来的"但是"次数，分不清"反复权衡"和"正常转折"。
    """
    n = len(reasoning)
    # (a) 反复权衡：转折词密度
    hedge = len(re.findall(r'但(是)?|不过|然而|可是|或者|要么|另一方面|反之|'
                           r'或者说|其实也|不一定|也可能', reasoning))
    # (b) 枚举：NPC 名字出现次数
    enumerated = 0
    for nm in ('白鹤', '韩铁柱', '玄清', '云逸', '逍遥子', '青云', '药老', '魔尊', '血手'):
        enumerated += reasoning.count(nm)
    # (c) 越权推演：提到 NPC 会怎么回应/反应
    speculate = len(re.findall(r'他会说|她会说|会回应|会怎么想|的反应|会告诉|'
                               r'可能回应|接下来会|下一轮', reasoning))
    # 自查块：把提示词里的自查清单又跑了一遍
    selfcheck = len(re.findall(r'自查|检查一下|再确认|核对一遍|让我再|最后确认', reasoning))
    return {
        'chars': n,
        'reply_chars': len(reply),
        'amplify': round(n / max(1, len(reply)), 1),
        '转折词': hedge,
        '枚举NPC名': enumerated,
        '越权推演': speculate,
        '自我复查': selfcheck,
    }


def main():
    gc = GameController('diag')
    gc.init_world(); gc.init_clock(); gc.init_all_npcs(); gc.init_player()

    l1 = L1_Perception()
    l1.set_world(gc.b1WorldLayer)

    enable_reasoning_capture(True)
    dump = ['# L1 推理正文（诊断用，非实验）', '',
            '读法：看它把时间花在哪一类思考上。判断标准见 `src/test/diag_reasoning.py` 顶部。',
            '']

    print('=' * 78)
    print('L1 推理正文诊断')
    print('=' * 78)
    print(f'{"用例":<20}{"耗时":>8}{"推理字数":>10}{"输出字数":>10}{"放大":>8}'
          f'   转折/枚举/越权/自查')
    print('-' * 78)

    rows = []
    for i, (title, player_input) in enumerate(CASES, 1):
        # 每个用例独立一局，避免互相污染
        gc.event_layer.reset()
        gc.event_context.new_context()
        gc.global_context.new_context()
        gc.add_context_all(f'叶尘（玩家）：{player_input}')

        drain_reasoning()
        try:
            l1.judge(
                event_context=gc.event_context,
                event_layer=gc.event_layer,
                all_npc_list=gc.all_npc_list,
                player=gc.player,
                director_context=gc.director_context,
                world_layer=gc.b1WorldLayer,
                player_input=player_input,
            )
        except Exception as e:
            print(f'{title:<20} 调用失败：{type(e).__name__}: {e}')
            continue

        recs = [r for r in drain_reasoning() if r['layer'] == 'L1']
        if not recs:
            print(f'{title:<20} 没抓到推理（模型可能没开思考）')
            continue

        r = recs[-1]
        s = summarize(r['reasoning'], r['reply'])
        rows.append((title, player_input, r, s))
        print(f'{title:<20}{r["seconds"]:>7.1f}s{s["chars"]:>10}{s["reply_chars"]:>10}'
              f'{s["amplify"]:>7.1f}x   {s["转折词"]}/{s["枚举NPC名"]}/'
              f'{s["越权推演"]}/{s["自我复查"]}')

        dump += [f'## {i}. {title}', '',
                 f'- 玩家输入：`{player_input}`',
                 f'- 耗时 **{r["seconds"]:.1f}s**，{r["usage"]}',
                 f'- 推理 {s["chars"]} 字 / 可见输出 {s["reply_chars"]} 字 = '
                 f'**放大 {s["amplify"]}×**',
                 f'- 分类信号：转折词 {s["转折词"]}、枚举NPC名 {s["枚举NPC名"]}、'
                 f'越权推演 {s["越权推演"]}、自我复查 {s["自我复查"]}',
                 '', '### 推理正文', '', '```', r['reasoning'], '```', '',
                 '### 它最终产出的 JSON', '', '```json', r['reply'], '```', '']

    os.makedirs(os.path.dirname(DUMP), exist_ok=True)
    open(DUMP, 'w', encoding='utf-8').write('\n'.join(dump))

    print('-' * 78)
    if rows:
        avg_amp = sum(s['amplify'] for _, _, _, s in rows) / len(rows)
        avg_sec = sum(r['seconds'] for _, _, r, _ in rows) / len(rows)
        print(f'平均：{avg_sec:.1f}s/次，推理是可见输出的 {avg_amp:.1f} 倍')
    print(f'\n推理正文已写入：{DUMP}')
    print('（这份文件是原始思考文本，很长——建议只看每个用例的前几百字，')
    print('  看它开头在纠结什么、结尾有没有收敛。）')


if __name__ == '__main__':
    main()
