import os

from Schema.B3_1_npc_sch import B3NPCLayer
from Schema.B4_player_sch import B4PlayerLayer
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient


class N6_Update:
    """
    N6 更新层（收尾层）
    角色：流水线最后一步，以客观视角做最终收尾——所有表都可能更新。
    限制：三层中唯一接受玩家实例的层。

    流水线：N4（推理）→ N5（回应）→ N6（更新收尾）
    """

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N6_Update')
        self.cli = LLMClient()

    def update_for_npc(self,
                       npc_inst: B3NPCLayer,
                       player_inst: B4PlayerLayer,
                       perception_msg: str,
                       inner_thought: str = ''):
        """
        客观收尾：根据内心想法和客观条件，落实所有表更新。

        :param npc_inst: N5回应后的NPC实例（原封不动）
        :param player_inst: 玩家实例
        :param perception_msg: N3感知层分发的带方向消息
        :param inner_thought: N5回应层产出的内心想法
        :return: (更新后的B3NPCLayer, 玩家增量dict)
        """
        with open(os.path.join(BASE_PATH, r'prompt\N6_update_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = [{'role': 'system', 'content': sys_prompt}]
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '感知输入': perception_msg,
            '玩家实例': player_inst.player_dict,
            '内心想法InnerThought': inner_thought,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N6_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context, label='N6')
        json_replay = safe_json_loads(reply, label='N6')

        # 清理空值：移除LLM可能输出的空list/dict字段
        npc_delta = strip_empty_delta(json_replay.get('npc实例', {}))
        player_delta = strip_empty_delta(json_replay.get('玩家实例', {}))

        self.logger.info(f'更新结果：npc_delta={npc_delta} player_delta={player_delta}')

        new_npc_inst = apply_npc_delta(npc_inst, npc_delta)

        return new_npc_inst, player_delta
