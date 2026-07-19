import os

from Schema.B4_player_sch import B4PlayerLayer
from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient
from Schema.Context_sch import ContextHistory
from Schema.B3_npclist_sch import B3NPCListLayer


class N5_Response():
    """
    以npc为视角，进行的主观操作，该阶段不接受玩家实例（防止隐藏事实暴露），进行主观判断、回答
    管线：N4（客观处理）→ N5（主观处理），N5不接收玩家实例，只接收N4产出的客观事实
    """
    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N5_response')
        self.cli = LLMClient()

    def response_say(self, npc_inst: B3NPCLayer, perception_msg: str, context_history: list = None, objective_facts: str = ''):
        """
        主观处理：生成NPC回应，并对NPC状态做主观微调
        :param npc_inst: N4更新后的NPC实例
        :param perception_msg: N3感知层分发的带方向消息
        :param context_history: 对话上下文
        :param objective_facts: N4产出的客观事实
        :return: (B3NPCLayer, str) 主观微调后的NPC实例和回应语句
        """
        with open(os.path.join(BASE_PATH, r'prompt\N5_response_sys'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = []
        all_context.append({'role': 'system', 'content': sys_prompt})
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '对话上下文': context_history if context_history else [],
            '感知输入': perception_msg,
            '客观事实ObjectiveFacts': objective_facts,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N5_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context)
        self.logger.debug(f'N5_NPC回应：{reply}')

        json_replay = safe_json_loads(reply, label='N5')
        response_text = json_replay.pop('响应Response', '')
        npc_delta = json_replay.get('npc实例', {})

        # 增量模式：只更新主观微调字段
        new_npc_inst = apply_npc_delta(npc_inst, npc_delta)
        return new_npc_inst, response_text.strip()
