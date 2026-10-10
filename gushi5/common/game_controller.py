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
from common.small_tool import load_json_to_dict, walk_category_files, load_npc_list_from_json, apply_npc_delta
from common import time_int
from common.game_clock import GameClock
from common.deferred_queue import DeferredQueue

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

        # ---- 输出接收器 ----
        # 所有面向玩家的文本都走 `self._out(kind, text)`，**不直接 print**。
        # 为什么：命令回显（/npcs、/player、存档提示）如果直接 print，网页端就
        # 看不到——表现为"命令执行了但页面上什么都没有"。注入接收器之后，
        # 终端和网页是同一份输出语义的两个实现。
        self.on_output = on_output or terminal_output

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

    #: `行程`（L3 的语义）→ **整段路程走到第几成** `frac ∈ (0, 1]`。
    #:
    #: ⚠️ 这张表是**我按中文语感猜的**，所以它的用法被限死成"安全"的：
    #:   · 认不出 / 没给 → 一律按 `frac = 1.0`（= **老口径"去向即到达"**）处理，
    #:     绝不让人卡在半路；
    #:   · 每次认不出都记一条 WARNING，好让**真机跑一轮之后照模型实际吐的词**
    #:     来定这张表，而不是照我的想象定（见 `地图实施计划.md` 的风险栏）。

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
        self.deferred_queue.clear()
        self.init_world()
        self.init_clock()
        self.init_all_npcs()
        self.init_player()

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

            st = t.get('世界状态变更', {}) or {}
            for ns in (st.get('增加', []) or []):
                if ns and ns not in self.b1WorldLayer.dict.get('世界状态列表', []):
                    self.b1WorldLayer.dict['世界状态列表'].append(ns)
            for os_ in (st.get('删减', []) or []):
                sl = self.b1WorldLayer.dict.get('世界状态列表', [])
                if os_ and os_ in sl:
                    sl.remove(os_)

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
                npc = apply_npc_delta(npc, npc_delta)
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

    def process_npc_pipeline(self, npc_name: str, npc: B3NPCLayer, should_print: bool,
                             n45_layer, n6_update, perception_msg: str):
        """
        单个NPC的完整 N45→N6 流水线，供 ThreadPoolExecutor 并行调用。

        流水线：N45（推理+回应，合并了原N4/N5）→ N6（收尾+耗时）

        :param perception_msg: N3感知层分发的带方向消息（"玩家对你说：..."或"玩家对[名字]说：..."）
        :return: (npc_final, response, should_print, player_delta, narration, minutes)
        """
        tag = '' if should_print else '（潜在）'

        # N45 理解回应层（不接受玩家实例：推理更新待解释事件 → 据此生成回应+内心想法）
        npc_after, response_text, inner_thought = n45_layer.response_for_npc(
            npc, perception_msg, self.global_context.context
        )
        logger.info(f'[{npc_name}]{tag} 理解回应完成')
        if should_print:
            logger.info(f'[{npc_name}] 回应：{response_text}')

        # N6 更新层（唯一接受玩家实例的层，收尾落实所有客观操作 + 估算本轮耗时）
        npc_final, player_delta, narration, minutes = n6_update.update_for_npc(
            npc_after, self.player, perception_msg, inner_thought
        )

        return npc_final, response_text, should_print, player_delta, narration, minutes

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
