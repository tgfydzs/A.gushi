import json
import os
import re
from copy import deepcopy
from typing import Dict, Any, List
from functools import lru_cache

from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B3_npclist_sch import B3NPCListLayer
from Schema.B3_1_npc_sch import B3NPCLayer, NUMERIC_TABLES
from common.log import get_logger
from common.base_cnf import BASE_PATH

logger = get_logger("small_tool")


# ===================== Prompt 加载（客制化占位符替换） =====================

_LAYER_PROMPT_MAP = {
    'N3_informationFiltering_sys.txt': 'N3',
    'N5_response_sys.txt': 'N5',
    # N45 复用了原 N5 的客制化占位符（如 {输出约束示例}）
    'N45_reason_response_sys.txt': 'N5',
    'N6_update_sys.txt': 'N6',
    'N6_player_sys.txt': 'N6_player',
    'N9b_narration_sys.txt': 'N9b',
    'N9c_behavior_sys.txt': 'N9c',
}


@lru_cache(maxsize=32)
def load_prompt(filename: str) -> str:
    """
    读取 prompt 文件并替换客制化占位符。

    从 prompt/{filename} 读取原始文本，
    查找对应的 data/prompt/{layer}_custom/ 目录，
    将 {占位符} 替换为同名的 .md 文件内容。

    :param filename: prompt 文件名，如 'N3_informationFiltering_sys.txt'
    :return: 替换后的完整 prompt 文本
    """
    with open(os.path.join(BASE_PATH, 'prompt', filename), 'r', encoding='utf-8') as f:
        text = f.read()

    layer = _LAYER_PROMPT_MAP.get(filename)
    if layer:
        custom_dir = os.path.join(BASE_PATH, 'data', 'prompt', f'{layer}_custom')
        if os.path.isdir(custom_dir):
            placeholders = re.findall(r'\{(\w+)\}', text)
            for ph in placeholders:
                md_path = os.path.join(custom_dir, f'{ph}.md')
                if os.path.isfile(md_path):
                    with open(md_path, 'r', encoding='utf-8') as f_md:
                        text = text.replace(f'{{{ph}}}', f_md.read().strip())
                        logger.debug(f'prompt占位符替换: {filename} → {ph}.md')

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

    # 2.5 **括号修复**：多/少一个闭合符时，整段解析会报 `Extra data` 或 `Expecting ','`。
    #     逐个试着删掉一个 `}` / `]`，只要删掉之后整段合法，就采纳并告警。
    #     ⚠️ 这一步**只补语法**（删一个多余的闭合符），不改任何内容、不猜模型意图——
    #     与"把 JSON 补全"是两件事：内容错了照样会露出来（见 N6 的缺键/未知键告警）。
    #     为什么要在 `raw_decode` 之前：`raw_decode` 会把"提前闭合的半截对象"当成功返回，
    #     于是 40% 的字段（玩家实例 / 旁白 / 耗时）被当成尾随内容丢掉，比崩更隐蔽。
    for i, ch in enumerate(raw):
        if ch not in '}]':
            continue
        candidate = raw[:i] + raw[i + 1:]
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        logger.warn(f'safe_json_loads[{label}] 括号修复：删掉第 {i} 字的 {ch!r} 后整段合法')
        return obj

    # 2.6 **只取第一个完整 JSON 值**，尾随内容丢掉并告警。
    #     为什么不能靠下面的"截断到最后一个 }"：模型在 JSON 后面又写了一段**带括号**的
    #     东西（第二个对象、或句子里带 `}`），`rfind('}')` 就会越过第一个对象、把两段
    #     一起切下来，候选串非法 → 四级降级全废 → 整轮 N6 抛异常、**回应一起丢了**。
    #     实测：2026-10-17 第 2/6 轮各崩一次，日志是 `Extra data: line 1 column 405/281`。
    for i, ch in enumerate(raw):
        if ch not in '{[':
            continue
        try:
            obj, end = json.JSONDecoder().raw_decode(raw, i)
        except json.JSONDecodeError:
            break          # 第一个括号起点就不成对象，交给后面的降级
        if isinstance(obj, (dict, list)):
            tail = raw[end:].strip()
            if tail:
                logger.warn(f'safe_json_loads[{label}] 尾部多余内容已丢弃 {len(tail)} 字：{tail[:80]!r}')
            return obj
        break

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


# ===================== 增量合并工具 =====================

def _apply_dict_delta(base: dict, delta: dict, report: dict = None):
    """
    将一个增量 delta 原地应用到 base 字典上。

    delta 结构：{"增加": {字段名: 值}, "删减": {字段名: 值}}
    - list 字段：增加→extend，删减→逐条移除
    - dict 字段：增加→update合并，删减→逐key删除
    - 其他(如str)：增加→覆盖

    :param report: 可选。收集"出了事但不会出声"的三种情况，供调用方统一记日志：
        `unknown`（字段名不在表里）、`missed`（删减对不上账本原文）、
        `noop`（对 str/数值做删减）。

    ⚠️ 为什么要这个 report：这三种情况**全都是静默的**。NPC 侧字段名拼错会在重建时
    被丢掉（`B3NPCLayer.to_dict()` 只认固定 12 键），删减对不上就一条都不删——
    实测模型在 `删减` 里放的是**改写过的句子**，于是表只增不减，靠截断收场。
    没有日志 = 结算无声消失，排查时只能猜。
    """
    for section_key, (section_label, op_func) in _DELTA_OPS.items():
        section = delta.get(section_key, {})
        if not section:
            continue
        for field, value in section.items():
            op_func(base, field, value, report)


def _note(report, kind: str, field: str, detail: str):
    if report is None:
        return
    report.setdefault(kind, []).append(f'{field}（{detail}）')


#: 拒绝本次改动的哨兵（非法值不许落盘，见 `_coerce_value`）
_REJECT = object()


def _coerce_value(field: str, existing, value, report: dict):
    """
    数值表（性格 / 好感度）要求 `{名: 数值}`。**形状不对就整条拒绝，不替模型猜。**

    这里原来写的是"模型按旧列表格式交来 → 就地转成 `{名: 数值}`"。那是错的方向：
    prompt 明写了要 dict，模型仍交 list 是**提示词没教住**，不是代码该补的事。
    代码一旦替它猜，就再也看不出模型在违背约定——而猜出来的数值会悄悄改写一张
    本来干净的 dict 表。所以现在是：**拒绝 + 告警，表保持原样**。
    """
    if field in NUMERIC_TABLES and isinstance(existing, dict) and not isinstance(value, dict):
        _note(report, 'rejected', field,
              '数值表要求 {"名": 数值}，收到非字典 → 本次改动整条丢弃')
        return _REJECT
    if isinstance(existing, (list, dict)) and isinstance(value, (list, dict)) \
            and type(existing) is not type(value):
        _note(report, 'type', field,
              f'{type(existing).__name__} 被 {type(value).__name__} 整表替换')
    return value


def take_npc_delta(payload: dict, label: str = '') -> dict:
    """
    从 LLM 回复里取 **NPC 增量**，返回统一的 `{'增加': {...}, '删减': {...}}`。

    为什么要这个函数：`npc实例:{增加,删减}` 是**三层嵌套**，模型写到长列表末尾时
    常把 `删减` 提到根层、并多闭合一层括号（实测一个日志里 7 次），于是
    `玩家实例 / 旁白 / 耗时` 全掉成"尾随数据"。现在契约拍平成两个顶层字段：

        新：`{"npc增加": {...}, "npc删减": {...}, "玩家实例": ...}`
        旧：`{"npc实例": {"增加": {...}, "删减": {...}}, ...}`（兼容，打 WARN）

    这样"删减写错层"这个错误类别在结构上不存在了——它和 `玩家实例` 是平级的。
    三种形态都收敛到同一结果，**畸形形态必须可见**（WARN），不静默吃掉。
    """
    payload = payload or {}
    inc = payload.get('npc增加')
    dec = payload.get('npc删减')
    if isinstance(inc, dict) or isinstance(dec, dict):
        return {'增加': dict(inc or {}), '删减': dict(dec or {})}

    legacy = payload.get('npc实例')
    if isinstance(legacy, dict) and (legacy.get('增加') or legacy.get('删减')
                                     or '增加' in legacy or '删减' in legacy):
        if label:
            logger.warn(f'{label} 仍在用旧形状 `npc实例:{{增加,删减}}`——'
                        f'请改用顶层的 `npc增加` / `npc删减`')
        return {'增加': dict(legacy.get('增加') or {}), '删减': dict(legacy.get('删减') or {})}

    # 模型把 增加/删减 提到了**根层**（最常见的畸形）：合并回来，但要说出来
    inc = payload.get('增加')
    dec = payload.get('删减')
    if isinstance(inc, dict) or isinstance(dec, dict):
        if label:
            logger.warn(f'{label} `增加/删减` 写在了根层（应在 `npc增加`/`npc删减`）——'
                        f'已按 NPC 增量收下，请修正输出形状')
        return {'增加': dict(inc or {}), '删减': dict(dec or {})}

    return {'增加': {}, '删减': {}}


def _delta_add(base: dict, field: str, value, report: dict = None):
    """增加操作"""
    if field not in base:
        _note(report, 'unknown', field, '字段名不在表里')
    value = _coerce_value(field, base.get(field), value, report)
    if value is _REJECT:
        return
    existing = base.get(field)
    if isinstance(value, dict) and isinstance(existing, dict):
        existing.update(value)
    elif isinstance(value, list) and isinstance(existing, list):
        existing.extend(value)
    else:
        base[field] = value


def _delta_del(base: dict, field: str, value, report: dict = None):
    """删减操作"""
    existing = base.get(field)
    if existing is None:
        _note(report, 'unknown', field, '删减一个不存在的字段')
        return
    value = _coerce_value(field, existing, value, report)
    if value is _REJECT:
        return
    if isinstance(value, dict) and isinstance(existing, dict):
        for k in value:
            if k not in existing:
                _note(report, 'missed', field, f'键「{str(k)[:20]}」不在表里')
            existing.pop(k, None)
    elif isinstance(value, list) and isinstance(existing, list):
        for item in value:
            if item in existing:
                existing.remove(item)
            else:
                _note(report, 'missed', field, f'条目「{str(item)[:24]}」对不上原文')
    elif isinstance(existing, (list, dict)) and isinstance(value, (list, dict)):
        # **形状不对**（dict 字段收到 list，或反之）。这里必须**拒绝并告警**，不能落到下面的
        # 通用 noop——实测过一次：模型把交出古卷写成 `删减: {'持有物Inventory': ['无名古卷（三页）']}`
        # （list 套在 dict 字段上），noop 之后**什么都没删**，于是旁白说"已交出"、
        # 国主账上却还留着，东西变成两份，而日志只有一句看不出问题的 WARN。
        _note(report, 'shape', field,
              f'{type(existing).__name__} 字段收到 {type(value).__name__} 形式的删减'
              f'（dict 写 {{名: 值}}，list 写 [条目]）→ 该字段本次不动')
    else:
        _note(report, 'noop', field, f'{type(existing).__name__} 无法删减，只能覆盖')


_DELTA_OPS = {
    '增加': ('add', _delta_add),
    '删减': ('del', _delta_del),
}


def _log_report(label: str, report: dict):
    """把 `_apply_dict_delta` 收集的异常一次性记成告警（三类各一行，不逐条刷屏）。"""
    if not report:
        return
    for kind, text in (
        ('unknown', '字段名不在表里'),
        ('missed', '删减未命中（账本原文对不上，表不会减少）'),
        ('noop', '删减对非 list/dict 无效'),
        ('rejected', '拒绝落盘（数值表要求 {名: 数值}，收到的形状不对）'),
        ('shape', '形状不符（dict 字段的删减要写 {名: 值}，list 字段要写 [条目]；该字段本次不动）'),
        ('type', '容器类型与表不符（整表被替换）'),
    ):
        items = report.get(kind) or []
        if not items:
            continue
        more = f' …另{len(items) - 6}处' if len(items) > 6 else ''
        logger.warn(f'[{label}] {text} {len(items)} 处：' + '；'.join(items[:6]) + more)


#: 身份四表 —— 只有它们受"可塑性"（`表更新成本UpdateCost`）约束。见 `B3_1_npc_sch` 的说明。
def _gate_by_update_cost(npc, delta: dict) -> tuple:
    """
    用这个 NPC 的**更新成本**给身份表的增删**限流**：成本越高，一次结算里能改的条数越少。

    这就是 `表更新成本UpdateCost` 设计之初的用途（"模拟 npc 进步退步、聪明"）——
    它自 2026-08 起一直没有消费者，是个死字段。

    :return: (新 delta, 裁剪说明 list)。成本取不到就原样返回，不猜。
    """
    from Schema.B3_1_npc_sch import MALLEABLE_TABLES, allowed_items_by_cost

    if not hasattr(npc, 'table_cost'):
        return delta, []
    out = {'增加': {}, '删减': {}}
    trimmed = []
    for section in ('增加', '删减'):
        for field, value in (delta.get(section) or {}).items():
            if field not in MALLEABLE_TABLES:
                out[section][field] = value
                continue
            allowed = allowed_items_by_cost(npc.table_cost(field))
            size = len(value) if isinstance(value, (list, dict)) else 1
            if allowed is None or size <= allowed:
                out[section][field] = value
                continue
            if isinstance(value, dict):
                out[section][field] = dict(list(value.items())[:allowed])
            else:
                out[section][field] = value[:allowed]
            trimmed.append(f'{field}·{section} {size}→{allowed}（成本{npc.table_cost(field)}）')
    return out, trimmed


def apply_npc_delta(npc: B3NPCLayer, delta: dict) -> B3NPCLayer:
    """
    将增量 delta 应用到 NPC 实例，返回新的 B3NPCLayer。

    ⚠️ 重建走 `B3NPCLayer(npc_dict)`，只认 `to_dict()` 那 12 个键——
    增量里任何**别的**字段名会被静默丢掉（现在会先记一条告警，见 `_log_report`）。

    :param npc:   原始 NPC 实例
    :param delta: {"增加": {...}, "删减": {...}} 或空dict
    :return: 更新后的 B3NPCLayer
    """
    if not delta or (not delta.get('增加') and not delta.get('删减')):
        return npc
    gated, trimmed = _gate_by_update_cost(npc, delta)
    if trimmed:
        logger.info(f'🧠 [{npc.name}] 可塑性限流：' + '；'.join(trimmed))
    npc_dict = npc.to_dict()
    report = {}
    _apply_dict_delta(npc_dict, gated, report)
    _log_report(npc.name, report)
    return B3NPCLayer(npc_dict)


def apply_player_delta(player_dict: dict, delta: dict) -> dict:
    """
    将增量 delta 应用到玩家 dict，返回深拷贝后的新 dict。

    ⚠️ 玩家表**没有 schema**（`B4PlayerLayer` 只是包了原始 dict），所以未知键会被真的
    新建并存进存档。这不是错，但那些字段没有任何一层会读——所以要让它可见。

    :param player_dict: 原始玩家字典
    :param delta: {"增加": {...}, "删减": {...}}
    :return: 更新后的玩家字典
    """
    if not delta or (not delta.get('增加') and not delta.get('删减')):
        return player_dict
    result = deepcopy(player_dict)
    report = {}
    _apply_dict_delta(result, delta, report)
    if report.get('unknown'):
        # 新建字段对玩家来说是"允许"的（无 schema），但要让人看见它长了什么
        logger.warn(f'[玩家] 增量新建了字段（已存进存档，但没有层会读它）：'
                    f'{"；".join(report["unknown"][:6])}')
    report.pop('unknown', None)
    _log_report('玩家', report)
    return result


#: LLM 不许写的位置字段——**位置是代码的活**（`去向` 才是它的输出）。
#: 放在模块级是为了让测试能直接引用，避免两处各写一份清单。
POSITION_KEYS = ('位置', '当前所处位置', '行进')


def extract_position_dest(delta: dict) -> tuple:
    """
    把 delta 里 `当前状态CurrentState` 下的位置字段**取出来当「去向」**。

    规则（位置只有代码能写，LLM 只给语义）：
      · `当前所处位置`（字符串·地名或完整路径）→ 作为**去向**返回，调用方据此落坐标
      · `位置`（坐标）/ `行进` → 直接丢：坐标是代码的活，LLM 给了也不算

    :return: `(清理后的新 delta, 去向地名 or '', 被取走的位置键列表)`
    """
    clean = {'增加': {}, '删减': {}}
    dest, dropped = '', []
    if not isinstance(delta, dict):
        return clean, dest, dropped
    for section in clean:
        body = delta.get(section) or {}
        if not isinstance(body, dict):
            continue
        for field, value in body.items():
            if field == '当前状态CurrentState' and isinstance(value, dict):
                value = dict(value)
                if section == '增加' and isinstance(value.get('当前所处位置'), str):
                    dest = value['当前所处位置'].strip()
                dropped.extend(k for k in POSITION_KEYS if k in value)
                value = {k: v for k, v in value.items() if k not in POSITION_KEYS}
                if not value:
                    continue      # 只剩位置 → 这一项没有别的变化
            clean[section][field] = value
    return clean, dest, dropped


def strip_position_keys(delta: dict, label: str = '') -> dict:
    """
    **代码层强制**：丢掉 delta 里的位置字段（**玩家**路径用——玩家的位置只认 N2 的 `去向`）。

    为什么非丢不可：`位置`（坐标）是**唯一真源**，`当前所处位置` 是它的**派生**，
    两者只有 `GameController.set_position()` 一个写入点。LLM 直接写派生字段，
    就得到"坐标说在 A、字段说在 B"——N2/N7 读坐标派生、N3 读那个字段，
    于是同一轮里跨层位置口径分叉（2026-10-08 事故的形状）。

    NPC 的位置不走这里（NPC 没有 N2 那样的去向通道），走
    `GameController.apply_npc_grounded_delta()`：把 `当前所处位置` 当**地名**落坐标。
    """
    clean, _, dropped = extract_position_dest(delta)
    if dropped:
        logger.warn(f'[{label or "delta"}] LLM 想写位置字段 {sorted(set(dropped))}，'
                    f'已丢弃——玩家的位置由代码从 N2 的「去向」落坐标（move_player_to）')
    return clean


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


def merge_list_delta(target: dict, source: dict):
    """
    合并 **list 型** 增量（`{"增加": ["条目", ...], "删减": [...]}`），原地改 target。

    ⚠️ 这个**不能**用 `merge_delta` 代替：那个是**字段字典**型
    （`{"增加": {"表名": 值}}`），拿到 list 会在 `.items()` 上当场炸。
    世界状态（公开事实）与 `N9a 世界状态变更` 都是 list 型，走这里。

    按值去重：几个 NPC 的 N6 报同一件事时，不该变成三条。
    """
    for section in ('增加', '删减'):
        tgt = target.setdefault(section, [])
        for item in (source.get(section) or []):
            if item not in tgt:
                tgt.append(deepcopy(item))





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




def resolve_move_markers(raw_input: str, world_layer: B1WorldLayer, player=None):
    """
    扫描玩家输入中的移动标记，触发交互式位置导航。

    触发标记（不区分大小写）：
      - {移动} 或 {move}      → 占位符替换
      - /move                 → 独立移动命令
      - /move 后续内容         → 移动 + 后续行动

    处理逻辑：
      1. 检测原始输入是否包含触发标记
      2. 若触发 → 调用 world_layer.navigate_interactive()
      3. 若导航取消 → 返回 (None, None)
      4. 若导航完成 → 将选中路径替换到标记位置，不询问移动方式

    :param raw_input: 玩家原始输入
    :param world_layer: B1WorldLayer 实例
    :param player: B4PlayerLayer 玩家实例（取当前位置作为出发地）
    :return: (替换后的完整输入, 移动信息dict)；取消时返回 (None, None)
             移动信息格式：{"from": str, "to": str}
    """

    # 模式1：{移动} 或 {move} 占位符
    placeholder_match = re.search(r'\{(移动|move)\}', raw_input, re.IGNORECASE)
    if placeholder_match:
        print('\n--- 选择目标位置 ---')
        selected = world_layer.navigate_interactive()
        if selected is None:
            return (None, None)
        from_loc = player.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', '') if player else ''
        result = raw_input[:placeholder_match.start()] + selected + raw_input[placeholder_match.end():]
        move_info = {'from': from_loc, 'to': selected}
        return (result, move_info)

    # 模式2：/move 命令（独立或带后续内容）
    cmd_match = re.match(r'^/move\s*(.*)', raw_input, re.IGNORECASE)
    if cmd_match:
        suffix = cmd_match.group(1).strip()
        print('\n--- 选择目标位置 ---')
        selected = world_layer.navigate_interactive()
        if selected is None:
            return (None, None)
        from_loc = player.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', '') if player else ''
        if suffix:
            result = f'前往{selected}，{suffix}'
        else:
            result = f'前往{selected}'
        move_info = {'from': from_loc, 'to': selected}
        return (result, move_info)

    # 无标记 → 原样返回，无移动信息
    return (raw_input, None)


def load_npc_list_from_json(file_path: str) -> B3NPCListLayer:
    """读取数组格式JSON，返回NPC列表层对象"""
    npc_list_layer = B3NPCListLayer()
    with open(file_path, "r", encoding="utf-8") as f:
        data_list = json.load(f)
        for i in data_list:
            npc = B3NPCLayer(i)
            npc_list_layer.add(npc)

    return npc_list_layer

















