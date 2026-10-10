import json

from Schema.B3_1_npc_sch import B3NPCLayer
from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads
from common.base_cnf import *
from common.LLMclient import LLMClient


class N5_Response:
    """
    N5 回应层
    角色：结合NPC内部所有表进行主观推断，生成回应和内心想法。
    关键约束：本层不修改NPC实例，只产出 response_text + inner_thought。
    限制：不接受玩家实例，防止隐藏事实暴露。
    """

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N5_Response')
        self.cli = LLMClient()

    def response_say(self, npc_inst: B3NPCLayer, perception_msg: str,
                     context_history: list = None):
        """
        生成NPC回应和内心想法。

        :param npc_inst: N4推理后的NPC实例（本层不改它）
        :param perception_msg: N3感知层分发的带方向消息
        :param context_history: 全局对话上下文
        :return: (response_text: str, inner_thought: str)
        """
        sys_prompt = load_prompt('N5_response_sys.txt')

        all_context = [{'role': 'system', 'content': sys_prompt}]
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '对话上下文': context_history if context_history else [],
            '感知输入': perception_msg,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N5_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context, label='N5')
        json_replay = safe_json_loads(reply, label='N5')
        response_text = json_replay['回应Response']
        inner_thought = json_replay['内心想法InnerThought']

        self.logger.info(f'回应：{response_text} | 内心想法：{inner_thought}')
        return response_text.strip(), inner_thought.strip()

