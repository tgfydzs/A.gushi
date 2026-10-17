# -*- coding: utf-8 -*-
"""时间链离线集成自检：历法 / 时间跳过换算 / N8 聚合 / 在途结算 / 心跳兜底"""
import sys
import types

import os
import tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# 构造 GameController 会建 LLMClient，而 common.base_cnf 在 **import 时**就取 API_KEY
# ——所以护栏必须在这之前，晚一行都来不及。
if not os.environ.get('DEEPSEEK_API_KEY'):
    print('SKIP：没有 DEEPSEEK_API_KEY（构造 GameController 需要它）')
    sys.exit(0)

from common import log

log.setup(log_file_path=os.path.join(tempfile.gettempdir(), 'g5_time_selftest.log'))


from common.game_clock import GameClock
from common.N8_time import N8_Time
from common.heartbeat import HeartbeatManager
from common.log import get_logger
from common.game_controller import GameController
from common.engine import GameEngine

ok = fail = 0


def check(name, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f'  [OK] {name}: {got}')
    else:
        fail += 1
        print(f'  [FAIL] {name}\n        got ={got!r}\n        want={want!r}')


D = 1440
YEAR = 360 * D

print('== 0. 拉起真实控制器（真地图 + 真存档格式）==')
gc = GameController(session_id='selftest-time')
gc.new_game()
check('玩家已载入', gc.player is not None, True)
check('起点时间', gc.game_clock.now_str(), '太初历三千七百四十二年·仲夏·午时初刻')
check('白鹤在库里', gc._find_npc('白鹤（天道宗外门长老，道号）、白雨桐（本名）') is not None, True)

print('== 1. 历法推进（玩家"外门2年"）==')
gc.game_clock.advance(2 * YEAR)
check('+2年', gc.game_clock.now_str(), '太初历三千七百四十四年·仲夏·午时初刻')
gc.game_clock.advance(30 * D)
check('+2年1月', gc.game_clock.now_str(), '太初历三千七百四十四年·季夏·午时初刻')

print('== 2. 时间跳过换算（ _declared_span_minutes ）==')
fake = types.SimpleNamespace(gc=types.SimpleNamespace(game_clock=gc.game_clock),
                            n8_time=N8_Time())
conv = GameEngine._declared_span_minutes

cases = [
    ('跳过 1月', {'时间判定': '跳过', '时间跨度': {'值': 1, '单位': '月'}}, 30 * D),
    ('跳过 2年', {'时间判定': '跳过', '时间跨度': {'值': 2, '单位': '年'}}, 2 * YEAR),
    ('跳过 3时辰', {'时间判定': '跳过', '时间跨度': {'值': 3, '单位': '时辰'}}, 360),
    ('跳过 5日', {'时间判定': '跳过', '时间跨度': {'值': 5, '单位': '日'}}, 5 * D),
    ('提到 1年 → 不动', {'时间判定': '提及', '时间跨度': {'值': 1, '单位': '年'}}, 0),
    ('无时间 → 不动', {'时间判定': '无', '时间跨度': {'值': 0, '单位': '分钟'}}, 0),
    ('跳过但值0 → 不动', {'时间判定': '跳过', '时间跨度': {'值': 0, '单位': '年'}}, 0),
    ('跳过但单位是字符串 → 拒绝', {'时间判定': '跳过', '时间跨度': '1个月'}, 0),
    ('未知单位 → 拒绝', {'时间判定': '跳过', '时间跨度': {'值': 3, '单位': '光年'}}, 0),
    ('缺字段 → 不动', {'时间判定': '提及'}, 0),
    ('整个空 → 不动', {}, 0),
    ('数字型跨度按分钟', {'时间判定': '跳过', '时间跨度': 90}, 90),
]
for name, n2, want in cases:
    check(name, conv(fake, n2), want)

cap = N8_Time.MAX_DECLARED_MINUTES
check('超上限截断', conv(fake, {'时间判定': '跳过', '时间跨度': {'值': 99999, '单位': '年'}}), cap)

print('== 3. N8 聚合：跳过取 max，不叠加 ==')
n8 = N8_Time()
check('跳过 1月 覆盖 3 分钟活动', n8.aggregate(npc_minutes=[3], declared_span_minutes=30 * D), 30 * D)
check('跳过比活动小 → 用活动', n8.aggregate(npc_minutes=[3], declared_span_minutes=2), 3)
check('没有跳过 → 老口径', n8.aggregate(npc_minutes=[5], player_minutes=2), 5)

print('== 4. 大跨度跳过后在途按"已抵达"结算 ==')
hb = gc._find_npc('白鹤（天道宗外门长老，道号）、白雨桐（本名）')
hn = hb.name
han = gc.b1WorldLayer.name_of('韩家村')
check('韩家村可解析', bool(han), True)

gc.set_position(hb.current_state, name='天道宗')
check('先放到天道宗', gc.b1WorldLayer.name_at(hb.current_state), gc.b1WorldLayer.name_of('天道宗'))
gc.npc_travel[hn] = {'目标': han, 'frac': 0.15}
gc.resolve_travel_on_skip(600)          # 10 分钟：不满足 1 日门槛
check('小跳过不结算在途', hn in gc.npc_travel, True)

done = gc.resolve_travel_on_skip(30 * D)   # 跳一个月
check('大跳过结算 1 条', done, 1)
check('在途记录已清', gc.npc_travel, {})
check('白鹤已到韩家村', gc.b1WorldLayer.name_at(hb.current_state), han)

print('== 5. 心跳轮次兜底 ==')
hbk = HeartbeatManager.__new__(HeartbeatManager)
hbk.logger = get_logger('HBTest')
hbk.queue = None
hbk.n9 = None
hbk.cli = None
hbk._last_trigger_elapsed_minutes = 0
hbk._last_trigger_round = 0
hbk._heartbeat_count = 0
hbk._npc_last_hb = {}
hbk.state_region_kw = []
hbk.region_keywords = {}
hbk._regions_ready = True      # 这个用例用 __new__ 造的壳，区域派生走不到，直接标就绪
hbk._heartbeat_worker = lambda *a, **k: None   # 不起线程、不联网络
# 用一个**干净**的时钟：前面几节已经把 gc 的时钟推了两年，时间条件早就满足了
clock2 = GameClock('太初历三千七百四十二年·仲夏·午时', '时辰制')
clock2.advance(213)   # 与实测存档同值：93 轮只走了 213 分钟


def trig(round_no, wl=None):
    return hbk.try_trigger(game_clock=clock2, world_layer=wl or gc.b1WorldLayer,
                           all_npc_list=gc.all_npc_list, director_context=[],
                           world_state_list=[], event_context=[], npc_responses=[],
                           player_input='', player_inst=gc.player, round_no=round_no)


check('第5轮不触发（时间也没到）', trig(5), False)
check('第8轮靠轮次兜底触发', trig(8), True)
check('第9轮不重复触发', trig(9), False)
check('计数=1', hbk._heartbeat_count, 1)

clock2.advance(12 * 60)
check('时间推够 12 小时 → 触发', trig(10), True)
check('计数=2', hbk._heartbeat_count, 2)


class W:
    def __init__(self, cfg):
        self.cfg = cfg

    def get_config(self, k, default=None):
        return self.cfg.get(k, default)


hbk._last_trigger_round = 10
hbk._last_trigger_elapsed_minutes = clock2.elapsed_minutes
check('间隔配成 3 轮 → 第12轮不触发', trig(12, W({'心跳_最少轮次': 3})), False)
check('第13轮触发', trig(13, W({'心跳_最少轮次': 3})), True)
hbk._last_trigger_round = 20
check('配成 0 = 关闭轮次兜底', trig(30, W({'心跳_最少轮次': 0})), False)

print(f'\n== 结果：{ok} 通过 / {fail} 失败 ==')
sys.exit(1 if fail else 0)
