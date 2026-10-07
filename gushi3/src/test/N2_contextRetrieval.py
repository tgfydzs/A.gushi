"""
故事3 主流程 v6

调用链（每轮）：
  N0_消费后台产出  →  N1_读输入+命令  →  N2_事件判断
  →  N3_感知分发  →  [N45→N6]_并行perNPC  →  N7_传播(纯规则)
  →  玩家自主行动(无NPC时)  →  N8_耗时聚合(纯规则)  →  心跳触发

关键路径 LLM 跳数：N2 → N3 → N45 → N6
  （原为 N2→N3→N4→N5→N6→N8 共6跳；N4+N5 合并为 N45，N8 的估算并入 N6 后降级为纯规则）
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from common.log import get_logger
from common import log as log_cfg
from common.small_tool import *
from common.base_cnf import *
from common.game_controller import GameController
from common.heartbeat import HeartbeatManager

from common.N2_contextRetrieval import N2_CE_Process
from common.N3_informationFiltering import N3_Info_Filtering
from common.N45_reason_response import N45_ReasonResponse
from common.N6_update import N6_Update
from common.N7_propagation import N7_Propagation
from common.N8_time import N8_Time
from common.N9_director import N9_Director
from common.debug_tools import snapshot_npcs, diff_npcs, snapshot_scene, DEBUG_MODE


# ═══════════════════ 初始化 ═══════════════════

log_cfg.setup(session_id="test001")
logger = get_logger("Main")

gc = GameController(session_id="test001")
gc.init_world()          # B1_worldState
gc.init_clock()          # GameClock + 三种上下文
gc.init_all_npcs()       # B3_npc
gc.init_player()         # B4_player（路径从世界配置读）

event_judge  = N2_CE_Process()
info_filter  = N3_Info_Filtering()
n45_layer    = N45_ReasonResponse()
n6_update    = N6_Update()
n7_prop      = N7_Propagation()
n8_time      = N8_Time()
n9_director  = N9_Director()

heartbeat    = HeartbeatManager(gc.deferred_queue, n9_director)

player_name  = gc.player.player_dict['玩家名称name']
snapshot     = gc.snapshot      # 别名
last_snap    = None
n9_last      = None
round_no     = 0


# ═══════════════════ 主循环 = 阶段函数 =═══════════════════

def N0_消费后台产出():
    """消化 DeferredQueue 中的 N9a/N9b/N9c 产出 → 打印一行简讯"""
    gc.consume_deferred(n9_director=n9_director)

def N0_显示场景():
    """打印当前位置 + 在场NPC + 游戏时间"""
    loc = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')
    print(f'\n📍 {loc}')
    path = gc.b1WorldLayer.parse_path(loc)
    npcs = gc.b1WorldLayer.find_npcs_at_location(path, gc.all_npc_list)
    if npcs:
        print(f'👥 在场：{"、".join(n.name for n in npcs)}')
    else:
        print(f'👥 （周围无人）')
    print(f'⏰ {gc.game_clock.now_str()}')

def N1_读输入():
    """返回 (player_input, move_info)；None/continue/break 返回对应标记"""
    raw = input('>')

    if not raw.strip():
        return None, None

    if raw.strip() == '!!':
        if last_snap is not None:
            gc.restore(last_snap)
            n9_director.restore(n9_last)
            print('↩ 已撤回上一条')
        else:
            print('↩ 没有可撤回的内容')
        return 'continue', None

    if raw.strip() == '/debug':
        import common.debug_tools as dt
        dt.DEBUG_MODE = not dt.DEBUG_MODE
        print(f'🔧 Debug：{"🟢" if dt.DEBUG_MODE else "🔴"}')
        return 'continue', None

    gc._n9_state = n9_director.to_dict()
    cmd_r = gc.handle_command(raw)
    if cmd_r == 'break':
        return 'break', None
    if cmd_r == 'continue':
        if getattr(gc, '_n9_state', None):
            n9_director.restore(gc._n9_state)
        return 'continue', None

    return resolve_move_markers(raw, gc.b1WorldLayer, gc.player)

def N2_事件判断(player_input: str):
    """
    N2 → LLM 判断 is_new_event + add/del event_npc + add/del related_npc。
    代码层：距离校验 + 新事件防死循环。
    """
    current_event_npc_names = [n.name for n in gc.event_npc.npc_list]
    all_npc_names = [n.name for n in gc.all_npc_list.npc_list]
    player_loc = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')

    near_dist = gc.b1WorldLayer.get_config('NPC_附近距离_段', 3)
    nearby = []
    for npc in gc.all_npc_list:
        npc_loc = npc.current_state.get('当前所处位置', '')
        if not npc_loc: continue
        d = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
        if d <= near_dist:
            nearby.append({'名字': npc.name, '位置': npc_loc, '距离': f'{d}段'})

    result = event_judge.judge(
        gc.event_context, current_event_npc_names, all_npc_names,
        gc.director_context, player_loc, nearby,
    )
    logger.info(f'N2 结果：{result}')

    is_new = bool(result.get('is_new_event', False))
    if is_new:
        ctx_len = len(gc.event_context)
        min_ctx = gc.b1WorldLayer.get_config('事件_最小上下文长度', 2)
        has_active = len(gc.event_npc) > 0 or len(gc.related_npc) > 0
        if ctx_len <= min_ctx and has_active:
            logger.warn(f'N2 is_new_event=true 但上下文仅{ctx_len}条且有活跃NPC，强制延续')
            is_new = False
        else:
            gc.event_context.new_context()
            gc.event_context.add_context(f'{player_name}（玩家）：{player_input}')
            gc.related_npc.new_list()

    # ---- 应用 NPC 增删 ----
    for nm in (result.get('add_npc') or []):
        npc = gc.all_npc_list.get_npc(nm)
        if npc: gc.event_npc.add(npc)
    for nm in (result.get('del_npc') or []):
        gc.event_npc.delete(gc.all_npc_list.get_npc(nm))
    for nm in (result.get('add_related_npc') or []):
        npc = gc.all_npc_list.get_npc(nm)
        if npc: gc.related_npc.add(npc)
    for nm in (result.get('del_related_npc') or []):
        gc.related_npc.delete(gc.all_npc_list.get_npc(nm))

    logger.info(f'N2 后：event_npc={len(gc.event_npc)} related_npc={len(gc.related_npc)}')

    return result

def N3_感知分发() -> dict:
    """N3 → LLM 判断每个NPC收到什么消息 + 地段传播数据。返回 N3 完整输出字典。"""
    result = info_filter.judge_for_npc(
        gc.event_context, gc.event_npc, gc.related_npc,
        gc.player, gc.director_context,
    )
    logger.info(f'N3 结果：event={len(result.get("事件涉及的npc",{}) or {})} '
                f'related={len(result.get("事件涉及/潜在npc",{}) or {})}')
    return result

def N45N6_并行(n3_result: dict):
    """
    仅 locked_set 中的NPC走 N45→N6 并行流水线。
    N45: 推理待解释事件 + 生成回应与内心想法（原N4+N5合并）
    N6:  落实增量 + 旁白 + 本轮耗时

    返回: (npc_responses, all_narrations, npc_minutes)
    """
    event_p = n3_result.get('事件涉及的npc', {}) or {}
    related_p = n3_result.get('事件涉及/潜在npc', {}) or {}
    locked = set(event_p.keys()) | set(related_p.keys())
    logger.info(f'N3 锁定：{len(locked)} NPC → {list(locked)[:6]}')

    tasks = []

    for nm, msg in event_p.items():
        if not msg or not msg.strip(): continue
        npc = gc.event_npc.get_npc(nm) or gc.related_npc.get_npc(nm) or gc.all_npc_list.get_npc(nm)
        if npc: tasks.append((nm, npc, msg))

    for nm, msg in related_p.items():
        if not msg or not msg.strip(): continue
        npc = gc.related_npc.get_npc(nm) or gc.all_npc_list.get_npc(nm)
        if npc: tasks.append((nm, npc, msg))

    if not tasks:
        logger.info('N45-N6：本轮无锁定NPC，跳过')
        return [], [], []

    logger.info(f'N45-N6：{len(tasks)} NPC（并行）')
    npc_responses = []
    narrations = []
    npc_minutes = []

    with ThreadPoolExecutor(max_workers=min(len(tasks), 6)) as pool:
        futures = {}
        for nm, npc, msg in tasks:
            fut = pool.submit(
                gc.process_npc_pipeline,
                nm, npc, True, n45_layer, n6_update, msg,
            )
            futures[fut] = nm

        merged_player_delta = {}
        for future in as_completed(futures):
            nm = futures[future]
            try:
                npc_final, response, _, player_delta, narration, minutes = future.result()
                gc.sync_npc_to_lists(npc_final)
                npc_responses.append({'name': nm, 'response': response})
                if response:
                    tag = nm.split('、')[0].strip()
                    print(f'\n{tag}：{response}')
                    gc.add_context_all(f'{tag}：{response}')
                if player_delta:
                    merge_delta(merged_player_delta, player_delta)
                if narration:
                    for line in narration:
                        if line and line not in narrations:
                            narrations.append(line)
                if minutes:
                    npc_minutes.append(minutes)
            except Exception as e:
                logger.error(f'NPC [{nm}] N45-N6异常：{e}')

    if merged_player_delta:
        gc.player.player_dict = apply_player_delta(gc.player.player_dict, merged_player_delta)
        logger.info(f'玩家状态更新：{merged_player_delta}')

    if narrations:
        for line in narrations:
            print(f'\033[3m  {line}\033[0m')
            gc.add_global_context(f'旁白：{line}')

    snapshot_npcs(round_no, 'after', gc.all_npc_list)
    diff_npcs(round_no, gc.all_npc_list)

    return npc_responses, narrations, npc_minutes

def N7_传播(n3_result: dict, tasks: list) -> dict:
    """
    N7: 纯规则地段传播（零耗时，无需并行）。

    返回: n7_result — 受影响的 NPC → 消息 映射
    """
    ground_msg = n3_result.get('地段传播', {})
    if not (ground_msg and ground_msg.get('msg')):
        return {}

    experienced = [nm for nm, _, _ in tasks]
    r7 = n7_prop.propagate(
        ground_msg, gc.b1WorldLayer, gc.all_npc_list,
        experienced,
        gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', ''),
    )
    if r7:
        logger.info(f'N7 传播：影响 {len(r7)} NPC')
    return r7

def N8后_处理移动(move_info: dict, n8_minutes: float):
    """移动上下文写入 + 玩家位置更新"""
    if not move_info: return
    logger.info(f'移动：{move_info["from"]} → {move_info["to"]}（{n8_minutes:.1f}分）')
    gc.player.player_dict['当前状态CurrentState']['当前所处位置'] = move_info['to']
    gc.add_context_all(
        f'{player_name}（移动）：从{move_info["from"]}前往{move_info["to"]}'
        f'（耗时{n8_minutes:.1f}分钟）'
    )

def 玩家自主行动(player_input: str, has_npc: bool, has_move: bool) -> int:
    """
    无NPC参与时，N6玩家层处理自主行动（修炼/赶路/探索等）。

    时间跨度由该层自行估算（原N8职责），作为本轮耗时的玩家侧来源返回。

    :return: 本轮耗时分钟；有NPC参与时返回 0
    """
    if has_npc:
        return 0
    delta, narrs, minutes = n6_update.process_player_action(
        player_inst=gc.player,
        player_input=player_input,
        game_time_str=gc.game_clock.now_str(),
        has_move=has_move,
    )
    if delta:
        gc.player.player_dict = apply_player_delta(gc.player.player_dict, delta)
        logger.info(f'玩家自主：{delta}')
    if narrs:
        for line in narrs:
            print(f'\033[3m  {line}\033[0m')
            gc.add_global_context(f'旁白：{line}')
    return minutes


# ═══════════════════ 主循环 ═══════════════════

while True:

    # ── N0: 后台产出 + 场景 ──
    N0_消费后台产出()
    N0_显示场景()

    # ── N1: 输入 + 命令 ──
    result = N1_读输入()
    if result[0] == 'break': break
    if result[0] == 'continue': continue
    if result[0] is None: continue
    player_input, move_info = result

    # ── 快照 ──
    last_snap = snapshot()
    n9_last   = n9_director.to_dict()
    snapshot_npcs(round_no, 'before', gc.all_npc_list)
    snapshot_scene(round_no, 'before', gc)

    # 回合开始位置：N8 靠「回合前 → 回合后」的位置差算移动耗时，
    # 这样 {移动} 标记和自由文本移动（"我来到青木山脉"）两条路都能覆盖
    loc_before = gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')

    logger.info(f'{player_name} 输入：{player_input}')
    gc.add_context_all(f'{player_name}（玩家）：{player_input}')

    # ── N2: 事件判断（同时产出移动方式/声明耗时，供 N8 折算） ──
    n2_result = N2_事件判断(player_input)

    # ── N3: 感知分发 ──
    n3_result = N3_感知分发()

    # ── N45→N6: 并行 per-NPC ──
    npc_responses, narrations, npc_minutes = N45N6_并行(n3_result)

    # ── N7: 纯规则地段传播（在移动落盘前，按出发地判定） ──
    tasks = [(nm, npc, msg) for nm, npc, msg in [
        (nm, gc.all_npc_list.get_npc(nm), msg)
        for nm, msg in {
            **(n3_result.get('事件涉及的npc', {}) or {}),
            **(n3_result.get('事件涉及/潜在npc', {}) or {}),
        }.items()
    ] if msg and msg.strip() and npc]
    n7_result = N7_传播(n3_result, tasks)

    # ── 玩家自主行动（无NPC时，该层自行估算耗时） ──
    self_minutes = 玩家自主行动(player_input, bool(npc_responses), bool(move_info))

    # ── N8: 纯规则耗时聚合 → 推进时钟 ──
    # 目的位置优先取 {移动} 标记的目标（此刻主循环还没落盘），否则取 N6 玩家增量改后的位置
    loc_after = (move_info or {}).get('to') or \
        gc.player.player_dict['当前状态CurrentState'].get('当前所处位置', '')
    n8_minutes = n8_time.process(
        game_clock=gc.game_clock,
        npc_minutes=npc_minutes,
        player_minutes=self_minutes,
        from_loc=loc_before,
        to_loc=loc_after,
        style=n2_result.get('move_style', ''),
        declared_minutes=n2_result.get('move_declared_minutes', 0),
        world_layer=gc.b1WorldLayer,
    )

    # ── N8 后: 移动落盘 ──
    N8后_处理移动(move_info, n8_minutes)

    # ── 后台心跳 ──
    round_no += 1
    heartbeat.try_trigger(
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
