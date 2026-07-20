"""
故事3 主流程
流水线：N1(输入) → N2(事件) → N3(感知) → [N4→N5→N6 并行] → [N7(传播) + N8(时间)] 并行 → N9(导演) → 回到N1
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
from common.N9_director import N9_Director


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
n9_director = N9_Director()       # N9 导演层（LLM驱动世界暗线）

player_name = gc.player.player_dict['玩家名称name']
last_snapshot = None  # 上轮执行前的快照，用于 !! 回滚


# ===================== 主循环 =====================
while True:
    # ============ N1: 玩家输入 ============
    raw_input = input('>')

    # 空输入跳过
    if not raw_input.strip():
        continue

    # 撤回：恢复至上轮输入前的完整快照（上下文 + NPC状态 + 玩家状态 + N9状态）
    if raw_input.strip() == '!!':
        if last_snapshot is not None:
            gc.restore(last_snapshot)
            n9_director.restore(last_n9_state)
            print('↩ 已撤回上一条（状态已全部回滚）')
        else:
            print('↩ 没有可撤回的内容')
        continue

    # 命令处理（存档/读档等，非流水线交互）
    gc._n9_state = n9_director.to_dict()
    cmd_result = gc.handle_command(raw_input)
    if cmd_result == 'break':
        break
    if cmd_result == 'continue':
        # load 命令会恢复存档中的 N9 状态，存入 gc._n9_state
        if getattr(gc, '_n9_state', None):
            n9_director.restore(gc._n9_state)
        continue

    # 移动标记解析（含移动方式选择）
    player_input, move_info = resolve_move_markers(raw_input, gc.b1WorldLayer, gc.player)
    if player_input is None:
        continue

    # ★ 流水线执行前保存全量快照，供 !! 撤回
    last_snapshot = gc.snapshot()
    last_n9_state = n9_director.to_dict()  # N9 导演层状态也一起保存

    logger.info(f'{player_name}输入：{player_input}')
    gc.add_context_all(f'{player_name}（玩家）：{player_input}')

    # ============ N2: 事件层 ============
    current_event_npc_names = [n.name for n in gc.event_npc.npc_list]
    all_npc_names = [n.name for n in gc.all_npc_list.npc_list]
    player_loc = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')

    # 构建玩家附近NPC列表（同区域内有谁，供N2判断是否加入事件）
    player_path = gc.b1WorldLayer.parse_path(player_loc)
    nearby_npcs_info = []
    for npc in gc.all_npc_list:
        npc_loc = npc.current_state.get('当前所处位置', '')
        if not npc_loc:
            continue
        dist = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
        if dist <= 3:  # 同一城市内
            nearby_npcs_info.append({
                '名字': npc.name,
                '位置': npc_loc,
                '距离': f'{dist}段',
            })

    event_back = event_judge.judge(
        gc.event_context, current_event_npc_names, all_npc_names,
        gc.director_context, player_loc, nearby_npcs_info
    )
    logger.info(f'事件处理结果：{event_back}')

    # 新事件 → 清空上下文+NPC列表（重置事件参与方）
    if event_back['is_new_event']:
        gc.event_context.new_context()
        gc.event_context.add_context(f'{player_name}（玩家）：{player_input}')
        gc.related_npc.new_list()
        logger.info('新事件：已清空 related_npc')

    # 增删事件NPC（代码层位置校验）
    for npc_name in (event_back['add_npc'] or []):
        npc = gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            continue
        npc_loc = npc.current_state.get('当前所处位置', '')
        dist = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
        # 直接互动：必须在同一城市级区域内（段数≤3，即同一城市内）
        if dist <= 3:
            gc.event_npc.add(npc)
        else:
            logger.warn(f'N2 add_npc [{npc_name}] 位置过远（距离={dist}段，玩家在"{player_loc}"，NPC在"{npc_loc}"），已拦截')
    for npc_name in (event_back['del_npc'] or []):
        gc.event_npc.delete(gc.all_npc_list.get_npc(npc_name))
    # 增删潜在NPC（位置校验更宽松：段数≤8，即同一国家内）
    for npc_name in (event_back['add_related_npc'] or []):
        npc = gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            continue
        npc_loc = npc.current_state.get('当前所处位置', '')
        dist = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
        if dist <= 8:
            gc.related_npc.add(npc)
        else:
            logger.warn(f'N2 add_related_npc [{npc_name}] 位置过远（距离={dist}段，玩家在"{player_loc}"，NPC在"{npc_loc}"），已拦截')
    for npc_name in (event_back['del_related_npc'] or []):
        gc.related_npc.delete(gc.all_npc_list.get_npc(npc_name))

    logger.info(f'事件层后：event_npc={len(gc.event_npc)} related_npc={len(gc.related_npc)}')

    # ============ N3: 感知层 ============
    dict_replay = info_filter.judge_for_npc(gc.event_context, gc.event_npc, gc.related_npc, gc.player, gc.director_context)
    logger.info(f'感知层结果：{dict_replay}')

    # ============ N4 + N5 + N6: 并行处理所有NPC ============
    event_perceptions = dict_replay.get('事件涉及的npc', {}) or {}
    related_perceptions = dict_replay.get('事件涉及/潜在npc', {}) or {}

    # 去重：同一个NPC如果在event中已出现，从related中移除
    for npc_name in list(related_perceptions.keys()):
        if npc_name in event_perceptions:
            del related_perceptions[npc_name]
            logger.info(f'N3去重：{npc_name} 已从潜在npc中移除（重复出现在事件npc中）')

    # 收集所有任务：(npc_name, npc_instance, should_print, perception_msg)
    tasks = []
    npc_responses = []       # NPC回应列表，供N8时间层和N9导演层使用

    for npc_name, perception_msg in event_perceptions.items():
        if not perception_msg or not perception_msg.strip():
            logger.info(f'N3跳空：[{npc_name}] 感知消息为空（可能不在同一区域），跳过N4-N6')
            continue
        # N3 可能把 NPC 分到"事件涉及的npc"，但实例在 related_npc 或 all_npc_list 中
        npc = gc.event_npc.get_npc(npc_name) or gc.related_npc.get_npc(npc_name) or gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            logger.warn(f'事件NPC [{npc_name}] 未找到')
            continue
        tasks.append((npc_name, npc, True, perception_msg))

    for npc_name, perception_msg in related_perceptions.items():
        if not perception_msg or not perception_msg.strip():
            logger.info(f'N3跳空：[{npc_name}] 感知消息为空（可能不在同一区域），跳过N4-N6')
            continue
        npc = gc.related_npc.get_npc(npc_name) or gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            logger.warn(f'潜在NPC [{npc_name}] 未找到')
            continue
        # 被玩家直接指名道姓（"玩家对你说"→ 直接目标）→ 必须打印回应
        is_direct_target = perception_msg.startswith('玩家对你说')
        tasks.append((npc_name, npc, is_direct_target, perception_msg))

    all_narrations = []       # N6产出的旁白，合并去重后打印
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

            merged_player_delta = {}

            for future in as_completed(futures):
                npc_name = futures[future]
                try:
                    npc_final, response, should_print, player_delta, narration = future.result()
                    gc.sync_npc_to_lists(npc_final)
                    npc_responses.append({'name': npc_name, 'response': response})
                    if should_print:
                        print(f'\n{response}')
                        if response:
                            gc.add_global_context(response)
                    if player_delta:
                        merge_delta(merged_player_delta, player_delta)
                    if narration:
                        for line in narration:
                            if line and line not in all_narrations:
                                all_narrations.append(line)
                except Exception as e:
                    logger.error(f'NPC [{npc_name}] 处理异常：{e}')

        if merged_player_delta:
            gc.player.player_dict = apply_player_delta(gc.player.player_dict, merged_player_delta)
            logger.info(f'玩家状态已更新：{merged_player_delta}')

        if all_narrations:
            for line in all_narrations:
                print(f'\033[3m  {line}\033[0m')
                gc.add_global_context(f'旁白：{line}')
            logger.info(f'N6 旁白：{all_narrations}')

    # 注：无NPC参与时（tasks为空），玩家自主更新在N8之后执行（见下方295行附近）

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
            player_input=player_input,
            event_context=gc.event_context.context,
            npc_responses=npc_responses,
            move_info=move_info,
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
        logger.info(f'移动：{move_info["from"]} → {move_info["to"]}（{n8_minutes:.1f}分钟）')
        player_loc = move_info['to']
        gc.player.player_dict['当前状态CurrentState']['当前所处位置'] = player_loc
        # 移动单独写入上下文
        move_context = f'{player_name}从{move_info["from"]}前往{move_info["to"]}（耗时{n8_minutes:.1f}分钟）'
        gc.add_context_all(move_context)

    # 玩家自主行动处理（无NPC参与时，由N6玩家更新层处理状态变更）
    if not tasks and n8_minutes >= 1:
        player_delta, player_narrations = n6_update.process_player_action(
            player_inst=gc.player,
            player_input=player_input,
            elapsed_minutes=int(n8_minutes),
            game_time_str=gc.game_clock.now_str(),
        )
        if player_delta:
            gc.player.player_dict = apply_player_delta(gc.player.player_dict, player_delta)
            logger.info(f'玩家自主行动状态变更：{player_delta}')
        if player_narrations:
            for line in player_narrations:
                if line and line not in all_narrations:
                    all_narrations.append(line)
        if all_narrations:
            for line in all_narrations:
                print(f'\033[3m  {line}\033[0m')
                gc.add_global_context(f'旁白：{line}')
            logger.info(f'玩家自主行动旁白：{all_narrations}')

    # ============ N9: 导演层（LLM驱动世界暗线，自行落地副作用） ============
    n9_summary = n9_director.process(
        game_controller=gc,
        event_context=gc.event_context.context,
        npc_responses=npc_responses,
        n7_result=n7_result,
    )

    print(f'\n⏰ [游戏时间 {gc.game_clock.now_str()}（+{n8_minutes:.0f}分）]')
