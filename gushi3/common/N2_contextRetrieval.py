import os

from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from Schema.Context_sch import ContextHistory
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient


class N2_CE_Process:

    def __init__(self, logger=None):
        self.cli = LLMClient()
        self.logger = logger if logger else get_logger("N2_CE")



    def judge(self, context_event: ContextHistory) -> dict:
        """
        接受ContextHistory对象，为当前事件上下文
        :param context_event: 接受ContextHistory对象，用于ai做新事件判断
        :return:{
                    "is_new_event": str | bool,
                    "add_npc": list,
                    "del_npc": list,
                    "add_related_npc": list,
                    "del_related_npc": list
                }
        """
        with open(os.path.join(BASE_PATH, r'prompt\N2_contextRetrieval_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = []
        all_context.append({'role': 'system', 'content': sys_prompt})
        context_str = '\n'.join(context_event.context)
        all_context.append({'role': 'user', 'content': context_str})
        self.logger.info(f'事件判断上下文：\n{all_context}')

        replay = self.cli.chat_by_context(all_context)
        json_replay = json.loads(replay)

        return json_replay




























