import json
import os
import re
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

















