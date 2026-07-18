
import os

#-------------common---------------
from common.log import get_logger
from common import log as log_cfg
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient

#----------------- 层级处理 -------------------
from common.N2_contextRetrieval import N2_CE_Process

#-------------层级schema------------------
from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B3_npc_sch import B3NPCLayer
from Schema.B4_player_sch import B4PlayerLayer
from Schema.N1_playerInput_sch import N1PlayerInput
from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from Schema.Context_sch import ContextHistory

# 初始化日志系统（全局唯一文件句柄，所有模块共享）
log_cfg.setup(session_id="test001", file_min_score=10, console_min_score=30)
logger = get_logger("N2_Test")



#=================== 准备数据 ========================
# -------------准备对象-----------
# 上下文
global_context = ContextHistory()
event_context = ContextHistory()

# 事件层处理器
event_judge = N2_CE_Process()


# 世界层
b1WorldLayer = B1WorldLayer(load_json_to_dict(os.path.join(BASE_PATH, r'data\B1_worldState\global.json'))['大唐贞观低武世界'])
logger.debug(f'世界数据：{b1WorldLayer.to_dict()}')

# NPC层
npc_source_path = os.path.join(BASE_PATH, r'data\B3_npc')
npcFileList = walk_category_files(npc_source_path)
logger.debug(f'读取的npc文件列表{npcFileList}')

for npcFile in npcFileList:
    all_npc_list = load_npc_list_from_json(npcFile)
logger.debug(f'取到npc数量:{len(all_npc_list)}')






# ======================== 小工具 ===========================
def add_context(context):
    """
    为了方便，同时添加上下文到全局和事件上下文
    :param context: '玩家：{player_input}' 字符串
    """
    global_context.add_context(context)
    event_context.add_context(context)

# =================== 测试 =====================
# 假的上下文
history = [
    '玩家：走到艾琳面前，询问：请问你是冒险者么？',
    '艾琳：您好，我是冒险者工会的前台，我没有冒险者身份，请问您有什么需要？',
    '玩家：我想了解一下，如果想成为冒险者，需要经过什么流程？',
    '艾琳：需要先在公会登记，然后完成基础测试，通过后才能获得冒险者徽章。'
]
for i in history:
    add_context(i)



#====================================== 主流程 ==============================================
while True:
    # --------------------- 玩家输入 ---------------------
    player_input = input('>')
    logger.debug(player_input)
    if player_input == 'exit':
        break
    add_context(f'玩家：{player_input}')

    # -------------------- 判断是否为新事件 -----------------
    is_new_event = event_judge.judge(event_context)
    logger.debug(f'事件处理结果：{str(is_new_event)}')

    if is_new_event['is_new_event']:
        event_context.new_context()

    # -------------------- 将事件，和npc传入感知层，处理npc所得事情 -----------------




















