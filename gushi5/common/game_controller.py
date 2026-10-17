"""
游戏控制器：统一管理运行时游戏状态、存档和命令处理。
将所有"管理类"逻辑从主流程中剥离，主流程只需关注 N1→N2→N3→N4→N5 的调用顺序。
"""

import os
import threading
import time
from copy import deepcopy

from common.log import get_logger
from common import log as log_mod
from common.save_load import SaveManager
from common.base_cnf import (BASE_PATH, SAVE_ROOT_PATH, SAVE_VERSION, WORLD_DATA_FILE,
                            WORLD_DATA_SELECT, WORLD_MAP_FILE, NPC_DATA_ROOT_PATH)
from common import map_io
from common.progress import terminal_output
from common.small_tool import (load_json_to_dict, walk_category_files,
                               load_npc_list_from_json, apply_npc_delta,
                               extract_position_dest)
from common import time_int
from common.game_clock import GameClock
from common.deferred_queue import DeferredQueue
from common.task_store import (TaskStore, KIND_DELIVER, KIND_LONG,
                               ACTIVE, DONE, FAILED)

from Schema.Context_sch import ContextHistory
from Schema.B3_npclist_sch import B3NPCListLayer
from Schema.B3_1_npc_sch import B3NPCLayer
from Schema.B4_player_sch import B4PlayerLayer
from Schema.B1_worldState_sch import B1WorldLayer

logger = get_logger("GameController")


class GameController:
    """
    管理所有运行时游戏状态，提供统一的存档/读档/命令处理接口。

    使用方式：
        gc = GameController(session_id="test001")
        gc.init_world()
        gc.init_all_npcs()
        gc.init_player()

        # 主循环中
        result = gc.handle_command(raw_input)
        if result == 'break': break
        if result == 'continue': continue
        # result is None → 正常输入，走流水线
    """

    def __init__(self, session_id: str = "default", on_output=None):
        self.save_manager = SaveManager()

        # ---- 游戏时钟 ----
        self.game_clock: GameClock = None

        # ---- 上下文（时钟注入后才创建）----
        self.global_context: ContextHistory = None
        self.event_context: ContextHistory = None
        self.director_context: ContextHistory = None  # N9内部上下文，不向玩家暴露

        # ---- NPC 列表 ----
        self.event_npc = B3NPCListLayer()
        self.related_npc = B3NPCListLayer()
        self.all_npc_list = B3NPCListLayer()

        # ---- 世界 & 玩家 ----
        self.b1WorldLayer: B1WorldLayer = None
        self.player: B4PlayerLayer = None

        # ---- 存档线程追踪 ----
        self._save_threads: dict[str, threading.Thread] = {}

        # ---- 后台心跳队列 ----
        self.deferred_queue = DeferredQueue()

        # ---- 随行 ----
        # `follow_map`：跟随者 → 被跟随者（`"玩家"` 或 NPC 完整名）。N6 的 npc 增量里
        # `跟随` / `同行` 决定进出；被跟随者每次移动落盘，跟随者就被一起带过去
        # （见 `_carry_followers`）。
        self.follow_map: dict = {}

        # ---- 任务表（**差事的唯一真源**）----
        # 一条任务 = 一条独立记录（id / 执行者 / 状态 / 期限 / 历史）。
        # 旧的 `npc_tasks`（{NPC名: {目标,去向,行程}}）一个 NPC 只能有一条、写完就覆盖，
        # 旧的那条既没完成也没失败就消失了——所以它降级成 `tasks` 的一个**派生视图**，
        # 不再是存储（见 `npc_tasks` 属性）。
        self.tasks = TaskStore()
        #: 模型用旧写法（`新任务` / `任务完成`）的次数。**看得见才知道 prompt 教没教住**——
        #: 只打一行 WARN 不够：它每轮都刷就变成噪音，不刷就没人知道还在违约。
        self._legacy_task_writes = 0

        # ---- NPC 的在途进度 ----
        # NPC 名 → {目标, frac}。**同一条路只进不退**：LLM 每轮都爱说"刚上路"
        # （它看不到自己在路上），没有这一条就永远停在整段路的 15% 处。
        self.npc_travel: dict = {}

        # ---- 输出接收器 ----
        # 所有面向玩家的文本都走 `self._out(kind, text)`，**不直接 print**。
        # 为什么：命令回显（/npcs、/player、存档提示）如果直接 print，网页端就
        # 看不到——表现为"命令执行了但页面上什么都没有"。注入接收器之后，
        # 终端和网页是同一份输出语义的两个实现。
        self.on_output = on_output or terminal_output

    @property
    def npc_tasks(self) -> dict:
        """
        `{NPC名: 最早的一条进行中任务}` —— **派生视图，不是存储**。

        保留这个名字是因为 N6 输入的 `task` 参数和 `_with_task_npcs` 都在用它。
        真正落盘的是 `self.tasks`；这里每次现算，所以不存在"两份账对不上"的可能。
        """
        return self.tasks.npc_open_map()

    # ===================== 输出 =====================

    def _out(self, kind: str, text: str):
        """
        输出一行。`kind` 是**语义标签，不是样式**：

        | kind        | 含义                   | 终端上          |
        |-------------|------------------------|-----------------|
        | `narration` | 旁白（玩家可见的氛围） | 灰斜体          |
        | `dim`       | 幕后简讯（世界/传播）  | 暗字            |
        | `info`      | 命令回显、状态提示     | 原样            |
        | `error`     | 失败提示               | 原样            |

        ANSI 码由接收器决定，数据里只有纯文本——同一份输出推到网页才不会被
        `\\033[3m` 污染。
        """
        try:
            self.on_output(kind, text)
        except Exception as e:      # 接收器坏了不能让游戏停
            logger.warn(f'输出接收器异常（{kind}）：{type(e).__name__}: {e}')
            print(text)

    # ===================== 初始化 =====================

    def init_world(self):
        """
        加载世界层数据。

        地理与世界观配置的**真源是 `.map`**（见 `地图格式.md`）：
        JSON 里只剩"没有几何、也不常手改"的那几份列表（规则/状态/演变/知识）。
        gushi3 原来把地点树和 `世界配置` 一起塞在 global.json 里，两处真源必然漂。

        ⚠️ 读不到 `.map` 就**报错停下**，不静默降级——空地图会让"所有地点都不存在"，
        而玩家看到的会是"NPC 集体失忆"这种查不出根因的现象。
        """
        world_data = load_json_to_dict(os.path.join(BASE_PATH, WORLD_DATA_FILE))
        world = world_data[WORLD_DATA_SELECT]

        map_path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
        if not os.path.isfile(map_path):
            raise FileNotFoundError(
                f'找不到世界地图 {WORLD_MAP_FILE}（地理与配置的真源）。'
                f'首次生成：python -m common.map_io --export --from-json')
        world = map_io.apply_map(world, map_io.load_world_map(map_path))
        self.b1WorldLayer = B1WorldLayer(world)
        logger.info(f'世界已载入：{WORLD_DATA_SELECT}（地理与配置来自 {WORLD_MAP_FILE}）')

    def init_clock(self):
        """从世界规则读取起始时间和时间格式，创建游戏时钟和上下文对象"""
        start_time = self.b1WorldLayer.get_start_time()
        time_fmt = self.b1WorldLayer.dict.get('时间格式', 'auto')
        self.game_clock = GameClock(start_time_str=start_time, time_format=time_fmt)
        self.game_clock.apply_calendar_config(self.b1WorldLayer)
        if self.game_clock.calendar_error:
            # 历法配错/起始串解析不出年 → 显示降级了。必须看得见，不许静默。
            logger.warn(f'🗓 历法：{self.game_clock.calendar_error}')
        self.global_context = ContextHistory(game_clock=self.game_clock)
        self.event_context = ContextHistory(game_clock=self.game_clock)
        self.director_context = ContextHistory(game_clock=self.game_clock)
        logger.info(f'⏰ 游戏时钟启动 → {start_time}（格式：{time_fmt}）')

    def init_all_npcs(self):
        """从 data/B3_npc 加载所有NPC，并用世界配置覆盖截断上限"""
        npc_source_path = os.path.join(BASE_PATH, NPC_DATA_ROOT_PATH)
        npc_file_list = walk_category_files(npc_source_path)
        logger.debug(f'读取的npc文件列表{npc_file_list}')

        for npc_file in npc_file_list:
            file_npcs = load_npc_list_from_json(npc_file)
            for npc in file_npcs:
                self.all_npc_list.add(npc)

        caps = self.apply_config_caps()
        logger.debug(f'取到npc数量:{len(self.all_npc_list)}, 表上限={caps}')

    def apply_config_caps(self) -> dict:
        """
        用世界配置覆写各张 NPC 表的截断上限。

        ⚠️ 键名是 `NPC_上限_<属性名>`（如 `NPC_上限_MAX_MEMORY`），**不是**
        `NPC_记忆上限` 那套旧名。旧名与代码查的键**零交集**，那几项从来没生效过，
        只因值恰好等于类默认值才没暴露——这是本项目栽过的著名一坑。
        上限是**类属性**，所以改配置只把字典盖一遍没有用，必须重跑这段覆写；
        编辑器保存后的热重载也要走它（不然界面说"已保存并热重载"，而上限纹丝不动）。
        """
        from Schema.B3_1_npc_sch import B3NPCLayer
        for _, attr in B3NPCLayer.CAP_MAP.items():
            val = self.b1WorldLayer.get_config(f'NPC_上限_{attr}')
            if val is not None:
                setattr(B3NPCLayer, attr, int(val))
        return {t.split('(')[0]: getattr(B3NPCLayer, a)
                for t, a in B3NPCLayer.CAP_MAP.items()}

    def init_player(self):
        """加载玩家数据（优先从世界配置读取玩家文件路径）"""
        player_file = self.b1WorldLayer.get_config('玩家初始数据', 'data/player/default.json')
        current_state = load_json_to_dict(os.path.join(BASE_PATH, player_file))
        self.player = B4PlayerLayer(current_state)

        # 位置迁移：把"路径字符串"形式的旧数据就地转成坐标（**幂等**）。
        # ⚠️ 必须在 NPC 与玩家都载入之后调用——它要给**所有**实体落坐标。
        # 现在顺序成立，是因为主循环把 init_player 放在最后；改顺序要一起改。
        self.migrate_positions()

    # ===================== 位置：坐标是真源，路径是派生 =====================
    #
    # 地图（`.map`）把**坐标**当真理，`当前所处位置` 是**派生**——派生字段
    # 留着是因为引擎里有十几处要"路径"这个形状。所以 **唯一写入点** 是
    # `set_position`：它一次写两样（坐标 + 派生路径），于是两样不可能各自漂。
    # **绝不允许别处直接写 `当前所处位置`**——那正是 2026-10-08 事故的形状
    # （往真路径后面接话 → 当轮所有地段能力静默失效）。

    @property
    def player_location(self) -> str:
        """玩家所在地块的**完整路径**（由坐标解析）。"""
        return self.b1WorldLayer.path_at(self.player_state)

    @property
    def player_state(self) -> dict:
        return self.player.player_dict.get('当前状态CurrentState') or {}

    @property
    def player_location_desc(self) -> str:
        """给 LLM 看的"他在哪"——**在路上的时候终于是"在路上"了**（S3）。"""
        return self.b1WorldLayer.location_desc(self.player_state)

    @property
    def player_location_context(self) -> list:
        """地段属性链。在路上时不再静默借用最近地块（S3）。"""
        return self.b1WorldLayer.location_context_for(self.player_state)


    def set_position(self, state: dict, coord=None, name: str = ''):
        """
        写位置：`coord` 优先（坐标），否则用 `name` 查坐标。

        :return: 是否写成功。查不到坐标就**不动**（宁可原地不动，也不要一个假位置）。
        """
        if coord is None and name:
            w = self.b1WorldLayer
            nm = w.name_of(name) or name
            # **地区（东域/中州…）没有落脚点**：它的 `坐标` 只是那个圈的圆心。
            # 作者说"我要去东域"，落地就该是东域里**离我最近的地点**（`地图格式.md` §9.1）。
            # 地点原样返回（`stop_in` 对非地区是 no-op）。
            if w.is_region(nm):
                x, y = w.coord_at(state) or (None, None)
                stop = w.stop_in(nm, x, y)
                if not stop:
                    logger.warn(f'📍 去向「{nm}」是地区，而它底下**一个能停的地点都没有**'
                                f'——原地不动（数据的问题：往里面加个地点）')
                    return False
                logger.info(f'📍 去向「{nm}」是地区 → 落到里面离你最近的「{stop}」')
                nm = stop
            coord = w.place_coord(nm)
        if not coord:
            return False
        state['位置'] = [float(coord[0]), float(coord[1])]
        state['当前所处位置'] = self.b1WorldLayer.path_at(state)     # 派生，不是真源
        return True

    def set_position_on_road(self, state: dict, road: dict, t: float,
                             frm: str = None) -> bool:
        """
        把人放在**路的某一点**上（`t` 从 `frm` 那一端算起）。S3 新增，仍走
        `set_position` 落坐标——位置的真源永远只有「坐标」一样东西。

        ⚠️ `frm` 不能省：路的 `路径线` 是**从 `从` 到 `到`** 排的，而人可能正从
        反方向走。不翻转的话，"从落霞坪往天道宗走 15%"会被算成"从天道宗往回走 15%"
        ——**方向整个反了**，而坐标看起来还挺正常（实测踩到过）。

        `t ≥ 1` 等价于"到了"（落在 `frm` 对面那一端），那时「在路上」自然变假。

        ⚠️ 这是"沿路推进"的**唯一落点**。它和 `set_position` 一样只写坐标与派生路径，
        **不写任何「行进」字段**——在哪条路哪一段由坐标 + 几何**派生**（`on_road_at`），
        少一个字段就少一处"坐标说在 A、行进说在路上"的不一致（见 `地图格式.md` §1.2）。
        """
        flip = bool(frm) and frm == road.get('到')
        if t >= 1.0:
            return self.set_position(state, name=(road.get('从') if flip else road.get('到')))
        pt = self.b1WorldLayer.road_point(road, (1.0 - t) if flip else t)
        if not pt:
            return False
        return self.set_position(state, coord=pt)

    #: `行程`（N2 判的语义进度）→ **整段路程走到第几成** `frac ∈ (0, 1]`。
    #:
    #: ⚠️ 这张表是**我按中文语感猜的**，所以它的用法被限死成"安全"的：
    #:   · 认不出 / 没给 → 一律按 `frac = 1.0`（= **老口径"去向即到达"**）处理，
    #:     绝不让人卡在半路；
    #:   · 每次认不出都记一条 WARNING，好让**真机跑一轮之后照模型实际吐的词**
    #:     来定这张表，而不是照我的想象定（见 `地图实施计划.md` 的风险栏）。
    #: `行程` → 落点成数。**只认 N2 prompt 里约定的这四个协议词**。
    #: 这里原来还有一堆近义词（"抵达""中途""启程""不日"…），那是替模型猜它说到哪了——
    #: 协议词是唯一的，模型写别的就是违约，应当走兜底并记 WARN，而不是让代码替它认。
    _STAGE_FRAC = (
        (('到了',), 1.0),
        (('将到',), 0.85),
        (('半路',), 0.5),
        (('刚上路',), 0.15),
    )

    @classmethod
    def travel_frac(cls, stage: str):
        """`行程` → `frac`；**认不出返回 None**（调用方按老口径处理）。"""
        s = (stage or '').strip()
        if not s:
            return None
        for words, frac in cls._STAGE_FRAC:
            if any(w in s for w in words):
                return frac
        return None

    def travel_frac_by_segment(self, state: dict, dest: str, stage: str):
        """
        `行程` 里报的是**段名**时（"走到雪岭了"），折算成整段路程的 frac；认不出给 None。

        为什么必须先有它**再铺段数据**：段名是**作者写的**，而 `_STAGE_FRAC` 只认
        那四个进度词。认不出的退路是 `frac = 1.0` ＝ **一步到目的地**——
        段数据铺得越多，模型越容易照着段名说，于是**越容易瞬移**。

        只认**第一跳**那条路上的段：`行程` 说的正是"我上路之后走到哪儿了"，
        而他现在要走的下一跳就是第一跳。多跳路线上报的段名落在哪一跳，本来也无从知道——
        与其猜，不如认不出就交给老口径（那一步由调用方决定）。
        """
        w = self.b1WorldLayer
        if not stage or not dest:
            return None
        c = w.coord_at(state)
        onrd = w.on_road_at(*c) if c else None
        base = onrd['从'] if onrd else w.name_at(state)
        if not base:
            return None
        hops = w.route(base, w.name_of(dest) or dest)
        if len(hops) < 2:
            return None
        legs = len(hops) - 1
        road = w.road_between(hops[0], hops[1])
        for name, t0, t1 in (w.segment_ranges(road) if road else []):
            if not name:
                continue
            hit = name in stage
            if not hit:
                # 作者爱把段名叫成「雪岭段」，而 LLM 往往只说「雪岭」——
                # 去掉尾缀再试一次，否则这种最常见的说法反而认不出。
                for suf in ('段', '一带', '之地', '区'):
                    if (name.endswith(suf) and len(name) > len(suf) + 1
                            and name[:-len(suf)] in stage):
                        hit = True
                        break
            if hit:
                return (t0 + t1) / 2 / legs      # 该段的中点，折算到整段路程
        return None

    def advance_travel(self, state: dict, dest: str, frac: float, why: str = '') -> bool:
        """
        朝 `dest` 走，走到**整段路程的 `frac` 处**。

        为什么是"整段路程的第几成"而不是"当前这一跳的第几成"：
        一跳一跳来的话，去一个五跳之外的地方要玩家打五轮字。按整段算，
        `frac=0.5` 就是"走到一半"——中途那几跳**照样会显示"在路上"**，
        因为它落在某一条路上而不是某个地块里。

        落点永远**在某条路的路径线上**（`set_position_on_road`）——所以
        "在路上"由坐标 + 几何**派生**，不另存字段。

        `frac` 从哪算起：还在路上就用那条路的**起点**当基准（不是"最近地块"——
        最近地块会随着 t 越过中点而换边，于是反复说"半路"会在中点来回摆）。
        """
        w = self.b1WorldLayer
        if not dest:
            return False
        # 模型写"云梦国"、地块名却是"南疆凡人国·云梦国"——这里做**唯一性**容错
        # （精确 / 全路径 / 末段 / 段 / 子串）。多个候选一律不动：宁可不动，也不猜。
        dest_name, how = w.resolve_place(dest)
        if not dest_name:
            logger.warn(f'🚶 去向「{dest}」对不上地块：{how}——位置不动')
            return False
        if how != '精确':
            logger.info(f'🚶 去向「{dest}」→「{dest_name}」（{how}匹配）')
        c = w.coord_at(state)
        onrd = w.on_road_at(*c) if c else None
        base = onrd['从'] if onrd else w.name_at(state)
        if not base:
            logger.warn(f'🚶 去向「{dest_name}」：当前位置解析不出地块名，位置不动')
            return False
        if dest_name == base:
            logger.info(f'🚶 已在「{dest_name}」，不重复移动（位置不动）')
            return False
        hops = w.route(base, dest_name)
        if not hops:
            # **目标是真存在的地点，却一条路都不通**（数据漏了——手写一张新地图时
            # 忘连一条路太正常了）。这里**不硬失败**：直接落点 + WARNING。
            #
            # 为什么不让玩家"走不动"：那是个死胡同。他想去的地方在世界里明明存在，
            # 卡住只会让人以为游戏坏了。**数据的问题要看得见**（WARNING），
            # 但不该表现为"玩家动不了"。
            # （"这不是已知地点"那条分支已删：`resolve_place` 只会返回真实地块名。）
            ok = self.set_position(state, name=dest_name)
            if ok:
                logger.warn(f'🚶 去向「{dest_name}」**一条路都没有**（{base} 通不到它）'
                            f'——先按"到了"落点。这是**地图数据的漏**，补一条路就好了')
            return ok
        if len(hops) < 2:
            logger.info(f'🚶 去向「{dest_name}」与当前所在「{base}」之间没有可走的跳，位置不动')
            return False

        legs = len(hops) - 1
        if frac >= 1.0:
            # 到了：直接落目的地（与老口径完全一致——所以"没给行程"不会退化）
            ok = self.set_position(state, name=dest_name)
            if ok:
                logger.info(f'🚶 行进：{base} → {dest_name}（{why or "到了"}，共 {legs} 跳）')
            return ok

        pos = frac * legs
        k = min(int(pos), legs - 1)
        t = pos - k
        road = w.road_between(hops[k], hops[k + 1])
        if not road:
            return False
        ok = self.set_position_on_road(state, road, t, frm=hops[k])
        if ok:
            logger.info(f'🚶 行进：{base} → {dest_name} 走到 {int(frac * 100)}%'
                        f'（第 {k + 1}/{legs} 跳：{hops[k]} → {hops[k + 1]}，t={round(t, 2)}'
                        f'{("，" + why) if why else ""}）')
        return ok

    def move_player_to(self, dest: str, stage: str = '') -> bool:
        """
        玩家移动的**唯一落点**：LLM 只给「去向 + 行程」，坐标与派生路径由这里落。

        去向解析不出 / 走不通 → **位置不动**（宁可不动，也不要一个编出来的位置）。
        N2 若把"天道宗上空"这种自造地名当地名吐出来，就在这里被挡住并记 WARNING。
        """
        dest = (dest or '').strip()
        if not dest:
            return False
        st = self.player.player_dict.get('当前状态CurrentState')
        if not isinstance(st, dict):
            logger.warn('🧭 玩家去向：玩家没有 `当前状态CurrentState`，位置不动')
            return False
        stage = (stage or '').strip()
        frac = self.travel_frac(stage)
        if frac is None and stage:
            # 词表认不出，再试**段名**（"走到雪岭了"）——段名是作者写的，
            # 而 LLM 现在看得到"正在穿过哪一段"，照着它说的机会不小。
            frac = self.travel_frac_by_segment(st, dest, stage)
        if frac is None:
            if stage:
                # 认得出就用，认不出**退回老口径**（当"到了"）——
                # 卡在半路比多走一步糟得多。这句 WARNING 就是那张表的待办事项。
                logger.warn(f'🚶 行程「{stage}」认不出，按"到了"处理'
                            f'（不会把人卡在半路）。真跑过之后照着模型'
                            f'实际吐的词来定 `_STAGE_FRAC` / 段名')
            frac = 1.0
        if self.advance_travel(st, dest, frac, why=stage):
            logger.info(f'🧭 玩家去向：{dest} → {st.get("当前所处位置")}')
            self._carry_followers()
            return True
        # 失败原因 `advance_travel` 已经各打了一条（未知地名 / 已在当地 / 一条路都没有）
        logger.info(f'🧭 去向「{dest}」未落地，位置不动——这比编一个假位置强')
        return False

    def _find_npc(self, name: str):
        """
        静默查找 NPC（找不到返回 None，**不打 WARN**）。

        与 `B3NPCListLayer.get_npc` 的区别：那个的语义是"查不到就是异常"，
        而调用方常把它串成 `a or b or c` 逐个列表试——每试一次就刷一条假告警
        （实测每轮一条，把真告警埋掉）。这里只回答"在不在"。
        """
        if not name or self.all_npc_list is None:
            return None
        key = str(name).split('（')[0].strip() or str(name)
        for n in self.all_npc_list.npc_list:
            if name == n.name or key in n.name:
                return n
        return None

    @staticmethod
    def _frac_word(frac: float) -> str:
        """整段路程的成数 → 给 LLM 看的**进度词**（与它自己写的 `行程` 同一套词）。"""
        if frac >= 1.0:
            return '到了'
        if frac >= 0.75:
            return '将到'
        if frac >= 0.4:
            return '半路'
        return '刚上路'

    #: NPC 每轮至少往前推这么多（整段路程的成数）。LLM 每轮都可能再说一次"刚上路"，
    #: 靠它保证**只进不退**：约三轮走完一段路，而不是永远停在 15% 处。
    NPC_TRAVEL_STEP = 0.34

    def npc_move_toward(self, npc: B3NPCLayer, dest: str, stage: str = '') -> bool:
        """
        NPC 的去向落点：与玩家**同一套** `advance_travel`（能落在路上、跨轮继续走）。

        ⚠️ **同一条路只进不退**：LLM 看不到自己"在路上"（它拿到的位置永远退到最近地块），
        于是每轮都重新说一遍"刚上路"——实测白鹤连着三轮"刚上路"，坐标原地打转。
        所以进度记在 `npc_travel` 里，这一轮的落点取
        `max(它说的, 上一轮 + NPC_TRAVEL_STEP)`；换了目的地就按新说的重算。
        """
        st = npc.current_state if isinstance(npc.current_state, dict) else {}
        dest = (dest or '').strip()
        if not dest or not st:
            return False
        dest_key = self.b1WorldLayer.name_of(dest) or dest
        before = self.b1WorldLayer.location_desc(st)

        # 空转：模型常把 `去向` 写成**他自己脚下**那个地点（实测两轮连着这么写）配 `行程: 到了`。
        # ⚠️ 必须在**动 `npc_travel` 之前**返回：否则一条空转的"到了"会先把真正的
        # 在途记录 pop 掉，人就在半路永远停住（"落点失败反把账抹了"的形状）。
        if dest_key == self.b1WorldLayer.name_at(st):
            logger.info(f'🚶 NPC 去向[{npc.name}]「{dest}」就是他此刻所在，不重复移动'
                        f'（在途记录保留）')
            return False

        frac = self.travel_frac(stage)
        if frac is None and stage:
            frac = self.travel_frac_by_segment(st, dest, stage)
        if frac is None:
            frac = 1.0
        rec = self.npc_travel.get(npc.name)
        if rec and rec.get('目标') == dest_key:
            frac = min(1.0, max(frac, float(rec.get('frac') or 0) + self.NPC_TRAVEL_STEP))
        if not self.advance_travel(st, dest_key, frac, why=stage):
            logger.warn(f'🚶 NPC 去向[{npc.name}]「{dest}」落不下去'
                        f'（不是已知地点 / 没连通的路），位置不动')
            return False
        # ⚠️ 记账放在**落点成功之后**：落不下去还清账的话，一次失败的移动
        # 就把"他走到哪了"抹掉了，下一轮又从"刚上路"重来。
        if frac >= 1.0:
            self.npc_travel.pop(npc.name, None)
        else:
            self.npc_travel[npc.name] = {'目标': dest_key, 'frac': frac}
        logger.info(f'🚶 NPC 行进[{npc.name}]：{before} → '
                    f'{self.b1WorldLayer.location_desc(st)}（{stage or "到了"}）')
        self._carry_followers(npc.name)
        return True

    #: 本轮**明确跳过**的时间 ≥ 这个分钟数（1 日）时，在途记录一律按"已抵达"结清。
    #: 依据：一段路按 10 分/段、约 3 轮走完，跳过一整天没有"还没走完"的道理。
    TRAVEL_FLUSH_SKIP_MINUTES = 1440

    def resolve_travel_on_skip(self, minutes: float) -> int:
        """
        时间**明确跳过**一大段之后，把在途记录一次性结清。

        为什么需要：`npc_travel` 的进度是"每轮 +0.34"，它假设时间**连续**。
        玩家说"（一个月后）"时轮次只走了一轮、世界却过了一个月——按 frac 记账的话，
        白鹤会在路上"走"一个月。所以跳过 ≥ 1 日时，在途的人按"早到了"落点。

        :return: 结算掉的在途记录条数
        """
        minutes = float(minutes or 0)
        if not self.npc_travel or minutes < self.TRAVEL_FLUSH_SKIP_MINUTES:
            return 0
        done = 0
        for name, rec in list(self.npc_travel.items()):
            npc = self._find_npc(name)
            dest = str((rec or {}).get('目标') or '').strip()
            # 解析不出的人/目的地：记录留着没有意义，但要说出来（静默丢是更糟的一种）
            if npc is None or not dest:
                self.npc_travel.pop(name, None)
                logger.warn(f'🚶 时间跳过：在途记录「{name}」{("目标「" + dest + "」") if dest else ""}'
                            f'解析不出实体/目的地，已清账')
                continue
            st = npc.current_state if isinstance(npc.current_state, dict) else {}
            if st and self.advance_travel(st, dest, 1.0, why='时间跳过'):
                self.npc_travel.pop(name, None)
                done += 1
                logger.info(f'🚶 时间跳过 {minutes:.0f} 分：[{npc.name}] 在途按已抵达结算 → '
                            f'{self.b1WorldLayer.location_desc(st)}')
                self._carry_followers(npc.name)
            else:
                self.npc_travel.pop(name, None)
                logger.warn(f'🚶 时间跳过：[{npc.name}] 的在途记录落不了地'
                            f'（目标「{dest}」），已清账、位置不动')
        return done

    # ===================== 任务通道 =====================

    def _apply_task_channel(self, owner: str, delta: dict, dest: str = '') -> int:
        """
        消费任务通道。`owner` = 这条增量属于谁（新建任务默认执行者就是他）。

        新通道：
            "任务": {"新建": [{标题,目标,执行者,类型,期限,进度,涉及地点,涉及NPC,完成条件}],
                     "更新": [{"id","进度"}],
                     "了结": [{"id","结果"}],
                     "失败": [{"id","原因"}]}

        旧通道（**仍认，但记 WARN**）：`新任务` 一句话目标 / `任务完成` true。
        旧写法一个 NPC 只能表达一条、写新的就覆盖旧的（这正是"差事悄悄消失"的病根），
        所以要把它显式标出来，而不是让模型一直用它。

        :return: 实际改动条数
        """
        changed = 0
        now = self.game_clock.now_str()
        chan = delta.get('任务')

        # ---- 新建 ----
        raw = []
        if isinstance(chan, dict):
            raw = chan.get('新建') or chan.get('增加') or []
            if isinstance(raw, (str, dict)):
                raw = [raw]
        for it in raw:
            if isinstance(it, str):
                it = {'目标': it}
            if not isinstance(it, dict):
                logger.warn(f'任务.新建 的条目格式不对（{it!r}），忽略')
                continue
            rec = self.tasks.new(
                目标=it.get('目标') or it.get('标题') or '',
                执行者=it.get('执行者') or owner,
                标题=it.get('标题') or '',
                委托人=it.get('委托人') or '',
                类型=it.get('类型') or KIND_DELIVER,
                期限=it.get('期限') if isinstance(it.get('期限'), dict) else None,
                进度=it.get('进度') or '',
                涉及地点=it.get('涉及地点') or ([dest] if dest else []),
                涉及NPC=it.get('涉及NPC') or [],
                完成条件=it.get('完成条件') or '',
                来源='对话',
                登记时间=now,
                clock=self.game_clock,
            )
            if rec:
                changed += 1

        # ---- 更新 / 了结 / 失败 ----
        if isinstance(chan, dict):
            changed += self._task_transition(chan.get('更新'), 'update')
            changed += self._task_transition(chan.get('了结'), 'finish')
            changed += self._task_transition(chan.get('失败'), 'fail')

        # ---- 旧通道（降级兼容）----
        old_new = delta.get('新任务')
        if isinstance(old_new, dict):
            old_new = old_new.get('目标') or old_new.get('任务') or ''
        if str(old_new or '').strip():
            self._legacy_task_writes += 1
            logger.warn(f'[{owner}] 用了旧写法 `新任务`（累计 {self._legacy_task_writes} 次）'
                        f'——只能记一条、写新的会丢旧的，请改用 `任务.新建`')
            rec = self.tasks.new(目标=str(old_new).strip(), 执行者=owner,
                                 涉及地点=[dest] if dest else [],
                                 来源='对话（旧写法）', 登记时间=now,
                                 clock=self.game_clock)
            if rec:
                changed += 1

        if delta.get('任务完成') is True:
            pool = self.tasks.owned(owner)
            if pool:
                self._legacy_task_writes += 1
                logger.warn(f'[{owner}] 用了旧写法 `任务完成`（累计 {self._legacy_task_writes} 次）'
                            f'——只有布尔、点不到具体哪一条，请改用 `任务.了结` 带 id')
                self.tasks.finish(pool[0]['id'], '（旧写法了结，未记结果）', self.game_clock)
                changed += 1
            else:
                logger.warn(f'[{owner}] 写了 `任务完成: true`，但手上没有进行中的任务——忽略')

        return changed

    def _task_transition(self, items, action: str) -> int:
        """`更新` / `了结` / `失败` 三种流转共用；每项必须带 id（没有 id 就点不到具体一条）。"""
        if isinstance(items, (str, dict)):
            items = [items]
        n = 0
        for it in (items or []):
            if isinstance(it, str):
                it = {'id': it}
            if not isinstance(it, dict):
                logger.warn(f'任务.{action} 的条目格式不对（{it!r}），忽略')
                continue
            tid = it.get('id') or it.get('任务id') or it.get('编号')
            if not tid:
                logger.warn(f'任务.{action} 缺少 id，定位不到是哪一条（{it!r}）')
                continue

            if action == 'update':
                ok = self.tasks.update(str(tid), 进度=it.get('进度') or it.get('结果'))
            elif action == 'finish':
                rec = self.tasks.finish(str(tid), it.get('结果') or '', self.game_clock)
                ok = rec is not None
                if rec:
                    self._announce_task(rec)
            else:
                rec = self.tasks.fail(str(tid), it.get('原因') or it.get('结果') or '',
                                      self.game_clock)
                ok = rec is not None
                if rec:
                    self._announce_task(rec)
            n += 1 if ok else 0
        return n

    def _announce_task(self, rec: dict):
        """
        任务结束是**公开事实**，必须落进世界状态。

        否则"他办成了"只活在这一轮的日志和台词里——下一轮没有任何一层记得，
        玩家也永远看不到"那件事有结果了"。
        """
        verb = '办成' if rec.get('状态') == DONE else '失败'
        text = f'任务「{rec.get("标题")}」{verb}：{rec.get("结果") or "（无结论）"}'
        try:
            self.apply_world_state_delta({'增加': [text], '删减': []},
                                         f'任务{rec.get("状态")}')
        except Exception as e:
            logger.warn(f'任务结果写世界状态失败：{type(e).__name__}: {e}')

    def settle_task_deadlines(self) -> int:
        """按当前游戏时间结算逾期任务，并把结果写进世界状态。"""
        overdue = self.tasks.settle_expired(self.game_clock.elapsed_minutes, self.game_clock)
        for rec in overdue:
            self._announce_task(rec)
        return len(overdue)

    def sync_preset_tasks(self, npc_present: list = None) -> int:
        """
        按触发条件激活世界预置任务（不花 LLM）。

        地点用**短名 + 全路径**一起匹配：预设里写"青木山脉"或写全路径都该能命中。
        """
        presets = self.b1WorldLayer.dict.get('预设任务') or []
        if not presets:
            return 0

        places = []
        loc = self.player_location
        if loc:
            places.append(loc)
            for name, path in (self.b1WorldLayer.place_paths() or {}).items():
                if path and (loc == path or str(loc).startswith(str(path) + '·')):
                    places.append(name)

        made = self.tasks.sync_presets(presets, places, npc_present or [],
                                       登记时间=self.game_clock.now_str(),
                                       clock=self.game_clock)
        return len(made)

    def apply_npc_grounded_delta(self, npc: B3NPCLayer, delta: dict,
                                 label: str = '') -> B3NPCLayer:
        """
        应用 NPC 增量：**位置 / 任务 / 随行这三类"代码的活"先摘出来，剩下的才当状态表应用**。

        | 字段（与 `增加` / `删减` 并列） | 语义 | 代码做什么 |
        |---|---|---|
        | `当前所处位置`（旧写法） | 地名或完整路径 | 当"去向·到了"落坐标 |
        | `去向` + `行程` | 目的地 + 走到哪了 | `advance_travel` 落在路上（与玩家同一套） |
        | `任务` | `{新建/更新/了结/失败}` | 进任务表（见 `_apply_task_channel`） |
        | `新任务`（旧） | 一句话目标 | 等价"任务.新建"，记 WARN 提示改写法 |
        | `任务完成`（旧） | `true` | 等价"任务.了结"，记 WARN 提示改写法 |
        | `跟随` | `"玩家"` 或 NPC 名 | 记进 `follow_map`；空 / `false` = 不跟了 |
        | `同行` | NPC 名列表 | 这些人跟随本 NPC（"我带着他们走"） |

        `位置`（坐标）/ `行进` 一律丢：坐标是代码的活——LLM 给坐标等于跳过地图规则。
        """
        delta = delta if isinstance(delta, dict) else {}
        clean, pos_field, dropped = extract_position_dest(delta)

        # 去向 / 行程：`去向`（新写法）优先；`当前所处位置`（旧写法）等价于"到了"
        dest = str(delta.get('去向') or pos_field or '').strip()
        stage = str(delta.get('行程') or '').strip()
        if pos_field and not delta.get('去向'):
            stage = stage or '到了'

        # 任务：新通道 `任务{新建/更新/了结/失败}`；旧通道 `新任务` / `任务完成` 仍认
        self._apply_task_channel(npc.name, delta, dest)

        # 状态表（其余字段）先应用；位置与随行随后处理
        has_body = bool(clean['增加'] or clean['删减'])
        new = apply_npc_delta(npc, clean) if has_body else npc

        # 随行：`跟随`（我跟着谁）与 `同行`（谁跟着我）
        if '跟随' in delta or '跟随玩家' in delta:
            follow = delta.get('跟随')
            if follow is None:                       # 兼容 `跟随玩家: true/false`
                follow = '玩家' if delta.get('跟随玩家') else ''
            if isinstance(follow, str):
                follow = follow.strip()
            elif follow is True:
                follow = '玩家'
            else:
                follow = ''
            if follow:
                self.follow_map[new.name] = follow
                logger.info(f'🚶 [{new.name}] 跟随「{follow}」')
            elif self.follow_map.pop(new.name, None):
                logger.info(f'🚶 [{new.name}] 不再跟随')
        mates = delta.get('同行')
        if isinstance(mates, str):
            mates = [mates]
        if isinstance(mates, dict):
            mates = list(mates.keys())
        for mate in (mates or []):
            target = self._find_npc(str(mate).strip())
            if target is None:
                logger.warn(f'🚶 [{new.name}] 要带的人「{mate}」不在名册里，忽略')
                continue
            self.follow_map[target.name] = new.name
            logger.info(f'🚶 [{target.name}] 随「{new.name}」同行')

        # 位置最后落（落完要带上跟随他的人）
        if dest:
            self.npc_move_toward(new, dest, stage)
        if dropped:
            logger.info(f'[{label or new.name}] 位置字段 {sorted(set(dropped))} '
                        f'由坐标落点取代；坐标 / 行进类字段一律丢弃')
        return new

    def _carry_followers(self, who: str = '玩家') -> int:
        """
        `who` 移动落盘后，**跟随他的人一起落到他所在地块**。

        跟随不模拟行程（不必每跳都算一遍），直接落到目标所在地块——"位置能刷新"就够了。
        落点仍走 `set_position`（坐标是真源），落不下去就留着不动。
        """
        followers = [k for k, v in self.follow_map.items() if v == who]
        if not followers:
            return 0
        if who == '玩家':
            dest = self.b1WorldLayer.name_at(self.player_state)
        else:
            src = self._find_npc(who)
            dest = self.b1WorldLayer.name_at(src.current_state) if src else ''
        if not dest:
            return 0
        moved, gone = [], []
        for name in sorted(followers):
            npc = self._find_npc(name)
            if npc is None:
                gone.append(name)          # 名册里没有了（数据换过）→ 清掉关系
                continue
            if npc.name == who:
                continue
            if self.set_position(npc.current_state, name=dest):
                moved.append(npc.name)
        for name in gone:
            self.follow_map.pop(name, None)
        if moved:
            logger.info(f'🚶 随行 {len(moved)} 人跟着「{who}」落到「{dest}」：'
                        f'{[n.split("（")[0] for n in moved]}')
        return len(moved)

    def migrate_positions(self):
        """
        把旧档/旧数据里的 `当前所处位置`（路径字符串）迁移成坐标。

        读档与 init 都过一遍——**幂等**：已经有 `位置` 就只刷新派生路径。
        """
        moved, stuck = 0, []
        targets = [(self.player.player_dict.get('当前状态CurrentState') or {}, '玩家')]
        for n in (self.all_npc_list.npc_list if self.all_npc_list else []):
            targets.append((n.current_state or {}, n.name))
        for st, who in targets:
            if self.set_position(st, coord=self.b1WorldLayer.coord_at(st)):
                moved += 1
                continue
            raw = (st.get('当前所处位置') or '').strip()
            if self.set_position(st, name=raw):
                moved += 1
            else:
                stuck.append(f'{who}:{raw or "(空)"}')
        if stuck:
            logger.warn(f'位置迁移：{len(stuck)} 个实体的位置解析不出，保持原样：{stuck[:5]}')
        logger.info(f'位置迁移完成：{moved} 个实体已落到坐标')



    # ===================== 小工具 =====================

    # ===================== 开局 / 重载 / 存档落盘 =====================

    def new_game(self):
        """
        开新游戏：把**这一局跑出来的**状态整个换掉，回到刚开局的样子。

        清什么：
          · NPC 名册 —— ⚠️ 必须 `new_list()`。`init_all_npcs` 是往列表里 `add`，
            不清就是**两局的 NPC 叠在一起**（名字还重复，`get_npc` 只会碰到先来的那个）。
          · **事件名单（`event_npc` / `related_npc`）** —— 这一局锁定过的人不能带过去。
          · **这一局跑出来的账本**：`tasks`（任务表）/ `npc_travel`（在途）/ `follow_map`（随行）。
            ⚠️ 这三个**都会被"强制并入名单"消费**（`_with_task_npcs` 不看距离），
            漏一个就是"新局里冒出上一局的人"。实测：读档迁进一条白鹤的差事，再开新游戏，
            白鹤就在落霞坪被拉进了南域的场景里。
          · 后台产出队列 —— 上一局的心跳产出与新的世界不兼容（与读档/撤回同一条理由）。
          · 世界 / 时钟 / 三个上下文 / 玩家 —— 由下面四个 `init_*` 重建。

        **不清**磁盘上的东西：`save/` 里的存档一条不动（那是上一局的成果，要删得
        显式 `/save del`），世界数据本身也不动。

        ⚠️ 这会**换掉 `self.b1WorldLayer` 这个对象**（`init_world` 新建的）。
        引擎侧那些把世界层存进字段的层（N7/N8/心跳）必须跟着**重新接线**，
        见 `GameEngine._wire()`——不重接就是"拿着上一局的世界在跑"，而且不报错。
        """
        self.all_npc_list.new_list()
        self.event_npc.new_list()
        self.related_npc.new_list()
        self.tasks = TaskStore()
        self._legacy_task_writes = 0
        self.npc_travel = {}
        self.follow_map = {}
        self.deferred_queue.clear()
        self.init_world()
        self.init_clock()
        self.init_all_npcs()
        self.init_player()
        # 清点一次：新局里**这些账本必须全是 0**。漏清任何一个，都会表现为
        # "新局里冒出上一档的人/事"，而那种现象很难从界面反推原因——所以让它自己喊出来。
        logger.info(
            '🆕 新局清点：任务%d / 在途%d / 随行%d / 事件NPC%d / 关联NPC%d / '
            '事件上下文%d条 / 幕后%d条 / 全局%d条'
            % (len(self.tasks.open_tasks()), len(self.npc_travel), len(self.follow_map),
               len(self.event_npc.npc_list), len(self.related_npc.npc_list),
               len(self.event_context.context), len(self.director_context.context),
               len(self.global_context.context))
        )

    def reload_world(self) -> dict:
        """
        把 `世界.map` 重新读进来，盖到**活着的**世界字典上（编辑器保存后用）。

        刻意**只**盖 `.map` 拥有的那几把键（见 `map_io.apply_map`）。整份重读 JSON
        是不行的——`世界状态列表` 是**运行期在长的**（N9a 每轮可能追加新状态），
        重读会把的它抹回开局，而那种丢失事后根本查不出来。

        几何早就算好的派生缓存必须清掉：不清就是"地图改了，世界还记得旧路"。

        ⚠️ 调用方要保证**当前没有回合在跑**（`web/server.py` 的保存接口挡了这条）。

        :return: **按"改完什么时候生效"分三类**。以前只回一句"已重载"，那是把三种
                 情况说成了一种——用户会以为改了 NPC 上限就生效，然后奇怪它为什么没变。
        """
        map_path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
        fresh = map_io.load_world_map(map_path)
        d = self.b1WorldLayer.dict
        before = len(d.get('世界状态列表') or [])
        # `游戏开始时间` / `时间格式` 是顶层键，**不在 `世界配置` 里**——只比
        # `世界配置` 的话，改了开局时刻会被算成"没有变动"，于是界面上一个字都不提。
        watch_before = {k: d.get(k) for k in ('游戏开始时间', '时间格式')}
        watch_before.update({f'配置:{k}': v
                             for k, v in (d.get('世界配置') or {}).items()})
        for k in ('世界地点列表', '世界配置', '路列表', '地图几何'):
            if fresh.get(k) is not None:
                d[k] = fresh[k]
        for k in ('游戏开始时间', '时间格式'):
            if fresh.get(k) is not None:
                d[k] = fresh[k]
        # 派生缓存全清：不清就是"地图改了，世界还记得旧路"
        for cache in ('_depths', '_paths', '_name_by_path', '_adj', '_roadtbl',
                      '_leaves', '_attrsn'):
            if hasattr(self.b1WorldLayer, cache):
                delattr(self.b1WorldLayer, cache)

        # 上限是**类属性**，盖字典不会自己生效——必须重跑覆写
        self.apply_config_caps()
        self.migrate_positions()

        after = len(d.get('世界状态列表') or [])
        watch_now = {k: d.get(k) for k in ('游戏开始时间', '时间格式')}
        watch_now.update({f'配置:{k}': v
                          for k, v in (d.get('世界配置') or {}).items()})
        changed = sorted(k for k in set(watch_before) | set(watch_now)
                         if watch_before.get(k) != watch_now.get(k))
        logger.info(f'世界地图已重载：{len(fresh.get("世界地点列表") or {})} 个根地块'
                    f'｜世界状态 {before} → {after} 条｜变动 {changed}')

        # 「游戏开始时间 / 时间格式」改了不算立即生效：时钟对象开局就建好了。
        # 「玩家初始数据」要下一次开局才读。「生命值体系」每轮现读，算立即生效。
        deferred = [k for k in changed
                    if k in ('玩家初始数据', '游戏开始时间', '时间格式')]
        now = [f'地点 {len(fresh.get("世界地点列表") or {})} 根',
               f'路 {len(fresh.get("路列表") or [])} 条',
               '几何']
        for k in changed:
            if k in deferred:
                continue
            kk = k.split(':', 1)[-1]
            # 上限是"类属性能改、已经长出来的不会缩回去"——说清楚，别让人以为
            # 改小了就立刻把记忆截掉
            now.append(f'配置 {kk}' + ('（只影响之后新长的数据）'
                                       if kk.startswith('NPC_上限_') else ''))
        return {'立即生效': now, '需重启': deferred,
                '世界状态条数': [before, after]}

    def wait_for_saves(self, timeout: float = 30.0) -> bool:
        """
        等待所有后台存档线程写完。**退出前必须调用。**

        写盘放在后台线程是为了不卡住主循环，但线程是 daemon 的——
        **进程一退，没写完的存档就没了**。`/save` 之后紧接着敲 `exit`
        （或者直接 Ctrl-C）就会踩到这条。体积小的存档通常几十毫秒就完事。

        :return: 是否全部写完
        """
        pending = [(n, t) for n, t in self._save_threads.items() if t.is_alive()]
        if not pending:
            return True

        self._out('info', f'⏳ 等待 {len(pending)} 个存档写完：'
                          f'{"、".join(n for n, _ in pending)}')
        deadline = time.time() + timeout
        for name, t in pending:
            t.join(timeout=max(0.1, deadline - time.time()))
            if t.is_alive():
                self._out('error', f'⚠️  存档 [{name}] 超时未完成，可能未写入完整')
                logger.error(f'存档线程超时：{name}')
                return False
        self._out('info', '✅ 存档已全部落盘')
        return True

    def add_context_all(self, text: str):
        """同时写入全局上下文和事件上下文（玩家输入、地段传播等AI需要处理的内容）"""
        self.global_context.add_context(text)
        self.event_context.add_context(text)

    def add_global_context(self, text: str):
        """仅写入全局上下文（NPC回应等仅供玩家查看的内容，不给AI处理）"""
        self.global_context.add_context(text)

    def add_director_context(self, text: str):
        """写入导演上下文（N9产出的幕后信息，仅供LLM层全局评估，不向玩家暴露）"""
        self.director_context.add_context(text)

    def snapshot(self) -> dict:
        """返回当前全部运行时状态的深拷贝快照，供 `!!` 回滚使用。"""
        return {
            'global_context': (deepcopy(self.global_context.context), self.global_context.count),
            'event_context': (deepcopy(self.event_context.context), self.event_context.count),
            'director_context': (deepcopy(self.director_context.context), self.director_context.count),
            'event_npc': deepcopy(self.event_npc.npc_list),
            'related_npc': deepcopy(self.related_npc.npc_list),
            'all_npc_list': deepcopy(self.all_npc_list.npc_list),
            'player': deepcopy(self.player.player_dict),
            'world': deepcopy(self.b1WorldLayer.dict),
            'game_clock': deepcopy(self.game_clock.to_dict()),
            'follow_map': deepcopy(self.follow_map),
            'tasks': deepcopy(self.tasks.to_dict()),
            'npc_travel': deepcopy(self.npc_travel),
        }

    def restore(self, snap: dict):
        """从 snapshot() 产出的快照全量恢复运行时状态。"""
        # 清空后台心跳队列（旧心跳基于回滚前的状态，与快照不兼容）
        self.deferred_queue.clear()

        self.game_clock = GameClock.from_dict(snap['game_clock'])
        self.global_context = ContextHistory(snap['global_context'][0], snap['global_context'][1], game_clock=self.game_clock)
        self.event_context = ContextHistory(snap['event_context'][0], snap['event_context'][1], game_clock=self.game_clock)
        self.director_context = ContextHistory(snap['director_context'][0], snap['director_context'][1], game_clock=self.game_clock)
        self.event_npc = B3NPCListLayer()
        for n in snap['event_npc']:
            self.event_npc.add(n)
        self.related_npc = B3NPCListLayer()
        for n in snap['related_npc']:
            self.related_npc.add(n)
        self.all_npc_list = B3NPCListLayer()
        for n in snap['all_npc_list']:
            self.all_npc_list.add(n)
        self.player = B4PlayerLayer(snap['player'])
        self.b1WorldLayer = B1WorldLayer(snap['world'])
        self.follow_map = dict(snap.get('follow_map') or {})
        self.tasks = TaskStore().from_dict(snap.get('tasks') or {})
        self.npc_travel = dict(snap.get('npc_travel') or {})

    def sync_npc_to_lists(self, updated_npc: B3NPCLayer):
        """
        将更新后的NPC按名字同步到 event_npc 和 all_npc_list。
        通过名字精确匹配定位旧实例并原地替换。
        同步前自动截断列表字段（observation/memory）。
        """
        updated_npc.cap_fields()

        for i, n in enumerate(self.event_npc.npc_list):
            if n.name == updated_npc.name:
                self.event_npc.npc_list[i] = updated_npc
                break
        for i, n in enumerate(self.all_npc_list.npc_list):
            if n.name == updated_npc.name:
                self.all_npc_list.npc_list[i] = updated_npc
                break

    # ===================== NPC 流水线 =====================

    def consume_deferred(self, n9_director=None) -> bool:
        """
        N0：消费后台心跳队列中的所有产出。
        收集摘要文本，统一打印一行简讯，避免刷屏。

        :param n9_director: N9_Director 实例
        :return: True 表示消费了至少一条
        """
        messages = self.deferred_queue.get_all()
        if not messages:
            return False

        # 收集摘要
        rule_events = []      # N9a 世界事件摘要
        npc_actions = []      # N9c NPC行为计数
        spread_count = 0      # N9c 传播条数

        for msg in messages:
            dtype = msg.get('data_type', '')

            if dtype == 'N9a':
                summary = self._consume_n9a(msg, n9_director)
                if summary:
                    rule_events.append(summary)

            elif dtype == 'N9b':
                self._consume_n9b(msg, n9_director)

            elif dtype == 'N9c':
                a, s = self._consume_n9c(msg)
                npc_actions.extend(a)
                spread_count += s

        # 打印一行简讯
        parts = []
        if rule_events:
            parts.append('🌍 ' + '；'.join(rule_events[:2]))
            if len(rule_events) > 2:
                parts[-1] += f' (+{len(rule_events)-2}条世界变化)'
        if npc_actions:
            names = '、'.join(npc_actions[:3])
            s = f'🎬 {names}'
            if len(npc_actions) > 3:
                s += f'等{len(npc_actions)}个NPC有所行动'
            else:
                s += '有所行动'
            parts.append(s)
        if spread_count:
            parts.append(f'📡 {spread_count}条情报在传播')

        if parts:
            # 幕后简讯：**纯文本 + 语义标签**，ANSI 由 `terminal_output` 加
            #（ANSI 码是终端的事，不是数据的事——否则同一份输出推到网页会被污染）
            self._out('dim', f'· {"  |  ".join(parts)}')

        logger.info(f'N0 消化完成：{len(messages)} 条后台消息')
        return True

    #: 世界状态（公开事实）**每轮最多新增几条**。几个 NPC 各自的 N6 会各写一份同一件事，
    #: 自由文本没法语义去重（"掌力震裂大阵" 与 "护山大阵灵光黯淡" 是两条），
    #: 所以按轮次设个闸：超出的记日志丢掉，而不是让这个列表每轮涨三条。
    MAX_WORLD_FACTS_PER_ROUND = 2

    def apply_world_state_delta(self, delta: dict, label: str = '') -> tuple:
        """
        世界状态（**公开事实**）增删的**唯一写入点**。

        `世界状态列表` = "这个世界当前已经发生、且人人可见的事实"账本。两个来源都走这里：
        后台 N9a（演变规则触发）与前台 N6（本轮收尾）。**不许再往别处 append 这个列表**——
        两处各写一套正是"跨层口径分叉"。

        ⚠️ 只收**已发生的事实陈述**（≤30 字，如"无名一掌震得玄清峰云层倒卷"），
        不收主观评价、不收只有当事人知道的事。写进来的条目**下一轮会喂回 N45/N6**，
        所以它是"玩家/NPC 的动作真的发生了"唯一的落盘处。

        :param delta: `{"增加": ["事实", ...], "删减": ["事实", ...]}`
        :return: (added, removed) 实际生效的条目
        """
        delta = delta if isinstance(delta, dict) else {}
        facts = self.b1WorldLayer.dict.setdefault('世界状态列表', [])
        added, removed = [], []
        for item in (delta.get('增加') or []):
            s = str(item or '').strip()
            if s and s not in facts and s not in added:
                added.append(s)
        for item in (delta.get('删减') or []):
            s = str(item or '').strip()
            if s and s in facts:
                facts.remove(s)
                removed.append(s)

        cap = self.MAX_WORLD_FACTS_PER_ROUND
        keep, dropped = added[:cap], added[cap:]
        facts.extend(keep)
        if keep:
            tag = f'（{label}）' if label else ''
            logger.info(f'🌍 世界状态 +{len(keep)} 条{tag}：{keep}｜共 {len(facts)} 条')
        if dropped:
            logger.info(f'🌍 世界状态：超出每轮上限({cap})，丢弃 {len(dropped)} 条：{dropped}')
        if removed:
            logger.info(f'🌍 世界状态 -{len(removed)} 条：{removed}')
        return keep, removed

    def _consume_n9a(self, msg: dict, n9_director):
        """
        消费 N9a：写入世界状态和上下文，不打印详细叙事。
        返回一句话摘要。
        """
        rules = msg.get('N9a_规则触发', []) or []
        summaries = []

        for t in rules:
            rule_text = t.get('规则原文', '')
            valid_rules = self.b1WorldLayer.dict.get('世界演变规则列表', [])
            if rule_text in valid_rules:
                rule_idx = valid_rules.index(rule_text)
            else:
                rule_idx = None

            if n9_director:
                if rule_idx is not None:
                    n9_director.triggered_rules[rule_idx] = t.get('进度', '初期')
                n9_director._last_rule_trigger_elapsed = getattr(getattr(self, 'game_clock', None), '_elapsed', 0)

            # 世界状态写入只有一条通道（见 `apply_world_state_delta`）
            self.apply_world_state_delta(t.get('世界状态变更') or {}, 'N9a')

            progress = t.get('进度', '')
            public_view = t.get('玩家可见叙述', '')[:300]
            behind_view = t.get('幕后详情', '')[:300]

            if public_view and public_view.strip() and public_view != '暂无公开迹象':
                self.add_context_all(f'世界事件（{progress}）：{public_view}')
            if behind_view:
                self.add_director_context(f'世界事件幕后（{progress}）：{behind_view}')

            short = ''
            if public_view and public_view.strip() and public_view != '暂无公开迹象':
                short = public_view[:15].rstrip('，。！？')
            summaries.append(short or rule_text[:20].rstrip('——'))

        return '；'.join(summaries) if summaries else None

    def _consume_n9b(self, msg: dict, n9_director):
        """
        消费 N9b：仅写入上下文和缓存，不打印。
        """
        narrations = msg.get('N9b_环境叙事', []) or []
        for n in narrations:
            if not n:
                continue
            self.add_context_all(f'旁白：{n}')
        if n9_director and narrations:
            n9_director.recent_narrations.extend(narrations)
            if len(n9_director.recent_narrations) > 20:
                n9_director.recent_narrations = n9_director.recent_narrations[-20:]
            for n in narrations:
                n9_director.narrated_themes.add(n[:15])
            if len(n9_director.narrated_themes) > 50:
                n9_director.narrated_themes = set(list(n9_director.narrated_themes)[-25:])

    def _consume_n9c(self, msg: dict):
        """
        消费 N9c：应用NPC增量，不打印详细行动描述。
        返回 (有行动的NPC名字列表, 传播条数)
        """
        acted_names = []

        npc_actions = msg.get('NPC自主行为', {}) or {}
        for npc_name, action_data in npc_actions.items():
            action_text = action_data.get('动作', '') or ''
            npc_delta = action_data.get('NPC增量')

            npc = self.all_npc_list.get_npc(npc_name)
            if npc is None:
                logger.warn(f'N9c队列NPC [{npc_name}] 未能匹配')
                continue
            if npc_delta:
                # 位置字段走「地名 → 代码落坐标」（幕后行动让 NPC 走位就靠这条）
                npc = self.apply_npc_grounded_delta(npc, npc_delta, f'N9c[{npc_name}]')
                self.sync_npc_to_lists(npc)
            if action_text:
                obs_entry = f'（幕后行动）{action_text}'
                if obs_entry not in npc.observation:
                    npc.observation.append(obs_entry)
                acted_names.append(npc.name)
                logger.info(f'📦 [{npc.name}] {action_text}')
            npc.cap_fields()

        spread_count = 0
        for spread in (msg.get('剧情级传播', []) or []):
            info = spread.get('信息', '')
            method = spread.get('方式', '')
            for npc_name in (spread.get('到达NPC', []) or []):
                npc = self.all_npc_list.get_npc(npc_name)
                if npc:
                    obs_entry = f'（情报·{method}）{info}'
                    if obs_entry not in npc.observation:
                        npc.observation.append(obs_entry)
                    npc.cap_fields()
            spread_count += 1

        return acted_names, spread_count

    #: 喂给 N6 的"可用地点名"要下钻到第几层。6 层够到最深的节点
    #: （`苍云大陆·南域·十万大山·上古遗迹·破云墟·破云墟入口`），再深没有。
    PLACE_LIST_MAX_DEPTH = 6

    def known_places(self) -> list:
        """
        给 LLM 的**地点名白名单**（全路径列表）。

        N2 早就有一份（`move_to` 只能从中挑），N6 **以前没有**——于是它只能凭记忆拼路径，
        把 `天道宗·执法堂·天刑殿` 写成 `天道宗·天刑殿` → `name_of` 查不到 → 静默落不下去。
        这里按同一口径补一份。

        ⚠️ 给的是**地名（语义）**，不是坐标（几何）——"几何只给眼睛"那条不管它。
        ⚠️ 缓存挂在**世界层对象**上（`id`）而不是 GC 上：编辑器保存会换地基，
        换了对象就重算，不会拿着一份过期地点表去校验新地图。
        """
        key = id(self.b1WorldLayer)
        cache = getattr(self, '_place_cache', None)
        if cache is None or cache[0] != key:
            cache = (key, self.b1WorldLayer.list_location_paths(
                max_depth=self.PLACE_LIST_MAX_DEPTH))
            self._place_cache = cache
        return cache[1]

    def world_facts(self) -> list:
        """当前世界状态（公开事实）。喂回 N45/N6——"已经发生的事"要能影响后续态度。

        ⚠️ 目前是**全量**喂给每个 NPC，等于把局部事件当世界常识（审查项 A3，未修）。
        """
        return list(self.b1WorldLayer.dict.get('世界状态列表') or [])

    def world_rules(self) -> list:
        """
        这个世界的**硬性常识**（`世界规则列表`：境界体系、寿元、盟约约束…）。

        给 N6 的两条链用——那是"人人皆知"的一层。
        ⚠️ **`世界知识列表` 不走这里**：那是秘辛，只进 N9 导演（见 heartbeat）。
        """
        return list(self.b1WorldLayer.dict.get('世界规则列表') or [])

    def process_npc_pipeline(self, npc_name: str, npc: B3NPCLayer, should_print: bool,
                             n45_layer, n6_update, perception_msg: str):
        """
        单个NPC的完整 N45→N6 流水线，供 ThreadPoolExecutor 并行调用。

        流水线：N45（推理+回应，合并了原N4/N5）→ N6（收尾+耗时+世界事实）

        :param perception_msg: N3感知层分发的带方向消息（"玩家对你说：..."或"玩家对[名字]说：..."）
        :return: (npc_final, response, should_print, player_delta, narration, minutes,
                  world_delta)
        """
        tag = '' if should_print else '（潜在）'

        # N45 理解回应层（不接受玩家实例：推理更新待解释事件 → 据此生成回应+内心想法）
        # `world_facts`：把上一轮落盘的公开事实喂进去，NPC 才不会把"已经发生的事"当"待处理的事"
        npc_after, response_text, inner_thought = n45_layer.response_for_npc(
            npc, perception_msg, self.global_context.context,
            world_facts=self.world_facts(),
        )
        logger.info(f'[{npc_name}]{tag} 理解回应完成')
        if should_print:
            logger.info(f'[{npc_name}] 回应：{response_text}')

        # 在路上的话，把"走到哪了"一并告诉它——否则它看不到自己在路上（拿到的位置
        # 永远退到最近地块），会每轮重新说一次"刚上路"，人就在原地打转。
        # ⚠️ 档位取 `npc_travel` 的 `frac`（**相对目的地**），不用 `location_desc`：
        # 后者按"路的哪一端"报档，方向一反就会把"刚上路"说成"将到"。
        coord = self.b1WorldLayer.coord_at(npc_after.current_state)
        on_road = self.b1WorldLayer.on_road_at(*coord) if coord else None
        rec = self.npc_travel.get(npc_after.name)
        if on_road and rec:
            where = (f'在「{on_road.get("路") or "路上"}」上，朝「{rec.get("目标", "")}」方向，'
                     f'已经{self._frac_word(float(rec.get("frac") or 0))}')
        elif on_road:
            where = self.b1WorldLayer.location_desc(npc_after.current_state)
        else:
            where = ''

        # N6 更新层（唯一接受玩家实例的层，收尾落实所有客观操作 + 估算本轮耗时）
        # `ground`：NPC 的 `去向`/`当前所处位置` 当**地名**落坐标，而不是直接写派生字段
        # `task` ：手上有差事的NPC每轮都看得到它，差事才能自己往下走
        npc_final, player_delta, narration, minutes, world_delta = n6_update.update_for_npc(
            npc_after, self.player, perception_msg, inner_thought,
            ground=self.apply_npc_grounded_delta,
            task=self.npc_tasks.get(npc.name) or self.npc_tasks.get(npc_after.name),
            # 手上全部任务（一行一条、带 id）：模型要拿 id 才能准确了结某一条，
            # 而不是永远只能了结"最老的那条"
            tasks=self.tasks.describe_all(npc_after.name),
            # 玩家自己接的任务也让这层看见：跟 NPC 在一起时 N6-Player 不跑，
            # 没有第二个地方能据实了结它。
            player_tasks=self.tasks.describe_all('玩家'),
            where=where,
            places=self.known_places(),
            world_facts=self.world_facts(),
        )

        return (npc_final, response_text, should_print, player_delta,
                narration, minutes, world_delta)

    # ===================== 存档 / 读档 =====================

    def build_save_state(self, name: str) -> dict:
        """将当前运行时状态打包为可序列化的字典"""
        return {
            "meta": {
                "save_name": name,
                "version": SAVE_VERSION,
                "timestamp": time_int.ts_to_readable(time_int.get_minute_ts()),
                "game_time": self.game_clock.now_str(),
                "player_name": self.player.player_dict.get("玩家名称name", "未知"),
                "location": self.player.player_dict.get("当前状态CurrentState", {}).get("当前所处位置", "未知"),
                "round_count": self.global_context.count,
                "event_round_count": self.event_context.count,
            },
            "global_context": {
                "count": self.global_context.count,
                "context": self.global_context.context,
            },
            "event_context": {
                "count": self.event_context.count,
                "context": self.event_context.context,
            },
            "director_context": {
                "count": self.director_context.count,
                "context": self.director_context.context,
            },
            "event_npc_list": self.event_npc.to_dict(),
            "related_npc_list": self.related_npc.to_dict(),
            "all_npc_list": self.all_npc_list.to_dict(),
            "player_dict": self.player.player_dict,
            "world_dict": self.b1WorldLayer.dict,
            "game_clock": self.game_clock.to_dict(),
            "n9_director_state": getattr(self, '_n9_state', {}),
            "follow_map": self.follow_map,
            "tasks": self.tasks.to_dict(),
            "npc_travel": self.npc_travel,
        }

    def rebuild_game_state(self, save_dict: dict):
        """从存档字典重建 self 的全部运行时状态"""
        # 清空后台心跳队列（旧数据与读档后的世界不兼容）
        self.deferred_queue.clear()

        # 时钟先恢复（上下文需要它）
        self.game_clock = GameClock.from_dict(save_dict.get("game_clock", {}))
        self.global_context = ContextHistory(
            context=save_dict["global_context"]["context"],
            count=save_dict["global_context"]["count"],
            game_clock=self.game_clock,
        )
        self.event_context = ContextHistory(
            context=save_dict["event_context"]["context"],
            count=save_dict["event_context"]["count"],
            game_clock=self.game_clock,
        )
        self.director_context = ContextHistory(
            context=save_dict["director_context"]["context"],
            count=save_dict["director_context"]["count"],
            game_clock=self.game_clock,
        )

        def _rebuild_npc_list(npc_dicts: list) -> B3NPCListLayer:
            lst = B3NPCListLayer()
            for d in npc_dicts:
                lst.add(B3NPCLayer(d))
            return lst

        self.all_npc_list = _rebuild_npc_list(save_dict["all_npc_list"])
        self.event_npc = _rebuild_npc_list(save_dict["event_npc_list"])
        self.related_npc = _rebuild_npc_list(save_dict["related_npc_list"])

        self.player = B4PlayerLayer(save_dict["player_dict"])
        self.b1WorldLayer = B1WorldLayer(save_dict["world_dict"])
        # 历法来自世界配置（`历法` 块）：时钟先于世界层构造，所以这里补挂一次
        self.game_clock.apply_calendar_config(self.b1WorldLayer)
        if self.game_clock.calendar_error:
            logger.warn(f'🗓 历法：{self.game_clock.calendar_error}')
        # 随行关系、在途进度（旧档没有这些键 → 空，不升 SAVE_VERSION）
        self.follow_map = dict(save_dict.get("follow_map") or {})
        self.npc_travel = dict(save_dict.get("npc_travel") or {})

        # 任务表：新档读 `tasks`；旧档只有 `npc_tasks`（{NPC名:{目标,去向,行程}}）→ 就地迁移
        self.tasks = TaskStore()
        if save_dict.get("tasks"):
            self.tasks.from_dict(save_dict["tasks"])
        elif save_dict.get("npc_tasks"):
            self.tasks.migrate_legacy(save_dict["npc_tasks"],
                                      登记时间=self.game_clock.now_str(),
                                      clock=self.game_clock)

        # N9 导演层状态（由主循环在 load 后取回使用）
        self._n9_state = save_dict.get("n9_director_state", {})

    # ===================== 存档辅助 =====================

    def _extra_save_files(self) -> dict[str, str]:
        """构建额外存档文件映射（日志等），返回 {目标文件名: 源绝对路径}"""
        extra = {}
        log_path = log_mod.get_log_path()
        if os.path.isfile(log_path):
            # 日志文件名不变，直接放入存档文件夹
            extra[os.path.basename(log_path)] = log_path
        return extra

    def _do_save(self, name: str):
        """执行存档（后台写入，不阻塞主循环）"""
        # 同名存档正在进行中 → 跳过
        prev = self._save_threads.get(name)
        if prev and prev.is_alive():
            self._out('error', f'⚠️  存档 [{name}] 正在进行中，请稍后')
            return

        # 先消费队列，确保快照包含最新的心跳结果
        self.consume_deferred()

        state = self.build_save_state(name)
        extra = self._extra_save_files()
        self._out('info', f'💾 正在存档：{name}...')
        t = threading.Thread(target=self._save_worker, args=(name, state, extra), daemon=True)
        self._save_threads[name] = t
        t.start()

    def _save_worker(self, name: str, state: dict, extra: dict):
        """后台写盘线程"""
        try:
            self.save_manager.save(state, name, extra)
            self._out('info', f'💾 已存档：{name}')
            logger.info(f'存档完成：{name}')
        except Exception as e:
            self._out('error', f'❌ 存档 [{name}] 失败：{e}')
            logger.error(f'存档失败 [{name}]：{e}')

    def _do_quick_save(self):
        """快速存档（后台写入，不阻塞主循环）"""
        self._do_save('quick_save')

    # ===================== 命令处理 =====================

    def handle_command(self, raw_input: str) -> str | None:
        """
        处理以 '/' 开头的控制台命令。

        :return: 'break'     → 主循环 break
                 'continue'  → 主循环 continue（跳过本轮流水线）
                 None        → 非命令，应继续流水线处理
        """
        if raw_input == 'exit':
            return 'break'

        if not raw_input.startswith('/'):
            return None

        parts = raw_input.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ''

        if cmd == '/save' and arg.startswith('del '):
            del_name = arg[4:].strip()
            if del_name:
                ok = self.save_manager.delete(del_name)
                self._out('error', f'🗑️  已删除存档：{del_name}' if ok else f'⚠️  存档不存在：{del_name}')
            else:
                self._out('info', '用法：/save del <存档名称>')

        elif cmd == '/save' and arg:
            self._do_save(arg)

        elif cmd == '/save' and arg == '':
            self._out('info', '用法：/save <存档名称>')

        elif cmd == '/load' and arg:
            state = self.save_manager.load(arg)
            if state is None:
                self._out('error', f'⚠️  存档不存在：{arg}')
            else:
                self.rebuild_game_state(state)
                self._out('info', f'📂 已读档：{arg}')
                self._out('info', f'  玩家：{state["meta"]["player_name"]}')
                self._out('info', f'  位置：{state["meta"]["location"]}')
                logger.info(f'读档完成：{arg}，全局轮次：{self.global_context.count}，事件轮次：{self.event_context.count}')

        elif cmd == '/load' and arg == '':
            self._out('info', '用法：/load <存档名称>')

        elif cmd == '/saves':
            saves = self.save_manager.list_saves()
            if not saves:
                self._out('info', '📭 暂无存档')
            else:
                self._out('info', f'📖 存档列表（共 {len(saves)} 个）：')
                for s in saves:
                    meta = s['meta']
                    if meta:
                        self._out('info', f'  [{s["name"]}] v{meta.get("version","?")} | {s["mtime"]} — '
                              f'{meta.get("player_name","?")} @ {meta.get("location","?")}  '
                              f'(轮次:{meta.get("round_count","?")}, {s["file_count"]}文件)')
                    else:
                        self._out('info', f'  [{s["name"]}] {s["mtime"]} — (元信息缺失)')

        elif cmd == '/quick_save':
            self._do_quick_save()

        elif cmd == '/quick_load':
            state = self.save_manager.quick_load()
            if state is None:
                self._out('error', '⚠️  无快速存档')
            else:
                self.rebuild_game_state(state)
                self._out('info', '📂 已快速读档')
                logger.info(f'快速读档完成，全局轮次：{self.global_context.count}')

        elif cmd == '/context':
            which = arg if arg else 'global'
            if which == 'event':
                ctx = self.event_context
                label = f'事件上下文（{ctx.count}轮）'
            elif which == 'director':
                ctx = self.director_context
                label = f'导演上下文（{ctx.count}轮 · 仅LLM可见）'
            else:
                ctx = self.global_context
                label = f'全局上下文（{ctx.count}轮）'
            self._out('info', f'┌ {label}')
            if ctx.context:
                for line in ctx.context:
                    self._out('info', f'│ {line}')
            else:
                self._out('info', '│ (空)')
            self._out('info', f'└── 共 {len(ctx.context)} 条')

        elif cmd == '/tasks':
            # 任务表是**唯一真源**，所以这个视图直接读 `self.tasks`，不读任何派生值
            pool = self.tasks.tasks
            if arg in ('all', 'done', '全部'):
                show = pool
            else:
                show = [t for t in pool if t.get('状态') == ACTIVE]
            live = [t for t in pool if t.get('状态') == ACTIVE]
            done = [t for t in pool if t.get('状态') == DONE]
            fail_n = [t for t in pool if t.get('状态') == FAILED]
            self._out('info', f'┌ 📋 任务表（进行中 {len(live)} / 已完成 {len(done)} / '
                              f'已失败 {len(fail_n)}／共 {len(pool)}）')
            if show:
                for t in show:
                    self._out('info', f'│ · {self.tasks.describe(t)}'
                                      f'｜执行者：{t.get("执行者")}'
                                      f'｜委托人：{t.get("委托人") or "未记"}')
            else:
                self._out('info', '│ (空)')
            self._out('info', '└── /tasks all 连已结束的一起看')

        elif cmd == '/npcs':
            self._out('info', f'┌ 事件NPC ({len(self.event_npc)}个)')
            for n in self.event_npc.npc_list:
                self._out('info', f'│ {n.name}')
            self._out('info', f'├ 关联NPC ({len(self.related_npc)}个)')
            for n in self.related_npc.npc_list:
                self._out('info', f'│ {n.name}')
            self._out('info', f'└── 全量NPC: {len(self.all_npc_list)}个 | /context 对话 /context event 事件 /context director 幕后')

        elif cmd == '/player':
            which = arg.lower() if arg else 'overview'
            state = self.player.player_dict.get('当前状态CurrentState', {})
            inv = self.player.player_dict.get('持有物Inventory', {})
            descs = self.player.player_dict.get('描述Description', [])

            if which in ('inv', 'inventory', 'bag'):
                # 持有物
                self._out('info', f'┌ 🎒 持有物')
                if inv:
                    for k, v in inv.items():
                        self._out('info', f'│ · {k}：{v}')
                else:
                    self._out('info', '│ (空)')
                self._out('info', f'└── 共 {len(inv)} 件')
            elif which in ('desc', 'description'):
                # 描述
                self._out('info', f'┌ 📋 人物描述')
                if descs:
                    for i, d in enumerate(descs, 1):
                        self._out('info', f'│ [{i}] {d}')
                else:
                    self._out('info', '│ (空)')
                self._out('info', f'└── 共 {len(descs)} 条')
            elif which in ('all', 'full'):
                # 全部信息
                self._out('info', f'┌ 👤 {self.player.player_dict.get("玩家名称name", "未知")}')
                self._out('info', f'│')
                self._out('info', f'│ 📍 位置：{state.get("当前所处位置", "未知")}')
                self._out('info', f'│ ⚡ 实力：{state.get("实力等级", "未知")}')
                self._out('info', f'│ 🪪 身份：{state.get("当前身份", "未知")}')
                self._out('info', f'│')
                if inv:
                    self._out('info', f'│ 🎒 持有物：')
                    for k, v in inv.items():
                        self._out('info', f'│   · {k}：{v}')
                else:
                    self._out('info', f'│ 🎒 持有物：(空)')
                self._out('info', f'│')
                if descs:
                    self._out('info', f'│ 📋 描述：')
                    for d in descs:
                        self._out('info', f'│   · {d}')
                self._out('info', f'└── 可简略查看：/player | /player inv | /player desc')
            else:
                # 默认：概览
                self._out('info', f'┌ 👤 {self.player.player_dict.get("玩家名称name", "未知")}')
                self._out('info', f'│ 📍 {state.get("当前所处位置", "未知")}')
                self._out('info', f'│ ⚡ {state.get("实力等级", "未知")}')
                self._out('info', f'│ 🪪 {state.get("当前身份", "未知")}')
                self._out('info', f'│ 🎒 持有物 {len(inv)} 件 | 📋 描述 {len(descs)} 条')
                self._out('info', f'└── /player inv 查看持有物 | /player desc 查看描述 | /player all 全部')

        else:
            self._out('info', f'未知命令：{cmd}  可用：/save /load /saves /quick_save /quick_load /context /npcs /player /debug')

        return 'continue'
