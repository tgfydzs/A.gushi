"""
N4+N5 合并层：理解回应层。

合并依据（不违背信息隔离）：
  N4 的输入是 (NPC实例, 感知输入)，N5 的输入是 (NPC实例, 感知输入, 对话上下文)。
  两层的**信息集本就相同**，N5 只是多一个全局对话上下文。合并后没有把任何
  受限信息喂给不该看的层——本层仍然**不接受玩家实例**，
  「N6 是唯一接受玩家实例的层」这条约束原样保留。

输出仍与原来两层的字段一一对应，代码层防幻觉照旧：
  {"npc实例":{"增加":{},"删减":{}}, "回应Response":"", "内心想法InnerThought":""}
且 npc实例增量只允许含 待解释事件PendingReasoning。

流水线：N45（理解→回应）→ N6（收尾）
"""

import json

from Schema.B3_1_npc_sch import B3NPCLayer
from common.log import get_logger
from common.small_tool import load_prompt, apply_npc_delta, safe_json_loads
from common.base_cnf import *
from common.LLMclient import LLMClient


class N45_ReasonResponse:
    """
    理解回应层：一次调用完成「推理 → 回应」。

    限制：不接受玩家实例，防止隐藏事实暴露。
    """

    # 本层唯一允许改动的NPC字段
    ALLOWED_FIELD = '待解释事件PendingReasoning'

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N45_Comprehension')
        self.cli = LLMClient('N45')

    def response_for_npc(self, npc_inst: B3NPCLayer, perception_msg: str,
                         context_history: list = None):
        """
        :param npc_inst: NPC实例（原始，未被本轮修改）
        :param perception_msg: N3感知层分发的带方向消息
        :param context_history: 全局对话上下文
        :return: (更新后的B3NPCLayer, response_text: str, inner_thought: str)
        """
        sys_prompt = load_prompt('N45_reason_response_sys.txt')

        user_input = {
            '你是谁': npc_inst.to_dict(),
            '对话上下文': context_history if context_history else [],
            '感知输入': perception_msg,
        }
        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]
        self.logger.debug(f'N45_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context, label='N45')
        json_replay = safe_json_loads(reply, label='N45')

        # ---- 推理部分：只取待解释事件增量，越权字段直接丢弃 ----
        npc_delta = self._sanitize_npc_delta(json_replay.get('npc实例', {}))
        new_npc_inst = apply_npc_delta(npc_inst, npc_delta)

        # ---- 回应部分 ----
        # 字段名容错：模型偶尔会漏掉 "InnerThought" 后缀
        response_text = self._pick(json_replay, '回应Response', '回应').strip()
        inner_thought = self._pick(json_replay, '内心想法InnerThought', '内心想法').strip()
        if not response_text:
            self.logger.warn(f'[{npc_inst.name}] N45 回应为空，置为默认打发句')
            response_text = '……'
        if not inner_thought:
            self.logger.warn(f'[{npc_inst.name}] N45 内心想法为空，原始回复：{json_replay}')
            inner_thought = '（无明确想法）'

        self.logger.info(f'[{npc_inst.name}] 推理增量：{npc_delta}')
        self.logger.info(f'[{npc_inst.name}] 回应：{response_text} | 内心想法：{inner_thought}')
        return new_npc_inst, response_text, inner_thought

    # ===================== 解析容错 =====================

    @staticmethod
    def _pick(result: dict, *keys) -> str:
        """按顺序取第一个非空值，容忍模型漏写字段名后缀。"""
        for k in keys:
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ''

    # ===================== 防越权 =====================

    def _sanitize_npc_delta(self, raw_delta: dict) -> dict:
        """
        只放行「待解释事件PendingReasoning」的增删，其余字段一律丢弃。
        对应原先 N4「只操作PendingReasoning，不改其他表」的硬约束。
        """
        if not isinstance(raw_delta, dict):
            return {}

        clean = {}
        for op in ('增加', '删减'):
            items = (raw_delta.get(op) or {})
            if not isinstance(items, dict):
                continue
            kept = items.get(self.ALLOWED_FIELD)
            if kept:
                clean.setdefault(op, {})[self.ALLOWED_FIELD] = kept
        return clean
