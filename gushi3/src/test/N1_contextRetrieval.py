
import os

#-------------common---------------
from common.log import SimpleLogger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient

#-------------层级------------------
from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B3_npc_sch import B3NPCLayer
from Schema.B4_player_sch import B4PlayerLayer
from Schema.N1_playerInput_sch import N1PlayerInput
from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from Schema.Context_sch import ContextHistory

logger = SimpleLogger(log_file_path=os.path.join(base_path, r'log\test_contextRetrieval.log'), file_min_score=10, console_min_score=20)# 日志 文件记录详细，控制台粗糙
client = LLMClient()



#=================== 准备数据 ========================
# 世界层
b1WorldLayer = B1WorldLayer(load_json_to_dict(os.path.join(base_path, r'data\B1_worldState\global.json'))['大唐贞观低武世界'])
logger.debug(f'世界数据：{b1WorldLayer.to_dict()}')

# NPC层
npc_source_path = os.path.join(base_path, r'data\B3_npc')
npcFileList = walk_category_files(npc_source_path)
logger.debug(f'读取的npc文件列表{npcFileList}')

for npcFile in npcFileList:
    npcList = load_npc_list_from_json(npcFile)
logger.debug(f'取到npc数量{len(npcList)}')
for npc in npcList:
    logger.debug(npc.to_dict())

# 上下文
contextRecorder = ContextHistory()

#================ 玩家输入 =======================
# while True:
#     player_input = input('>')
#     logger.debug(player_input)
#     if player_input == 'exit':
#         break
#     contextRecorder.add_context(f'玩家：{player_input}')


print('=============玩家输入列表================')
context_list = contextRecorder.get_all_context()
for context in context_list:
    print(context)



















