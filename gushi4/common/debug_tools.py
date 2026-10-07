"""
调试工具（`DEBUG_MODE=True` 时生效，运行中可用 `/debug` 切换）

    debug_rounds/round_0007/
        npcs_before.json      轮次开始时的全 NPC 快照
        npcs_after.json       轮次结束时的全 NPC 快照
        npcs_diff.json        before/after 的逐字段差异（只写有变化的）
        scene_before.json     世界状态 / 位置 / 上下文字数
        l1_output.json        L1 原始 JSON
        l2_outputs.json       L2 每个 NPC 的原始 JSON
        l3_output.json        L3 原始 JSON
        perception_audit.md   ★ 信息隔离审计（见下）

# perception_audit.md 是干什么的

设计方案 §八「质量怎么看」的第 1 条——**NPC 有没有说出它不该知道的**——
是三条检查里最关键的一条，也是最容易人工漏掉的一条。所以把它半自动化：

对每个 NPC，把它**实际收到的失真感知**和它**实际说出口的对白**并排落盘。
人只需要扫一眼"对白里有没有出现感知里没有的信息"。

这个文件不判断对错，只负责把两份东西摆在一起——判断留给看的人。
"""

import json
import os

from common.base_cnf import BASE_PATH

DEBUG_MODE = False

_DEBUG_DIR = os.path.join(BASE_PATH, 'debug_rounds')


def set_debug(on: bool):
    global DEBUG_MODE
    DEBUG_MODE = on
    return DEBUG_MODE


def _round_dir(round_num: int) -> str:
    d = os.path.join(_DEBUG_DIR, f'round_{round_num:04d}')
    os.makedirs(d, exist_ok=True)
    return d


def _log(msg: str):
    from common.log import get_logger
    get_logger('Debug').debug(msg)


def _write(path: str, data):
    with open(path, 'w', encoding='utf-8') as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ===================== NPC 快照 =====================

def snapshot_npcs(round_num: int, label: str, all_npc_list):
    if not DEBUG_MODE:
        return
    data = {npc.name: npc.to_dict() for npc in all_npc_list}
    path = os.path.join(_round_dir(round_num), f'npcs_{label}.json')
    _write(path, data)
    _log(f'📸 NPC {label}：{len(data)} 个 → {path}')


def diff_npcs(round_num: int, all_npc_list):
    """对比 before/after 快照，把差异写到 npcs_diff.json"""
    if not DEBUG_MODE:
        return
    rdir = _round_dir(round_num)
    before_p = os.path.join(rdir, 'npcs_before.json')
    after_p = os.path.join(rdir, 'npcs_after.json')
    if not (os.path.isfile(before_p) and os.path.isfile(after_p)):
        return

    with open(before_p, 'r', encoding='utf-8') as f:
        before = json.load(f)
    with open(after_p, 'r', encoding='utf-8') as f:
        after = json.load(f)

    changes = {}
    for name in sorted(set(before) | set(after)):
        d = _dict_diff(before.get(name, {}), after.get(name, {}))
        if d:
            changes[name] = d

    _write(os.path.join(rdir, 'npcs_diff.json'), changes or {})
    if changes:
        _log(f'🔍 轮次{round_num}：{len(changes)} 个 NPC 有变化')


def _dict_diff(before: dict, after: dict, _path: str = '') -> dict:
    changes = {}
    for k in set(before) | set(after):
        cur = _path + k if _path else k
        bv, av = before.get(k, _MISSING), after.get(k, _MISSING)
        if bv is _MISSING:
            changes[cur] = {'__added__': _summarize(av)}
        elif av is _MISSING:
            changes[cur] = {'__removed__': _summarize(bv)}
        elif isinstance(bv, dict) and isinstance(av, dict):
            changes.update(_dict_diff(bv, av, cur + '.'))
        elif bv != av:
            changes[cur] = {'before': _summarize(bv), 'after': _summarize(av)}
    return changes


class _MISSING_TYPE:
    pass


_MISSING = _MISSING_TYPE()


def _summarize(v) -> str:
    s = str(v)
    return s if len(s) <= 120 else s[:120] + '...'


# ===================== 场景快照 =====================

def snapshot_scene(round_num: int, label: str, gc):
    if not DEBUG_MODE:
        return
    ev = gc.event_layer
    data = {
        'game_time': gc.game_clock.now_str(),
        'player_name': gc.player.player_dict.get('玩家名称name', ''),
        'player_loc': gc.player_location,
        'event_title': ev.title,
        'event_scope': ev.scope.value,
        'event_involved': ev.involved,
        'event_direct_target': ev.direct_target,
        'event_rounds': ev.rounds,
        'global_context_count': gc.global_context.count,
        'event_context_count': gc.event_context.count,
        'director_context_count': gc.director_context.count,
        'world_state_list': gc.b1WorldLayer.dict.get('世界状态列表', []),
        'triggered_rules': gc.bg_state.get('triggered_rules', {}),
    }
    _write(os.path.join(_round_dir(round_num), f'scene_{label}.json'), data)


# ===================== 层 I/O 落盘 =====================

def dump_layer_io(round_num: int, layer: str, payload):
    """保存某一层的原始 LLM 输出（调参、复盘时最有用的一份数据）"""
    if not DEBUG_MODE:
        return
    _write(os.path.join(_round_dir(round_num), f'{layer.lower()}_output.json'), payload)


# ===================== 信息隔离审计 =====================

def save_perception_audit(round_num: int, l2_results: list,
                          l3_result: dict = None):
    """
    把「NPC 收到的失真感知」与「它实际说出口的对白」并排落盘。

    这是设计方案 §八 质量检查第 1 条（NPC 有没有说出它不该知道的）的**半自动**版本——
    工具不判断对错，只把两份材料摆在一起，人的眼睛扫一遍就知道有没有越界。

    同时落盘 L3 的旁白与结算增量，方便一并核对"状态变更自不自洽"（第 3 条）。
    """
    if not DEBUG_MODE:
        return

    lines = [f'# 第 {round_num} 轮 · 信息隔离审计', '']
    lines.append('> 检查方法：看每个 NPC 的「对白/行为」里有没有出现「它收到的感知」'
                 '之外的信息。')
    lines.append('> 出现即越界——它不该知道那个。')
    lines.append('')

    for r in (l2_results or []):
        name = r.get('name', '?')
        lines.append(f'## {name}')
        lines.append('')
        lines.append(f'**它收到的感知**：{r.get("感知输入") or "（无）"}')
        lines.append('')
        lines.append(f'**它的对白/行为**：{r.get("回应Response") or "（空）"}')
        lines.append('')
        lines.append(f'**内心想法**：{r.get("内心想法InnerThought") or "（空）"}')
        lines.append('')
        lines.append(f'**动作意图**：{json.dumps(r.get("动作意图") or {}, ensure_ascii=False)}')
        lines.append('')

    if l3_result:
        lines.append('---')
        lines.append('')
        lines.append('## L3 结算')
        lines.append('')
        lines.append(f'**旁白**：{json.dumps(l3_result.get("旁白") or [], ensure_ascii=False)}')
        lines.append('')
        lines.append(f'**玩家增量**：{json.dumps(l3_result.get("玩家实例") or {}, ensure_ascii=False)}')
        lines.append('')
        lines.append(f'**NPC 客观增量**：{json.dumps(l3_result.get("NPC实例") or {}, ensure_ascii=False)}')
        lines.append('')
        lines.append(f'**破裂观察**：{json.dumps(l3_result.get("破裂观察") or {}, ensure_ascii=False)}')
        lines.append('')

    _write(os.path.join(_round_dir(round_num), 'perception_audit.md'), '\n'.join(lines))
