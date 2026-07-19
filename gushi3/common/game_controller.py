"""
游戏控制器：统一管理运行时游戏状态、存档和命令处理。
将所有"管理类"逻辑从主流程中剥离，主流程只需关注 N1→N2→N3→N4→N5 的调用顺序。
"""

import os
from copy import deepcopy

from common.log import get_logger
from common import log as log_mod
from common.save_load import SaveManager
from common.base_cnf import BASE_PATH, SAVE_ROOT_PATH, SAVE_VERSION, WORLD_DATA_FILE, WORLD_DATA_SELECT, NPC_DATA_ROOT_PATH, PLAYER_DATA_FILE
from common.small_tool import load_json_to_dict, walk_category_files, load_npc_list_from_json
from common import time_int

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

        # ---- 上下文 ----
        self.global_context = ContextHistory()
        self.event_context = ContextHistory()

        # ---- NPC 列表 ----
        self.event_npc = B3NPCListLayer()
        self.related_npc = B3NPCListLayer()
        self.all_npc_list = B3NPCListLayer()

        # ---- 世界 & 玩家 ----
        self.b1WorldLayer: B1WorldLayer = None
        self.player: B4PlayerLayer = None

    # ===================== 初始化 =====================

    def init_world(self):
        """加载世界层数据"""
        world_data = load_json_to_dict(os.path.join(BASE_PATH, WORLD_DATA_FILE))
        self.b1WorldLayer = B1WorldLayer(world_data[WORLD_DATA_SELECT])
        logger.debug(f'世界数据：{self.b1WorldLayer.dict}')

    def init_all_npcs(self):
        """从 data/B3_npc 加载所有NPC"""
        npc_source_path = os.path.join(BASE_PATH, NPC_DATA_ROOT_PATH)
        npc_file_list = walk_category_files(npc_source_path)
        logger.debug(f'读取的npc文件列表{npc_file_list}')

        for npc_file in npc_file_list:
            file_npcs = load_npc_list_from_json(npc_file)
            for npc in file_npcs:
                self.all_npc_list.add(npc)
        logger.debug(f'取到npc数量:{len(self.all_npc_list)}')

    def init_player(self):
        """加载玩家数据"""
        current_state = load_json_to_dict(os.path.join(BASE_PATH, PLAYER_DATA_FILE))
        self.player = B4PlayerLayer(current_state)

    # ===================== 小工具 =====================

    def add_context_all(self, text: str):
        """同时写入全局上下文和事件上下文（玩家输入、地段传播等AI需要处理的内容）"""
        self.global_context.add_context(text)
        self.event_context.add_context(text)

    def add_global_context(self, text: str):
        """仅写入全局上下文（NPC回应等仅供玩家查看的内容，不给AI处理）"""
        self.global_context.add_context(text)

    def snapshot(self) -> dict:
        """返回当前全部运行时状态的深拷贝快照，供 `!!` 回滚使用。"""
        return {
            'global_context': (deepcopy(self.global_context.context), self.global_context.count),
            'event_context': (deepcopy(self.event_context.context), self.event_context.count),
            'event_npc': deepcopy(self.event_npc.npc_list),
            'related_npc': deepcopy(self.related_npc.npc_list),
            'all_npc_list': deepcopy(self.all_npc_list.npc_list),
            'player': deepcopy(self.player.player_dict),
            'world': deepcopy(self.b1WorldLayer.dict),
        }

    def restore(self, snap: dict):
        """从 snapshot() 产出的快照全量恢复运行时状态。"""
        self.global_context = ContextHistory(snap['global_context'][0], snap['global_context'][1])
        self.event_context = ContextHistory(snap['event_context'][0], snap['event_context'][1])
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
        """
        for i, n in enumerate(self.event_npc.npc_list):
            if n.name == updated_npc.name:
                self.event_npc.npc_list[i] = updated_npc
                break
        for i, n in enumerate(self.all_npc_list.npc_list):
            if n.name == updated_npc.name:
                self.all_npc_list.npc_list[i] = updated_npc
                break

    # ===================== NPC 流水线 =====================

    def process_npc_pipeline(self, npc_name: str, npc: B3NPCLayer, should_print: bool,
                             n4_update, n5_response, perception_msg: str):
        """
        单个NPC的完整 N4→N5 流水线，供 ThreadPoolExecutor 并行调用。

        :param perception_msg: N3感知层分发的带方向消息（"玩家对你说：..."或"玩家对[名字]说：..."）
        :return: (npc_final, response, should_print, player_delta)
        """
        tag = '' if should_print else '（潜在）'

        # N4 客观处理（有玩家实例，比对客观事物）
        npc_obj, player_delta, facts = n4_update.update_for_npc(npc, self.player, perception_msg)
        logger.info(f'[{npc_name}]{tag} 客观事实：{facts}')

        # N5 主观处理（无玩家实例，防止隐藏事实暴露）
        npc_final, response = n5_response.response_say(
            npc_obj, perception_msg, self.global_context.context, facts
        )
        if should_print:
            logger.info(f'[{npc_name}] 回应：{response}')

        return npc_final, response, should_print, player_delta

    # ===================== 存档 / 读档 =====================

    def build_save_state(self, name: str) -> dict:
        """将当前运行时状态打包为可序列化的字典"""
        return {
            "meta": {
                "save_name": name,
                "version": SAVE_VERSION,
                "timestamp": time_int.ts_to_readable(time_int.get_minute_ts()),
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
            "event_npc_list": self.event_npc.to_dict(),
            "related_npc_list": self.related_npc.to_dict(),
            "all_npc_list": self.all_npc_list.to_dict(),
            "player_dict": self.player.player_dict,
            "world_dict": self.b1WorldLayer.dict,
        }

    def rebuild_game_state(self, save_dict: dict):
        """从存档字典重建 self 的全部运行时状态"""
        self.global_context = ContextHistory(
            context=save_dict["global_context"]["context"],
            count=save_dict["global_context"]["count"],
        )
        self.event_context = ContextHistory(
            context=save_dict["event_context"]["context"],
            count=save_dict["event_context"]["count"],
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
        """执行存档，自动附带日志文件"""
        state = self.build_save_state(name)
        self.save_manager.save(state, name, self._extra_save_files())
        print(f'💾 已存档：{name}')

    def _do_quick_save(self):
        """执行快速存档，自动附带日志文件"""
        state = self.build_save_state("quick_save")
        self.save_manager.quick_save(state, self._extra_save_files())
        print('💾 已快速存档')

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
            print(f'└── 全量NPC: {len(self.all_npc_list)}个 | /context 查看对话 /context event 查看事件')

        else:
            print(f'未知命令：{cmd}  可用：/save /load /saves /quick_save /quick_load /context /npcs')

        return 'continue'
