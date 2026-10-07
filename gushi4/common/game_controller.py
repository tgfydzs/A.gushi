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
                             NPC_DATA_ROOT_PATH, SAVE_VERSION)
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
        self.b1WorldLayer = B1WorldLayer(world_data[WORLD_DATA_SELECT])
        logger.debug(f'世界数据已载入：{WORLD_DATA_SELECT}')

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

        # 用世界配置覆写截断上限。
        # gushi3 只覆盖了 3 张表，另外 4 张无上限无限增长——这里全部走 CAP_MAP。
        # 配置键：NPC_上限_<属性名>，如 NPC_上限_MAX_PLAYER_OBSERVATION
        for table, attr in B3NPCLayer.CAP_MAP.items():
            val = self.b1WorldLayer.get_config(f'NPC_上限_{attr}', None)
            if val is not None:
                setattr(B3NPCLayer, attr, int(val))
        caps = {t.split('(')[0]: getattr(B3NPCLayer, a) for t, a in B3NPCLayer.CAP_MAP.items()}
        logger.info(f'NPC 载入完成：{len(self.all_npc_list)} 个 | 表上限={caps}')

    def init_player(self):
        player_file = self.b1WorldLayer.get_config('玩家初始数据',
                                                   'data/player/default.json')
        self.player = B4PlayerLayer(load_json_to_dict(os.path.join(BASE_PATH, player_file)))
        logger.info(f'玩家载入：{self.player.player_dict.get("玩家名称name", "?")}')

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
        return self.player.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', '')

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
