
import os

from common.N3_informationFiltering import N3_Info_Filtering
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
from Schema.B3_npclist_sch import B3NPCListLayer
from Schema.B4_player_sch import B4PlayerLayer
from Schema.N1_playerInput_sch import N1PlayerInput
from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from Schema.Context_sch import ContextHistory

# 初始化日志系统（全局唯一文件句柄，所有模块共享）
log_cfg.setup(session_id="test001")
logger = get_logger("N2_Test")



#=================== 准备数据 ========================
# -------------准备对象-----------
# 上下文
global_context = ContextHistory()
event_context = ContextHistory()

# 事件层处理
event_judge = N2_CE_Process()
event_npc = B3NPCListLayer()# 事件回应npc
related_npc = B3NPCListLayer()# 事件涉及/潜在npc

# 感知层处理
info_filter = N3_Info_Filtering()



# 世界层
b1WorldLayer = B1WorldLayer(load_json_to_dict(os.path.join(BASE_PATH, WORLD_DATA_FILE))[WORLD_DATA_SELECT])
logger.debug(f'世界数据：{b1WorldLayer.dict}')

# NPC层
npc_source_path = os.path.join(BASE_PATH, NPC_DATA_ROOT_PATH)
npcFileList = walk_category_files(npc_source_path)
logger.debug(f'读取的npc文件列表{npcFileList}')

all_npc_list = B3NPCListLayer()
for npcFile in npcFileList:
    file_npcs = load_npc_list_from_json(npcFile)
    for npc in file_npcs:
        all_npc_list.add(npc)
logger.debug(f'取到npc数量:{len(all_npc_list)}')






# ======================== 小工具 ===========================
def add_context_all(context):
    """
    为了方便，同时添加上下文到全局和事件上下文
    :param context: '玩家：{player_input}' 字符串
    """
    global_context.add_context(context)
    event_context.add_context(context)

# =================== 测试 =====================
# # 假的上下文
# history = [
#     '玩家：走到艾琳面前，询问：请问你是冒险者么？',
#     '艾琳：您好，我是冒险者工会的前台，我没有冒险者身份，请问您有什么需要？',
#     '玩家：我想了解一下，如果想成为冒险者，需要经过什么流程？',
#     '艾琳：需要先在公会登记，然后完成基础测试，通过后才能获得冒险者徽章。'
# ]
# for i in history:
#     add_context_all(i)

# 假的玩家当前位置
current_state= load_json_to_dict(os.path.join(BASE_PATH, PLAYER_DATA_FILE))
player = B4PlayerLayer(current_state)

# 假的当前地点npc列表



#====================================== 主流程 ==============================================
while True:
    # --------------------- 玩家输入 ---------------------
    raw_input = input('>')
    logger.info(f'玩家输入：{raw_input}')
    if raw_input == 'exit':
        break

    # --- 移动标记解析 ---
    player_input = resolve_move_markers(raw_input, b1WorldLayer)
    if player_input is None:
        continue  # 用户取消了移动导航

    logger.info(f'{player.player_dict['玩家名称name']}输入前，事件npc数量：{len(event_npc)}，事件上下文长度：{len(event_context)}')
    logger.info(f'{player.player_dict['玩家名称name']}输入：{player_input}')
    add_context_all(f'{player.player_dict['玩家名称name']}（玩家）：{player_input}')

    # -------------------- 事件层工作 --------------------
    event_back = event_judge.judge(event_context)
    logger.info(f'事件处理结果：{event_back}')
    # 创建新事件
    if event_back['is_new_event']:
        event_npc.new_list()
        related_npc.new_list() # 新事件，清空npc列表，若有增删npc下部正常处理
        event_context.new_context() # 新事件，清空事件上下文

        event_context.add_context(f'{player.player_dict['玩家名称name']}（玩家）：{player_input}')


    # 处理事件npc列表
    if event_back['add_npc'] is not None:
        for npc_name in event_back['add_npc']:
            npc = all_npc_list.get_npc(npc_name)
            event_npc.add(npc)
    if event_back['del_npc'] is not None:
        for npc_name in event_back['del_npc']:
            npc = all_npc_list.get_npc(npc_name)
            event_npc.delete(npc)
    # 处理related_npc有关npc列表
    if event_back['add_related_npc'] is not None:
        for npc_name in event_back['add_related_npc']:
            npc = all_npc_list.get_npc(npc_name)
            related_npc.add(npc)
    if event_back['del_related_npc'] is not None:
        for npc_name in event_back['del_related_npc']:
            npc = all_npc_list.get_npc(npc_name)
            related_npc.delete(npc)

    logger.info(f'事件层处理后,事件回应npc数量：{len(event_npc)}，事件涉及/潜在npc数量{len(related_npc)}，事件上下文长度：{len(event_context)}')


    # ----------------------- 感知层 ----------------------------
    # ----------- 将事件，和npc传入感知层，处理npc所得事情 ------------
    json_replay = info_filter.judge_for_npc(event_context, event_npc, related_npc, player)
    logger.info(f'感知层处理结果：{json_replay}')



















