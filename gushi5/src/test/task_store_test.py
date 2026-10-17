# -*- coding: utf-8 -*-
"""任务系统离线自检：实体/状态机/期限/预设触发/通道兼容/节流/存档往返"""
import sys

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

log.setup(log_file_path=os.path.join(tempfile.gettempdir(), 'g5_task_selftest.log'))


from common.game_clock import GameClock
from common.game_controller import GameController
from common.engine import GameEngine
from common.task_store import (TaskStore, ACTIVE, DONE, FAILED, ABANDONED,
                               KIND_DELIVER, KIND_LONG)

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
HAN = '韩家村'
BAI = '白鹤（天道宗外门长老，道号）、白雨桐（本名）'
HANREN = '韩铁柱（青木山下猎人，自称）、韩大牛（小名）'
白鹤 = BAI

print('== 1. TaskStore 基本实体 ==')
ts = TaskStore()
clock = GameClock('太初历三千七百四十二年·仲夏·午时', '时辰制')
t1 = ts.new('去韩家村把人带回来', 白鹤, 标题='带人', 委托人='玩家',
            期限={'值': 1, '单位': '月'}, 登记时间=clock.now_str(), clock=clock)
check('拿到 id', t1['id'], 'T001')
check('状态', t1['状态'], ACTIVE)
check('期限换算成绝对时间', t1['截止时间'], '太初历三千七百四十二年·季夏·午时初刻')
check('截止分钟 = 30 天', t1['截止分钟'], 30 * D)

t1b = ts.new('去韩家村把人带回来', 白鹤)   # 同人同目标
check('同人同目标不重复建', t1b['id'], 'T001')
check('表里只有一条', len(ts.tasks), 1)

check('空目标被拒', ts.new('', 白鹤), None)
check('空执行者被拒', ts.new('做点什么', ''), None)

t2 = ts.new('守三年山门', 白鹤, 类型=KIND_LONG)
check('第二条独立 id', t2['id'], 'T002')
check('一个NPC能有多条', len(ts.owned(白鹤)), 2)
m = ts.npc_open_map()
check('派生视图取最早一条', m[白鹤]['id'], 'T001')

print('== 2. 状态机 ==')
check('更新进度', ts.update('T001', 进度='已到村口') and ts.get('T001')['进度'], '已到村口')
rec = ts.finish('T001', '人带回来了', clock)
check('了结', rec['状态'], DONE)
check('了结有结果', rec['结果'], '人带回来了')
check('了结有结算时间', bool(rec['结算时间']), True)
check('重复了结被忽略', ts.finish('T001'), None)
check('已结束的不再更新进度', ts.update('T001', 进度='x'), False)
check('了结后派生视图只剩 T002', list(ts.npc_open_map().values())[0]['id'], 'T002')

print('== 3. 逾期结算 ==')
ts2 = TaskStore()
c2 = GameClock('太初历三千七百四十二年·仲夏·午时', '时辰制')
a = ts2.new('三天内送到', 玩家_ := '玩家', 期限={'值': 3, '单位': '日'}, clock=c2)
check('3 天后的截止', a['截止分钟'], 3 * D)
check('没过期不结算', ts2.settle_expired(2 * D, c2), [])
out = ts2.settle_expired(3 * D, c2)
check('到点判失败', [t['id'] for t in out], ['T001'])
check('失败原因含逾期', '逾期' in out[0]['结果'], True)
check('已结束的不再重复结算', ts2.settle_expired(9 * D, c2), [])

print('== 4. 预设任务触发 ==')
pre = [{'id': 'P001', '标题': '查明石出', '目标': '查清石头来历', '执行者': '玩家',
        '类型': '交付', '触发': {'地点': ['青木山脉'], 'NPC': []},
        '期限': {'值': 2, '单位': '年'}, '完成条件': '查明'},
       {'id': 'P002', '标题': '黑风城', '目标': '摸清招募底细', '执行者': '玩家',
        '类型': '交付', '触发': {'地点': ['外围据点·黑风城']}, '期限': {'值': 6, '单位': '月'}},
       {'id': 'P003', '标题': '前置未完成', '目标': 'x', '执行者': '玩家',
        '触发': {'地点': ['青木山脉'], '前置任务': ['P001']}}]
ts3 = TaskStore()
made = ts3.sync_presets(pre, ['苍云大陆', '东域', '青木山脉'], [], clock=c2)
check('只触发地点匹配的', [t['预设id'] for t in made], ['P001'])
check('预设任务来源', made[0]['来源'], '预设')
check('预设任务带期限', made[0]['截止分钟'] > 0, True)
made2 = ts3.sync_presets(pre, ['苍云大陆', '东域', '青木山脉'], [], clock=c2)
check('已激活的不重复激活', made2, [])
# 前置任务完成后 P003 才该触发
ts3.finish(made[0]['id'], '查明了', c2)
made3 = ts3.sync_presets(pre, ['青木山脉'], [], clock=c2)
check('前置完成后 P003 触发', [t['预设id'] for t in made3], ['P003'])

print('== 5. 旧档迁移 ==')
ts4 = TaskStore()
n = ts4.migrate_legacy({白鹤: {'目标': '旧差事', '去向': '韩家村', '行程': '半路'}},
                       登记时间='太初历…', clock=c2)
check('迁移 1 条', n, 1)
check('迁移后执行者', ts4.tasks[0]['执行者'], 白鹤)
check('迁移后状态进行中', ts4.tasks[0]['状态'], ACTIVE)
check('迁移后用新 id', ts4.tasks[0]['id'], 'T001')

print('== 6. 存档往返 ==')
d = ts3.to_dict()
ts5 = TaskStore().from_dict(d)
check('往返条数', len(ts5.tasks), len(ts3.tasks))
check('往返 seq', ts5._seq, ts3._seq)
used_ids = {t['id'] for t in ts5.tasks}
nid = ts5.new('新的一件事', '玩家', clock=c2)['id']
check('往返后新 id 不与已有撞车', nid not in used_ids, True)
ts6 = TaskStore().from_dict({'tasks': [{'id': 'T007'}], 'seq': 2})
check('seq 小于现存 id 时按最大 id 补', ts6._seq, 7)

print('== 7. 真实控制器：任务通道 ==')
gc = GameController(session_id='selftest-task')
gc.new_game()
check('起点无任务', gc.tasks.tasks, [])
check('派生视图为空', gc.npc_tasks, {})

n_world = len(gc.b1WorldLayer.dict.get('世界状态列表') or [])
gc._apply_task_channel(白鹤, {'任务': {'新建': [{
    '标题': '带回拾物之人', '目标': '去韩家村把拾物之人带回来', '执行者': 白鹤,
    '类型': '交付', '期限': {'值': 1, '单位': '月'}, '完成条件': '人回到天道宗'}]}})
check('新通道建出任务', len(gc.tasks.open_tasks()), 1)
tid = gc.tasks.open_tasks()[0]['id']
check('派生视图跟随', gc.npc_tasks[白鹤]['id'], tid)

gc._apply_task_channel(白鹤, {'任务': {'更新': [{'id': tid, '进度': '已到村口'}]}})
check('更新进度落到表里', gc.tasks.get(tid)['进度'], '已到村口')

gc._apply_task_channel(白鹤, {'任务': {'了结': [{'id': tid, '结果': '人已带回'}]}})
check('了结后状态', gc.tasks.get(tid)['状态'], DONE)
check('了结写进了世界状态', len(gc.b1WorldLayer.dict['世界状态列表']) > n_world, True)
check('世界状态里有任务结果',
      any('带回拾物之人' in x for x in gc.b1WorldLayer.dict['世界状态列表']), True)

print('== 8. 旧通道仍认（且降级）==')
gc._apply_task_channel(白鹤, {'新任务': '去灵虚集买丹'})
old = gc.tasks.open_tasks()
check('旧新任务也能建', len(old), 1)
check('旧写法来源被标出', old[0]['来源'], '对话（旧写法）')
gc._apply_task_channel(白鹤, {'任务完成': True})
check('旧任务完成也能了结', gc.tasks.open_tasks(), [])

print('== 9. 无 id 的更新被拒（不静默）==')
before = len(gc.tasks.tasks)
gc._apply_task_channel(白鹤, {'任务': {'更新': [{'进度': '没写 id'}]}})
check('无 id 更新不产生新任务', len(gc.tasks.tasks), before)

print('== 10. 锁定名单节流 ==')
eng = GameEngine.__new__(GameEngine)
eng.gc = gc
gc.tasks.tasks = []
lt = gc.tasks.new('守三年山门', 白鹤, 类型=KIND_LONG, 标题='守山门')
dt_ = gc.tasks.new('去灵虚集买丹', BAI.replace('白鹤', '白鹤'), 标题='买丹') if False else None
gc.tasks.finish(lt['id'], 'x', gc.game_clock)
at = gc.tasks.new('去灵虚集买丹', 白鹤, 类型=KIND_DELIVER, 标题='买丹')

eng.round_no = 10
res = eng._with_task_npcs([])
check('交付型第 10 轮被推进', any('买丹' in m for _, m in res), True)
check('推进后记了轮次', at['最近推进轮'], 10)
check('已经在本轮名单里的人不重复并', len(eng._with_task_npcs([('白鹤', 'x')])), 1)

gc.tasks.tasks = []
lt2 = gc.tasks.new('守三年山门', 白鹤, 类型=KIND_LONG, 标题='守山门')
lt2['最近推进轮'] = 10
eng.round_no = 12
res = eng._with_task_npcs([])
check('长期型第 12 轮（间隔内）不推', res, [])
eng.round_no = 16
res = eng._with_task_npcs([])
check('长期型第 16 轮（满 6 轮）才推', len(res), 1)

print('== 11. 真实存档往返（含任务表）==')
gc.tasks.tasks = []
gc.tasks.new('去韩家村把拾物之人带回来', 白鹤, 标题='带回拾物之人',
             期限={'值': 1, '单位': '月'}, clock=gc.game_clock)
state = gc.build_save_state('selftest')
check('存档里有 tasks', 'tasks' in state, True)
check('存档里没有 npc_tasks（不再是第二真源）', 'npc_tasks' in state, False)
gc2 = GameController(session_id='selftest-task2')
gc2.new_game()
gc2.rebuild_game_state(state)
check('读档后任务还在', len(gc2.tasks.open_tasks()), 1)
check('读档后 id 一致', gc2.tasks.open_tasks()[0]['id'], gc.tasks.open_tasks()[0]['id'])
check('读档后派生视图一致', gc2.npc_tasks[白鹤]['标题'], '带回拾物之人')

print('== 12. 只读视图 / 描述 ==')
desc = TaskStore.describe(gc2.tasks.open_tasks()[0])
check('描述含 id', 'T' in desc, True)
check('描述含期限', '期限' in desc, True)

print(f'\n== 结果：{ok} 通过 / {fail} 失败 ==')
sys.exit(1 if fail else 0)
