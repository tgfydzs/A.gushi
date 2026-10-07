"""
量 L1 的**失真质量** —— 这是全项目最该盯的一个指标。

# 为什么是它

失真感知（§2.3）是整个设计的枢纽：越级战斗、信息不对称、NPC 误判都从这里来。
而它**只在 L1 产出**——L2/L3 只读不写 `感知分发`。所以：

    **关不关 L2/L3 的推理，对失真率没有任何影响。**
    要量失真，只需要看 L1 那一次调用的输出。

# 怎么量

拿 L1 给**单个 NPC** 写的那段感知文本，数里面有没有"看不透"式的表达：

    "瞧不出深浅" / "摸不到底" / "说不清" / "拿不准" / "分辨不出" ...

这是**代理指标**，测的是失真的一种形态（**模棱两可**）。失真还有另一种形态——
**自信的误判**（"像个炼气初期的后生"，实际是中期），那也是失真，而且那是越级战斗
的燃料。**本脚本数不到那一种。**

真正有判别力的场景：**观察者与玩家修为相近**。远高于玩家的观察者"照实看穿"
本来就是正确行为，那种数据没意义。所以这个脚本默认只读
`近修为剧本`（炼气初期的韩铁柱看炼气中期的玩家）。

跑法：
    python src/test/measure_distortion.py                    # 读全部已留档的组
    python src/test/measure_distortion.py 基线 全关           # 只看指定组
    python src/test/measure_distortion.py --peer=path         # 换留档目录

⚠️ 只读文件，**不调用 API**。
"""

import json
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 观察者必须是**与玩家修为相近**的人，否则数据没有判别力
OBSERVER = '韩铁柱'
TRUTH = '炼气中期'          # 玩家的真实实力等级

# ⚠️ 这组词要够全，否则会**低估**失真率。
# 第一版只写了 `分辨不[出清]`，把"分不清"漏了；只写了 `看不|瞧不`，把"听不出"漏了——
# 于是 12 份里 2 份明显是失真的被算成"没失真"。**指标漏词 = 结论反向。**
#
# 只收"表达看不清对方深浅"的说法，**不收情绪词**（发慌、发怵）——
# 情绪是反应不是失真，收进来会把指标稀释成一个没意义的大数。
HEDGE = re.compile(
    r'(看不[出透清真]|瞧不[出透]|听不[出到]|感[觉应]不[到出]|摸不[准到清底]|'
    r'分不[清出]|分辨不[出清]|辨不[出清]|认不[出清]|拿不准|说不[准清上]|'
    r'无法[判看]|难以[判看估揣]|不知[道他其]|不知深浅|'
    r'瞧不真|看不真[切]|摸不到底|一眼看不[透穿]|'
    r'不敢[细多]|说不上[来]?|看不[明透])')


def load(group_dir: str) -> dict:
    """读一个组的所有留档，统计 N 份感知里有多少含'看不透'表达"""
    tot = hedge = echo = 0
    samples = []
    for f in sorted(glob.glob(os.path.join(group_dir, 'rep*', 'round_*', 'l1_output.json'))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        for nm, txt in ((d or {}).get('感知分发') or {}).items():
            if OBSERVER not in nm:
                continue
            tot += 1
            t = str(txt)
            if HEDGE.search(t):
                hedge += 1
            if TRUTH in t:
                echo += 1
            samples.append(t)
    return {'n': tot, 'hedge': hedge, 'echo': echo, 'samples': samples}


def main():
    runs_dir = os.path.join(ROOT, 'src', 'test', 'ablation_runs')
    for a in sys.argv[1:]:
        if a.startswith('--peer='):
            runs_dir = a.split('=', 1)[1]

    only = {a for a in sys.argv[1:] if not a.startswith('--')}
    groups = sorted(d for d in os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []
    if only:
        groups = [g for g in groups if g in only]

    if not groups:
        print(f'没有可读的组（{runs_dir}）')
        return 1

    print(f'观察者：{OBSERVER}（与玩家修为相近，才有判别力）')
    print(f'玩家真实实力：{TRUTH}')
    print('=' * 78)
    print('%-16s%9s%13s%8s%14s' % ('组', '感知份数', '含看不透式', '占比', '逐字复述真值'))
    print('-' * 78)
    for g in groups:
        r = load(os.path.join(runs_dir, g))
        if not r['n']:
            print(f'{g:<16}{"（无该观察者的数据）":>30}')
            continue
        print(f"{g:<16}{r['n']:>9}{r['hedge']:>13}"
              f"{r['hedge'] / r['n'] * 100:>7.0f}%{r['echo']:>14}")

    print()
    print('读法：')
    print('  · **含"看不透"占比** 越高 = 失真越"像那么回事"（92% 是推理全开时的水平）')
    print('  · **逐字复述真值** 是硬失败——NPC 直接报出玩家的真实等级，等于没失真')
    print('  · 本指标只数"模棱两可"这一种失真；**自信的误判**（也是一种失真，')
    print('    而且是越级战斗的燃料）它数不到，得人读 perception_audit.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
