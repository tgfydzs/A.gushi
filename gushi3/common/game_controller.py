"""
游戏控制器：统一管理运行时游戏状态、存档和命令处理。
将所有"管理类"逻辑从主流程中剥离，主流程只需关注 N1→N2→N3→N4→N5 的调用顺序。
"""

import os

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
        """同时写入全局上下文和事件上下文"""
        self.global_context.add_context(text)
        self.event_context.add_context(text)

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
                             n4_update, n5_response, player_input: str):
        """
        单个NPC的完整 N4→N5 流水线，供 ThreadPoolExecutor 并行调用。

        :return: (npc_final, response, should_print, player_delta)
                 player_delta = {"增加": {...}, "删减": {...}} 用于合并回玩家实例
        """
        tag = '' if should_print else '（潜在）'

        # N4 客观处理（有玩家实例，比对客观事物）
        npc_obj, player_delta, facts = n4_update.update_for_npc(npc, self.player, player_input)
        logger.info(f'[{npc_name}]{tag} 客观事实：{facts}')

        # N5 主观处理（无玩家实例，防止隐藏事实暴露）
        npc_final, response = n5_response.response_say(
            npc_obj, player_input, self.global_context.context, facts
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

        else:
            print(f'未知命令：{cmd}  可用：/save /load /saves /quick_save /quick_load')

        return 'continue'
