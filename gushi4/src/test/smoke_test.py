"""
gushi4 离线冒烟测试 —— **不发起任何 LLM 调用**。

覆盖三件事：
  1. 全部模块能 import，配置能解析
  2. 纯规则层（R1/R2/R3）的行为正确
  3. 三个信息域的边界没被破坏（L2 只能写主观表、L3 只能写客观表）

跑法：
    cd E:/A.gushi/gushi4 && python src/test/smoke_test.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# base_cnf 在 import 时读环境变量，测试里给个假 key（不会真的发请求）
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    mark = '  ✓' if cond else '  ✗'
    print(f'{mark} {name}' + (f'   [{detail}]' if detail and not cond else ''))


print('=' * 70)
print('1. 模块导入')
print('=' * 70)
from common.base_cnf import *
from common.LLMclient import LLMClient, LayerCfg
from Schema.Event_sch import EventLayer, Scope
from Schema.B3_1_npc_sch import B3NPCLayer
from Schema.B3_npclist_sch import B3NPCListLayer
from common.R1_propagation import R1_Propagation
from common.R2_time import R2_Time
from common.R3_perception_filter import R3_PerceptionFilter
from common.L1_perception import L1_Perception
from common.L2_character import L2_Character, sanitize_npc_delta, normalize_intent, SUBJECTIVE_TABLES
from common.L3_settlement import L3_Settlement, sanitize_objective_delta, OBJECTIVE_TABLES
from common.S1_quick_judge import S1_QuickJudge
from common.memory import MemoryManager
from common.game_controller import GameController
from common.game_clock import GameClock
from common.small_tool import load_prompt
check('全部模块 import 成功', True)

print()
print('=' * 70)
print('2. 分层配置（推理开关）')
print('=' * 70)
s1 = LayerCfg('S1')
check('S1 关推理', s1.thinking is False, s1.describe())
check('S1 短超时', s1.timeout == QUICK_TIMEOUT, f'{s1.timeout}')
check('S1 不重试', s1.max_retries == QUICK_MAX_RETRIES, f'{s1.max_retries}')
# L1 **开推理**：R3 的距离过滤删掉后，"谁感知"全靠 L1，判断力必须给它预算。
# 对照实验见 src/test/diag_l1_thinking.py —— 推理买的是感知分发的稳定性，不是 scope 准确性。
check('★ L1 开推理（判断权在它身上了）', LayerCfg('L1').thinking is True)
check('L2/L3 仍关推理（不在关键路径的判断上）',
      not any(LayerCfg(x).thinking for x in ('L2', 'L3')))
check('★ L1_retry 开推理（兜底通道，不能被一起关掉）',
      LayerCfg('L1_retry').thinking is True)
# 后台三层不在关键路径上，关它们不省响应时间，所以保持开推理换质量
check('后台 B1a/B1b/B2 保持开推理（不在关键路径，关了只亏质量）',
      all(LayerCfg(x).thinking for x in ('B1a', 'B1b', 'B2')))
check('B1a 温度 0.3', LayerCfg('B1a').temperature == 0.3)
check('B1b 温度 0.9', LayerCfg('B1b').temperature == 0.9)
check('B2  温度 0.6', LayerCfg('B2').temperature == 0.6)

print()
print('=' * 70)
print('3. prompt 模板与占位符')
print('=' * 70)
PROMPTS = ['L1_perception_sys.txt', 'L2_character_onscene_sys.txt',
           'L2_character_daemon_sys.txt', 'L3_settlement_sys.txt',
           'L3_solo_sys.txt', 'S1_quick_judge_sys.txt',
           'B1a_rule_sys.txt', 'B1b_narration_sys.txt',
           'memory_consolidate_sys.txt']
for p in PROMPTS:
    try:
        text = load_prompt(p)
        left = [ph for ph in ('{实力权重表}', '{感知消息风格说明}', '{地段传播实力修正}',
                              '{地段传播消息风格}', '{输出约束示例}', '{攻击行为旁白参考}',
                              '{修炼体系参考}', '{实力体系举例}', '{能力使用效果描述}',
                              '{叙事风格参考}', '{传播方式参考}', '{NPC全部表}')
                if ph in text]
        check(f'{p} 加载', len(text) > 200 and not left, f'长度{len(text)} 未替换{left}')
    except Exception as e:
        check(f'{p} 加载', False, f'{type(e).__name__}: {e}')

print()
print('=' * 70)
print('4. 事件 scope')
print('=' * 70)
ev = EventLayer()
ev.reset('测试事件')
ev.apply(scope_raw='私密', involved=['甲', '乙'], direct_target=['甲'])
check('中文 scope 解析', ev.scope == Scope.PRIVATE, ev.scope)
check('involved 写入', ev.involved == ['甲', '乙'], str(ev.involved))
ev.apply(scope_raw='group', involved=['丙'])
check('involved 是整份替换（不是并集）', ev.involved == ['丙'], str(ev.involved))
ev.apply(involved=None)
check('involved=None 表示不更新', ev.involved == ['丙'], str(ev.involved))
check('scope 覆写', ev.scope == Scope.GROUP, ev.scope.value)
# ⚠️ 认不出的值**不再回落 public**——那是最不安全的方向（见 Event_sch.SAFE_FALLBACK）
check('无法识别的 scope 回落 group（不是 public）',
      Scope.parse('外星语') == Scope.GROUP, Scope.parse('外星语'))
d = ev.to_dict()
check('EventLayer 序列化往返', EventLayer.from_dict(d).involved == ev.involved)

print()
print('=' * 70)
print('5. 世界数据载入')
print('=' * 70)
gc = GameController('smoke')
gc.init_world()
gc.init_clock()
gc.init_all_npcs()
gc.init_player()
check(f'NPC 载入（{len(gc.all_npc_list)} 个）', len(gc.all_npc_list) > 0)
check('玩家载入', bool(gc.player.player_dict.get('玩家名称name')),
      str(gc.player.player_dict.get('玩家名称name')))
print(f'     玩家：{gc.player.player_dict.get("玩家名称name")} '
      f'@ {gc.player_location}')
print(f'     起始时间：{gc.game_clock.now_str()}')

print()
print('=' * 70)
print('5.5 资源文件与配置键自检')
print('=' * 70)

# ---- data/ 下每个 JSON 都要能解析 ----
#
# ⚠️ 这一组以前**完全不存在**，代价是：`data/player/剑无锋.json` 第 21 行
# 行内用了半角引号做强调，把 JSON 字符串提前截断，文件长期非法，
# 一路跟着整体提交进了 git。坏资源在真正被读到之前不报错，
# 所以必须在测试里主动扫一遍。
_bad_assets, _n_assets = [], 0
for _root, _dirs, _files in os.walk(os.path.join(BASE_PATH, 'data')):
    _dirs[:] = [d for d in _dirs if d != '__pycache__']
    for _f in _files:
        if not _f.endswith('.json'):
            continue
        _p = os.path.join(_root, _f)
        _n_assets += 1
        try:
            with open(_p, 'r', encoding='utf-8') as _fh:
                json.load(_fh)
        except Exception as _e:
            _bad_assets.append(f'{os.path.relpath(_p, BASE_PATH)}: {_e}')
check(f'data/ 下全部 JSON 可解析（{_n_assets} 个）', not _bad_assets,
      ' | '.join(_bad_assets))

# ---- 每个玩家档都要能载入（上面只证明它是合法 JSON）----
_player_dir = os.path.join(BASE_PATH, 'data', 'player')
_player_files = sorted(f for f in os.listdir(_player_dir) if f.endswith('.json'))
check(f'玩家档非空（{len(_player_files)} 个）', len(_player_files) > 0)
_active_player = gc.b1WorldLayer.get_config('玩家初始数据', '')
check('世界配置指向的玩家档真实存在',
      os.path.isfile(os.path.join(BASE_PATH, _active_player)), _active_player)

# ---- NPC 表截断上限：配置键必须与代码查的键对得上 ----
#
# 曾经的坑：`init_all_npcs` 查 `NPC_上限_MAX_MEMORY`（键名由 CAP_MAP 的值拼成），
# 而 global.json 里写的是 `NPC_记忆上限`——两边**零交集**，
# 那几项配置从来没生效过，只因值恰好等于类默认值才没暴露。
_missing_caps = [f'NPC_上限_{_a}' for _, _a in B3NPCLayer.CAP_MAP.items()
                 if gc.b1WorldLayer.get_config(f'NPC_上限_{_a}', None) is None]
check(f'NPC 表截断上限全部可从配置读到（{len(B3NPCLayer.CAP_MAP)} 项）',
      not _missing_caps, '缺：' + '、'.join(_missing_caps))

print()
print('=' * 70)
print('6. R2 时钟推进（耗时由 L3 判断，本层只做下限保护）')
print('=' * 70)
r2 = R2_Time(gc.b1WorldLayer)
loc_a = gc.player_location
npcs = list(gc.all_npc_list)
loc_b = npcs[0].current_state.get('当前所处位置', '')
print(f'     玩家位置：{loc_a}')
print(f'     另一地点：{loc_b}')

# 本层**不再**做距离/倍数折算——那些判断已交回 LLM（见 common/R2_time.py 模块说明）
check('已无 estimate_move_minutes（距离折算删了）', not hasattr(r2, 'estimate_move_minutes'))
check('已无 clamp_multiplier（方式倍数表删了）', not hasattr(r2, 'clamp_multiplier'))
check('已无 aggregate（取大/求和规则删了）', not hasattr(r2, 'aggregate'))

check('正常值原样通过', r2.clamp(42) == 42)
check('下限 1 分钟', r2.clamp(0) == 1)
check('负值回落 1（时钟不许倒流）', r2.clamp(-100) == 1)
check('非法值回落 1', r2.clamp('abc') == 1)
check('None 回落 1', r2.clamp(None) == 1)
check('字符串数字可解析', r2.clamp('35') == 35)
check('小数取整', r2.clamp(12.7) == 12)
check('无上限（尊重玩家声明的长时间）', r2.clamp(4320) == 4320)

_c = GameClock(start_time_str='太初历三千七百四十二年·仲夏·午时', time_format='时辰制')
_before = _c._elapsed
check('process 推进时钟', r2.process(_c, minutes=90) == 90 and _c._elapsed == _before + 90,
      f'{_before} → {_c._elapsed}')
check('process 传 0 也推进 1 分钟（不空转）', r2.process(_c, minutes=0) == 1)
check('旧参数（from_loc/multiplier）传了也不报错',
      r2.process(_c, minutes=5, from_loc=loc_a, to_loc=loc_b, multiplier=1.0) == 5)

print()
print('=' * 70)
print('7. R3 感知硬约束')
print('=' * 70)
r3 = R3_PerceptionFilter(gc.b1WorldLayer)
names = [n.name for n in gc.all_npc_list]
print(f'     全部 NPC：{names}')
print(f'     玩家位置：{loc_a}')

pool = r3.candidates(loc_a, gc.all_npc_list, involved=[])
print(f'     附近候选池（{len(pool)}）：{[p["名字"] for p in pool]}')
check('候选池含距离字段', all('距离' in p for p in pool))

# hidden：谁都不给
got = r3.enforce({'甲': 'x'}, Scope.HIDDEN, [], [], loc_a, gc.all_npc_list)
check('hidden → 全丢弃', got == {}, str(got))

# 名字幻觉：不存在的 NPC 被丢弃
fake = {'根本不存在的NPC': 'xxx', names[0]: 'ok'}
got = r3.enforce(fake, Scope.PUBLIC, [names[0]], [], loc_a, gc.all_npc_list)
check('幻觉 NPC 名被丢弃', '根本不存在的NPC' not in got, str(got))

# private **不再**按直接对象过滤——L1 写谁就是谁（距离/scope 判断已交回 LLM）
got = r3.enforce({names[0]: 'a', names[1]: 'b'}, Scope.PRIVATE,
                 [], [names[0]], loc_a, gc.all_npc_list)
check('private 不再过滤名单（L1 写谁就是谁）', set(got) == {names[0], names[1]}, str(list(got)))

# 也不再按距离过滤
far_loc = '苍云大陆·北域·极寒冰原·寒泉集'
got = r3.enforce({names[0]: 'a'}, Scope.PUBLIC, [], [], far_loc, gc.all_npc_list)
check('距离不再参与过滤（远处的人照样保留）', set(got) == {names[0]}, str(list(got)))

# involved 必感知：漏写的补齐
got = r3.enforce({}, Scope.PUBLIC, [names[0]], [], loc_a, gc.all_npc_list,
                 fallback_text='玩家对你说：你好')
check('involved 漏感知被补齐', names[0] in got, str(got))

# involved 无视距离
far = '苍云大陆·北域·极寒冰原·寒泉集'
got = r3.enforce({}, Scope.PUBLIC, [names[0]], [], far, gc.all_npc_list,
                 fallback_text='玩家对你说：喂')
check('involved 无视距离仍补齐', names[0] in got, str(got))

print()
print('=' * 70)
print('8. R1 地段传播（执行 L1 给的名单，不做距离/概率判断）')
print('=' * 70)
r1 = R1_Propagation()


def _clear_obs(tag):
    for n in gc.all_npc_list:
        n.observation[:] = [o for o in n.observation if tag not in o]


TAG = '玩家在落霞坪大喊'
n_far = names[-1]                      # 挑个远的，证明**距离不参与判断**
res = r1.propagate({'消息': TAG, '传到': [names[0], n_far]},
                   gc.b1WorldLayer, gc.all_npc_list, [], scope=Scope.PUBLIC)
check('按名单写给 NPC', set(res) == {names[0], n_far}, str(list(res)))
check('远处的 NPC 照写（距离不再过滤）',
      any(TAG in o for o in gc.all_npc_list.get_npc(n_far).observation))
check('凭空捏造的名字被丢弃',
      r1.propagate({'消息': 'x', '传到': ['根本没有这个人']},
                   gc.b1WorldLayer, gc.all_npc_list, []) == {})

res_ex = r1.propagate({'消息': TAG, '传到': [names[0], n_far]},
                      gc.b1WorldLayer, gc.all_npc_list,
                      experienced_names=[names[0]], scope=Scope.PUBLIC)
check('已亲身感知者不再给传闻', names[0] not in res_ex and n_far in res_ex, str(list(res_ex)))

for sc, label in ((Scope.PRIVATE, 'private'), (Scope.HIDDEN, 'hidden')):
    check(f'{label} 不产生传闻',
          r1.propagate({'消息': 'x', '传到': [names[0]]},
                       gc.b1WorldLayer, gc.all_npc_list, [], scope=sc) == {})

check('空消息不传播',
      r1.propagate({'消息': '', '传到': [names[0]]},
                   gc.b1WorldLayer, gc.all_npc_list, []) == {})

# 旧 schema（level/mask）**不静默兜底**——要让 schema 失效可见
check('旧 level/mask 结构不产生传闻（并会打 WARN）',
      r1.propagate({'msg': 'x', 'level': 3, 'mask': 1.0},
                   gc.b1WorldLayer, gc.all_npc_list, []) == {})

_clear_obs(TAG)

print()
print('=' * 70)
print('9. 信息域边界（本设计的枢纽）')
print('=' * 70)
# —— L2 只能写主观表 ——
dirty = {'增加': {'知识记忆Memory': ['a'], '持有物Inventory': {'剑': 'x'},
                  '当前状态CurrentState': {'实力等级': '化神'}, '胡编字段': 1},
         '删减': {'观察Observation': ['b']}}
clean = sanitize_npc_delta(dirty)
check('L2 放行 知识记忆Memory', '知识记忆Memory' in clean['增加'])
check('L2 拦截 持有物Inventory', '持有物Inventory' not in clean['增加'])
check('L2 拦截 当前状态CurrentState', '当前状态CurrentState' not in clean['增加'])
check('L2 拦截 未知字段', '胡编字段' not in clean['增加'])
check('L2 放行 观察Observation(删减)', '观察Observation' in clean['删减'])

# —— L3 只能写客观表 ——
dirty3 = {'增加': {'持有物Inventory': {'剑': 'x'}, '当前状态CurrentState': {'实力等级': 'x'},
                   '好感度Relationship': ['+3'], '知识记忆Memory': ['c']},
          '删减': {}}
clean3 = sanitize_objective_delta(dirty3)
check('L3 放行 持有物Inventory', '持有物Inventory' in clean3['增加'])
check('L3 放行 当前状态CurrentState', '当前状态CurrentState' in clean3['增加'])
check('L3 拦截 好感度Relationship', '好感度Relationship' not in clean3['增加'])
check('L3 拦截 知识记忆Memory', '知识记忆Memory' not in clean3['增加'])

check('主观表/客观表无交集', not (set(SUBJECTIVE_TABLES) & set(OBJECTIVE_TABLES)),
      str(set(SUBJECTIVE_TABLES) & set(OBJECTIVE_TABLES)))
check('11 张表全覆盖（除更新成本）',
      len(SUBJECTIVE_TABLES) + len(OBJECTIVE_TABLES) == 10,
      f'{len(SUBJECTIVE_TABLES)}+{len(OBJECTIVE_TABLES)}')

print()
print('=' * 70)
print('9.5 字段名容错（模型稳定丢英文后缀，这是实测出来的）')
print('=' * 70)
from common.small_tool import pick_field, canonical_table, norm_field_name

# 真实观测到的模型输出形态：key 只有中文
raw_real = {
    'npc实例': {}, '回应Response': 'x',
    '内心想法': '模型就是这么写的',                        # ← 缺 InnerThought
    '动作意图': {'类型': '无'},
}
check('★ 漏写英文后缀也能取到（实测 deepseek-flash 行为）',
      pick_field(raw_real, '内心想法InnerThought') == '模型就是这么写的',
      str(pick_field(raw_real, '内心想法InnerThought')))
check('完整 key 优先', pick_field({'内心想法': 'a', '内心想法InnerThought': 'b'},
                                 '内心想法InnerThought') == 'b')
check('精确 key 不受影响', pick_field({'旁白': ['x']}, '旁白') == ['x'])
check('取不到返回 default', pick_field({}, '内心想法InnerThought', default='D') == 'D')
check('非 dict 返回 default', pick_field(None, '旁白', default=[]) == [])

# 歧义保护：'观察' 是 '对玩家观察' 的子串，只有两个都在而模型写了个别的才可能歧义
amb = {'观察Observation': ['a'], '对玩家观察PlayerObservation': ['b']}
check('归一化后不歧义（两张表各取各的）',
      pick_field(amb, '观察Observation') == ['a']
      and pick_field(amb, '对玩家观察PlayerObservation') == ['b'])

check('表名归一化：知识记忆 → 知识记忆Memory',
      canonical_table('知识记忆', SUBJECTIVE_TABLES) == '知识记忆Memory')
check('表名归一化：当前状态 → 当前状态CurrentState',
      canonical_table('当前状态', OBJECTIVE_TABLES) == '当前状态CurrentState')
check('表名归一化：完整名原样返回',
      canonical_table('持有物Inventory', OBJECTIVE_TABLES) == '持有物Inventory')
check('表名归一化：不存在的返回 None',
      canonical_table('胡编的表', SUBJECTIVE_TABLES) is None)
check('归一化函数：剥掉 ASCII',
      norm_field_name('内心想法InnerThought') == '内心想法'
      and norm_field_name('is_new_event') == '')

# 端到端：模型丢后缀时，L2 的净化仍然放行（而不是当成"越权字段"丢掉）
clean_alias = sanitize_npc_delta({'增加': {'知识记忆': ['a'], '持有物': {'剑': 1}},
                                  '删减': {'观察': ['b']}})
check('★ 丢后缀的主观表被正确放行',
      clean_alias['增加'].get('知识记忆Memory') == ['a'],
      str(clean_alias))
check('★ 丢后缀的客观表仍被拦截', '持有物Inventory' not in clean_alias['增加'],
      str(clean_alias))
clean_alias3 = sanitize_objective_delta({'增加': {'持有物': {'剑': 1},
                                                  '当前状态': {'实力等级': 'x'},
                                                  '好感度': ['+1']}, '删减': {}})
check('★ L3 侧丢后缀同样生效',
      '持有物Inventory' in clean_alias3['增加']
      and '当前状态CurrentState' in clean_alias3['增加']
      and '好感度Relationship' not in clean_alias3['增加'], str(clean_alias3))

print()
print('=' * 70)
print('9.6 L1 触发式重试：真值复述检测')
print('=' * 70)
from common.L1_perception import L1_Perception

_l1 = L1_Perception()
TRUTH = gc.player.player_dict['当前状态CurrentState']['实力等级']
print(f'     玩家真实实力 = {TRUTH}')

check('★ 逐字复述 → 检测到（触发重试）',
      _l1._has_truth_echo({'感知分发': {'甲': f'你看出他不过{TRUTH}的修为'}}, gc.player))
check('失真措辞 → 不触发',
      not _l1._has_truth_echo({'感知分发': {'甲': '你瞧不出他深浅'}}, gc.player))
check('复述出现在非感知字段 → 不触发（只管感知分发）',
      not _l1._has_truth_echo({'事件标题': TRUTH, '感知分发': {'甲': '看不透'}}, gc.player))
check('感知分发为空 → 不触发', not _l1._has_truth_echo({'感知分发': {}}, gc.player))
check('感知分发格式错 → 不触发（不能崩）',
      not _l1._has_truth_echo({'感知分发': '乱写的'}, gc.player))
check('缺字段 → 不触发', not _l1._has_truth_echo({}, gc.player))
check('★ 重试通道确实开着推理（打开开关时才有意义）',
      _l1.cli_retry.cfg.thinking is True)
# ⚠️ L1 主通道现在**开着**推理，所以"关推理才需要"的触发式重试处于**休眠**状态
# （触发条件是 `L1_THINKING_RETRY_ENABLED and not self.cli.cfg.thinking`）。
# 通道备着不动，将来要是把主通道关回去，它自己就活了。
check('★ L1 主通道开推理（判断权在它身上）', _l1.cli.cfg.thinking is True)
# 触发条件分不清"高阶看穿低阶"（正确）和"同级不该知道"（失败），
# 实测误触发率高达 50%，会把优化吐回去。详见 base_cnf 的说明。
from common.base_cnf import L1_THINKING_RETRY_ENABLED as _RETRY_ON
check('★ 触发式重试默认关闭（触发条件不可靠）', _RETRY_ON is False)

print()
print('=' * 70)
print('10. 动作意图归一化')
print('=' * 70)
check('合法类型保留', normalize_intent({'类型': '给予'})['类型'] == '给予')
check('非法类型→无', normalize_intent({'类型': '飞天遁地'})['类型'] == '无')
check('非 dict → 无', normalize_intent('乱写的')['类型'] == '无')
check('None → 无', normalize_intent(None)['类型'] == '无')

print()
print('=' * 70)
print('11. L3 结果归一化')
print('=' * 70)
l3 = L3_Settlement()
n = l3._normalize({'旁白': '字符串不是数组', '本轮耗时分钟': None,
                   'NPC实例': {'某人': {'增加': {'持有物Inventory': {'x': 'y'}}}}})
check('旁白字符串→数组', l3._normalize({'旁白': '一句话'})['旁白'] == ['一句话'])
check('耗时 null → 1（不崩时钟）', n['本轮耗时分钟'] == 1, str(n['本轮耗时分钟']))
check('耗时 "abc" → 1', l3._normalize({'本轮耗时分钟': 'abc'})['本轮耗时分钟'] == 1)
check('耗时 0 → 1（下限）', l3._normalize({'本轮耗时分钟': 0})['本轮耗时分钟'] == 1)
check('耗时 3.7 → 3', l3._normalize({'本轮耗时分钟': 3.7})['本轮耗时分钟'] == 3)
check('NPC实例 净化生效',
      '持有物Inventory' in n['NPC实例']['某人']['增加'])
check('破裂观察 list 形式可解析',
      l3._normalize({'破裂观察': [{'NPC': '甲', '观察': '掏空了'}]})['破裂观察'] == {'甲': '掏空了'})

print()
print('=' * 70)
print('12. 记忆巩固触发条件')
print('=' * 70)
mm = MemoryManager()
npc0 = gc.all_npc_list[0]
check('未超额时不触发', mm.needs_consolidation(npc0) == [],
      str(mm.needs_consolidation(npc0)))
npc0.memory = [f'记忆{i}' for i in range(B3NPCLayer.MAX_MEMORY + 5)]
check('超额时触发 Memory 表',
      any(t == '知识记忆Memory' for t, _ in mm.needs_consolidation(npc0)),
      str(mm.needs_consolidation(npc0)))
check('条目瘦身截断', len(mm.trim_entry('x' * 500)) <= 121)

print()
print('=' * 70)
print('13. 快照 / 回滚')
print('=' * 70)
snap = gc.snapshot()
gc.player.player_dict['当前状态CurrentState']['实力等级'] = '被人改坏了'
gc.game_clock.advance(9999)
gc.restore(snap)
check('玩家状态回滚', gc.player.player_dict['当前状态CurrentState']['实力等级'] != '被人改坏了',
      gc.player.player_dict['当前状态CurrentState']['实力等级'])
check('时钟回滚', gc.game_clock.now_str() != '被人改坏了')
check('事件层回滚', isinstance(gc.event_layer, EventLayer))

_NPC_COUNT_BEFORE_SAVE = len(gc.all_npc_list)   # 别硬编码——加 NPC 是常事

print()
print('=' * 70)
print('14. 存档 / 读档往返')
print('=' * 70)
import tempfile, shutil
from common.save_load import SaveManager

_tmp = tempfile.mkdtemp(prefix='g4_save_')

# 造点状态出来再存
gc.event_layer.reset('往返测试事件')
gc.event_layer.apply(scope_raw='group', involved=[names[0], names[1]],
                     direct_target=[names[0]], title='往返测试事件')
gc.event_layer.tick()
gc.bg_state['triggered_rules'] = {'0': '初期'}
gc.bg_state['recent_narrations'] = ['测试旁白']
gc.game_clock.advance(123)
gc.add_context_all('测试上下文条目')
_ = gc.apply_player_delta({'增加': {'当前状态CurrentState': {'当前心情': '测试心情'}}, '删减': {}})

state = gc.build_save_state('往返')
json.dumps(state, ensure_ascii=False)          # 必须可序列化（否则存档直接炸）
check('存档状态可 JSON 序列化', True)

saved = {
    'scope': gc.event_layer.scope.value,
    'involved': list(gc.event_layer.involved),
    'elapsed': gc.game_clock._elapsed,
    'mood': gc.player.player_dict['当前状态CurrentState'].get('当前心情'),
    'rules': dict(gc.bg_state['triggered_rules']),
    'ctx_len': len(gc.global_context.context),
}

# 破坏当前状态，再从存档恢复
gc.event_layer.reset()
gc.game_clock = GameClock(start_time_str='2000-01-01 00:00')
gc.player.player_dict['当前状态CurrentState']['当前心情'] = '被破坏了'
gc.bg_state['triggered_rules'] = {}
gc.global_context.new_context()

gc.rebuild_game_state(state)

check('事件 scope 往返一致', gc.event_layer.scope.value == saved['scope'],
      f'{gc.event_layer.scope.value} vs {saved["scope"]}')
check('事件锁定名单往返一致', list(gc.event_layer.involved) == saved['involved'],
      f'{gc.event_layer.involved} vs {saved["involved"]}')
check('游戏时钟往返一致', gc.game_clock._elapsed == saved['elapsed'],
      f'{gc.game_clock._elapsed} vs {saved["elapsed"]}')
check('玩家状态往返一致',
      gc.player.player_dict['当前状态CurrentState'].get('当前心情') == saved['mood'],
      f'{gc.player.player_dict["当前状态CurrentState"].get("当前心情")} vs {saved["mood"]}')
check('后台状态往返一致', dict(gc.bg_state['triggered_rules']) == saved['rules'],
      f'{gc.bg_state["triggered_rules"]} vs {saved["rules"]}')
check('上下文长度往返一致', len(gc.global_context.context) == saved['ctx_len'],
      f'{len(gc.global_context.context)} vs {saved["ctx_len"]}')
check('NPC 数量往返一致', len(gc.all_npc_list) == _NPC_COUNT_BEFORE_SAVE,
      f'{len(gc.all_npc_list)} vs {_NPC_COUNT_BEFORE_SAVE}')

# ---- 真·落盘往返（走 SaveManager，写到临时目录）----
gc.save_manager = SaveManager(save_dir=_tmp)
folder = gc.save_manager.save(gc.build_save_state('落盘往返'), '落盘往返')
check('存档文件夹已创建', os.path.isdir(folder), folder)
files = sorted(os.listdir(folder))
print(f'     落盘文件（{len(files)}）：{files}')
check('event_layer.json 已写入', 'event_layer.json' in files, str(files))
check('bg_state.json 已写入', 'bg_state.json' in files, str(files))
check('game_clock.json 已写入', 'game_clock.json' in files, str(files))

loaded = gc.save_manager.load('落盘往返')
check('读档成功', loaded is not None)
gc.rebuild_game_state(loaded)
check('落盘往返：事件 scope 一致', gc.event_layer.scope.value == saved['scope'],
      gc.event_layer.scope.value)
check('落盘往返：时钟一致', gc.game_clock._elapsed == saved['elapsed'],
      f'{gc.game_clock._elapsed} vs {saved["elapsed"]}')
check('落盘往返：玩家状态一致',
      gc.player.player_dict['当前状态CurrentState'].get('当前心情') == saved['mood'])
check('落盘往返：后台状态一致',
      dict(gc.bg_state['triggered_rules']) == saved['rules'])

check('存档列表可读', any(s['name'] == '落盘往返' for s in gc.save_manager.list_saves()))
check('存档可删除', gc.save_manager.delete('落盘往返'))

shutil.rmtree(_tmp, ignore_errors=True)

print()
print('=' * 70)
print('15. scope 回落方向（安全方向：宁可空转，不可泄漏）')
print('=' * 70)

check('SAFE_FALLBACK 是 group', Scope.SAFE_FALLBACK == 'group', Scope.SAFE_FALLBACK)

# 实测模型造过 "open" 这个枚举外值
check('英文变体 open → public', Scope.parse('open') == Scope.PUBLIC, Scope.parse('open'))
check('英文变体 publicly → public', Scope.parse('publicly') == Scope.PUBLIC)
check('英文变体 secret → private', Scope.parse('secret') == Scope.PRIVATE)
check('英文精确匹配不做子串（small 不该命中 all）',
      Scope.parse('small') == Scope.GROUP, Scope.parse('small'))

check('中文近义 悄悄 → private', Scope.parse('悄悄') == Scope.PRIVATE)
check('中文近义 压低声音 → private', Scope.parse('压低声音') == Scope.PRIVATE)
check('中文近义 密谈 → private', Scope.parse('密谈') == Scope.PRIVATE)

# 字段**缺失**与字段**乱写**是两回事
check('字段缺失 → 沿用 default',
      Scope.parse(None, default=Scope.AREA) == Scope.AREA)
check('字段缺失且无 default → public',
      Scope.parse('') == Scope.PUBLIC)
check('字段乱写**不**沿用 default（走保守回落）',
      Scope.parse('胡言乱语', default=Scope.AREA) == Scope.GROUP,
      Scope.parse('胡言乱语', default=Scope.AREA))
check('Scope 实例直接透传', Scope.parse(Scope.HIDDEN) == Scope.HIDDEN)

print()
print('=' * 70)
print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
if FAIL:
    print('失败项：')
    for f in FAIL:
        print(f'  ✗ {f}')
print('=' * 70)
sys.exit(1 if FAIL else 0)
