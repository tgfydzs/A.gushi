"""
故事3 主流程
流水线：N1(输入) → N2(事件) → N3(感知) → [N4→N5→N6 并行] → [N7(传播) + N8(时间)] 并行 → 回到N1
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
from common.N4_reasoning import N4_Reasoning
from common.N5_response import N5_Response
from common.N6_update import N6_Update
from common.N7_propagation import N7_Propagation
from common.N8_time import N8_Time


# ===================== 初始化 =====================
log_cfg.setup(session_id="test001")
logger = get_logger("Main")

# 游戏控制器（状态管理、存档、命令处理）
gc = GameController(session_id="test001")
gc.init_world()
gc.init_clock()        # 从世界规则读起始时间，创建时钟和上下文
gc.init_all_npcs()
gc.init_player()

# 流水线各层处理器
event_judge = N2_CE_Process()       # N2 事件层
info_filter = N3_Info_Filtering()    # N3 感知层
n4_reasoning = N4_Reasoning()      # N4 推理层
n5_response = N5_Response()        # N5 回应层（主观）
n6_update = N6_Update()            # N6 更新层（收尾，客观）
n7_propagation = N7_Propagation()   # N7 传播层（纯规则，零耗时）
n8_time = N8_Time()               # N8 时间层（规则+LLM混合）

player_name = gc.player.player_dict['玩家名称name']
last_snapshot = None  # 上轮执行前的快照，用于 !! 回滚


# ===================== 主循环 =====================
while True:
    # ============ N1: 玩家输入 ============
    raw_input = input('>')

    # 空输入跳过
    if not raw_input.strip():
        continue

    # 撤回：恢复至上轮输入前的完整快照（上下文 + NPC状态 + 玩家状态）
    if raw_input.strip() == '!!':
        if last_snapshot is not None:
            gc.restore(last_snapshot)
            print('↩ 已撤回上一条（状态已全部回滚）')
        else:
            print('↩ 没有可撤回的内容')
        continue

    # 命令处理（存档/读档等，非流水线交互）
    cmd_result = gc.handle_command(raw_input)
    if cmd_result == 'break':
        break
    if cmd_result == 'continue':
        continue

    # 移动标记解析（含移动方式选择）
    player_input, move_info = resolve_move_markers(raw_input, gc.b1WorldLayer, gc.player)
    if player_input is None:
        continue

    # ★ 流水线执行前保存全量快照，供 !! 撤回
    last_snapshot = gc.snapshot()

    logger.info(f'{player_name}输入：{player_input}')
    gc.add_context_all(f'{player_name}（玩家）：{player_input}')

    # ============ N2: 事件层 ============
    current_event_npc_names = [n.name for n in gc.event_npc.npc_list]
    all_npc_names = [n.name for n in gc.all_npc_list.npc_list]
    event_back = event_judge.judge(gc.event_context, current_event_npc_names, all_npc_names)
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

    # ============ N4 + N5 + N6: 并行处理所有NPC ============
    event_perceptions = dict_replay.get('事件涉及的npc', {})
    related_perceptions = dict_replay.get('事件涉及/潜在npc', {})

    # 收集所有任务：(npc_name, npc_instance, should_print, perception_msg)
    tasks = []

    for npc_name, perception_msg in event_perceptions.items():
        npc = gc.event_npc.get_npc(npc_name)
        if npc is None:
            logger.warn(f'事件NPC [{npc_name}] 未找到')
            continue
        tasks.append((npc_name, npc, True, perception_msg))

    for npc_name, perception_msg in related_perceptions.items():
        npc = gc.related_npc.get_npc(npc_name) or gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            logger.warn(f'潜在NPC [{npc_name}] 未找到')
            continue
        tasks.append((npc_name, npc, False, perception_msg))

    if tasks:
        # 并行执行，max_workers 上限 6，不同NPC互不依赖
        with ThreadPoolExecutor(max_workers=min(len(tasks), 6)) as pool:
            futures = {}
            for npc_name, npc, should_print, perception_msg in tasks:
                fut = pool.submit(
                    gc.process_npc_pipeline,
                    npc_name, npc, should_print,
                    n4_reasoning, n5_response, n6_update,
                    perception_msg
                )
                futures[fut] = npc_name

            merged_player_delta = {}  # 所有NPC产出的玩家增量合并到这里
            npc_responses = []       # NPC回应列表，供N8时间层使用

            for future in as_completed(futures):
                npc_name = futures[future]
                try:
                    npc_final, response, should_print, player_delta = future.result()
                    gc.sync_npc_to_lists(npc_final)
                    npc_responses.append({'name': npc_name, 'response': response})
                    if should_print:
                        print(f'\n{response}')
                        if response:
                            gc.add_global_context(response)  # NPC回应只入全局，不入事件
                    # 合并玩家增量
                    if player_delta:
                        merge_delta(merged_player_delta, player_delta)
                except Exception as e:
                    logger.error(f'NPC [{npc_name}] 处理异常：{e}')

        # 将合并后的玩家增量写回玩家实例
        if merged_player_delta:
            gc.player.player_dict = apply_player_delta(gc.player.player_dict, merged_player_delta)
            logger.info(f'玩家状态已更新：{merged_player_delta}')

    # ============ N7 + N8: 传播层和时间层并行 ============
    ground_msg = dict_replay.get('地段传播', {})
    experienced_names = [name for name, _, _, _ in tasks]
    player_loc = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')

    def _run_n7():
        """传播层（纯规则计算，不调用LLM）"""
        if ground_msg and ground_msg.get('msg'):
            return n7_propagation.propagate(
                ground_msg, gc.b1WorldLayer, gc.all_npc_list,
                experienced_names, player_loc
            )
        return {}

    def _run_n8():
        """时间层（常规移动→规则；异能移动→LLM；无移动→LLM对话估算）"""
        if move_info and move_info.get('to'):
            gc.player.player_dict['当前状态CurrentState']['当前所处位置'] = move_info['to']
        return n8_time.process(
            game_clock=gc.game_clock,
            world_layer=gc.b1WorldLayer,
            player_inst=gc.player,
            move_info=move_info,
            event_context=gc.event_context.context,
            npc_responses=npc_responses,
            player_input=player_input,
        )

    # N7 和 N8 并行启动（N7 零耗时，N8 可能有LLM调用）
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_n7 = pool.submit(_run_n7)
        fut_n8 = pool.submit(_run_n8)

        n7_result = fut_n7.result()
        n8_minutes = fut_n8.result()

    # N7 结果日志
    if n7_result:
        logger.info(f'N7 传播层：影响 {len(n7_result)} 个NPC → {list(n7_result.keys())}')
        for npc_name, rumors in n7_result.items():
            logger.info(f'  [{npc_name}] ← {rumors}')

    # N8 时间推进后的位置更新（移动→规则走这里）
    if move_info:
        logger.info(f'移动：{move_info["from"]} → {move_info["to"]}（{move_info["method"]}，{n8_minutes:.1f}分钟）')
        player_loc = move_info['to']
        gc.player.player_dict['当前状态CurrentState']['当前所处位置'] = player_loc
        # 移动单独写入上下文
        move_context = f'{player_name}从{move_info["from"]}前往{move_info["to"]}（{move_info["method"]}，耗时{n8_minutes:.1f}分钟）'
        gc.add_context_all(move_context)

    print(f'\n⏰ [游戏时间 {gc.game_clock.now_str()}]+{n8_minutes:.0f}分]')
