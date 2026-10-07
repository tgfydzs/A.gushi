"""
L2 角色层（角色域，每 NPC 1 次，并行）

合并了 gushi3 的 N45（理解回应）+ N6 的**主观**部分。
一次调用完成「理解 → 回应 → 主观落实」。

    L1 分发失真感知
      ↓
    L2 per NPC：推理待解释事件 → 据此回应 + 内心想法 → 落实主观表 → 出动作意图
      ↓
    L3 统一结算客观状态

# 不拿玩家实况

L2 **完全看不到**玩家的真实状态（持有物、描述、真实实力）。
它对这个玩家的一切认知，都来自 L1 给它的那一份**失真感知**。

理由：L2 产出自由文本对白，**代码无法裁剪其中的泄漏**——它要是知道了玩家兜里有
一百灵石，下一句话里就可能带出来。所以只能在入口过滤。

代价（明确接受）：NPC 判断战斗结果时，依据的是**它自己（可能错误）的感知**。
**这正是越级战斗成立的前提**，不是缺陷。

# 只出「动作意图」，不落实客观变更

L2 看不到玩家实况，**无法验证玩家的声明**。玩家说"我给你一百灵石，把剑给我"，
L2 只能信——NPC 在现实中同样看不到你的钱包。

如果 L2 擅自把剑扣了，L3 事后发现玩家付不起，**剑已经出去了，没人能收回来**。

所以正确切法：L2 只表达"我想做什么"（动作意图），客观是否成立由 L3 判定。
交易因此天然一致——**L3 同时看到双方持有物，判定成立与否，双方一起变或一起不变。**

# 与 B2（后台幕后）的关系

**同一套输出 Schema、同一套落盘代码**，只换 prompt：
`respond_onstage()` 用到场 prompt（需要"回应"），`act_offstage()` 用幕后 prompt
（不需要对白，行为描述即可）。这是设计方案里"职能整合"的落点。
"""

import json
from copy import deepcopy

from common.LLMclient import LLMClient
from common.log import get_logger
from common.small_tool import (load_prompt, safe_json_loads, pick_field,
                               canonical_table, strip_empty_values)

logger = get_logger('L2_Character')


# 角色域可以写的表（**主观**）。不在白名单里的一律丢弃。
SUBJECTIVE_TABLES = (
    '知识记忆Memory',
    '信念Belief',
    '性格Personality',
    '生活目标Goals',
    '好感度Relationship',
    '观察Observation',
    '对玩家观察PlayerObservation',
    '待解释事件PendingReasoning',
)

# 客观表，属于结算域。L2 无论写出什么都丢弃——由 L3 决定。
OBJECTIVE_TABLES = ('当前状态CurrentState', '持有物Inventory')

# 动作意图的合法类型。未列出的归 '无'——比让脏类型流进 L3 好。
INTENT_TYPES = ('给予', '收取', '攻击', '移动', '使用', '求助', '无')


def sanitize_npc_delta(delta: dict) -> dict:
    """
    **代码层强制**：L2 产出里只有主观表能通过。

    这不是防手滑，是防 LLM 越权。模型被要求在内心想法里写"我打算把剑给他"，
    它很容易顺手把 `持有物Inventory` 也改了。一旦放行，D12
    「L3 是唯一的客观状态写入口」就废了，交易一致性也就没了。
    """
    if not isinstance(delta, dict):
        return {'增加': {}, '删减': {}}

    out = {'增加': {}, '删减': {}}
    dropped = []
    for section in ('增加', '删减'):
        body = pick_field(delta, section, default={}) or {}
        if not isinstance(body, dict):
            continue
        for field, value in body.items():
            # 表名要过一道规范化：模型常把 `知识记忆Memory` 写成 `知识记忆`
            canon = canonical_table(field, SUBJECTIVE_TABLES)
            if canon:
                # 8 张主观表**全是 list 型**（dict 型的当前状态/持有物属结算域，已在上面拦掉）。
                # 模型偶尔会把单条写成裸字符串，这里包一层，免得下游 extend 把字符串拆成字符。
                if not isinstance(value, list):
                    value = [value]
                out[section][canon] = value
            else:
                dropped.append(field)

    if dropped:
        logger.warn(f'L2 越权字段被丢弃（属结算域或未知）：{sorted(set(dropped))}')
    # 去掉 `{"表名": []}` 这种空壳——它会让"本轮变了什么"失真
    out = {sec: strip_empty_values(body) for sec, body in out.items()}
    return out


def normalize_intent(raw) -> dict:
    """归一化动作意图。缺失/格式错 → 安全的"无"。"""
    if not isinstance(raw, dict):
        return {'类型': '无', '描述': '', '对象': '', '物品': ''}
    t = str(pick_field(raw, '类型', 'type', default='无') or '无').strip()
    if t not in INTENT_TYPES:
        logger.warn(f'未识别的动作意图类型「{t}」，按"无"处理')
        t = '无'
    return {
        '类型': t,
        '描述': str(pick_field(raw, '描述', 'description', default='') or '')[:120],
        '对象': str(pick_field(raw, '对象', 'target', default='') or '').strip(),
        '物品': str(pick_field(raw, '物品', 'item', default='') or '').strip(),
    }


class L2_Character:
    """角色层：理解 → 回应 → 主观落实 → 动作意图"""

    def __init__(self):
        self.cli = LLMClient('L2')

    # ===================== 输入组装 =====================

    @staticmethod
    def _npc_view(npc) -> dict:
        """NPC 的 11 张表全量（截断已由 cap_fields 保证）"""
        return npc.to_dict()

    # ===================== 到场模式 =====================

    def respond_onstage(self, npc, perception: str, global_context: list = None) -> dict:
        """
        在场 NPC：理解 + 回应。

        :param npc: B3NPCLayer
        :param perception: L1 给这个 NPC 的**失真感知**（不是玩家原话）
        :param global_context: 全局对话上下文（供承接话题）
        :return: {
            'name','npc_instance'(已净化),'回应Response','内心想法InnerThought',
            '动作意图'(已归一),'raw'
        }
        """
        user_input = {
            '你是谁': self._npc_view(npc),
            '感知输入': perception,
            '对话上下文': list(global_context or [])[-12:],
        }
        return self._call(user_input, 'L2_character_onscene_sys.txt',
                          label=f'L2·{npc.name.split("、")[0][:8]}', npc=npc,
                          perception=perception)

    # ===================== 幕后模式（B2） =====================

    def act_offstage(self, npc, world_state: list = None,
                     director_context: list = None, onstage_names: list = None) -> dict:
        """
        幕后 NPC：心跳触发时推演它在做什么。

        **与到场模式同一套 Schema 和落盘代码**，只是不需要对白——
        `回应Response` 在这里表示"它做了什么"，直接进 Observation。

        :param onstage_names: 本轮在台前的 NPC，避免重复推演
        """
        user_input = {
            '你是谁': self._npc_view(npc),
            '世界状态': list(world_state or []),
            '幕后情报': list(director_context or [])[-10:],
            '台前人物': list(onstage_names or []),
        }
        return self._call(user_input, 'L2_character_daemon_sys.txt',
                          label=f'B2·{npc.name.split("、")[0][:8]}', npc=npc,
                          perception='')

    # ===================== 公共调用 =====================

    def _call(self, user_input: dict, prompt_file: str, label: str,
              npc, perception: str) -> dict:
        sys_prompt = load_prompt(prompt_file)
        context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]
        reply = self.cli.chat_by_context(context, label=label)
        raw = safe_json_loads(reply, label=label) or {}

        # 用 pick_field 而不是 raw.get —— 模型稳定地把英文后缀丢掉，
        # 返回的是 '内心想法' 而不是 '内心想法InnerThought'（见 small_tool 的说明）
        delta = sanitize_npc_delta(pick_field(raw, 'npc实例', default={}))

        response = str(pick_field(raw, '回应Response', '行为Action', default='') or '').strip()
        if not response:
            logger.warn(f'[{npc.name}] L2 回应为空——模型忽略了"切勿沉默"')

        thought = str(pick_field(raw, '内心想法InnerThought', default='') or '').strip()
        if not thought:
            logger.warn(f'[{npc.name}] L2 内心想法为空——L3 将失去判断真实意图的依据')

        intent = normalize_intent(pick_field(raw, '动作意图', default=None))

        logger.info(f'[{npc.name}] L2 完成 | 主观增量={list(delta["增加"]) + list(delta["删减"])} '
                    f'| 意图={intent["类型"]}{"：" + intent["描述"][:24] if intent["描述"] else ""}')
        if response:
            logger.debug(f'[{npc.name}] 回应：{response}')

        return {
            'name': npc.name,
            'npc_instance': delta,
            '回应Response': response,
            '内心想法InnerThought': thought,
            '动作意图': intent,
            '感知输入': perception,
            'raw': raw,
        }

    # ===================== 增量落地（主观表） =====================

    @staticmethod
    def apply_subjective(npc, delta: dict):
        """
        把主观增量写回 NPC（原地修改）。

        客观表**不在这里落地**——它们在 L3 结算后由 GameController 统一写入。
        这里再做一次白名单检查，防止调用方绕过 sanitize_npc_delta 直接传脏数据。
        """
        from common.small_tool import _apply_dict_delta

        clean = {k: v for k, v in (delta or {}).items() if k in ('增加', '删减')}
        if not clean:
            return npc
        safe = {'增加': {}, '删减': {}}
        for section in ('增加', '删减'):
            for field, value in (clean.get(section) or {}).items():
                if field in SUBJECTIVE_TABLES:
                    safe[section][field] = deepcopy(value)
        if not safe['增加'] and not safe['删减']:
            return npc

        npc_dict = npc.to_dict()
        _apply_dict_delta(npc_dict, safe)
        from Schema.B3_1_npc_sch import B3NPCLayer
        return B3NPCLayer(npc_dict)
