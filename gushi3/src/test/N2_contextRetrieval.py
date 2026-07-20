"""
故事3 主流程 v5
流水线：N0→N1→N2→N3(锁定) → [仅锁定NPC走N4N5N6] → [N7+N8并行] → N9a+N9b → 心跳触发 → 回到N0

核心设计：
- N3输出event_npc+related_npc = locked_set，只有这些NPC走N4N5N6
- N9c移至后台心跳，产出推入DeferredQueue，N0时消化
- 后台心跳每3轮或游戏时间≥12小时触发一次
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from common.log import get_logger
from common import log as log_cfg
from common.small_tool import *
from common.base_cnf import *
from common.game_controller import GameController
from common.heartbeat import HeartbeatManager

#----------------- 层级处理 -------------------
from common.N2_contextRetrieval import N2_CE_Process
from common.N3_informationFiltering import N3_Info_Filtering
from common.N4_reasoning import N4_Reasoning
from common.N5_response import N5_Response
from common.N6_update import N6_Update
from common.N7_propagation import N7_Propagation
from common.N8_time import N8_Time
from common.N9_director import N9_Director
from common.debug_tools import snapshot_npcs, diff_npcs, snapshot_scene, DEBUG_MODE


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
n8_time = N8_Time()               # N8 时间层（纯LLM）
n9_director = N9_Director()       # N9a+N9b 导演层（主线程）

# 后台心跳管理器（管理 N9a+N9b+N9c 全部后台运行）
heartbeat = HeartbeatManager(gc.deferred_queue, n9_director)

player_name = gc.player.player_dict['玩家名称name']
last_snapshot = None  # 上轮执行前的快照，用于 !! 回滚
round_count = 0       # 主循环轮次计数


# ===================== 主循环 =====================
while True:
    # ============ N0: 循环顶（消化后台队列 + 场景快照） ============
    # 消费后台 N9a+N9b+N9c 产出
    has_news = gc.consume_deferred(n9_director=n9_director)

    # 展示当前场景快照
    player_loc = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')
    print(f'\n📍 {player_loc}')
    # 附近NPC
    nearby = []
    npcs_here = gc.b1WorldLayer.find_npcs_at_location(gc.b1WorldLayer.parse_path(player_loc), gc.all_npc_list)
    for n in npcs_here:
        nearby.append(n.name)
    if nearby:
        print(f'👥 在场：{"、".join(nearby)}')
    else:
        print(f'👥 （周围无人）')
    print(f'⏰ {gc.game_clock.now_str()}')
    if has_news:
        print('（后台有动态更新，世界在你没注意的地方运转着）')

    # ============ N1: 玩家输入 ============
    raw_input = input('>')

    # 空输入跳过
    if not raw_input.strip():
        continue

    # 撤回：恢复至上轮输入前的完整快照
    if raw_input.strip() == '!!':
        if last_snapshot is not None:
            gc.restore(last_snapshot)
            n9_director.restore(last_n9_state)
            print('↩ 已撤回上一条（状态已全部回滚）')
        else:
            print('↩ 没有可撤回的内容')
        continue

    # 命令处理（存档/读档等，非流水线交互）
    if raw_input.strip() == '/debug':
        from common import debug_tools as dt
        dt.DEBUG_MODE = not dt.DEBUG_MODE
        print(f'🔧 Debug模式：{"🟢 开启" if dt.DEBUG_MODE else "🔴 关闭"}')
        continue

    gc._n9_state = n9_director.to_dict()
    cmd_result = gc.handle_command(raw_input)
    if cmd_result == 'break':
        break
    if cmd_result == 'continue':
        if getattr(gc, '_n9_state', None):
            n9_director.restore(gc._n9_state)
        continue

    # 移动标记解析
    player_input, move_info = resolve_move_markers(raw_input, gc.b1WorldLayer, gc.player)
    if player_input is None:
        continue

    # ★ 流水线执行前保存全量快照，供 !! 撤回
    last_snapshot = gc.snapshot()
    last_n9_state = n9_director.to_dict()

    # Debug：流水线前NPC快照
    snapshot_npcs(round_count, 'before', gc.all_npc_list)
    snapshot_scene(round_count, 'before', gc)

    logger.info(f'{player_name}输入：{player_input}')
    gc.add_context_all(f'{player_name}（玩家）：{player_input}')

    # ============ N2: 事件层 ============
    current_event_npc_names = [n.name for n in gc.event_npc.npc_list]
    all_npc_names = [n.name for n in gc.all_npc_list.npc_list]
    player_loc = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')

    # 构建玩家附近NPC列表（供N2判断是否加入事件）
    player_path = gc.b1WorldLayer.parse_path(player_loc)
    nearby_npcs_info = []
    for npc in gc.all_npc_list:
        npc_loc = npc.current_state.get('当前所处位置', '')
        if not npc_loc:
            continue
        dist = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
        if dist <= 3:
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

    # 新事件判断（LLM返回string "true"/"false"，Python string "false"是truthy！需显式判断）
    is_new = str(event_back.get('is_new_event', 'false')).strip().lower() == 'true'

    if is_new:
        # 防死循环：event_context很短但还有活跃NPC → 强制保留
        ctx_len = len(gc.event_context)
        if ctx_len <= 2 and len(gc.event_npc) > 0:
            logger.warn(f'N2 is_new_event=true 但上下文仅{ctx_len}条且有活跃NPC，强制延续')
            is_new = False
        else:
            gc.event_context.new_context()
            gc.event_context.add_context(f'{player_name}（玩家）：{player_input}')
            gc.related_npc.new_list()
            logger.info(f'新事件：已清空上下文（原{ctx_len}条）')

    # 增删事件NPC（代码层位置校验）
    for npc_name in (event_back['add_npc'] or []):
        npc = gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            continue
        npc_loc = npc.current_state.get('当前所处位置', '')
        dist = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
        if dist <= 3:
            gc.event_npc.add(npc)
        else:
            logger.warn(f'N2 add_npc [{npc_name}] 位置过远（距离={dist}段），已拦截')
    for npc_name in (event_back['del_npc'] or []):
        gc.event_npc.delete(gc.all_npc_list.get_npc(npc_name))
    for npc_name in (event_back['add_related_npc'] or []):
        npc = gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            continue
        npc_loc = npc.current_state.get('当前所处位置', '')
        dist = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
        if dist <= 8:
            gc.related_npc.add(npc)
        else:
            logger.warn(f'N2 add_related_npc [{npc_name}] 位置过远（距离={dist}段），已拦截')
    for npc_name in (event_back['del_related_npc'] or []):
        gc.related_npc.delete(gc.all_npc_list.get_npc(npc_name))

    logger.info(f'事件层后：event_npc={len(gc.event_npc)} related_npc={len(gc.related_npc)}')

    # ============ N3: 感知层 → 产出 locked_set ============
    dict_replay = info_filter.judge_for_npc(
        gc.event_context, gc.event_npc, gc.related_npc,
        gc.player, gc.director_context
    )
    logger.info(f'感知层结果：{dict_replay}')

    # N3 输出 → locked_set（这一轮哪些NPC需要走N4N5N6）
    event_perceptions = dict_replay.get('事件涉及的npc', {}) or {}
    related_perceptions = dict_replay.get('事件涉及/潜在npc', {}) or {}
    locked_set = set(event_perceptions.keys()) | set(related_perceptions.keys())
    logger.info(f'N3锁定：{len(locked_set)} 个NPC进入流水线 → {list(locked_set)[:6]}')

    # ============ N4 + N5 + N6: 仅 locked_set 中的NPC ============
    tasks = []
    npc_responses = []       # NPC回应列表，供N8时间层和N9a+N9b使用
    all_narrations = []

    for npc_name, perception_msg in event_perceptions.items():
        if not perception_msg or not perception_msg.strip():
            logger.info(f'N3跳空：[{npc_name}] 感知消息为空，跳过N4-N6')
            continue
        npc = (gc.event_npc.get_npc(npc_name)
               or gc.related_npc.get_npc(npc_name)
               or gc.all_npc_list.get_npc(npc_name))
        if npc is None:
            logger.warn(f'事件NPC [{npc_name}] 未找到')
            continue
        tasks.append((npc_name, npc, True, perception_msg))

    for npc_name, perception_msg in related_perceptions.items():
        if not perception_msg or not perception_msg.strip():
            logger.info(f'N3跳空：[{npc_name}] 感知消息为空，跳过N4-N6')
            continue
        npc = gc.related_npc.get_npc(npc_name) or gc.all_npc_list.get_npc(npc_name)
        if npc is None:
            logger.warn(f'潜在NPC [{npc_name}] 未找到')
            continue
        is_direct_target = perception_msg.startswith('玩家对你说')
        tasks.append((npc_name, npc, is_direct_target, perception_msg))

    if tasks:
        logger.info(f'N4-N6流水线：{len(tasks)} 个NPC（锁定中）')
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

        # Debug：N6后NPC快照与变更对比
        snapshot_npcs(round_count, 'after', gc.all_npc_list)
        diff_npcs(round_count, gc.all_npc_list)
    else:
        logger.info('N4-N6流水线：本轮无锁定NPC，跳过')

    # ============ N7 + N8: 传播层和时间层并行 ============
    ground_msg = dict_replay.get('地段传播', {})
    experienced_names = [name for name, _, _, _ in tasks]
    player_loc = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')

    def _run_n7():
        if ground_msg and ground_msg.get('msg'):
            return n7_propagation.propagate(
                ground_msg, gc.b1WorldLayer, gc.all_npc_list,
                experienced_names, player_loc
            )
        return {}

    def _run_n8():
        if move_info and move_info.get('to'):
            gc.player.player_dict['当前状态CurrentState']['当前所处位置'] = move_info['to']
        return n8_time.process(
            game_clock=gc.game_clock,
            player_input=player_input,
            event_context=gc.event_context.context,
            npc_responses=npc_responses,
            move_info=move_info,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_n7 = pool.submit(_run_n7)
        fut_n8 = pool.submit(_run_n8)
        n7_result = fut_n7.result()
        n8_minutes = fut_n8.result()

    # N7 结果日志
    if n7_result:
        logger.info(f'N7 传播层：影响 {len(n7_result)} 个NPC')
        for npc_name, rumors in n7_result.items():
            logger.info(f'  [{npc_name}] ← {rumors}')

    # N8 时间推进后的位置更新
    if move_info:
        logger.info(f'移动：{move_info["from"]} → {move_info["to"]}（{n8_minutes:.1f}分钟）')
        player_loc = move_info['to']
        gc.player.player_dict['当前状态CurrentState']['当前所处位置'] = player_loc
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

    # ============ 后台心跳触发（N9a+N9b+N9c 全部在后台运行） ============
    round_count += 1
    heartbeat.try_trigger(
        current_round=round_count,
        game_clock=gc.game_clock,
        world_layer=gc.b1WorldLayer,
        all_npc_list=gc.all_npc_list,
        director_context=gc.director_context.context,
        world_state_list=gc.b1WorldLayer.dict.get('世界状态列表', []),
        event_context=gc.event_context.context,
        npc_responses=npc_responses,
        player_input=player_input,
        player_inst=gc.player,
        n7_result=n7_result,
    )

    print(f'\n⏰ [游戏时间 {gc.game_clock.now_str()}（+{n8_minutes:.0f}分）]')
