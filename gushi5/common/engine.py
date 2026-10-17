"""
gushi5 引擎 —— **一轮流水线的唯一实现**。

# 为什么要有这个文件

gushi3 的主循环住在 `src/test/N2_contextRetrieval.py` 里，是**模块级的 `while True`
＋一堆全局变量**。终端能跑，但网页驱动不了它——网页需要的是"起一个回合、把过程当
事件推出去、同时还能随时读状态"，而这三件事在脚本里没有落点。

于是把主循环收进这个类：**终端与网页共用同一份 `run_round()`**，各写各的渲染器，
不会两边各改各的漂移。

    engine.attach(renderer)     ← 终端：把事件还原成 print
    engine.subscribe(queue)     ← 网页：把事件塞进 Queue 推 SSE

**这是同一个接口的两个实现**，不是两份逻辑。

# 一轮的调用链（与 gushi3 逐段对应）

    N0 消费后台产出 + 显示场景
    N1 读输入 + 命令（`handle_input` 只判断该怎么走，不执行流水线）
    ── 快照（供 !! 回滚）──
    N2 事件判断                  ← 关键路径第 1 跳
    N3 感知分发                  ← 第 2 跳
    N45→N6 并行 per NPC          ← 第 3/4 跳（N45 与 N6 同一池并行）
    N7 地段传播（纯规则，在移动落盘之前，按出发地判定）
    玩家自主行动（仅无 NPC 时；该层自行估算耗时）
    N8 耗时聚合（纯规则）→ 推进时钟 → 移动落盘
    心跳触发（后台线程，立即返回）

# 并发

**写**（`run_round`）全程持 `self._lock`，一轮一次；**只读视图不加锁**——
写回路径整体重绑定而非原地改，组装前先浅拷贝。这条与 gushi4 同。
"""

import queue
import threading

from common import debug_tools as dt
from common.game_controller import GameController
from common.heartbeat import HeartbeatManager
from common.log import get_logger
from common.progress import Stage
from common.small_tool import (apply_player_delta, merge_delta, merge_list_delta,
                               resolve_move_markers, strip_position_keys)

from common.N2_contextRetrieval import N2_CE_Process
from common.N3_informationFiltering import N3_Info_Filtering
from common.N45_reason_response import N45_ReasonResponse
from common.N6_update import N6_Update
from common.N7_propagation import N7_Propagation
from common.N8_time import N8_Time
from common.N9_director import N9_Director
from common.task_store import TaskStore, KIND_LONG

logger = get_logger('Engine')

#: 这几个事件是**瞬时**的（进度、排队），不进叙事流历史——恢复它没有意义。
_TRANSIENT = {'stage', 'round_queued', 'round_done'}

#: `global_context` 行的解析前缀（F5 之后重建叙事流用）。
#: 格式固定为 `{轮次号} | {时间} | {内容}`，与 `ContextHistory.add_context` 一致。
_NARR_PREFIX = '旁白：'
_PLAYER_SUFFIX = '（玩家）：'
_MOVE_SUFFIX = '（移动）：'


def parse_context_line(line: str):
    """
    把一行 `global_context` 还原成一个事件。

    用途：**页面 F5 之后靠它恢复叙事流**，否则一刷新就只剩一片空白。
    也是读档/撤回之后重建历史的唯一依据（那两种操作把状态整个换掉了，
    而结构化事件队列里留的还是旧内容——`global_context` 跟着状态一起换）。

    认不出就返回 None，**不猜**：宁可少一行，也不要编错发言人和内容。
    """
    parts = str(line).split(' | ', 2)
    if len(parts) < 3:
        return None
    ts, content = parts[1], parts[2].strip()
    if not content:
        return None

    if content.startswith(_NARR_PREFIX):
        return {'type': 'narration', 'text': content[len(_NARR_PREFIX):], 'ts': ts}

    if _PLAYER_SUFFIX in content:
        who, said = content.split(_PLAYER_SUFFIX, 1)
        return {'type': 'round_start', 'input': said, 'who': who, 'ts': ts}

    if _MOVE_SUFFIX in content:
        who, rest = content.split(_MOVE_SUFFIX, 1)
        return {'type': 'info', 'text': f'{who}{_MOVE_SUFFIX}{rest}', 'ts': ts}

    # 其余按对白处理：`名字：内容`
    if '：' in content:
        who, said = content.split('：', 1)
        return {'type': 'dialogue', 'speaker': who, 'text': said, 'ts': ts}

    return {'type': 'info', 'text': content, 'ts': ts}


class GameEngine:
    """流水线 + 事件流 + 只读视图。终端与网页都驱动它。"""

    def __init__(self, session_id: str = 'gushi5'):
        self.session_id = session_id
        self._lock = threading.Lock()          # 写锁：run_round 全程持锁
        self._renders = []                     # 终端渲染器（attach 进来）
        self._subs = []                        # 网页订阅队列
        self._subs_lock = threading.Lock()
        self._history = []                     # 叙事流历史（不含瞬时事件）

        # 控制器：**输出走 on_output 接收器**，命令回显才能推到网页
        self.gc = GameController(session_id, on_output=self._gc_output)
        self.gc.init_world()                   # 世界（地理与配置来自 世界.map）
        self.gc.init_clock()                   # 时钟 + 三种上下文
        self.gc.init_all_npcs()                # NPC 名册（并按世界配置覆写表上限）
        self.gc.init_player()                  # 玩家（并做一次位置迁移）

        self.round_no = 0
        self.last_snap = None                  # 供 !! 回滚
        self.n9_last = None                    # N9 状态（随快照一起回滚）
        self.player_name = self.gc.player.player_dict.get('玩家名称name', '玩家')

        self._wire()

    def _wire(self):
        """
        建各层，并把它们接到**当前**的 `gc` 上。

        ⚠️ 单独抽出来是因为 `/new`（开新游戏）会**换掉 `gc.b1WorldLayer` 这个对象**
        （`init_world` 新建的），而心跳、N7、N8 这些层在构造时把世界层存进了字段——
        不重新接线就是"拿着上一局的世界在跑"，**而且不报错**，只表现为"地图改了没生效"。

        顺带一个好处：重造各层等于把它们的内部计时清零，所以不需要给每层写 `reset()`。
        """
        self.event_judge = N2_CE_Process()
        self.info_filter = N3_Info_Filtering()
        self.n45_layer = N45_ReasonResponse()
        self.n6_update = N6_Update()
        self.n7_prop = N7_Propagation()
        self.n8_time = N8_Time()
        self.n9_director = N9_Director()
        self.heartbeat = HeartbeatManager(self.gc.deferred_queue, self.n9_director)

    # ===================== 事件流 =====================

    def attach(self, renderer):
        """挂一个渲染器（终端）。返回一个可调用的卸载函数。"""
        self._renders.append(renderer)
        return lambda: self._renders.remove(renderer)

    def emit(self, ev: dict):
        """发一个事件：**只广播，不进历史**。瞬时事件（stage/round_done）走这里。"""
        for r in list(self._renders):
            try:
                r(ev)
            except Exception as e:
                logger.warn(f'渲染器异常：{type(e).__name__}: {e}')
        with self._subs_lock:
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass                        # 页面卡住时丢事件，不能让后台线程堵死

    def _emit(self, ev: dict):
        """发一个事件，并**记进叙事流历史**（页面 F5 之后靠它恢复）。"""
        if ev.get('type') not in _TRANSIENT:
            self._history.append(ev)
        self.emit(ev)

    def _gc_output(self, kind: str, text: str):
        """`GameController.on_output` 的适配器：`(kind, text)` → 事件。"""
        self._emit({'type': kind if kind in ('narration', 'dim') else
                    ('error' if kind == 'error' else 'info'),
                    'text': text})

    def subscribe(self, maxsize: int = 500) -> queue.Queue:
        q = queue.Queue(maxsize=maxsize)
        with self._subs_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._subs_lock:
            if q in self._subs:
                self._subs.remove(q)

    def close(self):
        """
        收尾。**存档写在后台线程里，而线程是 daemon 的——不等它落盘就会丢存档。**
        `/save` 之后紧接着退出（或者 Ctrl-C）会踩到这条。
        """
        try:
            self.gc.wait_for_saves(timeout=20)
        except Exception as e:
            logger.warn(f'退出前等待存档失败：{type(e).__name__}: {e}')

    def history(self) -> list:
        """叙事流历史（不含瞬时事件）。"""
        return list(self._history)

    def hydrate_history(self, limit: int = 400):
        """
        从 `global_context` **重建**叙事流历史。

        什么时候需要：**读档**和**撤回**之后——那两种操作把状态整个换掉了，
        而 `_history` 里留的还是旧内容。`global_context` 跟着状态一起换，所以用它重建。
        """
        self._history.clear()
        for line in list(self.gc.global_context.context)[-limit:]:
            ev = parse_context_line(line)
            if ev:
                self._history.append(ev)
        logger.info(f'叙事流历史已重建：{len(self._history)} 条')

    # ===================== N0 =====================

    def consume_background(self):
        """消费后台心跳产出。控制器自己会往事件流里写简讯与旁白。"""
        self.gc.consume_deferred(n9_director=self.n9_director)

    def show_scene(self):
        """显示场景（位置 / 在场 NPC / 游戏时间）。"""
        self._emit(self._scene_event())

    def _scene_event(self) -> dict:
        gc = self.gc
        loc = gc.player_location
        present = []
        try:
            found = gc.b1WorldLayer.find_npcs_at_location(
                gc.b1WorldLayer.parse_path(loc), gc.all_npc_list)
            present = [self._display_name(n.name) for n in found]
        except Exception as e:
            logger.warn(f'查找在场 NPC 失败：{type(e).__name__}: {e}')
        return {'type': 'scene', 'location': loc, 'present': present,
                'clock': gc.game_clock.now_str(), 'round': self.round_no}

    # ===================== 一轮 =====================

    def run_round(self, player_input: str, move_info: dict = None):
        """
        推进一轮。**全程持锁**：一轮没跑完不许开第二轮，也不许换地基（/new、编辑器保存）。

        :param player_input: 玩家原话（走 `{移动}` 标记时是改写后的句子）
        :param move_info: **终端**交互式导航选出的 `{from, to}`；网页与自由文本不给——
            那种移动由 N2 判 `move_to`（去向）+ `行程`，落坐标见 `_apply_move`
        """
        with self._lock:
            gc = self.gc
            self._emit({'type': 'round_start', 'input': player_input,
                        'round': self.round_no})

            # ── 快照（供 !! 回滚）──
            self.last_snap = gc.snapshot()
            self.n9_last = self.n9_director.to_dict()
            dt.snapshot_npcs(self.round_no, 'before', gc.all_npc_list)
            dt.snapshot_scene(self.round_no, 'before', gc)

            # 位置一律取**坐标派生的路径**（坐标是真源）。它在 migrate_positions
            # 与 set_position 之后与 `当前状态CurrentState.当前所处位置` 保持同步。
            loc_before = gc.player_location
            logger.info(f'━━ 第 {self.round_no} 轮 ━━ {self.player_name}：{player_input}')
            gc.add_context_all(f'{self.player_name}（玩家）：{player_input}')

            # 世界的**硬性常识**（境界体系 / 寿元 / 盟约约束…）每轮灌给 N6 两条链。
            # 只灌"人人皆知"的规则；`世界知识列表` 是秘辛，只进 N9 导演（见 heartbeat）。
            self.n6_update.set_world_rules(gc.world_rules())

            # ── N2 事件判断 ──
            with Stage('N2 事件判断', on_event=self.emit):
                n2_result = self._n2(player_input)

            # ── N3 感知分发 ──
            with Stage('N3 感知分发', on_event=self.emit):
                n3_result = self._n3()

            # ── 预设任务触发（纯规则，不花 LLM）──
            # 放在 N2/N3 之后：名单刚刷新过，"在场 NPC" 才是这一轮的。
            try:
                gc.sync_preset_tasks(
                    [n.name for n in gc.event_npc.npc_list]
                    + [n.name for n in gc.related_npc.npc_list])
            except Exception as e:
                logger.error(f'预设任务触发失败：{type(e).__name__}: {e}')

            # ── N45→N6 并行 per NPC ──
            tasks = self._with_task_npcs(self._locked_tasks(n3_result))
            with Stage(f'N45→N6（{len(tasks)} 个NPC）', on_event=self.emit):
                npc_responses, narrations, npc_minutes = self._n45_n6(tasks)

            # ── N7 地段传播（纯规则，在移动落盘**之前**，按出发地判定）──
            with Stage('N7 地段传播', min_show=1.0, on_event=self.emit):
                n7_result = self._n7(n3_result, tasks)

            # ── 去向：交互式导航点选的（终端 `{移动}`）优先，否则取 N2 判的 `move_to` ──
            # 两者都为空 = 这一轮没移动。自由文本移动全靠 N2 这条——网页端没有别的入口。
            dest = ((move_info or {}).get('to') or (n2_result.get('move_to') or '')).strip()
            stage = ((move_info or {}).get('stage') or n2_result.get('行程') or '').strip()

            # ── 玩家自主行动（仅无 NPC 参与；该层自行估算耗时）──
            self_minutes = self._player_self_action(
                player_input, bool(npc_responses), bool(dest))

            # ── N8 耗时聚合 → 推进时钟 ──
            # 目的位置优先取「去向」（此刻还没落盘），否则取当前坐标派生的位置。
            # ⚠️ 必须**归一成全路径**再交给 N8：N2 吐的是短名（"天道宗"），而 loc_before
            # 是全路径——不归一，同一处会被算成相距 4 段，原地不动也收 40 分钟（实测过）。
            # ⚠️ 用 `place_paths()`（名→全路径）。**不能用 `name_of()`**：那是反向的
            # （路径→地块名），拿它做归一是个空操作，短名原样传下去，bug 照旧。
            loc_after = ((gc.b1WorldLayer.place_paths() or {}).get(dest) or dest) \
                if dest else gc.player_location
            with Stage('N8 耗时', on_event=self.emit):
                n8_minutes, declared_span = self._n8(n2_result, npc_minutes,
                                                     self_minutes, loc_before, loc_after)

            # 明确跳过一大段时间后，在途的人按"早就到了"结清——
            # 否则"（一个月后）"只走一轮，白鹤会在路上"走"一个月。
            if declared_span:
                gc.resolve_travel_on_skip(declared_span)

            # 时钟已经推过了：逾期任务在这里结算（大跨度跳过会一次逾期一批，那是对的）
            try:
                gc.settle_task_deadlines()
            except Exception as e:
                logger.error(f'任务逾期结算失败：{type(e).__name__}: {e}')

            # ── 移动落盘（位置永远只有一条写入通道：move_player_to → set_position）──
            self._apply_move(dest, stage, n8_minutes)

            dt.snapshot_npcs(self.round_no, 'after', gc.all_npc_list)
            dt.diff_npcs(self.round_no, gc.all_npc_list)

            # ── 后台心跳（独立线程，立即返回）──
            self.round_no += 1
            try:
                self.heartbeat.try_trigger(
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
                    round_no=self.round_no,
                )
            except Exception as e:
                logger.error(f'心跳触发失败：{type(e).__name__}: {e}')

            # ── 时间（场景留到下一轮开头由调用方发：终端每轮只该有一个场景块）──
            self._emit({'type': 'time', 'text': gc.game_clock.now_str(),
                        'minutes': n8_minutes, 'round': self.round_no})
            logger.info(f'━━ 第 {self.round_no - 1} 轮结束 ━━')
            return {'narration': narrations, 'minutes': n8_minutes}

    # ---------- 各阶段（与 gushi3 主循环逐段对应）----------

    def _n2(self, player_input: str) -> dict:
        """
        N2 → LLM 判断 is_new_event + add/del event_npc + add/del related_npc。
        代码层：附近 NPC 距离预计算 + 新事件防死循环。
        """
        gc = self.gc
        current_event_npc_names = [n.name for n in gc.event_npc.npc_list]
        all_npc_names = [n.name for n in gc.all_npc_list.npc_list]
        player_loc = gc.player_location

        near_dist = gc.b1WorldLayer.get_config('NPC_附近距离_段', 3)
        nearby = []
        for npc in gc.all_npc_list:
            npc_loc = npc.current_state.get('当前所处位置', '')
            if not npc_loc:
                continue
            d = gc.b1WorldLayer.location_distance(player_loc, npc_loc)
            if d <= near_dist:
                nearby.append({'名字': npc.name, '位置': npc_loc, '距离': f'{d}段'})

        result = self.event_judge.judge(
            gc.event_context, current_event_npc_names, all_npc_names,
            gc.director_context, player_loc, nearby,
            # 已知地点：让 N2 的 `move_to` 只能从树里挑，别自己编"天道宗上空"。
            # ⚠️ 用 `gc.known_places()`（与 N6 同一份，深度 6），**不要在这里写死一个
            # 深度**：原来这里是 `max_depth=3`，于是 `韩家村`(4 层)、`王宫·承天殿`(5 层)
            # 这些**玩家真要去的地方根本不在名单里**，N2 只能填个上位的"云梦国"。
            gc.known_places(),
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
                # 散场 = **清名单 + 清事件上下文**，两件一起做。
                # 只清一半就是"事件永不翻篇"：上下文一直连着，名单也一直连着。
                gc.event_context.new_context()
                gc.event_context.add_context(f'{self.player_name}（玩家）：{player_input}')
                gc.related_npc.new_list()

        for nm in (result.get('add_npc') or []):
            npc = gc.all_npc_list.get_npc(nm)
            # 查重：模型会把**已经在名单里的人**再 add 一次（实测同一人被加了两遍，
            # 名单里出现重复项）。重复不会让 N3 多算（它按名字出 dict），但会污染日志、
            # 也会在"本轮在场几人"这类判断里撒谎——所以这里挡住并记一次。
            if npc and npc not in gc.event_npc.npc_list:
                gc.event_npc.add(npc)
            elif npc:
                logger.info(f'名单查重：{nm} 已在事件名单里，add_npc 忽略')
        for nm in (result.get('del_npc') or []):
            gc.event_npc.delete(gc.all_npc_list.get_npc(nm))
        for nm in (result.get('add_related_npc') or []):
            npc = gc.all_npc_list.get_npc(nm)
            if npc and npc not in gc.related_npc.npc_list:
                gc.related_npc.add(npc)
        for nm in (result.get('del_related_npc') or []):
            gc.related_npc.delete(gc.all_npc_list.get_npc(nm))

        logger.info(f'N2 后：event_npc={len(gc.event_npc)} related_npc={len(gc.related_npc)}')
        return result

    def _n3(self) -> dict:
        """N3 → LLM 判断每个 NPC 收到什么消息 + 地段传播数据。"""
        gc = self.gc
        result = self.info_filter.judge_for_npc(
            gc.event_context, gc.event_npc, gc.related_npc,
            gc.player, gc.director_context,
        )
        logger.info(f'N3 结果：event={len(result.get("事件涉及的npc", {}) or {})} '
                    f'related={len(result.get("事件涉及/潜在npc", {}) or {})}')
        return result

    @staticmethod
    def _locked_tasks(n3_result: dict) -> list:
        """把 N3 的两份分发结果整理成 [(名字, npc?, 感知)] 的列表。"""
        event_p = n3_result.get('事件涉及的npc', {}) or {}
        related_p = n3_result.get('事件涉及/潜在npc', {}) or {}
        return [(nm, msg) for nm, msg in list(event_p.items()) + list(related_p.items())
                if msg and str(msg).strip()]

    #: 长期任务（养成 / 守卫 / 潜伏）的推进间隔（轮）。一次交付型每轮都推。
    LONG_TASK_PUSH_INTERVAL = 6

    def _with_task_npcs(self, tasks: list, limit: int = 2) -> list:
        """
        把**手上还有没办完的事**的 NPC 强制并进名单（上限 `limit` 个）。

        "没办完"有两个来源，**都要兜**：

        1. `gc.tasks` 里执行者是他的**进行中任务**。
        2. `gc.npc_travel` —— **正在路上**的。

        ⚠️ **交付型每轮推，长期型按 `LONG_TASK_PUSH_INTERVAL` 抽**：
        长期任务（"外门两年""守三年山门"）按定义就不可能一轮走完，每轮把他拉进名单
        只是每轮多烧一组 N45+N6，而这类任务的进度本来就是**时钟**推的。
        旧实现在这里是持续成本漏洞——一条没期限的长期差事会把 NPC 永久钉在名单里。

        ⚠️ 为什么仍要**独立**兜"在途"：任务表依赖模型写任务通道，而它并不总写
        （实测把"接下差事"写进 `世界状态`、`去向` 也写对了，任务表却是空的）。
        只靠任务表的话，玩家一不再点他，他就掉出锁定名单、走到半路再没人算。
        到了目的地 `npc_travel` 自然清空，他就不再被强制。

        代价是每个这样的 NPC 每轮多一次 N45+N6 调用，所以有上限。
        """
        gc = self.gc

        def key(s):
            return str(s).split('（')[0].strip()

        have = {key(nm) for nm, _ in tasks}
        TODO_MSG = ('（本轮没有新的感知输入：你手上还有一件差事没了结——{desc}。'
                    '照你的性格和能力往下办：该动身就写「去向」；办完了写 `任务` 的'
                    '「了结」（带 id 与结果）；办不成照实写「失败」）')
        ONROAD_MSG = ('（本轮没有新的感知输入：你正在半路上，接着往下走——'
                      '「行程」只许往后报（已经半路就别再说刚上路），走到了就写「到了」）')

        # ── 1) 有进行中任务的人（长期型按间隔抽）──
        due = []
        for t in gc.tasks.open_tasks():
            owner = str(t.get('执行者') or '').strip()
            if not owner or owner == '玩家':
                continue
            if t.get('类型') == KIND_LONG:
                last = int(t.get('最近推进轮') or 0)
                if last and self.round_no - last < self.LONG_TASK_PUSH_INTERVAL:
                    continue
            due.append(t)

        extra = []
        taken = set(have)
        for t in due:
            owner = str(t.get('执行者')).strip()
            k = key(owner)
            if k in taken:
                continue
            taken.add(k)
            extra.append((owner, TODO_MSG.format(desc=TaskStore.describe(t)), t))
            if len(extra) >= limit:
                break

        # ── 2) 在途的（任务里已经有的就不重复）──
        for nm in gc.npc_travel:
            if len(extra) >= limit:
                break
            k = key(nm)
            if k in taken:
                continue
            taken.add(k)
            extra.append((nm, ONROAD_MSG, None))

        if not extra:
            return tasks

        for nm, _msg, t in extra:
            if t is not None:
                t['最近推进轮'] = self.round_no
            logger.info(f'📋 推进{"在办任务" if t is not None else "在途"}：{key(nm)}'
                        + (f'（{t.get("id")}｜{t.get("类型")}｜{t.get("标题")}）' if t else ''))
        return tasks + [(nm, msg) for nm, msg, _t in extra]

    def _n45_n6(self, tasks: list):
        """
        对锁定名单里的每个 NPC 跑 N45→N6（**并行**）。

        N45：推理待解释事件 + 生成回应与内心想法（原 N4+N5 合并）
        N6： 落实增量 + 旁白 + 本轮耗时

        :return: (npc_responses, narrations, npc_minutes)
        """
        gc = self.gc
        if not tasks:
            logger.info('N45-N6：本轮无锁定 NPC，跳过')
            return [], [], []

        # 先把名字解析成实例，解析不出（防幻觉）的直接丢。
        # 用 `_find_npc`（静默）而不是 `event/related/all` 三个列表逐个试——
        # 后者每次 miss 都刷一条假告警，每轮一条，把真告警埋掉。
        real = []
        for nm, msg in tasks:
            npc = gc._find_npc(nm)
            if npc:
                real.append((nm, npc, msg))
            else:
                logger.warn(f'N3 分发给了不存在的 NPC：{nm}（丢弃）')
        if not real:
            return [], [], []

        logger.info(f'N45-N6：{len(real)} NPC（并行）')
        npc_responses, narrations, npc_minutes = [], [], []

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(len(real), 6)) as pool:
            futures = {pool.submit(gc.process_npc_pipeline, nm, npc, False,
                                   self.n45_layer, self.n6_update, msg): nm
                       for nm, npc, msg in real}
            merged_player_delta = {}
            # 世界状态（公开事实）是 **list 型**增量，合并规则与玩家增量不同（那个是字段字典），
            # 所以另开一个累加器；**整轮只落盘一次**（见下面 `apply_world_state_delta`）。
            merged_world_delta = {'增加': [], '删减': []}
            for fut in as_completed(futures):
                nm = futures[fut]
                try:
                    (npc_final, response, _, player_delta,
                     narration, minutes, world_delta) = fut.result()
                    gc.sync_npc_to_lists(npc_final)
                    npc_responses.append({'name': nm, 'response': response})
                    if response:
                        tag = self._display_name(nm)
                        self._emit({'type': 'dialogue', 'speaker': tag, 'text': response})
                        gc.add_context_all(f'{tag}：{response}')
                    if player_delta:
                        merge_delta(merged_player_delta, player_delta)
                    if narration:
                        for line in narration:
                            if line and line not in narrations:
                                narrations.append(line)
                    if minutes:
                        npc_minutes.append(minutes)
                    if world_delta:
                        merge_list_delta(merged_world_delta, world_delta)
                except Exception as e:
                    logger.error(f'NPC [{nm}] N45-N6 异常：{type(e).__name__}: {e}')

        if merged_player_delta:
            # 位置字段归代码：NPC 层产出的玩家增量同样不许写位置
            merged_player_delta = strip_position_keys(
                merged_player_delta, 'N45-N6 玩家增量')
        if merged_player_delta and (merged_player_delta.get('增加')
                                    or merged_player_delta.get('删减')):
            gc.player.player_dict = apply_player_delta(
                gc.player.player_dict, merged_player_delta)
            logger.info(f'玩家状态更新：{merged_player_delta}')

        # ── 世界事实落盘：整轮一次 ──
        # 几个 NPC 的 N6 会各写一份同一件事，所以合并后**只调一次**
        #（去重 + 每轮上限都在 `apply_world_state_delta` 里）。
        # 这一条是"玩家的动作真的发生了"唯一的落盘处：写了它，下一轮 N45/N6 才看得到。
        if merged_world_delta['增加'] or merged_world_delta['删减']:
            gc.apply_world_state_delta(merged_world_delta, 'N45-N6')

        for line in narrations:
            self._emit({'type': 'narration', 'text': line})
            gc.add_global_context(f'旁白：{line}')

        return npc_responses, narrations, npc_minutes

    def _n7(self, n3_result: dict, tasks: list) -> dict:
        """N7：纯规则地段传播（零耗时，不需要并行）。"""
        gc = self.gc
        ground_msg = n3_result.get('地段传播', {})
        if not (ground_msg and ground_msg.get('msg')):
            return {}
        experienced = [nm for nm, _ in tasks]
        r7 = self.n7_prop.propagate(
            ground_msg, gc.b1WorldLayer, gc.all_npc_list, experienced,
            gc.player_location)
        if r7:
            logger.info(f'N7 传播：影响 {len(r7)} NPC')
        return r7

    def _player_self_action(self, player_input: str, has_npc: bool,
                            has_move: bool) -> float:
        """
        无 NPC 参与时，N6 玩家层处理自主行动（修炼/赶路/探索…）。

        时间跨度由该层自行估算（原 N8 职责），作为本轮耗时的玩家侧来源返回。

        :return: 本轮耗时分钟；有 NPC 参与时返回 0
        """
        if has_npc:
            return 0
        gc = self.gc
        delta, narrs, minutes, world_delta, task_delta = self.n6_update.process_player_action(
            player_inst=gc.player,
            player_input=player_input,
            game_time_str=gc.game_clock.now_str(),
            has_move=has_move,
            world_facts=gc.world_facts(),
            tasks=gc.tasks.describe_all('玩家'),
        )
        # 独处时也能建/了结任务（"我决定去把那颗石头要回来"）——两条入口链同改，
        # 否则只有跟 NPC 说话才存在任务，一个人行动就永远建不出任务来。
        if isinstance(task_delta, dict) and task_delta:
            gc._apply_task_channel('玩家', {'任务': task_delta})
        if delta:
            # 位置字段归代码：LLM 只能给「去向」，写位置一律丢（见 move_player_to）
            delta = strip_position_keys(delta, 'N6-Player')
        if delta and (delta.get('增加') or delta.get('删减')):
            gc.player.player_dict = apply_player_delta(gc.player.player_dict, delta)
            logger.info(f'玩家自主：{delta}')
        # 无 NPC 参与时，玩家的动作同样要有落盘处——否则"我劈开这座山"只活在旁白里，
        # 下一轮没有任何一层记得它发生过。
        if world_delta:
            gc.apply_world_state_delta(world_delta, 'N6-Player')
        for line in narrs or []:
            self._emit({'type': 'narration', 'text': line})
            gc.add_global_context(f'旁白：{line}')
        return minutes or 0

    def _n8(self, n2_result: dict, npc_minutes: list, self_minutes: float,
            loc_before: str, loc_after: str):
        """
        N8：纯规则耗时聚合 → 推进时钟。移动方式与声明耗时由 N2 判断。

        :return: (本轮推进分钟, 玩家声明的跳过分钟)
        """
        span = self._declared_span_minutes(n2_result)
        minutes = self.n8_time.process(
            game_clock=self.gc.game_clock,
            npc_minutes=npc_minutes,
            player_minutes=self_minutes,
            from_loc=loc_before,
            to_loc=loc_after,
            declared_minutes=n2_result.get('move_declared_minutes', 0),
            judged_minutes=n2_result.get('move_minutes', 0),
            declared_span_minutes=span,
            world_layer=self.gc.b1WorldLayer,
        )
        return minutes, span

    def _declared_span_minutes(self, n2_result: dict) -> int:
        """
        玩家**明确声明的时间跳过** → 分钟；只是"提到时间"一律 0。

        判据由 N2 给（`时间判定` ∈ 跳过 / 提及 / 无），这里只做单位换算与上限截断。

        ⚠️ 为什么要分"跳过"和"提及"：玩家说"一年后我要你做到"是在**提要求**，
        时钟一分钟都不该动；只有"等一年""修炼一年""（一年后）"这种才是时间真的流逝。
        判错就是把时钟推飞，所以判定结果每轮都落在 `N2 结果` 那行日志里。
        """
        verdict = str(n2_result.get('时间判定') or '').strip()
        if verdict != '跳过':
            return 0

        # 合法单位由**本世界的历法阶梯**决定，不写死在提示里（换纪年换了单位名，提示要跟着变）
        clock = self.gc.game_clock
        ok_units = '、'.join(['分钟', '周'] + list(clock.unit_minutes().keys()))

        span = n2_result.get('时间跨度')
        if isinstance(span, dict):
            value = span.get('值', span.get('数值'))
            unit = span.get('单位', '分钟')
        elif isinstance(span, (int, float)) and not isinstance(span, bool):
            value, unit = span, '分钟'
            logger.warn(f'时间跳过没给单位（{span!r}），按分钟算——N2 应返回 '
                        f'{{"值":数字,"单位":"{ok_units}"}}')
        else:
            logger.warn(f'时间判定为「跳过」但没有可解析的 `时间跨度`（{span!r}），本轮不跳')
            return 0

        minutes = clock.span_to_minutes(value, unit)
        if minutes is None:
            logger.warn(f'时间跳过的单位「{unit}」认不出（{value}），本轮不跳'
                        f'（本世界历法只认 {ok_units}）')
            return 0
        if minutes <= 0:
            return 0
        cap = self.n8_time.MAX_DECLARED_MINUTES
        if minutes > cap:
            logger.warn(f'时间跳过 {minutes} 分超出上限，截断为 {cap} 分')
            minutes = cap
        logger.info(f'⏭ 时间跳过：{value}{unit} = {minutes} 分'
                    f'（跳到 {clock.str_at(clock.elapsed_minutes + minutes)}）')
        return minutes

    def _apply_move(self, dest: str, stage: str, minutes: float):
        """
        移动落盘：**只走 `move_player_to`**（坐标是真源、路径是派生，一次写两样）。

        ⚠️ 这里**不许**直接写 `当前所处位置`。gushi3 原来是直接写那个字段的——
        在新地图下那就是"坐标说在 A、路径说在 B"，而且是那个 2026-10-08 事故的形状
        （往真路径后面接话 → 当轮所有地段能力静默失效）。

        `dest` 来自两处，优先级：终端交互式导航点选的去向 > N2 判的 `move_to`
        （自由文本移动）。解析不出 / 已在当地 / 走不通 → **原地不动**（原因各有日志）。
        """
        if not dest:
            return
        gc = self.gc
        frm = gc.player_location
        if not gc.move_player_to(dest, stage):
            return
        logger.info(f'移动：{frm} → {dest}（{minutes:.1f}分）')
        gc.add_context_all(
            f'{self.player_name}（移动）：从{frm}前往{dest}（耗时{minutes:.1f}分钟）')

    # ===================== 输入 / 命令 =====================

    @staticmethod
    def is_move_input(raw: str) -> bool:
        """
        这一条是不是**交互式导航**（`{移动}` 标记或 `/move`）。

        它必须由**终端**自己处理——导航要一问一答地选节点，网页上没法做。
        网页端点地图走的是"合成一句『我往X去』"，不需要标记。
        """
        s = (raw or '').strip()
        return s == '/move' or '{移动}' in s

    def resolve_move(self, raw: str):
        """终端专用：跑交互式导航，返回 `(改写后的输入, {from,to})`。取消返回 `(None, None)`。"""
        return resolve_move_markers(raw, self.gc.b1WorldLayer, self.gc.player)

    def handle_input(self, raw: str) -> dict:
        """
        判断一条输入该怎么走，**不执行流水线**。

        :return: `{'action': ..., 'text': ...}`

        | action   | 含义                                       |
        |----------|--------------------------------------------|
        | `empty`  | 空输入，忽略                               |
        | `break`  | 退出                                       |
        | `skip`   | 已处理（命令 / 撤回 / 导航不可用），跳过本轮 |
        | `run`    | 正常推进一轮，用 `text`                    |
        """
        if not (raw or '').strip():
            return {'action': 'empty'}

        s = raw.strip()

        if s == '!!':
            self.undo()
            return {'action': 'skip'}

        if s == '/debug':
            self._emit({'type': 'info',
                        'text': f'🔧 Debug：{"🟢" if dt.set_debug(not dt.DEBUG_MODE) else "🔴"}'})
            return {'action': 'skip'}

        if s == '/new':
            self.new_game()
            return {'action': 'skip'}

        if s == '/reload':
            self.reload_world()
            return {'action': 'skip'}

        if s == '/help':
            for line in (
                '  /save <名> /load <名> /saves /quick_save /quick_load',
                '  /new 开新游戏（清掉这一局的进度；磁盘上的存档不动）',
                '  /reload 手动重载世界地图（编辑器保存时会自动做）',
                '  /context [global|event|director]  /npcs  /player [inv|desc]',
                '  {移动} 在句中插入一个目的地（**仅终端**）    !! 撤回上一条    exit',
            ):
                self._emit({'type': 'info', 'text': line})
            return {'action': 'skip'}

        # 交互式导航只在终端能做——网页端不能在这里阻塞等输入
        if self.is_move_input(raw):
            self._emit({'type': 'info',
                        'text': '📍 交互式导航只能在终端用（它要一问一答地选节点）；'
                                '网页上请直接点地图，或打字说去哪。'})
            return {'action': 'skip'}

        cmd = self.gc.handle_command(raw)
        if cmd == 'break':
            return {'action': 'break'}
        if cmd == 'continue':
            # 读档同撤回：状态整个换掉了，叙事流历史得跟着重建
            if s.lower().startswith(('/load', '/quick_load')):
                self.hydrate_history()
            return {'action': 'skip'}

        return {'action': 'run', 'text': raw}

    def undo(self) -> bool:
        """撤回上一轮（全量快照回滚 + 重建叙事流）。"""
        if self.last_snap is None:
            self._emit({'type': 'info', 'text': '↩ 没有可撤回的内容'})
            return False
        self.gc.restore(self.last_snap)
        if self.n9_last is not None:
            self.n9_director.restore(self.n9_last)
        # 状态整个换回去了，叙事流历史也得跟着重建——不然网页上还留着"撤回掉的那一轮"
        self.hydrate_history()
        self._emit({'type': 'info', 'text': '↩ 已撤回上一条'})
        return True

    # ===================== 开局 / 重载 =====================

    def new_game(self) -> bool:
        """开新游戏（`/new`）。抢不到锁就明说，**不排队**——排队会让人以为点了没反应。"""
        if not self._lock.acquire(blocking=False):
            self._emit({'type': 'info', 'text': '⏳ 上一轮还在跑，等它跑完再开新游戏'})
            return False
        try:
            self.round_no = 0
            self.last_snap = None
            self.n9_last = None
            self._history.clear()
            self.gc.new_game()          # 世界 / 名单 / 上下文 / 时钟 / 队列 / 玩家
            self._wire()                # 各层重新接到新世界上（顺带清零内部计时）
            self.player_name = self.gc.player.player_dict.get('玩家名称name', '玩家')
            logger.info(f'🆕 开新游戏：{self.player_name} @ {self.gc.player_location}')
            self._emit({'type': 'info', 'text': f'🆕 新游戏：{self.player_name} @ '
                                                f'{self.gc.player_location}'})
            self.show_scene()
            return True
        finally:
            self._lock.release()

    def reload_world(self) -> bool:
        """手动重载世界地图（`/reload`）。编辑器保存时也走这条路径。"""
        if not self._lock.acquire(blocking=False):
            self._emit({'type': 'info', 'text': '⏳ 上一轮还在跑，等它跑完再重载'})
            return False
        try:
            r = self.gc.reload_world()
            for line in (r.get('立即生效') or []):
                self._emit({'type': 'info', 'text': f'♻️ 立即生效：{line}'})
            for line in (r.get('需重启') or []):
                self._emit({'type': 'info',
                            'text': f'♻️ 改「{line}」要重启才生效（下一局才读它）'})
            before, after = r.get('世界状态条数', [None, None])
            self._emit({'type': 'info', 'text': f'♻️ 世界已重载'
                                                f'（世界状态 {before} → {after} 条）'})
            return True
        finally:
            self._lock.release()

    # ===================== 只读视图（给 UI）=====================
    #
    # 全部**不加锁**，组装前先浅拷贝。理由见模块 docstring 的「并发」一节。

    @staticmethod
    def _display_name(name: str) -> str:
        """
        名字的**首名**（`、` 之前那段），与 `N45N6_并行` 里打印用的是同一条规则。

        ⚠️ 注意**不剥括号**——`n.name.split('、')[0]` 本来就是带括号的，
        多剥一层终端输出就对不上了。UI 想显示更短的名字，自己在前端裁。
        """
        return (name or '').split('、')[0].strip()

    @staticmethod
    def _short_name(name: str) -> str:
        """更短的名字，**只用于 UI 展示字段**，不参与任何与终端对齐的输出。"""
        return (name or '').split('、')[0].split('（')[0].strip()

    def _npc_brief(self, npc) -> dict:
        gc = self.gc
        st = dict(npc.current_state or {})
        in_event = any(npc.name == n.name for n in gc.event_npc.npc_list)
        in_related = any(npc.name == n.name for n in gc.related_npc.npc_list)
        return {
            'name': npc.name,
            'short': self._short_name(npc.name),
            'display': self._display_name(npc.name),
            'location': st.get('当前所处位置', ''),
            'level': st.get('实力等级', ''),
            'identity': st.get('当前身份', ''),
            'mood': st.get('当前心情', ''),
            'physical': st.get('体力状态', ''),
            'involved': bool(in_event or in_related),
            'event': bool(in_event),
            'related': bool(in_related),
        }

    def scene_view(self) -> dict:
        """一屏状态：玩家 / 时钟 / 在场 NPC / 当前事件。供状态面板与 NPC 列表使用。"""
        gc = self.gc
        pd = dict(gc.player.player_dict)               # 浅拷贝，见并发说明
        st = dict(pd.get('当前状态CurrentState') or {})
        loc = gc.player_location

        present = []
        try:
            found = gc.b1WorldLayer.find_npcs_at_location(
                gc.b1WorldLayer.parse_path(loc), gc.all_npc_list)
            present = [self._npc_brief(n) for n in found]
        except Exception as e:
            logger.warn(f'查找在场 NPC 失败：{type(e).__name__}: {e}')

        # gushi5 没有 scope 枚举（可见性判在 N3 的 prompt 里），事件标题也没有——
        # 这两个字段留着给前端用，填 gushi5 真实有的东西。
        involved = [n.name for n in gc.event_npc.npc_list] + \
                   [n.name for n in gc.related_npc.npc_list]
        return {
            'round': self.round_no,
            'session': self.session_id,
            'player': {
                'name': pd.get('玩家名称name', '玩家'),
                'location': loc,
                'status': st,                          # 含 实力等级/身份/体力状态/心情…
                'inventory': dict(pd.get('持有物Inventory') or {}),
                'description': list(pd.get('描述Description') or []),
            },
            'clock': {
                'text': gc.game_clock.now_str(),
                'is_night': gc.game_clock.is_night(),
            },
            'present_npcs': present,
            'npc_total': len(gc.all_npc_list),
            'event': {
                'title': '',
                'scope': '',
                'rounds': len(gc.event_context),
                'involved': involved,
                'direct_target': [],
                'started_at': '',
            },
            'can_undo': self.last_snap is not None,
        }

    def map_view(self) -> dict:
        """
        小地图要的全部数据（**当前世界**那一份）。**只读接口，不加锁**。

        算法在 `map_io.map_view_of()`——它只认"一个世界层 + 玩家在哪"，
        于是编辑器能拿一份**还没写盘**的世界层去算同一张图（见 `/api/map/preview`）。
        预览与线上共用一个函数，就不会"长得像但两回事"。
        """
        from common import map_io
        st = dict(self.gc.player.player_dict.get('当前状态CurrentState') or {})
        return map_io.map_view_of(self.gc.b1WorldLayer, st, self.gc.player_location)

    def location_tree(self) -> dict:
        """
        地点树（规范化成前端好渲染的形状）+ 当前位置。

        ⚠️ 遍历必须走 `normalize_node`——直接读原始节点的键，`_属性` 会被当成
        一个叫"_属性"的子地点渲染出来，前端就会多出一堆假节点。
        """
        from common import map_io
        gc = self.gc
        raw = gc.b1WorldLayer.get_location_tree() or {}
        norm = gc.b1WorldLayer.normalize_node

        def walk(node, prefix: str) -> list:
            out = []
            _, _, kids = norm(node)
            for k, v in kids.items():
                path = f'{prefix}·{k}' if prefix else k
                attrs, desc, grand = norm(v)
                attrs = {a: b for a, b in attrs.items()
                         if a not in map_io.FRONTEND_HIDDEN_ATTRS}
                out.append({'name': k, 'path': path, 'leaf': not grand,
                            'desc': desc, 'attrs': attrs,
                            'children': walk(v, path) if grand else []})
            return out

        tree = walk(raw, '')
        current = gc.player_location
        return {
            'tree': tree,
            'current_path': current,
            'current_parts': gc.b1WorldLayer.parse_path(current),
            'in_tree': any(n['path'] == current for n in self._flatten(tree)),
        }

    @staticmethod
    def _flatten(nodes: list) -> list:
        out = []
        for n in nodes:
            out.append(n)
            out.extend(GameEngine._flatten(n['children']))
        return out

    def npc_detail(self, name: str) -> dict:
        """单个 NPC 的 11 张表原文。**玩家不可见**，供调试面板。"""
        npc = self.gc.all_npc_list.get_npc(name)
        if npc is None:
            return {'found': False, 'name': name}
        return {'found': True, 'name': npc.name,
                'short': self._short_name(npc.name),
                'brief': self._npc_brief(npc),
                'tables': npc.to_dict()}

    def list_npcs(self) -> list:
        return [self._npc_brief(n) for n in self.gc.all_npc_list.npc_list]

    def list_saves(self) -> list:
        return self.gc.save_manager.list_saves()

    def contexts(self, which: str = 'global') -> dict:
        """`global` / `event` / `director` 三种上下文的原文。"""
        ctx = {'event': self.gc.event_context,
               'director': self.gc.director_context}.get(which, self.gc.global_context)
        return {'which': which, 'count': ctx.count, 'lines': list(ctx.context)}
