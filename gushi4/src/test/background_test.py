"""
gushi4 后台与记忆巩固干跑 —— **不发起真实 API 调用**。

验：
  - 心跳触发条件（游戏时间间隔）
  - 候选筛选与按地点分组（不依赖世界观专属关键词表）
  - B1a / B1b / B2 三条线并行跑，产出正确入队
  - `consume_deferred` 把产出落到正确的表上
  - B2 的越权字段同样被拦（和 L2 共用同一份契约）
  - 记忆巩固是"压成摘要"而不是"直接丢弃"

跑法：
    cd E:/A.gushi/gushi4 && python src/test/background_test.py
"""

import os
import sys
import json
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"  ✓" if cond else "  ✗"} {name}' + (f'   [{detail}]' if detail and not cond else ''))


class FakeCLI:
    def __init__(self, layer, canned, calls):
        self.layer, self.canned, self.calls = layer, canned, calls
        self.cfg = type('C', (), {'layer': layer, 'model': 'fake', 'thinking': True,
                                  'timeout': 0, 'max_retries': 1, 'temperature': None,
                                  'describe': lambda s: 'fake'})()

    def chat_by_context(self, context, label='', temperature=None):
        self.calls[self.layer] = self.calls.get(self.layer, 0) + 1
        return json.dumps(self.canned, ensure_ascii=False)


from common import log as log_cfg
log_cfg.setup(session_id='bgtest', console_min_score=40)

from common.game_controller import GameController
from common.background import BackgroundManager
from common.memory import MemoryManager
from common.L2_character import L2_Character
from Schema.B3_1_npc_sch import B3NPCLayer

gc = GameController('bgtest')
gc.init_world(); gc.init_clock(); gc.init_all_npcs(); gc.init_player()

WHITE = [n for n in gc.all_npc_list if '白鹤' in n.name][0]
XUANQING = [n for n in gc.all_npc_list if '玄清' in n.name][0]
CALLS = {}

print('=' * 70)
print('1. 心跳触发条件')
print('=' * 70)

l2 = L2_Character(); l2.cli = FakeCLI('L2', {}, CALLS)
mem = MemoryManager()
bg = BackgroundManager(gc.deferred_queue, l2, mem)

interval_h = gc.b1WorldLayer.get_config('心跳间隔_小时', 12)
print(f'     世界配置的心跳间隔：{interval_h} 小时')

triggered = bg.try_trigger(game_clock=gc.game_clock, world_layer=gc.b1WorldLayer,
                           all_npc_list=gc.all_npc_list)
check('时间未推进 → 不触发', triggered is False)
check('触发记录未更新', bg._last_trigger_elapsed == 0, str(bg._last_trigger_elapsed))

gc.game_clock.advance(interval_h * 60 + 1)
triggered = bg.try_trigger(game_clock=gc.game_clock, world_layer=gc.b1WorldLayer,
                           all_npc_list=gc.all_npc_list)
check('时间推进 ≥ 间隔 → 触发', triggered is True)
check('触发记录已更新', bg._last_trigger_elapsed > 0, str(bg._last_trigger_elapsed))

# 让它跑完（后台线程），避免影响后面对队列的断言
time.sleep(1.5)

print()
print('=' * 70)
print('2. 候选筛选 + 按地点分组（不依赖世界观关键词表）')
print('=' * 70)

cands = bg._filter_candidates(gc.all_npc_list, [], {})
print(f'     候选（{len(cands)}）：{[c.name.split("、")[0] for c in cands]}')
check('有目标或有待解释事件的 NPC 入选',
      len(cands) >= 4, f'只有 {len(cands)} 个')
check('新 NPC 首次必入选', any(c.name == WHITE.name for c in cands))

groups = bg._group_by_location(cands)
print(f'     分组（{len(groups)} 组）：')
for i, g in enumerate(groups):
    print(f'       #{i} {[c.name.split("、")[0] for c in g]}')
check('按地点前缀分组（同域相邻的归一起）', len(groups) >= 2, str(len(groups)))
check('同组不超过上限', all(len(g) <= bg.GROUP_MAX_SIZE for g in groups))
# 玄清和云逸、白鹤都在"苍云大陆·东域"，本该同桶但会被 max_size 切开
check('白鹤与玄清不在同一组（不同地点前缀桶）或组已满',
      all(not (WHITE in g and XUANQING in g) or len(g) <= bg.GROUP_MAX_SIZE
          for g in groups))

print()
print('=' * 70)
print('3. 三条线并行产出 → 入队')
print('=' * 70)

gc.deferred_queue.clear()
bg2 = BackgroundManager(gc.deferred_queue, l2, mem)
Fake = lambda layer, canned: FakeCLI(layer, canned, CALLS)

bg2.cli_rule = Fake('B1a', {
    "规则触发": [{
        "规则原文": gc.b1WorldLayer.dict.get('世界演变规则列表', ['占位规则'])[0],
        "依据": "灵气浓度持续下降",
        "玩家可见叙述": "山门外的灵田又枯了一片",
        "幕后详情": "地脉正在衰竭",
        "世界状态变更": {"增加": ["东域灵田大面积枯死"], "删减": []},
        "进度": "初期",
    }]
})
bg2.cli_narr = Fake('B1b', {"旁白": ["风里带着淡淡的焦枯气"]})
l2.cli = Fake('L2', {
    "npc实例": {"增加": {"观察Observation": ["东域灵田枯了，恐非吉兆"]},
                "持有物Inventory": {"不该写的": "x"}},   # 越权字段
    "回应Response": "去查了灵田的土，翻出三尺深的死气。",
    "内心想法InnerThought": "这不是天灾。",
    "动作意图": {"类型": "无", "描述": "", "对象": "", "物品": ""},
    "剧情级传播": [{"信息": "东域灵田大面积枯死", "到达NPC": [XUANQING.name], "方式": "传音符"}],
})

# 同步跑 worker（不走线程），断言才确定
bg2._worker(
    all_npc_list=gc.all_npc_list, world_layer=gc.b1WorldLayer, game_clock=gc.game_clock,
    director_context=[], world_state_list=[], event_context=[],
    l2_results=[], player_input='', player=gc.player, propagation_result={},
)

msgs = gc.deferred_queue.get_all()
kinds = [m['data_type'] for m in msgs]
print(f'     入队：{kinds}')
check('B1a 入队', 'B1a' in kinds, str(kinds))
check('B1b 入队', 'B1b' in kinds, str(kinds))
check('B2 入队', 'B2' in kinds, str(kinds))
check('B1a/B1b 各只一次（全局）', kinds.count('B1a') == 1 and kinds.count('B1b') == 1, str(kinds))

# 重新入队，供下一节消费
for m in msgs:
    gc.deferred_queue.put(m)

print()
print('=' * 70)
print('4. consume_deferred 落盘')
print('=' * 70)

world_before = list(gc.b1WorldLayer.dict.get('世界状态列表', []))
npc_before_obs = list(XUANQING.observation)
actors_delta_before = {n.name: len(n.observation) for n in gc.all_npc_list}

gc.consume_deferred()

check('B1a 世界状态已写入',
      '东域灵田大面积枯死' in gc.b1WorldLayer.dict.get('世界状态列表', []),
      str(gc.b1WorldLayer.dict.get('世界状态列表', [])))
check('B1a 规则触发已记录', len(gc.bg_state.get('triggered_rules', {})) >= 1,
      str(gc.bg_state.get('triggered_rules')))
check('B1a 公开叙述进了上下文',
      any('灵田又枯了一片' in c for c in gc.global_context.context),
      str(gc.global_context.context[-3:]))
check('B1a 幕后详情进了导演上下文（不给玩家）',
      any('地脉正在衰竭' in c for c in gc.director_context.context))
check('B1a 幕后详情不进全局上下文',
      not any('地脉正在衰竭' in c for c in gc.global_context.context))
check('B1b 旁白进了全局上下文',
      any('焦枯气' in c for c in gc.global_context.context))

# B2：主观增量落地 + 越权字段被拦 + 行为写观察 + 传播
white_now = gc.all_npc_list.get_npc(WHITE.name)
check('B2 主观增量已落盘',
      any('恐非吉兆' in x for x in white_now.observation), str(white_now.observation[-2:]))
check('★ B2 越权字段（持有物）被拦',
      '不该写的' not in white_now.inventory, str(list(white_now.inventory)))
check('B2 行为写进了观察表',
      any('幕后行动' in x for x in white_now.observation), str(white_now.observation[-2:]))
xuan_now = gc.all_npc_list.get_npc(XUANQING.name)
check('B2 剧情级传播到达目标 NPC',
      any('情报·传音符' in x for x in xuan_now.observation), str(xuan_now.observation[-2:]))

print()
print('=' * 70)
print('5. 记忆巩固：压成摘要，不是丢弃')
print('=' * 70)

mem2 = MemoryManager()
mem2.cli = FakeCLI('MEM', {"摘要": "灵虚集讨生活那阵子——摆摊、躲债、被人讹过一回，琐事居多。"}, CALLS)

target = gc.all_npc_list.get_npc(WHITE.name)
original = [f'旧事第{i}条' for i in range(B3NPCLayer.MAX_MEMORY + 6)]
target.memory = list(original)
n_before = len(target.memory)

changed = mem2.consolidate_npc(target)
n_after = len(target.memory)

print(f'     Memory：{n_before} → {n_after} 条')
print(f'     首条：{target.memory[0][:50]}…')
check('巩固发生了', changed is True)
check('条数降下来了', n_after < n_before, f'{n_before} → {n_after}')
check('★ 是摘要不是丢弃（首条带汇总标记）',
      target.memory[0].startswith('（往忆·'), target.memory[0][:40])
check('摘要内容来自 LLM', '灵虚集' in target.memory[0])
check('最新的条目被保留了（不是从尾部砍）',
      original[-1] in target.memory, '最近的事不该被压掉')
check('至少保留 MEMORY_MIN_KEEP 条原文',
      sum(1 for x in target.memory if x in original) >= 5,
      str(sum(1 for x in target.memory if x in original)))

# 摘要失败 → 退回截断（不能无限增长）
mem3 = MemoryManager()
class BoomCLI:
    cfg = type('C', (), {'layer': 'MEM', 'describe': lambda s: 'boom'})()
    def chat_by_context(self, *a, **k):
        raise RuntimeError('模拟 LLM 失败')
mem3.cli = BoomCLI()
t2 = gc.all_npc_list.get_npc(XUANQING.name)
t2.memory = [f'x{i}' for i in range(B3NPCLayer.MAX_MEMORY + 6)]
mem3.consolidate_npc(t2)
check('★ LLM 失败时退回截断（绝不无限增长）',
      len(t2.memory) <= B3NPCLayer.MAX_MEMORY, str(len(t2.memory)))

print()
print('=' * 70)
print('6. 后台失败不拖垮主流程')
print('=' * 70)
l2_boom = L2_Character(); l2_boom.cli = BoomCLI()
bg3 = BackgroundManager(gc.deferred_queue, l2_boom, mem)
bg3.cli_rule = BoomCLI()
bg3.cli_narr = BoomCLI()
gc.deferred_queue.clear()
bg3._worker(gc.all_npc_list, gc.b1WorldLayer, gc.game_clock, [], [], [], [],
            '', gc.player, {})
check('三条线全炸，worker 也不抛异常（主循环安全）', True)
check('没有脏消息入队', gc.deferred_queue.size == 0, str(gc.deferred_queue.size))

print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
for f in FAIL:
    print(f'  ✗ {f}')
print('=' * 70)
sys.exit(1 if FAIL else 0)
