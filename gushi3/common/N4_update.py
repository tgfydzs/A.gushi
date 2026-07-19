import os

from Schema.B4_player_sch import B4PlayerLayer
from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient
from Schema.Context_sch import ContextHistory
from Schema.B3_npclist_sch import B3NPCListLayer

class N4_Update:
    """
    以npc为视角，进行的客观操作，该阶段接受玩家实例，但因为是客观操作，不涉及秘密等隐藏、主观信息
    """
    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N4_Update')
        self.cli = LLMClient()

    def update_for_npc(self, npc_inst: B3NPCLayer, player_inst: B4PlayerLayer, perception_msg: str):
        """
        :param npc_inst:   NPC 实例
        :param player_inst: 玩家实例
        :param perception_msg: N3感知层分发的带方向消息（如"玩家对你说：..."或"玩家对[名字]说：..."）
        :return: (更新后的B3NPCLayer, 玩家增量dict, 客观事实str)
        """
        with open(os.path.join(BASE_PATH, r'prompt\N4_update_sys'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = []
        all_context.append({'role': 'system', 'content': sys_prompt})
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '感知输入': perception_msg,
            '玩家实例': player_inst.player_dict
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N4_LLM消息：{all_context}')

        replay = self.cli.chat_by_context(all_context)
        json_replay = safe_json_loads(replay, label='N4')

        objective_facts = json_replay.pop('客观事实ObjectiveFacts', '无')
        npc_delta = json_replay.get('npc实例', {})
        player_delta = json_replay.get('玩家实例', {})

        # 增量模式：只更新有变更的字段
        new_npc_inst = apply_npc_delta(npc_inst, npc_delta)

        return new_npc_inst, player_delta, objective_facts
