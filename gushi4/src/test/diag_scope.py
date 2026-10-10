"""
诊断：L1 对"悄悄话"到底输出了什么 —— **真实调用 LLM**。

把 L1 的**原始 JSON** 打出来，不做任何加工。用于定位
"scope 从来不是 private"到底是模型没判、还是判了被后续代码吞了。

跑法：
    cd <项目根> && python src/test/diag_scope.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

from common.engine import GameEngine
from Schema.Context_sch import ContextHistory

CASES = [
    ('A. 耳边低语（最典型的 private）',
     '我凑到白鹤长老耳边，用只有两个人能听见的声音说：弟子昨夜在后山捡到一枚令牌，没敢声张。'),
    ('B. 明确点名 + 压低声音',
     '我把白鹤长老拉到一边，压低声音，只对他说：那枚令牌的事，千万别外传。'),
    ('C. 递纸条（零声音）',
     '我左右看了看，确认无人注意，把一张纸条塞进白鹤长老手心，上面写着：后山有魔道令牌。'),
    ('D. 当众宣布（对照，应当 public/area）',
     '我站在落霞坪中央高声宣布：后山有魔道令牌！'),
]

engine = GameEngine(session_id='diagscope')

for label, text in CASES:
    print('=' * 70)
    print(label)
    print('=' * 70)
    print(f'输入：{text}')

    engine.gc.event_context = ContextHistory(game_clock=engine.gc.game_clock)
    engine.gc.event_layer.reset(title='')

    r = engine.l1_layer.judge(
        event_context=engine.gc.event_context,
        event_layer=engine.gc.event_layer,
        all_npc_list=engine.gc.all_npc_list,
        player=engine.gc.player,
        director_context=engine.gc.director_context,
        world_layer=engine.gc.b1WorldLayer,
        player_input=text,
    )

    print(f'\n>>> 代码解析结果：scope={r["scope"].value}  '
          f'锁定={r["锁定名单"]}  感知分发={list(r["感知分发"].keys())}')

    raw = r.get('raw') or {}
    print(f'\n>>> L1 原始输出（模型真实产物）：')
    print(json.dumps(raw, ensure_ascii=False, indent=2)[:1400])
    print()
