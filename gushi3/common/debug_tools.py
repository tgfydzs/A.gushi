import os
from common.base_cnf import *

# ===================== 快照目录 =====================

_DEBUG_DIR = os.path.join(os.path.dirname(__file__), '..', 'debug_rounds')
os.makedirs(_DEBUG_DIR, exist_ok=True)


def _round_dir(round_num: int) -> str:
    """每轮独立文件夹 debug_rounds/round_{:04d}/"""
    d = os.path.join(_DEBUG_DIR, f'round_{round_num:04d}')
    os.makedirs(d, exist_ok=True)
    return d


# ===================== NPC 快照 =====================

def snapshot_npcs(round_num: int, label: str, all_npc_list):
    """
    保存当前所有NPC的完整 to_dict() 快照到JSON。
    label: 'before' / 'after' / 'after_n9' 等，用于区分流水线不同阶段
    """
    if not DEBUG_MODE:
        return

    rdir = _round_dir(round_num)
    path = os.path.join(rdir, f'npcs_{label}.json')

    import json
    from Schema.B3_1_npc_sch import B3NPCLayer

    data = {}
    npc_iter = all_npc_list.npc_list if hasattr(all_npc_list, 'npc_list') else all_npc_list
    for npc in npc_iter:
        data[npc.name] = npc.to_dict()

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _log(f'📸 {label}: {len(data)} NPC → {path}')


# ===================== 增量对比 =====================

def diff_npcs(round_num: int, all_npc_list):
    """
    对比 after 和 before 快照，输出差异摘要。
    只在 round_{:04d}/ 下有 npcs_before.json 和 npcs_after.json 时生效。
    """
    if not DEBUG_MODE:
        return

    import json

    rdir = _round_dir(round_num)
    before_path = os.path.join(rdir, 'npcs_before.json')
    after_path = os.path.join(rdir, 'npcs_after.json')

    if not os.path.isfile(before_path) or not os.path.isfile(after_path):
        return

    with open(before_path, 'r', encoding='utf-8') as f:
        before = json.load(f)
    with open(after_path, 'r', encoding='utf-8') as f:
        after = json.load(f)

    changes = {}
    all_names = set(before.keys()) | set(after.keys())
    for name in sorted(all_names):
        b = before.get(name, {})
        a = after.get(name, {})
        diffs = _dict_diff(b, a)
        if diffs:
            changes[name] = diffs

    if not changes:
        _log(f'🔍 轮次{round_num}：NPC无变化')
        return

    # 将差异写入文件 + 控制台
    summary_path = os.path.join(rdir, 'npcs_diff.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(changes, f, ensure_ascii=False, indent=2)

    # 控制台不打印，只写文件


def _dict_diff(before: dict, after: dict, _path: str = '') -> dict:
    """递归对比两个dict，仅输出有差异的叶子节点。"""
    changes = {}
    all_keys = set(before.keys()) | set(after.keys())
    for k in all_keys:
        cur = _path + k if _path else k
        bv = before.get(k, _MISSING)
        av = after.get(k, _MISSING)

        if bv is _MISSING:
            changes[cur] = {'__added__': _summarize(av)}
        elif av is _MISSING:
            changes[cur] = {'__removed__': _summarize(bv)}
        elif isinstance(bv, dict) and isinstance(av, dict):
            nested = _dict_diff(bv, av, cur + '.')
            if nested:
                changes.update(nested)
        elif bv != av:
            changes[cur] = {
                'before': _summarize(bv),
                'after': _summarize(av),
            }
    return changes


class _MISSING_TYPE:
    pass
_MISSING = _MISSING_TYPE()


def _summarize(v) -> str:
    """长内容截断显示"""
    s = str(v)
    if len(s) > 120:
        return s[:120] + '...'
    return s


# ===================== 场景快照 =====================

def snapshot_scene(round_num: int, label: str, gc):
    """记录世界状态、玩家状态、上下文字数等（不打印，纯记录）"""
    if not DEBUG_MODE:
        return

    import json
    rdir = _round_dir(round_num)
    path = os.path.join(rdir, f'scene_{label}.json')

    data = {
        'game_time': gc.game_clock.now_str(),
        'player_loc': gc.player.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', ''),
        'player_name': gc.player.player_dict.get('玩家名称name', ''),
        'event_npc_count': len(gc.event_npc),
        'related_npc_count': len(gc.related_npc),
        'global_context_count': gc.global_context.count if gc.global_context else 0,
        'event_context_count': gc.event_context.count if gc.event_context else 0,
        'world_state_list': gc.b1WorldLayer.dict.get('世界状态列表', []),
        'triggered_rules_len': len(getattr(gc, '_n9_state', {}).get('triggered_rules', {})),
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===================== 日志 =====================

_debug_logger = None

def _log(msg: str):
    global _debug_logger
    if _debug_logger is None:
        from common.log import get_logger
        _debug_logger = get_logger('Debug')
    _debug_logger.debug(msg)
