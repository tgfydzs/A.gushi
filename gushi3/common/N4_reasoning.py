import json

from Schema.B3_1_npc_sch import B3NPCLayer
from common.log import get_logger
from common.small_tool import load_prompt, apply_npc_delta, safe_json_loads
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
        new_npc_inst = apply_npc_delta(npc_inst, npc_delta)

        return new_npc_inst
