"""
故事3 主流程
流水线：N1(输入) → N2(事件) → N3(感知) → N4(客观更新) → N5(主观响应)
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from common.log import get_logger
from common import log as log_cfg
from common.small_tool import *
from common.base_cnf import *
from common.game_controller import GameController

#----------------- 层级处理 -------------------
from common.N2_contextRetrieval import N2_CE_Process
from common.N3_informationFiltering import N3_Info_Filtering
from common.N4_update import N4_Update
from common.N5_response import N5_Response


# ===================== 初始化 =====================
log_cfg.setup(session_id="test001")
logger = get_logger("Main")

# 游戏控制器（状态管理、存档、命令处理）
gc = GameController(session_id="test001")
gc.init_world()
gc.init_all_npcs()
gc.init_player()

# 流水线各层处理器
event_judge = N2_CE_Process()       # N2 事件层
info_filter = N3_Info_Filtering()    # N3 感知层
n4_update = N4_Update()              # N4 更新层（客观）
n5_response = N5_Response()          # N5 响应层（主观）

player_name = gc.player.player_dict['玩家名称name']


# ===================== 主循环 =====================
while True:
    # ============ N1: 玩家输入 ============
    raw_input = input('>')

    # 命令处理（存档/读档等，非流水线交互）
    cmd_result = gc.handle_command(raw_input)
    if cmd_result == 'break':
        break
    if cmd_result == 'continue':
        continue

    # 移动标记解析
    player_input = resolve_move_markers(raw_input, gc.b1WorldLayer)
    if player_input is None:
        continue

    logger.info(f'{player_name}输入：{player_input}')
    gc.add_context_all(f'{player_name}（玩家）：{player_input}')

    # ============ N2: 事件层 ============
    event_back = event_judge.judge(gc.event_context)
    logger.info(f'事件处理结果：{event_back}')

    # 新事件 → 只清空上下文（话题变更），NPC保留由后续add_npc/del_npc管理
    if event_back['is_new_event']:
        gc.event_context.new_context()
        gc.event_context.add_context(f'{player_name}（玩家）：{player_input}')

    # 增删事件NPC
    for npc_name in (event_back['add_npc'] or []):
        gc.event_npc.add(gc.all_npc_list.get_npc(npc_name))
    for npc_name in (event_back['del_npc'] or []):
        gc.event_npc.delete(gc.all_npc_list.get_npc(npc_name))
    # 增删潜在NPC
    for npc_name in (event_back['add_related_npc'] or []):
        gc.related_npc.add(gc.all_npc_list.get_npc(npc_name))
    for npc_name in (event_back['del_related_npc'] or []):
        gc.related_npc.delete(gc.all_npc_list.get_npc(npc_name))

    logger.info(f'事件层后：event_npc={len(gc.event_npc)} related_npc={len(gc.related_npc)}')

    # ============ N3: 感知层 ============
    dict_replay = info_filter.judge_for_npc(gc.event_context, gc.event_npc, gc.related_npc, gc.player)
    logger.info(f'感知层结果：{dict_replay}')

    # ============ N4+N5: 并行处理所有NPC ============
    event_perceptions = dict_replay.get('事件涉及的npc', {})
    related_perceptions = dict_replay.get('事件涉及/潜在npc', {})

    # 收集所有任务：(npc_name, npc_instance, should_print_response)
    tasks = []

    for npc_name in event_perceptions:
        npc = gc.event_npc.get_npc(npc_name)
        if npc is None:
            logger.warn(f'事件NPC [{npc_name}] 未找到')
            continue
        tasks.append((npc_name, npc, True))

    for npc_name in related_perceptions:
        npc = gc.related_npc.get_npc(npc_name) or gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            logger.warn(f'潜在NPC [{npc_name}] 未找到')
            continue
        tasks.append((npc_name, npc, False))

    if tasks:
        # 并行执行，max_workers 上限 6，不同NPC互不依赖
        with ThreadPoolExecutor(max_workers=min(len(tasks), 6)) as pool:
            futures = {}
            for npc_name, npc, should_print in tasks:
                fut = pool.submit(
                    gc.process_npc_pipeline,
                    npc_name, npc, should_print,
                    n4_update, n5_response,
                    player_input
                )
                futures[fut] = npc_name

            for future in as_completed(futures):
                npc_name = futures[future]
                try:
                    npc_final, response, should_print = future.result()
                    gc.sync_npc_to_lists(npc_final)
                    if should_print:
                        print(f'\n{response}')
                except Exception as e:
                    logger.error(f'NPC [{npc_name}] 处理异常：{e}')

    # --- 地段传播 ---
    ground_msg = dict_replay.get('地段传播', {})
    if ground_msg and ground_msg.get('msg'):
        logger.info(f'地段传播：{ground_msg["msg"]}')
        gc.add_context_all(f'（地段传播）：{ground_msg["msg"]}')
