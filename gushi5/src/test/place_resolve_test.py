# -*- coding: utf-8 -*-
"""地名解析：模型写人话，解析层要对得上，但**只认唯一**。

背景：只认一字不差时，模型写「云梦国」（地块名是 `南疆凡人国·云梦国`）就会被判
"不在已知地点里"，玩家说"我要去云梦国"原地不动。反过来，松到"猜最像的"又会把
人送到错地方。所以判据是：**结构上唯一命中才采纳，多个候选一律不动**。
"""
import os
import sys

if not os.environ.get('DEEPSEEK_API_KEY'):
    print('SKIP：没有 DEEPSEEK_API_KEY（解析层要构造 GameController）')
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common import log
log.setup(log_file_path=os.path.join(os.environ.get('TEMP', '.'), 'g5_place_test.log'))
from common.game_controller import GameController

PASS = FAIL = 0


def check(ok, msg):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f'  ✗ {msg}')


gc = GameController('place-test')
gc.new_game()
w = gc.b1WorldLayer

# ── 精确与全路径 ──
nm, how = w.resolve_place('韩家村')
check(nm == '韩家村' and how == '精确', f'精确名：{nm}/{how}')
nm, how = w.resolve_place('苍云大陆·南域·南疆凡人国·云梦国')
check(nm == '南疆凡人国·云梦国' and how == '全路径', f'全路径：{nm}/{how}')

# ── 模型最常写的形态：只写末段 ──
nm, how = w.resolve_place('云梦国')
check(nm == '南疆凡人国·云梦国', f'末段「云梦国」：{nm}/{how}')
nm, how = w.resolve_place('古藤废墟')
check(nm == '荒废传送阵·古藤废墟', f'末段「古藤废墟」：{nm}/{how}')
nm, how = w.resolve_place('落霞坪')
check(nm == '外门弟子修炼区·落霞坪', f'末段「落霞坪」：{nm}/{how}')

# ── 只写了中间那一段 ──
nm, how = w.resolve_place('王宫')
check(nm == '王宫·承天殿', f'段「王宫」：{nm}/{how}')

# ── 前后空白 ──
nm, _ = w.resolve_place('  云梦国  ')
check(nm == '南疆凡人国·云梦国', f'空白容忍：{nm}')

# ── 歧义必须拒绝，并说清候选 ──
nm, how = w.resolve_place('云梦')
check(nm == '' and '歧义' in how, f'歧义拒绝：{nm}/{how}')

# ── 编造的地名 / 方位表达式一律拒绝 ──
for bad in ('幽冥界', '天道宗上空', '南面三里', ''):
    nm, how = w.resolve_place(bad)
    check(nm == '', f'拒绝「{bad}」：{nm}/{how}')

# ── 白名单必须够深：N2 只能从这份名单里挑，名单里没有的地方它永远去不了 ──
# （这边曾经写死 max_depth=3，于是 韩家村(4层)、王宫·承天殿(5层) 全不在名单里）
places = gc.known_places()
check(any(p.endswith('·王宫·承天殿') for p in places), '白名单含 王宫·承天殿（5 层）')
check(any(p.endswith('·韩家村') for p in places), '白名单含 韩家村（4 层）')
check(any(p.endswith('·云梦泽·渔火渡') for p in places), '白名单含 渔火渡')

# ── 端到端：短名真的能落地 ──
st = gc.player.player_dict['当前状态CurrentState']
ok = gc.move_player_to('云梦国', '到了')
check(ok and st.get('当前所处位置') == '苍云大陆·南域·南疆凡人国·云梦国',
      f'落到云梦国：{ok} / {st.get("当前所处位置")}')

ok = gc.move_player_to('王宫', '到了')
check(ok and st.get('当前所处位置', '').endswith('王宫·承天殿'),
      f'落到王宫：{ok} / {st.get("当前所处位置")}')

# ── 端到端：编造的地名位置不动 ──
before = st.get('当前所处位置')
ok = gc.move_player_to('幽冥界', '到了')
check(not ok and st.get('当前所处位置') == before, '编造地名：位置不动')

print(f'\n== 地名解析 结果：{PASS} 通过 / {FAIL} 失败 ==')
sys.exit(0 if FAIL == 0 else 1)
