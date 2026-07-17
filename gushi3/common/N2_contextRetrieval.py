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



    def judge(self, context_event: ContextHistory) -> bool:
        """
        接受ContextHistory对象，为当前事件上下文
        :param context_event: 接受ContextHistory对象，用于ai做新事件判断
        :return:
        """
        with open(os.path.join(BASE_PATH, r'prompt\N2_contextRetrieval_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = []
        all_context.append({'role': 'system', 'content': sys_prompt})
        event_context = context_event.context
        for context in event_context:
            all_context.append({'role': 'user', 'content': context})
        self.logger.info(f'事件判断上下文：\n{all_context}')

        replay = self.cli.chat_by_context(all_context)
        json_replay = json.loads(replay)
        judge = json_replay['is_new_event']

        return judge




























