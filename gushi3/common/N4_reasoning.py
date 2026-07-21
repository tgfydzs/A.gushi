import json

from Schema.B3_1_npc_sch import B3NPCLayer
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient


class N4_Reasoning:
    """
    N4 推理层
    角色：接受perception_msg+NPC实例，结合NPC自身条件推理玩家话语中
         无法用客观事实直接解释的事情，更新「待解释事件」表。
    限制：不接受玩家实例，防止隐藏事实暴露。
    """

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N4_Reasoning')
        self.cli = LLMClient()

    def reason_for_npc(self, npc_inst: B3NPCLayer, perception_msg: str):
        """
        推理层：更新NPC的待解释事件表

        :param npc_inst: NPC实例
        :param perception_msg: N3感知层分发的带方向消息
        :return: 更新后的B3NPCLayer
        """
        sys_prompt = load_prompt('N4_reasoning_sys.txt')

        all_context = [{'role': 'system', 'content': sys_prompt}]
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '感知输入': perception_msg,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N4_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context, label='N4')
        json_replay = safe_json_loads(reply, label='N4')
        self.logger.info(f'推理结果：{json_replay}')

        npc_delta = json_replay.get('npc实例', {})

        # 代码层防护：N4只允许修改PendingReasoning，过滤LLM越权修改其他字段
        _N4_ALLOWED_FIELDS = {'待解释事件PendingReasoning'}
        for section in ('增加', '删减'):
            sec = npc_delta.get(section, {})
            for field in list(sec.keys()):
                if field not in _N4_ALLOWED_FIELDS:
                    self.logger.warn(f'N4 拦截越权字段 [{field}]（N4只允许修改PendingReasoning）')
                    del sec[field]

        new_npc_inst = apply_npc_delta(npc_inst, npc_delta)

        # 去重：LLM可能重复追加相同的pending条目，Python侧兜底
        seen = []
        deduped = []
        for item in new_npc_inst.pending_reasoning:
            # 用前30字作为去重key（末尾细节差异不影响去重）
            key = item[:30]
            if key not in seen:
                seen.append(key)
                deduped.append(item)
        if len(deduped) < len(new_npc_inst.pending_reasoning):
            removed = len(new_npc_inst.pending_reasoning) - len(deduped)
            new_npc_inst.pending_reasoning = deduped
            self.logger.info(f'🧹 待解释事件去重：清理了{removed}条重复项')

        # 截断：最多保留10条待解释事件，超出时丢弃最旧的
        if len(new_npc_inst.pending_reasoning) > 10:
            old_count = len(new_npc_inst.pending_reasoning)
            new_npc_inst.pending_reasoning = new_npc_inst.pending_reasoning[-10:]
            self.logger.info(f'🧹 待解释事件截断：{old_count}→10条')

        return new_npc_inst
