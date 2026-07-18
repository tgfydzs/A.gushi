import os

from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from Schema.Context_sch import ContextHistory
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient
from Schema.Context_sch import ContextHistory
from Schema.B3_npc_sch import B3NPCLayer










class N3_Info_Filtering:
    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger("N3_Filtering")
        self.cli = LLMClient()



    def judge_for_npc(self, event_context: ContextHistory, npc_list: list[B3NPCLayer]):
        all_context = []
