import json
import os
import re
from copy import deepcopy
from typing import Dict, Any, List
from functools import lru_cache

from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B3_npclist_sch import B3NPCListLayer
from Schema.B3_1_npc_sch import B3NPCLayer
from common.log import get_logger
from common.base_cnf import BASE_PATH

logger = get_logger("small_tool")


# ===================== Prompt 加载（客制化占位符替换） =====================

# prompt 文件名 → 按顺序查找的客制化目录（不含 _custom 后缀）
# 值可以是单个字符串，也可以是元组（一个 prompt 复用到多个阶段的资料时用）
_LAYER_PROMPT_MAP = {
    'L1_perception_sys.txt': 'L1',
    'L2_character_onscene_sys.txt': 'L2',
    # 幕后模式与在场模式共用同一份 Schema，但立场资料两份都要（如"传播方式参考"）
    'L2_character_daemon_sys.txt': ('L2', 'B2'),
    'L3_settlement_sys.txt': 'L3',
    'L3_solo_sys.txt': 'L3_solo',
    'B1b_narration_sys.txt': 'B1b',
}


@lru_cache(maxsize=32)
def load_prompt(filename: str) -> str:
    """
    读取 prompt 文件并替换客制化占位符。

    从 `prompt/{filename}` 读取原始文本，到 `data/prompt/{dir}_custom/` 里
    找同名的 `.md`，把 `{占位符}` 换成文件内容。

    这样**换世界观只需要换 data/ 下的 md 文件，prompt 模板本身不用动**——
    地点名、实力体系、称呼习惯这些全部通过占位符注入。

    :param filename: prompt 文件名，如 'L1_perception_sys.txt'
    :return: 替换后的完整 prompt 文本
    """
    with open(os.path.join(BASE_PATH, 'prompt', filename), 'r', encoding='utf-8') as f:
        text = f.read()

    dirs = _LAYER_PROMPT_MAP.get(filename)
    if not dirs:
        return text
    if isinstance(dirs, str):
        dirs = (dirs,)

    placeholders = set(re.findall(r'\{(\w+)\}', text))
    if not placeholders:
        return text

    for d in dirs:
        custom_dir = os.path.join(BASE_PATH, 'data', 'prompt', f'{d}_custom')
        if not os.path.isdir(custom_dir):
            continue
        for ph in list(placeholders):
            md_path = os.path.join(custom_dir, f'{ph}.md')
            if os.path.isfile(md_path):
                with open(md_path, 'r', encoding='utf-8') as f_md:
                    text = text.replace(f'{{{ph}}}', f_md.read().strip())
                placeholders.discard(ph)
                logger.debug(f'prompt占位符替换: {filename} → {d}_custom/{ph}.md')
        if not placeholders:
            break

    if placeholders:
        logger.warn(f'{filename} 有未替换的占位符：{sorted(placeholders)}')
    return text


def safe_json_loads(text: str, label: str = "") -> dict:
    """
    安全解析LLM返回的JSON，处理常见格式问题：
      1. 剥离 markdown 代码块包裹（```json ... ```）
      2. 尝试 strict 模式解析
      3. 截断到最后一个 } 或 ]（处理LLM在JSON后追加额外文本）
      4. 降级 strict=False（允许字符串内嵌控制字符如 \\n \\t \\r）
      5. 仍失败则记录原始文本并抛出

    :param text:  LLM 原始返回文本
    :param label: 调用方标识，用于日志定位
    :return: 解析后的 dict
    """
    raw = text.strip()

    # 1. 剥离 markdown 代码块
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()

    # 2. 尝试 strict 解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 3. 截断到最后一个 } 或 ]（处理LLM在JSON后追加额外文本）
    for bracket in ('}', ']'):
        idx = raw.rfind(bracket)
        if idx != -1:
            candidate = raw[:idx + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    return json.loads(candidate, strict=False)
                except json.JSONDecodeError:
                    pass

    # 4. 降级：允许控制字符
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError as e:
        logger.error(f'safe_json_loads[{label}] 解析失败: {e}')
        logger.error(f'safe_json_loads[{label}] 原始文本(前2000字符): {raw[:2000]}')
        raise


# ===================== 字段名容错 =====================
#
# 背景：本项目的字段名是「中文+英文后缀」的混合形式（`内心想法InnerThought`、
# `知识记忆Memory`、`当前状态CurrentState`）。**模型会稳定地把英文后缀丢掉**——
# 实测 deepseek-flash 返回的是 `"内心想法"` 和 `"知识记忆"`，而不是完整 key。
#
# 这不是偶发的手滑，是稳定行为（Gushi3 的 N45 就栽在同一个坑上，
# 当时靠一个只认 InnerThought 的 `_pick()` 打补丁）。所以这里做通用解法：
#
#   **读取时按"归一化名"匹配**——剥掉 ASCII 字母只留中文，再比对。
#
# 为什么不干脆把字段名改成纯中文：NPC 数据文件、存档、prompt 全部用的是
# 完整名，改一遍是伤筋动骨；而且中文和英文后缀各自承担了不同用途
# （中文给人读，英文给代码当键）。**读取端宽容**是成本最低的位置。

_ASCII_RE = re.compile(r'[A-Za-z]+')


def norm_field_name(name) -> str:
    """
    归一化字段名：剥掉 ASCII 字母，只留中文/数字。

        '内心想法InnerThought' → '内心想法'
        '知识记忆'             → '知识记忆'
        'is_new_event'         → '_'      （纯英文只剩符号）
    """
    return _ASCII_RE.sub('', str(name)).strip('_ ').strip()


def pick_field(d, *names, default=None):
    """
    从 LLM 返回的 dict 里取字段，**容忍漏写英文后缀**。

    匹配顺序：
      1. 精确命中
      2. 归一化名相同（'内心想法' ≈ '内心想法InnerThought'）
      3. 子串包含（任一方是另一方的子串）

    :param d: LLM 返回的原始 dict
    :param names: 候选字段名，按优先级排列
    """
    if not isinstance(d, dict):
        return default

    # 1. 精确
    for n in names:
        if n in d:
            return d[n]

    # 2. 归一化
    targets = []
    for n in names:
        t = norm_field_name(n)
        if t:
            targets.append(t)
    for k, v in d.items():
        if norm_field_name(k) in targets:
            return v

    # 3. 子串 —— **只在不歧义时才认**
    #    '观察' 是 '对玩家观察' 的子串，若表里两个都在、而模型只写了一个，
    #    子串匹配会猜错。宁可不认（返回 default）也不要认错。
    hits = []
    for k, v in d.items():
        nk = norm_field_name(k)
        if not nk:
            continue
        if any(t in nk or nk in t for t in targets):
            hits.append(v)
    if len(hits) == 1:
        return hits[0]

    return default


def canonical_table(raw, valid_tables):
    """
    把 LLM 写的表名映射回规范名。认不出来返回 None。

        canonical_table('知识记忆', SUBJECTIVE_TABLES) → '知识记忆Memory'

    同样走「精确 → 归一化 → 子串」。**归一化是最关键的一步**：
    '知识记忆' 和 '知识记忆Memory' 归一化后都是 '知识记忆'，直接命中。
    """
    if not isinstance(raw, str):
        return None
    if raw in valid_tables:
        return raw

    nk = norm_field_name(raw)
    if not nk:
        return None

    for t in valid_tables:
        if norm_field_name(t) == nk:
            return t

    # 子串匹配同样只在不歧义时才认（'观察' 是 '对玩家观察' 的子串）
    hits = [t for t in valid_tables
            if nk in norm_field_name(t) or norm_field_name(t) in nk]
    return hits[0] if len(hits) == 1 else None


def strip_empty_values(body: dict) -> dict:
    """
    丢掉增量里的**空壳值**。

    模型经常输出 `{"持有物Inventory": {}}` 这种"提到但没内容"的条目。
    功能上无害（`dict.update({})` 是 no-op），但有两个实际危害：

      1. **污染日志**——"本轮 NPC 客观增量={白鹤: [持有物Inventory]}" 看起来像动了，其实没动
      2. **污染质量检查**——§八 第 3 条要判"状态变更自不自洽"，
         把空壳算成变更就没法判了

    而且它还会让上游白白重建一次 NPC 对象。
    """
    if not isinstance(body, dict):
        return {}
    return {k: v for k, v in body.items() if v not in ({}, [], '', None)}


# ===================== 增量合并工具 =====================

def _apply_dict_delta(base: dict, delta: dict):
    """
    将一个增量 delta 原地应用到 base 字典上。

    delta 结构：{"增加": {字段名: 值}, "删减": {字段名: 值}}
    - list 字段：增加→extend，删减→逐条移除
    - dict 字段：增加→update合并，删减→逐key删除
    - 其他(如str)：增加→覆盖
    """
    for section_key, (section_label, op_func) in _DELTA_OPS.items():
        section = delta.get(section_key, {})
        if not section:
            continue
        for field, value in section.items():
            op_func(base, field, value)


def _delta_add(base: dict, field: str, value):
    """增加操作"""
    existing = base.get(field)
    if isinstance(value, dict) and isinstance(existing, dict):
        existing.update(value)
    elif isinstance(value, list) and isinstance(existing, list):
        existing.extend(value)
    else:
        base[field] = value


def _delta_del(base: dict, field: str, value):
    """删减操作"""
    existing = base.get(field)
    if existing is None:
        return
    if isinstance(value, dict) and isinstance(existing, dict):
        for k in value:
            existing.pop(k, None)
    elif isinstance(value, list) and isinstance(existing, list):
        for item in value:
            if item in existing:
                existing.remove(item)


_DELTA_OPS = {
    '增加': ('add', _delta_add),
    '删减': ('del', _delta_del),
}


def apply_npc_delta(npc: B3NPCLayer, delta: dict) -> B3NPCLayer:
    """
    将增量 delta 应用到 NPC 实例，返回新的 B3NPCLayer。

    :param npc:   原始 NPC 实例
    :param delta: {"增加": {...}, "删减": {...}} 或空dict
    :return: 更新后的 B3NPCLayer
    """
    if not delta or (not delta.get('增加') and not delta.get('删减')):
        return npc
    npc_dict = npc.to_dict()
    _apply_dict_delta(npc_dict, delta)
    return B3NPCLayer(npc_dict)


def apply_player_delta(player_dict: dict, delta: dict) -> dict:
    """
    将增量 delta 应用到玩家 dict，返回深拷贝后的新 dict。

    :param player_dict: 原始玩家字典
    :param delta: {"增加": {...}, "删减": {...}}
    :return: 更新后的玩家字典
    """
    if not delta or (not delta.get('增加') and not delta.get('删减')):
        return player_dict
    result = deepcopy(player_dict)
    _apply_dict_delta(result, delta)
    return result


def merge_delta(target: dict, source: dict):
    """
    将 source delta 合并到 target delta（原地修改 target）。

    用于多个 NPC 产出的玩家增量合并。
    """
    for section_key in ('增加', '删减'):
        src_section = source.get(section_key, {})
        if not src_section:
            continue
        tgt_section = target.setdefault(section_key, {})
        for field, value in src_section.items():
            if field not in tgt_section:
                tgt_section[field] = deepcopy(value)
            elif isinstance(tgt_section[field], dict) and isinstance(value, dict):
                tgt_section[field].update(value)
            elif isinstance(tgt_section[field], list) and isinstance(value, list):
                tgt_section[field].extend(value)
            else:
                tgt_section[field] = value





def load_json_to_dict(file_path: str) -> Dict[str, Any]:
    """
    读取指定路径的JSON文件，返回解析后的字典
    :param file_path: json文件绝对/相对路径
    :return: 解析完成的dict字典
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        logger.warn(f"文件不存在：{file_path}")
        raise FileNotFoundError(f"文件不存在：{file_path}")
    except json.JSONDecodeError:
        logger.warn(f"{file_path} 不是合法JSON文件，解析失败")
        raise ValueError(f"{file_path} 不是合法JSON文件，解析失败")


def walk_category_files(root_folder: str):
    """
    遍历根目录下一级分类文件夹，再遍历每个分类内所有文件
    :param root_folder: 顶层总文件夹路径
    """
    file_list = []
    if not os.path.isdir(root_folder):
        logger.warn("路径不存在")
        return

    for cat_name in os.listdir(root_folder):
        cat_path = os.path.join(root_folder, cat_name)
        if not os.path.isdir(cat_path):
            continue

        # 遍历分类内文件
        for file_name in os.listdir(cat_path):
            file_path = os.path.join(cat_path, file_name)
            if os.path.isfile(file_path):
                file_list.append(file_path)

    return file_list




# ── 移动标记：已退役（2026-10-08）─────────────────────────────────────────
#
# 这里原本有 `find_move_marker` / `apply_move_target` / `resolve_move_markers`，
# 配套 `{移动}` 占位符与 `/move` 命令。它们存在的唯一理由是
# **让前端给引擎一个"树上的合法路径"**（`move_info`），好让引擎自己把位置写下去。
#
# 位置改由 **L3 出 `去向`（地名）+ 代码落坐标**之后，这个理由没有了：
#   - 地图是主输入（点地图 = 下达去向）
#   - 手打"我往青木山脉去"本来就是自由文本，直接当普通一轮交给 L3 即可
#   - 终端版**一点能力都没损失**（照样打字移动）
# 顺带少了一处**绕过唯一客观写入口**的地方（引擎里那段直接用 move_info 写位置的兜底）。


def load_npc_list_from_json(file_path: str) -> B3NPCListLayer:
    """读取数组格式JSON，返回NPC列表层对象"""
    npc_list_layer = B3NPCListLayer()
    with open(file_path, "r", encoding="utf-8") as f:
        data_list = json.load(f)
        for i in data_list:
            npc = B3NPCLayer(i)
            npc_list_layer.add(npc)

    return npc_list_layer

















