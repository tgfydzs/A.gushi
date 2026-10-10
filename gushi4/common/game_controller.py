"""
游戏控制器：统一管理运行时状态、存档、命令处理。

主循环只负责**调用顺序**，所有"管理类"逻辑收在这里：

    N0 消费后台产出 → N1 读输入 → 快照 → S1‖L1 → L2并行 → R1 → L3 → R2 → 心跳

三条写回路径（对应三个信息域的落点）：

| 来源 | 写什么 | 落点 |
|------|--------|------|
| L2 | NPC **主观**表 | `apply_l2_subjective()` |
| L3 | 玩家 + NPC **客观**表 | `apply_l3_settlement()` |
| B1/B2 | 世界状态 · 幕后叙事 · 后台 NPC 主观表 | `consume_deferred()` |
"""

import os
import threading
import time
from copy import deepcopy

from common.log import get_logger
from common import log as log_mod
from common.progress import terminal_output
from common.save_load import SaveManager
from common.base_cnf import (BASE_PATH, WORLD_DATA_FILE, WORLD_DATA_SELECT,
                             WORLD_MAP_FILE, NPC_DATA_ROOT_PATH, SAVE_VERSION)
from common import map_io
from common.small_tool import (load_json_to_dict, walk_category_files,
                               load_npc_list_from_json, _apply_dict_delta)
from common import time_int
from common.game_clock import GameClock
from common.deferred_queue import DeferredQueue

from Schema.Context_sch import ContextHistory
from Schema.Event_sch import EventLayer
from Schema.B3_npclist_sch import B3NPCListLayer
from Schema.B3_1_npc_sch import B3NPCLayer
from Schema.B4_player_sch import B4PlayerLayer
from Schema.B1_worldState_sch import B1WorldLayer

logger = get_logger("GameController")


class GameController:

    def __init__(self, session_id: str = "default", on_output=None):
        self.session_id = session_id
        self.save_manager = SaveManager()

        # ---- 输出接收器 ----
        # 用 sink 而不是"把 print 改成返回行列表"，是因为 `_do_save` / `_save_worker`
        # 是在**后台线程**里输出的（'💾 已存档：xxx'）——返回值送不回调用方。
        # 默认值是终端渲染器，所以直接构造 GameController 的测试行为不变。
        self.on_output = on_output or terminal_output

        # ---- 游戏时钟与上下文 ----
        self.game_clock: GameClock = None
        self.global_context: ContextHistory = None
        self.event_context: ContextHistory = None
        self.director_context: ContextHistory = None

        # ---- NPC ----
        self.all_npc_list = B3NPCListLayer()

        # ---- 世界 / 玩家 / 事件 ----
        self.b1WorldLayer: B1WorldLayer = None
        self.player: B4PlayerLayer = None
        self.event_layer: EventLayer = None

        # ---- 后台心跳 ----
        self.deferred_queue = DeferredQueue()
        # B1a 规则去重 + B1b 主题防重复（原本挂在 N9_Director 上）
        self.bg_state = {'triggered_rules': {}, 'recent_narrations': [],
                         'narrated_themes': []}

        self._save_threads: dict = {}

    # ===================== 初始化 =====================

    def init_world(self):
        world_data = load_json_to_dict(os.path.join(BASE_PATH, WORLD_DATA_FILE))
        world = world_data[WORLD_DATA_SELECT]

        # 地理与配置的**真源是 `.map`**（见 地图格式.md）。JSON 只留没有几何、
        # 也不常手改的那几份列表（规则/状态/演变/知识）。
        # ⚠️ 读不到就**报错停下**，不静默降级——空地图会让"所有地点都不存在"，
        # 而玩家看到的会是"NPC 集体失忆"这种查不出根因的现象。
        map_path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
        if not os.path.isfile(map_path):
            raise FileNotFoundError(
                f'找不到世界地图 {WORLD_MAP_FILE}（地理与配置的真源）。'
                f'首次生成：python -m common.map_io --export --from-json')
        world = map_io.apply_map(world, map_io.load_world_map(map_path))
        self.b1WorldLayer = B1WorldLayer(world)
        logger.info(f'世界已载入：{WORLD_DATA_SELECT}（地理与配置来自 {WORLD_MAP_FILE}）')

    def init_clock(self):
        start_time = self.b1WorldLayer.get_start_time()
        time_fmt = self.b1WorldLayer.dict.get('时间格式', 'auto')
        self.game_clock = GameClock(start_time_str=start_time, time_format=time_fmt)
        self.global_context = ContextHistory(game_clock=self.game_clock)
        self.event_context = ContextHistory(game_clock=self.game_clock)
        self.director_context = ContextHistory(game_clock=self.game_clock)
        self.event_layer = EventLayer(game_clock=self.game_clock)
        logger.info(f'⏰ 游戏时钟启动 → {start_time}（格式：{time_fmt}）')

    def init_all_npcs(self):
        npc_root = os.path.join(BASE_PATH, NPC_DATA_ROOT_PATH)
        for npc_file in walk_category_files(npc_root) or []:
            for npc in load_npc_list_from_json(npc_file):
                self.all_npc_list.add(npc)

        self.apply_config_caps()
        logger.info(f'NPC 载入完成：{len(self.all_npc_list)} 个')

    def apply_config_caps(self) -> dict:
        """
        用世界配置覆写各张 NPC 表的截断上限。

        gushi3 只覆盖了 3 张表，另外 4 张无上限无限增长——这里全部走 `CAP_MAP`。
        配置键：`NPC_上限_<属性名>`，如 `NPC_上限_MAX_PLAYER_OBSERVATION`。

        ⚠️ 单独抽出来是因为它是**类属性**：编辑器改了配置，光把字典盖一遍没有用，
        必须重跑这个覆写。不抽出来的话，界面会说"已保存并热重载"，而
        `MAX_MEMORY` 纹丝不动——正是 `NPC_上限_*` 那个前科的形状。
        """
        for _, attr in B3NPCLayer.CAP_MAP.items():
            val = self.b1WorldLayer.get_config(f'NPC_上限_{attr}', None)
            if val is not None:
                setattr(B3NPCLayer, attr, int(val))
        caps = {t.split('(')[0]: getattr(B3NPCLayer, a)
                for t, a in B3NPCLayer.CAP_MAP.items()}
        logger.info(f'表上限已应用={caps}')
        return caps

    def init_player(self):
        player_file = self.b1WorldLayer.get_config('玩家初始数据',
                                                   'data/player/default.json')
        self.player = B4PlayerLayer(load_json_to_dict(os.path.join(BASE_PATH, player_file)))
        logger.info(f'玩家载入：{self.player.player_dict.get("玩家名称name", "?")}')
        # 位置迁移放在最后：此刻玩家与 NPC 都已就位，一次把两边都落成坐标
        self.migrate_positions()

    def new_game(self):
        """
        开新游戏：把**这一局跑出来的**状态整个换掉，回到刚开局的样子。

        清什么：
          · NPC 名册 —— ⚠️ 必须 `new_list()`。`init_all_npcs` 是往列表里 `add`，
            不清就是**两局的 NPC 叠在一起**（而且名字重复，`get_npc` 只会碰到先来的那个）。
          · 后台产出队列 —— 上一局的心跳产出与新的世界不兼容。与读档 / 撤回
            同一条理由（`rebuild_game_state` / `restore` 也在清它）。
          · 世界层 / 时钟 / 全局·事件·导演三个上下文 / 事件层 / 玩家 —— 由下面
            四个 `init_*` 重建。

        **不清**磁盘上的东西：`save/` 里的存档一条不动（那是上一局的成果，要删得
        显式 `/save del`），世界数据本身也不动。

        ⚠️ 这会**换掉 `self.b1WorldLayer` 这个对象**（`init_world` 新建的）。
        引擎侧那些把世界层存进字段的层（L1/L3/R2/R4）必须跟着**重新接线**，
        见 `GameEngine._wire()`——不重接就是"拿着上一局的世界在跑"，而且不报错。
        """
        self.all_npc_list.new_list()
        self.deferred_queue.clear()
        self.init_world()
        self.init_clock()
        self.init_all_npcs()
        self.init_player()
        logger.info(f'🆕 新游戏：{len(self.all_npc_list)} 个 NPC，'
                    f'玩家 {self.player.player_dict.get("玩家名称name", "?")}'
                    f' @ {self.player_location}')

    # ===================== 上下文小工具 =====================

    def add_context_all(self, text: str):
        """写入全局 + 事件上下文（玩家输入、世界事件——AI 需要据此处理）"""
        self.global_context.add_context(text)
        self.event_context.add_context(text)

    def add_global_context(self, text: str):
        """仅写全局上下文（NPC 回应、旁白——只给玩家看，不给 AI 当输入）"""
        self.global_context.add_context(text)

    def add_director_context(self, text: str):
        """写幕后上下文（B1a/B2 产出，仅 LLM 可见，不向玩家暴露）"""
        self.director_context.add_context(text)

    # ===================== 输出 =====================

    def _out(self, kind: str, text: str):
        """
        输出一行。`kind` 是**语义标签**，不是样式：

        | kind        | 含义                   | 终端渲染     |
        |-------------|------------------------|--------------|
        | `narration` | 旁白（玩家可见的氛围） | 灰斜体       |
        | `dim`       | 幕后简讯（世界/传播）  | 暗字         |
        | `info`      | 命令回显、状态提示     | 原样         |
        | `error`     | 失败提示               | 原样         |

        ANSI 码留给终端渲染器——数据层不该带 `\\033[3m`，否则同一份文本推到网页就脏了。
        """
        self.on_output(kind, text)

    # ===================== 玩家读写 =====================

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

    # --------------------- 位置：坐标是真源，路径是派生 ---------------------
    #
    # 为什么还留一个 `当前所处位置`（路径）？因为引擎里有十几处读它，
    # 而它们要的是"路径"这个形状。所以：
    #     **唯一写入点**是下面这个 `set_position` —— 它一次写两样（坐标 + 派生路径）
    #     于是两样不可能各自漂；`map_migration_test` 另有一条一致性断言盯着。
    # 绝不允许别处直接写 `当前所处位置`：那正是 2026-10-08 事故的形状
    # （L3 往真路径后面接话 → 当轮所有地段能力静默失效）。

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

    #: `行程`（L3 的语义）→ **整段路程走到第几成** `frac ∈ (0, 1]`。
    #:
    #: ⚠️ 这张表是**我按中文语感猜的**，所以它的用法被限死成"安全"的：
    #:   · 认不出 / 没给 → 一律按 `frac = 1.0`（= **老口径"去向即到达"**）处理，
    #:     绝不让人卡在半路；
    #:   · 每次认不出都记一条 WARNING，好让**真机跑一轮之后照模型实际吐的词**
    #:     来定这张表，而不是照我的想象定（见 `地图实施计划.md` 的风险栏）。
    _STAGE_FRAC = (
        (('到了', '已到', '抵达', '到达'), 1.0),
        (('将到', '快到', '将近', '即将', '不日'), 0.85),
        (('半路', '过半', '途中', '中途'), 0.5),
        (('刚上路', '上路', '出发', '启程', '动身', '刚走'), 0.15),
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
        段数据铺得越多，L3 越容易照着段名说，于是**越容易瞬移**。

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
                # 作者爱把段名叫成「雪岭段」，而 L3 往往只说「雪岭」——
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
        dest_name = w.name_of(dest) or dest
        c = w.coord_at(state)
        onrd = w.on_road_at(*c) if c else None
        base = onrd['从'] if onrd else w.name_at(state)
        if not base:
            return False
        if dest_name == base:
            return False
        hops = w.route(base, dest_name)
        if not hops:
            # **目标是真存在的地点，却一条路都不通**（数据漏了——手写一张新地图时
            # 忘连一条路太正常了）。这里**不硬失败**：直接落点 + WARNING。
            #
            # 为什么不让玩家"走不动"：那是个死胡同。他想去的地方在世界里明明存在，
            # 卡住只会让人以为游戏坏了。**数据的问题要看得见**（WARNING），
            # 但不该表现为"玩家动不了"。
            if dest_name not in w.place_paths():
                return False          # 根本不是已知地点：不动（老口径，不编假位置）
            ok = self.set_position(state, name=dest_name)
            if ok:
                logger.warn(f'🚶 去向「{dest_name}」**一条路都没有**（{base} 通不到它）'
                            f'——先按"到了"落点。这是**地图数据的漏**，补一条路就好了')
            return ok
        if len(hops) < 2:
            return False

        legs = len(hops) - 1
        if frac >= 1.0:
            # 到了：直接落目的地（与老口径完全一致——所以"没给行程"不会退化）
            ok = self.set_position(state, name=dest_name)
            if ok:
                logger.info(f'🚶 行进：{base} → {dest_name}（到了'
                            f'{("，" + why) if why else ""}，共 {legs} 跳）')
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

    def reload_world(self) -> dict:
        """
        把 `世界.map` 重新读进来，盖到**活着的**世界字典上（编辑器保存后用）。

        刻意**只**盖 `.map` 拥有的那几把键（见 `map_io.apply_map`）。整份重读 JSON
        是不行的——`世界状态列表` 是**运行期在长的**（L3 每轮可能追加新状态），
        重读会把它抹回开局，而那种丢失事后根本查不出来。

        几何早就算好的派生缓存（`_depths` / `_paths` / `_name_by_path`）必须清掉：
        不清就是"地图改了，世界还记得旧路"。

        ⚠️ 调用方要保证**当前没有回合在跑**（`web/server.py` 的保存接口挡了这条）。

        :return: **按"改完什么时候生效"分三类**。以前只回一句"已重载"，那是把三种
                 情况说成了一种——用户会以为改了 NPC 上限就生效，然后奇怪它为什么没变。
        """
        from common import map_io
        map_path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
        fresh = map_io.load_world_map(map_path)
        d = self.b1WorldLayer.dict
        before = len(d.get('世界状态列表') or [])
        # `游戏开始时间` / `时间格式` 是顶层键，**不在 `世界配置` 里**——
        # 只比 `世界配置` 的话，改了开局时刻会被算成"没有变动"，于是界面上
        # 一个字都不提，而玩家下一轮发现时间倒着走了。所以两处一起比。
        watch_before = {k: d.get(k) for k in ('游戏开始时间', '时间格式')}
        watch_before.update({f'配置:{k}': v
                             for k, v in (d.get('世界配置') or {}).items()})
        for k in ('世界地点列表', '世界配置', '路列表', '地图几何'):
            if fresh.get(k) is not None:
                d[k] = fresh[k]
        for k in ('游戏开始时间', '时间格式'):
            if fresh.get(k) is not None:
                d[k] = fresh[k]
        # 派生缓存全清：不清就是"地图改了，世界还记得旧路"。
        # `_adj`（邻接表）与 `_roadtbl`（路的采样表）是 S3 加的，同样只取决于几何；
        # `_attrsn`（按名字取属性，`类型` 从那儿读）是"区域/地点分家"加的——
        # 不清的话改了 `类型` 之后，世界还按旧的"谁是区域"来判。
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
        # 「玩家初始数据」要下一次开局才读。「生命值体系」由 L3 每轮现读，算立即生效。
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
        return {
            '立即生效': now,
            '需重启': deferred,
            '世界状态条数': [before, after],
        }

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

    def apply_player_delta(self, delta: dict):
        """应用增量到玩家字典（原地，deepcopy 保护）"""
        if not delta or (not delta.get('增加') and not delta.get('删减')):
            return
        new = deepcopy(self.player.player_dict)
        _apply_dict_delta(new, delta)
        self.player.player_dict = new
        logger.info(f'👤 玩家状态更新：{list(delta.get("增加", {})) + list(delta.get("删减", {}))}')

    # ===================== NPC 读写 =====================

    def get_npc(self, name: str):
        return self.all_npc_list.get_npc(name)

    def sync_npc_to_lists(self, updated_npc: B3NPCLayer):
        """按名字定位旧实例并原地替换，同步前截断列表字段"""
        updated_npc.cap_fields()
        for i, n in enumerate(self.all_npc_list.npc_list):
            if n.name == updated_npc.name:
                self.all_npc_list.npc_list[i] = updated_npc
                return

    def apply_l2_subjective(self, l2_result: dict):
        """
        落地 L2 的**主观**增量。

        再次过一遍白名单——即便 L2 内部已经净化过，这里是唯一的写入口，
        多一道检查的成本远低于让客观字段从这条缝里漏进去。
        """
        from common.L2_character import SUBJECTIVE_TABLES, sanitize_npc_delta

        npc = self.get_npc(l2_result.get('name', ''))
        if npc is None:
            logger.warn(f'L2 结果找不到 NPC：{l2_result.get("name")}')
            return
        clean = sanitize_npc_delta(l2_result.get('npc_instance') or {})
        if not clean['增加'] and not clean['删减']:
            return
        d = npc.to_dict()
        _apply_dict_delta(d, clean)
        self.sync_npc_to_lists(B3NPCLayer(d))

    def apply_l3_settlement(self, l3_result: dict):
        """
        落地 L3：玩家增量 + 各 NPC **客观**增量 + 破裂观察。

        这是**唯一的客观状态写入口**（设计方案 D12）。一致性由结构保证，
        而不是靠各处自律。
        """
        self.apply_player_delta(l3_result.get('玩家实例') or {})

        # 玩家的**去向**：L3 只给地名（语义），落坐标是这里的活。
        # 这是位置唯一的写入通道（L3 的 `位置`/`当前所处位置` 白名单外，写了也会被丢）。
        dest = (l3_result.get('去向') or '').strip()
        if dest:
            st = self.player.player_dict.get('当前状态CurrentState') or {}
            stage = (l3_result.get('行程') or '').strip()
            frac = self.travel_frac(stage)
            if frac is None and stage:
                # 词表认不出，再试**段名**（"走到雪岭了"）——段名是作者写的，
                # 而 L3 现在看得到"正在穿过哪一段"，照着它说的机会不小。
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
                self.player.player_dict['当前状态CurrentState'] = st
                logger.info(f'🧭 玩家去向：{dest} → {st.get("当前所处位置")}')
            else:
                logger.warn(f'🧭 去向「{dest}」走不通（不是已知地点 / 没有连通的路），'
                            f'位置不动——这比编一个假位置强')

        npc_map = l3_result.get('NPC实例') or {}
        for name, delta in npc_map.items():
            npc = self.get_npc(name)
            if npc is None:
                logger.warn(f'L3 增量指向不存在的 NPC：{name}')
                continue
            d = npc.to_dict()
            _apply_dict_delta(d, delta)
            self.sync_npc_to_lists(B3NPCLayer(d))
        if npc_map:
            logger.info(f'🌍 客观结算：{ {k: list(v["增加"]) + list(v["删减"]) for k, v in npc_map.items()} }')

        # 破裂观察：L3 写进 NPC 观察表的唯一通道（事实记录，不是心理描写）
        for name, obs in (l3_result.get('破裂观察') or {}).items():
            npc = self.get_npc(name)
            if npc is None:
                continue
            entry = f'（当场）{obs}'
            if entry not in npc.observation:
                npc.observation.append(entry)
                npc.cap_fields()
            logger.info(f'💥 破裂观察注入 [{name}]：{obs}')

    # ===================== 快照 / 回滚 =====================

    def snapshot(self) -> dict:
        return {
            'global_context': (deepcopy(self.global_context.context), self.global_context.count),
            'event_context': (deepcopy(self.event_context.context), self.event_context.count),
            'director_context': (deepcopy(self.director_context.context), self.director_context.count),
            'all_npc_list': deepcopy(self.all_npc_list.npc_list),
            'player': deepcopy(self.player.player_dict),
            'world': deepcopy(self.b1WorldLayer.dict),
            'game_clock': deepcopy(self.game_clock.to_dict()),
            'event_layer': deepcopy(self.event_layer.to_dict()),
            'bg_state': deepcopy({k: (list(v) if isinstance(v, (set, list)) else v)
                                  for k, v in self.bg_state.items()}),
        }

    def restore(self, snap: dict):
        # 旧心跳基于回滚前的状态，与快照不兼容 → 清空
        self.deferred_queue.clear()

        self.game_clock = GameClock.from_dict(snap['game_clock'])
        self.global_context = ContextHistory(snap['global_context'][0], snap['global_context'][1],
                                             game_clock=self.game_clock)
        self.event_context = ContextHistory(snap['event_context'][0], snap['event_context'][1],
                                            game_clock=self.game_clock)
        self.director_context = ContextHistory(snap['director_context'][0], snap['director_context'][1],
                                               game_clock=self.game_clock)
        self.all_npc_list = B3NPCListLayer()
        for n in snap['all_npc_list']:
            self.all_npc_list.add(n)
        self.player = B4PlayerLayer(snap['player'])
        self.b1WorldLayer = B1WorldLayer(snap['world'])
        self.event_layer = EventLayer.from_dict(snap.get('event_layer'), self.game_clock)
        self.bg_state = deepcopy(snap.get('bg_state') or self.bg_state)

    # ===================== N0：消费后台产出 =====================

    def consume_deferred(self) -> bool:
        """
        消费后台队列（B1a/B1b/B2），打印一行简讯 + 环境叙事。

        :return: 是否消费了至少一条
        """
        messages = self.deferred_queue.get_all()
        if not messages:
            return False

        world_events, npc_actions, spread_count, narrations = [], [], 0, []

        for msg in messages:
            dtype = msg.get('data_type', '')
            if dtype == 'B1a':
                s = self._consume_b1a(msg)
                if s:
                    world_events.append(s)
            elif dtype == 'B1b':
                narrations.extend(msg.get('B1b_环境叙事') or [])
            elif dtype == 'B2':
                a, sp = self._consume_b2(msg)
                npc_actions.extend(a)
                spread_count += sp

        # 环境叙事：灰字打印（是给玩家看的氛围，藏起来就白调了一次 LLM）
        for line in narrations:
            self._out('narration', line)
            self.add_context_all(f'旁白：{line}')

        # 其余收成一行简讯，避免刷屏
        parts = []
        if world_events:
            head = '；'.join(world_events[:2])
            if len(world_events) > 2:
                head += f'（+{len(world_events) - 2}条）'
            parts.append(f'🌍 {head}')
        if npc_actions:
            names = '、'.join(npc_actions[:3])
            more = f'等{len(npc_actions)}人' if len(npc_actions) > 3 else ''
            parts.append(f'🎬 {names}{more}有所行动')
        if spread_count:
            parts.append(f'📡 {spread_count}条情报在传播')
        if parts:
            self._out('dim', f'· {"  |  ".join(parts)}')

        logger.info(f'N0 消化 {len(messages)} 条后台消息')
        return True

    def _consume_b1a(self, msg: dict):
        """B1a：写入世界状态 + 上下文，返回一句话摘要"""
        summaries = []
        for t in (msg.get('B1a_规则触发') or []):
            rule_text = t.get('规则原文', '')
            valid = self.b1WorldLayer.dict.get('世界演变规则列表', [])
            if rule_text in valid:
                self.bg_state['triggered_rules'][str(valid.index(rule_text))] = t.get('进度', '初期')

            st = t.get('世界状态变更', {}) or {}
            for ns in (st.get('增加') or []):
                if ns and ns not in self.b1WorldLayer.dict.get('世界状态列表', []):
                    self.b1WorldLayer.dict['世界状态列表'].append(ns)
            for os_ in (st.get('删减') or []):
                sl = self.b1WorldLayer.dict.get('世界状态列表', [])
                if os_ in sl:
                    sl.remove(os_)

            progress = t.get('进度', '')
            public = (t.get('玩家可见叙述') or '').strip()
            behind = (t.get('幕后详情') or '').strip()
            if public and public != '暂无公开迹象':
                self.add_context_all(f'世界事件（{progress}）：{public[:300]}')
            if behind:
                self.add_director_context(f'世界事件幕后（{progress}）：{behind[:300]}')

            short = public[:15].rstrip('，。！？') if public and public != '暂无公开迹象' else ''
            summaries.append(short or rule_text[:20].rstrip('——'))
            logger.info(f'🌍 世界规则触发：{rule_text[:30]}… 进度={progress}')

        return '；'.join(summaries) if summaries else None

    def _consume_b2(self, msg: dict):
        """B2：应用 NPC 主观增量 + 幕后行为写观察 + 剧情级传播"""
        acted = []
        from common.L2_character import sanitize_npc_delta

        for npc_name, data in (msg.get('NPC自主行为') or {}).items():
            npc = self.get_npc(npc_name)
            if npc is None:
                logger.warn(f'B2 队列 NPC [{npc_name}] 未能匹配')
                continue
            delta = sanitize_npc_delta(data.get('NPC增量') or {})
            if delta['增加'] or delta['删减']:
                d = npc.to_dict()
                _apply_dict_delta(d, delta)
                npc = B3NPCLayer(d)
                self.sync_npc_to_lists(npc)

            action = (data.get('动作') or '').strip()
            if action:
                entry = f'（幕后行动）{action}'
                if entry not in npc.observation:
                    npc.observation.append(entry)
                npc.cap_fields()
                acted.append(npc.name)
                logger.info(f'🎬 [{npc.name}] {action[:40]}')

        spread_count = 0
        for sp in (msg.get('剧情级传播') or []):
            info, method = sp.get('信息', ''), sp.get('方式', '')
            for npc_name in (sp.get('到达NPC') or []):
                npc = self.get_npc(npc_name)
                if npc is None:
                    continue
                entry = f'（情报·{method}）{info}'
                if entry not in npc.observation:
                    npc.observation.append(entry)
                    npc.cap_fields()
            spread_count += 1

        return acted, spread_count

    # ===================== 存档 / 读档 =====================

    def build_save_state(self, name: str) -> dict:
        """
        把当前运行时状态打包成可序列化的字典。

        ⚠️ **最后一定要 deepcopy**：下面的字段全都是"把对象塞进字典"，
        `player.player_dict`、`all_npc_list.to_dict()` 里的列表、`bg_state`——
        取到的都是**活的引用**。不深拷贝的话：

          1. `_do_save` 是**后台线程写盘**的，主循环不会停。等它真正序列化时，
             下一轮已经把 player_dict 改了 —— **存下去的是未来的状态**。
          2. `rebuild_game_state` 从存档恢复时，恢复出来的对象和存档字典
             共享引用，改一个等于改两个。

        存档是罕见的操作，深拷贝的代价可以忽略。
        """
        state = {
            "meta": {
                "save_name": name,
                "version": SAVE_VERSION,
                "timestamp": time_int.ts_to_readable(time_int.get_minute_ts()),
                "game_time": self.game_clock.now_str(),
                "player_name": self.player.player_dict.get("玩家名称name", "未知"),
                "location": self.player_location or "未知",
                "round_count": self.global_context.count,
                "event_round_count": self.event_context.count,
            },
            "global_context": {"count": self.global_context.count,
                               "context": self.global_context.context},
            "event_context": {"count": self.event_context.count,
                              "context": self.event_context.context},
            "director_context": {"count": self.director_context.count,
                                 "context": self.director_context.context},
            "all_npc_list": self.all_npc_list.to_dict(),
            "player_dict": self.player.player_dict,
            "world_dict": self.b1WorldLayer.dict,
            "game_clock": self.game_clock.to_dict(),
            "event_layer": self.event_layer.to_dict(),
            "bg_state": self.bg_state,
        }
        return deepcopy(state)

    def rebuild_game_state(self, save_dict: dict):
        # 同样要深拷贝：下面构造的 B1/B3/B4/Context 都是**浅引用**存档字典里的
        # 嵌套 list/dict，不隔离的话读档后的运行时状态会和存档字典共享内存。
        save_dict = deepcopy(save_dict)

        self.deferred_queue.clear()

        self.game_clock = GameClock.from_dict(save_dict.get("game_clock", {}))
        for name, key in (('global_context', 'global_context'),
                          ('event_context', 'event_context'),
                          ('director_context', 'director_context')):
            blk = save_dict[key]
            setattr(self, name, ContextHistory(blk["context"], blk["count"],
                                               game_clock=self.game_clock))

        self.all_npc_list = B3NPCListLayer()
        for d in save_dict["all_npc_list"]:
            self.all_npc_list.add(B3NPCLayer(d))

        self.player = B4PlayerLayer(save_dict["player_dict"])
        self.b1WorldLayer = B1WorldLayer(save_dict["world_dict"])
        self.event_layer = EventLayer.from_dict(save_dict.get("event_layer"), self.game_clock)
        self.bg_state = save_dict.get("bg_state") or self.bg_state

    # ===================== 命令 =====================

    def _extra_save_files(self) -> dict:
        extra = {}
        log_path = log_mod.get_log_path()
        if os.path.isfile(log_path):
            extra[os.path.basename(log_path)] = log_path
        return extra

    def _do_save(self, name: str):
        prev = self._save_threads.get(name)
        if prev and prev.is_alive():
            self._out('info', f'⚠️  存档 [{name}] 正在进行中，请稍后')
            return
        self.consume_deferred()          # 先收后台产出，保证快照最新
        state = self.build_save_state(name)
        extra = self._extra_save_files()
        self._out('info', f'💾 正在存档：{name}...')
        t = threading.Thread(target=self._save_worker, args=(name, state, extra), daemon=True)
        self._save_threads[name] = t
        t.start()

    def wait_for_saves(self, timeout: float = 30.0) -> bool:
        """
        等待所有后台存档线程写完。**退出前必须调用。**

        写盘放在后台线程是为了不卡住主循环，但线程是 daemon 的——
        **进程一退，没写完的存档就没了**。`/save` 之后紧接着敲 `exit`
        （或者直接 Ctrl-C）就会踩到这条。
        体积小的存档通常几十毫秒就完事，这个等待实际上感觉不到。

        :return: 是否全部写完
        """
        pending = [(n, t) for n, t in self._save_threads.items() if t.is_alive()]
        if not pending:
            return True

        self._out('info', f'⏳ 等待 {len(pending)} 个存档写完：{"、".join(n for n, _ in pending)}')
        deadline = time.time() + timeout
        for name, t in pending:
            t.join(timeout=max(0.1, deadline - time.time()))
            if t.is_alive():
                self._out('error', f'⚠️  存档 [{name}] 超时未完成，可能未写入完整')
                logger.error(f'存档线程超时：{name}')
                return False
        self._out('info', '✅ 存档已全部落盘')
        return True

    def _save_worker(self, name, state, extra):
        # ⚠️ 这里跑在**后台线程**里，输出必须走 sink——返回值没有调用方可以接
        try:
            self.save_manager.save(state, name, extra)
            self._out('info', f'💾 已存档：{name}')
            logger.info(f'存档完成：{name}')
        except Exception as e:
            self._out('error', f'❌ 存档 [{name}] 失败：{e}')
            logger.error(f'存档失败 [{name}]：{e}')

    def handle_command(self, raw_input: str):
        """
        :return: 'break' → 主循环退出
                 'continue' → 跳过本轮流水线
                 None → 正常输入，继续走流水线
        """
        if raw_input == 'exit':
            return 'break'
        if not raw_input.startswith('/'):
            return None

        parts = raw_input.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ''

        if cmd == '/save' and arg.startswith('del '):
            nm = arg[4:].strip()
            if nm:
                ok = self.save_manager.delete(nm)
                self._out('info', f'🗑️  已删除存档：{nm}' if ok else f'⚠️  存档不存在：{nm}')
            else:
                self._out('info', '用法：/save del <存档名称>')
        elif cmd == '/save' and arg:
            self._do_save(arg)
        elif cmd == '/save':
            self._out('info', '用法：/save <存档名称>')

        elif cmd == '/load' and arg:
            # 同名存档可能还在后台写着（刚 /save 完就 /load 会踩到），先等它落盘
            prev = self._save_threads.get(arg)
            if prev and prev.is_alive():
                self._out('info', f'⏳ 存档 [{arg}] 还在写入，等它写完...')
                prev.join(timeout=30)
            state = self.save_manager.load(arg)
            if state is None:
                self._out('error', f'⚠️  存档不存在：{arg}')
            else:
                self.rebuild_game_state(state)
                self._out('info', f'📂 已读档：{arg}')
                self._out('info', f'  玩家：{state["meta"]["player_name"]}  位置：{state["meta"]["location"]}')
                logger.info(f'读档完成：{arg}，轮次：{self.global_context.count}')
        elif cmd == '/load':
            self._out('info', '用法：/load <存档名称>')

        elif cmd == '/saves':
            saves = self.save_manager.list_saves()
            if not saves:
                self._out('info', '📭 暂无存档')
            else:
                self._out('info', f'📖 存档列表（共 {len(saves)} 个）：')
                for s in saves:
                    m = s['meta']
                    if m:
                        self._out('info', f'  [{s["name"]}] v{m.get("version","?")} | {s["mtime"]} — '
                                          f'{m.get("player_name","?")} @ {m.get("location","?")}  '
                                          f'(轮次:{m.get("round_count","?")}, {s["file_count"]}文件)')
                    else:
                        self._out('info', f'  [{s["name"]}] {s["mtime"]} — (元信息缺失)')

        elif cmd == '/quick_save':
            self._do_save('quick_save')
        elif cmd == '/quick_load':
            state = self.save_manager.quick_load()
            if state is None:
                self._out('error', '⚠️  无快速存档')
            else:
                self.rebuild_game_state(state)
                self._out('info', '📂 已快速读档')

        elif cmd == '/context':
            which = arg or 'global'
            if which == 'event':
                ctx, label = self.event_context, f'事件上下文（{self.event_context.count}轮）'
            elif which == 'director':
                ctx, label = self.director_context, f'幕后上下文（{self.director_context.count}轮 · 仅LLM可见）'
            else:
                ctx, label = self.global_context, f'全局上下文（{self.global_context.count}轮）'
            self._out('info', f'┌ {label}')
            for line in (ctx.context or ['(空)']):
                self._out('info', f'│ {line}')
            self._out('info', f'└── 共 {len(ctx.context)} 条')

        elif cmd == '/event':
            ev = self.event_layer
            self._out('info', f'┌ 当前事件：{ev.title or "(无题)"}  第{ev.rounds}轮')
            self._out('info', f'│ 可见性：{ev.scope.value}   锁定 {len(ev.involved)} 人')
            if ev.involved:
                self._out('info', f'│ 名单：{"、".join(ev.involved)}')
            if ev.direct_target:
                self._out('info', f'│ 直接对象：{"、".join(ev.direct_target)}')
            self._out('info', f'└── 起始：{ev.started_at or "?"}')

        elif cmd == '/npcs':
            self._out('info', f'┌ 全部 NPC（{len(self.all_npc_list)}）')
            for n in self.all_npc_list.npc_list:
                mark = '★' if n.name in self.event_layer.involved else ' '
                loc = (n.current_state or {}).get('当前所处位置', '')
                self._out('info', f'│{mark} {n.name}  @{loc}')
            self._out('info', '└── ★ = 当前事件锁定 | /context 对话 | /event 事件')

        elif cmd == '/player':
            which = arg.lower() or 'overview'
            pd = self.player.player_dict
            st = pd.get('当前状态CurrentState', {})
            inv = pd.get('持有物Inventory', {})
            descs = pd.get('描述Description', [])
            if which in ('inv', 'inventory', 'bag'):
                self._out('info', '┌ 🎒 持有物')
                for k, v in (inv or {}).items():
                    self._out('info', f'│ · {k}：{v}')
                if not inv:
                    self._out('info', '│ (空)')
                self._out('info', f'└── 共 {len(inv)} 件')
            elif which in ('desc', 'description'):
                self._out('info', '┌ 📋 人物描述')
                for i, d in enumerate(descs, 1):
                    self._out('info', f'│ [{i}] {d}')
                if not descs:
                    self._out('info', '│ (空)')
                self._out('info', f'└── 共 {len(descs)} 条')
            else:
                self._out('info', f'┌ 👤 {pd.get("玩家名称name", "未知")}')
                self._out('info', f'│ 📍 {st.get("当前所处位置", "未知")}')
                self._out('info', f'│ ⚡ {st.get("实力等级", "未知")}')
                self._out('info', f'│ 🪪 {st.get("当前身份", "未知")}')
                self._out('info', f'│ 🎒 持有物 {len(inv)} 件 | 📋 描述 {len(descs)} 条')
                self._out('info', '└── /player inv | /player desc')

        else:
            self._out('info', f'未知命令：{cmd}  可用：/save /load /saves /quick_save /quick_load '
                              f'/context /event /npcs /player /s1 /debug')
        return 'continue'
