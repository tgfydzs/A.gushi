"""
gushi4 端到端干跑 —— 用**假 LLM** 跑完整轮次，验证管线闭合。

**不发起任何真实 API 调用。** 每个层的 `cli` 被换成 `FakeCLI`，
按调用方给的 label 返回预设 JSON。

验的是**管道**，不是模型：
  - L1 的感知分发能不能流到 L2
  - L2 的主观增量能不能落到 NPC 表上
  - L2 的动作意图能不能流到 L3
  - L3 的客观增量能不能落到玩家和 NPC 上
  - S1 的倍数有没有真的进 R2
  - 交易破裂（玩家付不出）时，双方是不是都不变
  - 时钟有没有推进

跑法：
    cd <项目根> && python src/test/dry_run.py
"""

import os
import sys
import json
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"  ✓" if cond else "  ✗"} {name}' + (f'   [{detail}]' if detail and not cond else ''))


# ═══════════════════ 假 LLM ═══════════════════

class FakeCLI:
    """按 label 返回预设 JSON 的假客户端。记录调用次数，验证并行确实并行了。"""

    def __init__(self, layer, canned, calls=None):
        self.layer = layer
        self.canned = canned
        self.calls = calls if calls is not None else {}
        self.cfg = type('C', (), {'layer': layer, 'model': 'fake',
                                  'thinking': True, 'timeout': 0,
                                  'max_retries': 1, 'temperature': None,
                                  'describe': lambda s: 'fake'})()

    def chat_by_context(self, context, label='', temperature=None):
        key = self.layer
        self.calls[key] = self.calls.get(key, 0) + 1
        # 记下 L2 实际收到的感知输入，供后面断言
        try:
            payload = json.loads(context[-1]['content'])
        except Exception:
            payload = {}
        if key == 'L2':
            self.calls.setdefault('L2_inputs', []).append(payload)
        if key == 'L3':
            self.calls.setdefault('L3_input', []).append(payload)
        if key == 'L1':
            self.calls.setdefault('L1_input', []).append(payload)
        return json.dumps(self.canned, ensure_ascii=False)

    def chat(self, *a, **k):
        return self.chat_by_context([], self.layer)


# ═══════════════════ 预设响应 ═══════════════════

from common.game_controller import GameController
from common.L1_perception import L1_Perception
from common.L2_character import L2_Character
from common.L3_settlement import L3_Settlement
from common.R1_propagation import R1_Propagation
from common.R2_time import R2_Time
from common import log as log_cfg

log_cfg.setup(session_id='dryrun', console_min_score=40)

gc = GameController('dryrun')
gc.init_world()
gc.init_clock()
gc.init_all_npcs()
gc.init_player()

WHITE_CRANE = [n for n in gc.all_npc_list if '白鹤' in n.name][0]
# 挑一个**不在落霞坪**的 NPC，用来验证"传闻不受距离限制"（距离判断已交回 L1）
NEIGHBOR = [n for n in gc.all_npc_list
            if n.current_state.get('当前所处位置') != gc.player_location][0]
PLAYER = gc.player.player_dict['玩家名称name']
PLAYER_STATE = gc.player.player_dict['当前状态CurrentState']

print('=' * 70)
print(f'干跑：{PLAYER} @ {gc.player_location}')
print(f'对手：{WHITE_CRANE.name}')
print('=' * 70)

CALLS = {}

# —— L1：判定为一个公开事件，白鹤在场 ——
l1_canned = {
    "is_new_event": True,
    "事件标题": "弟子向长老索药",
    "scope": "public",
    "add_npc": [WHITE_CRANE.name],
    "del_npc": [],
    "add_related_npc": [],
    "del_related_npc": [],
    "直接对象": [WHITE_CRANE.name],
    # 注意：这段感知是**真正失真**的——它没有复述玩家的真实等级。
    # 一个金丹后期的长老看炼气期弟子，本来就能看透，但措辞仍是"他"的说法，
    # 而不是把 CurrentState 里的字面值抄一遍。
    "感知分发": {
        WHITE_CRANE.name: "玩家对你说：长老，弟子想要一颗聚气丹。你一眼便看出这弟子根基浅薄、"
                          "灵力驳杂，连内门都够不上，站在你面前时连头都不敢抬。"
    },
    # 新 schema：传播范围由 L1 直接给名单（原来这里是 level+mask，由 R1 按段数算）
    "地段传播": {
        "消息": "玩家在落霞坪向白鹤长老讨要丹药",
        "传到": [WHITE_CRANE.name, NEIGHBOR.name],
    },
}

# —— L2：白鹤展开怀疑 + 给出意图（**给出一把剑，想换玩家的灵石**）——
l2_canned = {
    "npc实例": {
        "增加": {
            "观察Observation": ["这弟子当众向我讨药，胆子不小"],
            "待解释事件PendingReasoning": ["他为何不去丹房领，偏来找我？"],
            # ↓ 越权字段：应被代码丢弃
            "持有物Inventory": {"清霜剑": "不该被 L2 写进去"},
        },
        "删减": {},
    },
    "回应Response": "聚气丹？去丹房领你的月例去。",
    "内心想法InnerThought": "他若真拿得出灵石，倒可以试试他。",
    "动作意图": {"类型": "给予", "描述": "把清霜剑给他，换他手里的灵石",
                 "对象": PLAYER, "物品": "清霜剑"},
}

# —— L3：**玩家付不出灵石 → 交易破裂，双方都不变** ——
l3_canned = {
    "玩家实例": {"增加": {}, "删减": {}},
    "NPC实例": {},
    "旁白": ["白鹤长老袖袍一拂，转身欲走"],
    "本轮耗时分钟": 3,
    "耗时理由": "简短交谈",
    "破裂观察": {WHITE_CRANE.name: "玩家掏不出灵石，交易当场破裂"},
    # ↓ 越权字段：应被代码丢弃
    "_test": None,
}
l3_canned['NPC实例'] = {WHITE_CRANE.name: {"增加": {"好感度Relationship": ["+3"]}, "删减": {}}}

# —— S1：疾行 ——


# ═══════════════════ 装配各层（替换 cli）═══════════════════

l1 = L1_Perception(); l1.set_world(gc.b1WorldLayer); l1.cli = FakeCLI('L1', l1_canned, CALLS)
l2 = L2_Character(); l2.cli = FakeCLI('L2', l2_canned, CALLS)
l3 = L3_Settlement(); l3.cli = FakeCLI('L3', l3_canned, CALLS)
r1 = R1_Propagation()
r2 = R2_Time(gc.b1WorldLayer)


# ═══════════════════ 跑一轮 ═══════════════════

player_input = '我对白鹤长老说：长老，弟子想要一颗聚气丹。'
loc_before = gc.player_location
sword_before = dict(WHITE_CRANE.inventory)
player_inv_before = dict(gc.player.player_dict['持有物Inventory'])
clock_before = gc.game_clock.now_str()

gc.add_context_all(f'{PLAYER}（玩家）：{player_input}')

# （S1 旁路已摘除：移动耗时改由 L3 判断，见 common/R2_time.py）
l1_result = l1.judge(
    event_context=gc.event_context, event_layer=gc.event_layer,
    all_npc_list=gc.all_npc_list, player=gc.player,
    director_context=gc.director_context, world_layer=gc.b1WorldLayer,
    player_input=player_input,
)
print(f'\n[L1] scope={l1_result["scope"].value} 锁定={l1_result["锁定名单"]} '
      f'传闻传到={l1_result["地段传播"].get("传到")}')

# L2 并行
perceptions = l1_result['感知分发']
tasks = [(gc.all_npc_list.get_npc(n), msg) for n, msg in perceptions.items()]
tasks = [(n, m) for n, m in tasks if n]
l2_results = []
with ThreadPoolExecutor(max_workers=4) as p:
    for r in p.map(lambda t: l2.respond_onstage(t[0], t[1], []), tasks):
        l2_results.append(r)
for r in l2_results:
    gc.apply_l2_subjective(r)
print(f'[L2] {[(r["name"].split("、")[0], r["动作意图"]["类型"]) for r in l2_results]}')

# R1
prop = r1.propagate(l1_result['地段传播'], gc.b1WorldLayer, gc.all_npc_list,
                    list(perceptions.keys()), loc_before, l1_result['scope'])
print(f'[R1] 传闻到达 {len(prop)} 人')

# L3
locked = [gc.all_npc_list.get_npc(n) for n in l1_result['锁定名单']]
locked = [n for n in locked if n]
l3_result = l3.settle_with_npcs(gc.player, locked, l2_results, player_input,
                                loc_before, None,
                                gc.b1WorldLayer.dict.get('世界状态列表', []))
gc.apply_l3_settlement(l3_result)
print(f'[L3] 旁白={l3_result["旁白"]} 破裂={l3_result["破裂观察"]}')

# R2：耗时由 L3 判断（原地活动 + 赶路一起），R2 只推进时钟
total = r2.process(game_clock=gc.game_clock, minutes=l3_result['本轮耗时分钟'])
clock_after = gc.game_clock.now_str()
print(f'[R2] +{total}分 → {clock_after}')


# ═══════════════════ 断言 ═══════════════════

print()
print('=' * 70)
print('管线闭合检查')
print('=' * 70)

check('L1 被调用一次', CALLS.get('L1') == 1, str(CALLS.get('L1')))
check('L2 被调用一次（1 个锁定 NPC）', CALLS.get('L2') == 1, str(CALLS.get('L2')))
check('L3 被调用一次', CALLS.get('L3') == 1, str(CALLS.get('L3')))
# S1 旁路已摘除（移动耗时改由 L3 判断），关键路径现在是 L1 → L2 → L3 三跳
check('★ S1 不再被调用（旁路已摘除）', CALLS.get('S1') is None, str(CALLS.get('S1')))

# —— L1 → L2 的感知真的传下去了 ——
l2_input = CALLS.get('L2_inputs', [{}])[0]
perception_seen = l2_input.get('感知输入', '')
check('L1 的失真感知流到了 L2',
      '根基浅薄' in perception_seen, perception_seen[:60])

# —— L2 绝不能看到玩家实况 ——
# 用玩家持有物里的具体条目名去搜，比搜字段名更硬——字段名可以改，东西的名字改不了
l2_blob = json.dumps(l2_input, ensure_ascii=False)
leaked = [item for item in player_inv_before if item in l2_blob]
check('★ L2 输入里没有玩家的任何一件持有物', not leaked, f'泄漏了 {leaked}')
check('L2 输入里没有玩家描述', '描述Description' not in l2_blob)
check('L2 输入里没有玩家的真实实力字段名外的痕迹',
      PLAYER_STATE.get('实力等级') not in l2_blob,
      f'玩家实力 {PLAYER_STATE.get("实力等级")} 出现在 L2 输入里')
check('L2 输入里的持有物是 NPC 自己的（该给的要给）',
      l2_input.get('你是谁', {}).get('持有物Inventory', {}) == sword_before,
      'NPC 自己的表应当可见')

# —— L2 主观增量落地，越权字段被拦 ——
wc = gc.all_npc_list.get_npc(WHITE_CRANE.name)
check('L2 观察增量已落盘', any('胆子不小' in x for x in wc.observation), str(wc.observation[-2:]))
check('L2 待解释事件已落盘', any('为何不去丹房' in x for x in wc.pending_reasoning),
      str(wc.pending_reasoning[-2:]))
check('★ L2 写入的持有物被拦截（NPC 的剑没被它改）',
      wc.inventory == sword_before,
      f'{list(wc.inventory)} vs {list(sword_before)}')

# —— L3 客观增量：越权字段被拦，破裂观察注入 ——
check('★ L3 写入的好感度被拦截', not any('+3' in str(x) for x in wc.relationship),
      str(wc.relationship))
check('★ 交易破裂：玩家掏不出灵石 → 双方持有物都没变',
      wc.inventory == sword_before and gc.player.player_dict['持有物Inventory'] == player_inv_before,
      f'NPC={list(wc.inventory)} 玩家={list(gc.player.player_dict["持有物Inventory"])}')
check('破裂观察已注入 NPC 观察表',
      any('交易当场破裂' in x for x in wc.observation), str(wc.observation[-2:]))

# —— L3 看不到 NPC 的主观表 ——
l3_input = CALLS.get('L3_input', [{}])[0]
npcs_in = l3_input.get('参与NPC客观侧', [])
obj_tables = set()
for n in npcs_in:
    obj_tables |= set(n.keys())
check('L3 只拿到 NPC 客观侧（名字+2张表）',
      obj_tables <= {'名字', '当前状态CurrentState', '持有物Inventory'}, str(obj_tables))
check('L3 拿到 NPC 的持有物',
      any(n.get('持有物Inventory') == sword_before for n in npcs_in))
check('L3 拿到玩家的全量实况',
      '持有物Inventory' in l3_input.get('玩家实况', {})
      and '描述Description' in l3_input.get('玩家实况', {}))

# —— L3 判断的耗时原样进了时钟（R2 不再做任何折算）——
check('★ L3 的耗时原样落进时钟（R2 不折算）',
      total == l3_result['本轮耗时分钟'], f'total={total}')
# 注意不能断言 now_str() 变了：这个世界的时刻格式是"时辰·刻"，一刻 = 30 分钟，
# 走 3 分钟时间戳纹丝不动是**正确**行为。要看时钟有没有走，得看 _elapsed。
check('时钟推进了（按 _elapsed 判定）',
      gc.game_clock._elapsed == 3, f'_elapsed={gc.game_clock._elapsed}')

# —— 事件层 ——
check('事件层记录了 scope', gc.event_layer.scope.value == 'public', gc.event_layer.scope.value)
check('事件层记录了锁定名单', WHITE_CRANE.name in gc.event_layer.involved)
check('事件层轮次 +1', gc.event_layer.rounds == 1, str(gc.event_layer.rounds))

# —— 快照/回滚 ——
snap = gc.snapshot()
gc.apply_player_delta({'增加': {'当前状态CurrentState': {'实力等级': '被人改坏了'}}, '删减': {}})
gc.restore(snap)
check('回滚后玩家状态恢复',
      gc.player.player_dict['当前状态CurrentState'].get('实力等级') != '被人改坏了',
      gc.player.player_dict['当前状态CurrentState'].get('实力等级'))

print()
print('=' * 70)
print('额外：耗时链路（L3 判断 → R2 推进时钟）')
print('=' * 70)
_before = gc.game_clock._elapsed
got = r2.process(game_clock=gc.game_clock, minutes=345600)
print(f'  L3 报 345600 分（八个月） → 时钟推进 {got} 分 → {gc.game_clock.now_str()}')
check('L3 判断的分钟数原样落进时钟',
      got == 345600 and gc.game_clock._elapsed == _before + 345600, str(got))
check('距离/倍数折算确实没了', not hasattr(r2, 'estimate_move_minutes'))

print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
for f in FAIL:
    print(f'  ✗ {f}')
print('=' * 70)
sys.exit(1 if FAIL else 0)
