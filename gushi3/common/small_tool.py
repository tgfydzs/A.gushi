import json
import os
import re
from copy import deepcopy
from typing import Dict, Any, List

from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B3_npclist_sch import B3NPCListLayer
from Schema.B3_1_npc_sch import B3NPCLayer
from common.log import get_logger

logger = get_logger("small_tool")


def safe_json_loads(text: str, label: str = "") -> dict:
    """
    安全解析LLM返回的JSON，处理常见格式问题：
      1. 剥离 markdown 代码块包裹（```json ... ```）
      2. 尝试 strict 模式解析
      3. 降级 strict=False（允许字符串内嵌控制字符如 \\n \\t \\r）
      4. 仍失败则记录原始文本并抛出

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

    # 3. 降级：允许控制字符
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError as e:
        logger.error(f'safe_json_loads[{label}] 解析失败: {e}')
        logger.error(f'safe_json_loads[{label}] 原始文本(前2000字符): {raw[:2000]}')
        raise


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
        logger.warn("{file_path} 不是合法JSON文件，解析失败")
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




def resolve_move_markers(raw_input: str, world_layer: B1WorldLayer) -> str | None:
    """
    扫描玩家输入中的移动标记，触发交互式导航，返回替换后的输入。

    触发标记（不区分大小写）：
      - {移动} 或 {move}      → 占位符替换
      - /move                 → 独立移动命令
      - /move 后续内容         → 移动 + 后续行动

    处理逻辑：
      1. 检测原始输入是否包含触发标记
      2. 若触发 → 调用 world_layer.navigate_interactive()
      3. 若导航取消 → 返回 None
      4. 若导航完成 → 将选中路径替换到标记位置

    替换示例：
      "我准备去{移动}然后找赵铁手" + 选中"华国·东南沿海市·旧城区·城中村"
      → "我准备去华国·东南沿海市·旧城区·城中村然后找赵铁手"

      "/move" + 选中"华国·东南沿海市·港口区"
      → "前往华国·东南沿海市·港口区"

      "/move 找赵铁手聊聊" + 选中"华国·东南沿海市·旧城区·城中村"
      → "前往华国·东南沿海市·旧城区·城中村，找赵铁手聊聊"

    :param raw_input: 玩家原始输入
    :param world_layer: B1WorldLayer 实例
    :return: 替换后的完整输入；取消时返回 None
    """

    # 模式1：{移动} 或 {move} 占位符
    placeholder_match = re.search(r'\{(移动|move)\}', raw_input, re.IGNORECASE)
    if placeholder_match:
        print('\n--- 选择目标位置 ---')
        selected = world_layer.navigate_interactive()
        if selected is None:
            return None
        result = raw_input[:placeholder_match.start()] + selected + raw_input[placeholder_match.end():]
        return result

    # 模式2：/move 命令（独立或带后续内容）
    cmd_match = re.match(r'^/move\s*(.*)', raw_input, re.IGNORECASE)
    if cmd_match:
        suffix = cmd_match.group(1).strip()
        print('\n--- 选择目标位置 ---')
        selected = world_layer.navigate_interactive()
        if selected is None:
            return None
        if suffix:
            result = f'前往{selected}，{suffix}'
        else:
            result = f'前往{selected}'
        return result

    # 无标记 → 原样返回
    return raw_input


def load_npc_list_from_json(file_path: str) -> B3NPCListLayer:
    """读取数组格式JSON，返回NPC列表层对象"""
    npc_list_layer = B3NPCListLayer()
    with open(file_path, "r", encoding="utf-8") as f:
        data_list = json.load(f)
        for i in data_list:
            npc = B3NPCLayer(i)
            npc_list_layer.add(npc)

    return npc_list_layer

















