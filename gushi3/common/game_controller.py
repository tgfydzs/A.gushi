"""
游戏控制器：统一管理运行时游戏状态、存档和命令处理。
将所有"管理类"逻辑从主流程中剥离，主流程只需关注 N1→N2→N3→N4→N5 的调用顺序。
"""

import os
import threading
from copy import deepcopy

from common.log import get_logger
from common import log as log_mod
from common.save_load import SaveManager
from common.base_cnf import BASE_PATH, SAVE_ROOT_PATH, SAVE_VERSION, WORLD_DATA_FILE, WORLD_DATA_SELECT, NPC_DATA_ROOT_PATH
from common.small_tool import load_json_to_dict, walk_category_files, load_npc_list_from_json, apply_npc_delta, strip_empty_delta
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

    def __init__(self, session_id: str = "default"):
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

    # ===================== 初始化 =====================

    def init_world(self):
        """加载世界层数据"""
        world_data = load_json_to_dict(os.path.join(BASE_PATH, WORLD_DATA_FILE))
        self.b1WorldLayer = B1WorldLayer(world_data[WORLD_DATA_SELECT])
        logger.debug(f'世界数据：{self.b1WorldLayer.dict}')

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

        # 从世界配置覆盖NPC截断上限
        from Schema.B3_1_npc_sch import B3NPCLayer
        obs_max = self.b1WorldLayer.get_config('NPC_观察上限')
        if obs_max is not None:
            B3NPCLayer.MAX_OBSERVATION = obs_max
        mem_max = self.b1WorldLayer.get_config('NPC_记忆上限')
        if mem_max is not None:
            B3NPCLayer.MAX_MEMORY = mem_max
        pend_max = self.b1WorldLayer.get_config('NPC_待推理上限')
        if pend_max is not None:
            B3NPCLayer.MAX_PENDING = pend_max
        logger.debug(f'取到npc数量:{len(self.all_npc_list)}, 截断上限: obs={B3NPCLayer.MAX_OBSERVATION} mem={B3NPCLayer.MAX_MEMORY} pend={B3NPCLayer.MAX_PENDING}')

    def init_player(self):
        """加载玩家数据（优先从世界配置读取玩家文件路径）"""
        player_file = self.b1WorldLayer.get_config('玩家初始数据', 'data/player/default.json')
        current_state = load_json_to_dict(os.path.join(BASE_PATH, player_file))
        self.player = B4PlayerLayer(current_state)

    # ===================== 小工具 =====================

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
            print(f'  \033[2m· {"  |  ".join(parts)}\033[0m')

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
            if rule_text not in valid_rules:
                logger.warn(f'N9a 队列返回不在规则列表中的规则：{rule_text[:60]}')
                continue
            rule_idx = valid_rules.index(rule_text)

            if n9_director and rule_idx in n9_director.triggered_rules:
                continue

            if n9_director:
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

            # 上下文写入不变
            if public_view and public_view.strip() and public_view != '暂无公开迹象':
                self.add_context_all(f'世界事件（{progress}）：{public_view}')
            if behind_view:
                self.add_director_context(f'世界事件幕后（{progress}）：{behind_view}')

            # 提取摘要（取叙述前15字）
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
        capped: set[str] = set()  # 追踪已截断的NPC，避免重复调用

        def _maybe_cap(npc) -> None:
            if npc.name not in capped:
                npc.cap_fields()
                capped.add(npc.name)

        npc_actions = msg.get('NPC自主行为', {}) or {}
        for npc_name, action_data in npc_actions.items():
            if isinstance(action_data, str):
                action_data = {'动作': action_data}
            action_text = action_data.get('动作', '') or ''
            npc_delta = action_data.get('NPC增量')

            # get_npc 自带括号剥离（"血手（殷九娘）"→取"血手"做 in 匹配）
            npc = self.all_npc_list.get_npc(npc_name)
            if npc is None:
                logger.warn(f'N9c队列NPC [{npc_name}] 未能匹配')
                continue
            if npc_delta:
                npc_delta = strip_empty_delta(npc_delta)
                if npc_delta:
                    npc = apply_npc_delta(npc, npc_delta)
                    self.sync_npc_to_lists(npc)
            if action_text:
                obs_entry = f'（幕后行动）{action_text}'
                if obs_entry not in npc.observation:
                    npc.observation.append(obs_entry)
                acted_names.append(npc.name)
                logger.info(f'📦 [{npc.name}] {action_text}')
            _maybe_cap(npc)

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
                    _maybe_cap(npc)
            spread_count += 1

        return acted_names, spread_count

    def process_npc_pipeline(self, npc_name: str, npc: B3NPCLayer, should_print: bool,
                             n4_reasoning, n5_response, n6_update, perception_msg: str):
        """
        单个NPC的完整 N4→N5→N6 流水线，供 ThreadPoolExecutor 并行调用。

        流水线：N4（推理）→ N5（回应）→ N6（收尾）

        :param perception_msg: N3感知层分发的带方向消息（"玩家对你说：..."或"玩家对[名字]说：..."）
        :return: (npc_final, response, should_print, player_delta, narration)
        """
        tag = '' if should_print else '（潜在）'

        # N4 推理层（不接受玩家实例，更新待解释事件表）
        npc_after_reasoning = n4_reasoning.reason_for_npc(npc, perception_msg)
        logger.info(f'[{npc_name}]{tag} 推理完成')

        # N5 回应层（不接受玩家实例，不修改NPC实例，仅产出回应+内心想法）
        response_text, inner_thought = n5_response.response_say(
            npc_after_reasoning, perception_msg, self.global_context.context
        )
        if should_print:
            logger.info(f'[{npc_name}] 回应：{response_text}')
        logger.info(f'[{npc_name}]{tag} 内心想法：{inner_thought}')

        # N6 更新层（唯一接受玩家实例的层，收尾落实所有客观操作）
        npc_final, player_delta, narration = n6_update.update_for_npc(
            npc_after_reasoning, self.player, perception_msg, inner_thought
        )

        return npc_final, response_text, should_print, player_delta, narration

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
            print(f'⚠️  存档 [{name}] 正在进行中，请稍后')
            return

        # 先消费队列，确保快照包含最新的心跳结果
        self.consume_deferred()

        state = self.build_save_state(name)
        extra = self._extra_save_files()
        print(f'💾 正在存档：{name}...')
        t = threading.Thread(target=self._save_worker, args=(name, state, extra), daemon=True)
        self._save_threads[name] = t
        t.start()

    def _save_worker(self, name: str, state: dict, extra: dict):
        """后台写盘线程"""
        try:
            self.save_manager.save(state, name, extra)
            print(f'💾 已存档：{name}')
            logger.info(f'存档完成：{name}')
        except Exception as e:
            print(f'❌ 存档 [{name}] 失败：{e}')
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
                print(f'🗑️  已删除存档：{del_name}' if ok else f'⚠️  存档不存在：{del_name}')
            else:
                print('用法：/save del <存档名称>')

        elif cmd == '/save' and arg:
            self._do_save(arg)

        elif cmd == '/save' and arg == '':
            print('用法：/save <存档名称>')

        elif cmd == '/load' and arg:
            state = self.save_manager.load(arg)
            if state is None:
                print(f'⚠️  存档不存在：{arg}')
            else:
                self.rebuild_game_state(state)
                print(f'📂 已读档：{arg}')
                print(f'  玩家：{state["meta"]["player_name"]}')
                print(f'  位置：{state["meta"]["location"]}')
                logger.info(f'读档完成：{arg}，全局轮次：{self.global_context.count}，事件轮次：{self.event_context.count}')

        elif cmd == '/load' and arg == '':
            print('用法：/load <存档名称>')

        elif cmd == '/saves':
            saves = self.save_manager.list_saves()
            if not saves:
                print('📭 暂无存档')
            else:
                print(f'📖 存档列表（共 {len(saves)} 个）：')
                for s in saves:
                    meta = s['meta']
                    if meta:
                        print(f'  [{s["name"]}] v{meta.get("version","?")} | {s["mtime"]} — '
                              f'{meta.get("player_name","?")} @ {meta.get("location","?")}  '
                              f'(轮次:{meta.get("round_count","?")}, {s["file_count"]}文件)')
                    else:
                        print(f'  [{s["name"]}] {s["mtime"]} — (元信息缺失)')

        elif cmd == '/quick_save':
            self._do_quick_save()

        elif cmd == '/quick_load':
            state = self.save_manager.quick_load()
            if state is None:
                print('⚠️  无快速存档')
            else:
                self.rebuild_game_state(state)
                print('📂 已快速读档')
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
            print(f'┌ {label}')
            if ctx.context:
                for line in ctx.context:
                    print(f'│ {line}')
            else:
                print('│ (空)')
            print(f'└── 共 {len(ctx.context)} 条')

        elif cmd == '/npcs':
            print(f'┌ 事件NPC ({len(self.event_npc)}个)')
            for n in self.event_npc.npc_list:
                print(f'│ {n.name}')
            print(f'├ 关联NPC ({len(self.related_npc)}个)')
            for n in self.related_npc.npc_list:
                print(f'│ {n.name}')
            print(f'└── 全量NPC: {len(self.all_npc_list)}个 | /context 对话 /context event 事件 /context director 幕后')

        elif cmd == '/player':
            which = arg.lower() if arg else 'overview'
            state = self.player.player_dict.get('当前状态CurrentState', {})
            inv = self.player.player_dict.get('持有物Inventory', {})
            descs = self.player.player_dict.get('描述Description', [])

            if which in ('inv', 'inventory', 'bag'):
                # 持有物
                print(f'┌ 🎒 持有物')
                if inv:
                    for k, v in inv.items():
                        print(f'│ · {k}：{v}')
                else:
                    print('│ (空)')
                print(f'└── 共 {len(inv)} 件')
            elif which in ('desc', 'description'):
                # 描述
                print(f'┌ 📋 人物描述')
                if descs:
                    for i, d in enumerate(descs, 1):
                        print(f'│ [{i}] {d}')
                else:
                    print('│ (空)')
                print(f'└── 共 {len(descs)} 条')
            elif which in ('all', 'full'):
                # 全部信息
                print(f'┌ 👤 {self.player.player_dict.get("玩家名称name", "未知")}')
                print(f'│')
                print(f'│ 📍 位置：{state.get("当前所处位置", "未知")}')
                print(f'│ ⚡ 实力：{state.get("实力等级", "未知")}')
                print(f'│ 🪪 身份：{state.get("当前身份", "未知")}')
                print(f'│')
                if inv:
                    print(f'│ 🎒 持有物：')
                    for k, v in inv.items():
                        print(f'│   · {k}：{v}')
                else:
                    print(f'│ 🎒 持有物：(空)')
                print(f'│')
                if descs:
                    print(f'│ 📋 描述：')
                    for d in descs:
                        print(f'│   · {d}')
                print(f'└── 可简略查看：/player | /player inv | /player desc')
            else:
                # 默认：概览
                print(f'┌ 👤 {self.player.player_dict.get("玩家名称name", "未知")}')
                print(f'│ 📍 {state.get("当前所处位置", "未知")}')
                print(f'│ ⚡ {state.get("实力等级", "未知")}')
                print(f'│ 🪪 {state.get("当前身份", "未知")}')
                print(f'│ 🎒 持有物 {len(inv)} 件 | 📋 描述 {len(descs)} 条')
                print(f'└── /player inv 查看持有物 | /player desc 查看描述 | /player all 全部')

        else:
            print(f'未知命令：{cmd}  可用：/save /load /saves /quick_save /quick_load /context /npcs /player /debug')

        return 'continue'
