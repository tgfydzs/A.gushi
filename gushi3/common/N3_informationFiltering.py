import os

from Schema.B4_player_sch import B4PlayerLayer
from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient
from Schema.Context_sch import ContextHistory
from Schema.B3_npclist_sch import B3NPCListLayer










class N3_Info_Filtering:
    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N3_IF')
        self.cli = LLMClient()



    def judge_for_npc(self,
                      event_context: ContextHistory,
                      event_npcs: B3NPCListLayer,
                      related_npcs: B3NPCListLayer,
                      player: B4PlayerLayer = None
                      ):
        """
        感知层，用于判断哪个npc知道哪些事情
        :param event_context: 事件上下文
        :param event_npcs: 事件涉及npc
        :param related_npcs: 事件涉及/潜在npc
        :param player: 玩家实例
        :return: {
                    "事件涉及的npc":{
                        "NPC名字":"玩家：NPC收到的信息"
                    },
                    "事件涉及/潜在npc":{
                        "NPC名字":"玩家：NPC收到的信息"
                    },
                    "地段传播":{
                        "msg":"玩家：过滤后的地段广播信息",
                        "level":3,
                        "mask":0.6
                    }
                }
        """
        with open(os.path.join(BASE_PATH, r'prompt\N3_informationFiltering_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()
        all_context = []
        all_context.append({'role': 'system', 'content': sys_prompt})
        user_input = {
            '该事件对话上下文': event_context.context,
            '事件涉及的npc': [npc_name['名字Name'] for npc_name in event_npcs.to_dict()],
            '事件涉及/潜在npc': [npc_name['名字Name'] for npc_name in related_npcs.to_dict()],
            '玩家当前位置': player.player_dict['当前状态CurrentState']['当前所处位置'] if player else ''
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.info(f'感知层LLM_messages：{all_context}')

        replay = self.cli.chat_by_context(all_context)
        json_replay = json.loads(replay)
        return json_replay





























