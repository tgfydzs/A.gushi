import json
import os
import shutil
import datetime

from common.base_cnf import BASE_PATH, SAVE_ROOT_PATH, SAVE_VERSION
from common.log import get_logger

logger = get_logger("save_load")


# ===================== 异常定义 =====================

class SaveVersionError(Exception):
    """存档版本不兼容异常。调用方可选择降级处理或拒绝加载。"""
    def __init__(self, save_version: int, current_version: int):
        self.save_version = save_version
        self.current_version = current_version
        super().__init__(f"存档版本 v{save_version} 与当前 v{current_version} 不兼容")


class SaveManager:
    """
    存档管理器，负责游戏存档的保存、加载、列表和删除。

    每个存档是一个独立的文件夹，内部按数据组件分散为多个 JSON 文件：

        save/
        └── <存档名>/
            ├── meta.json               ← 元信息（玩家名、位置、轮次等）
            ├── global_context.json     ← 全局对话历史
            ├── event_context.json      ← 当前事件上下文
            ├── event_npc_list.json     ← 事件回应NPC列表
            ├── related_npc_list.json   ← 事件涉及NPC列表
            ├── all_npc_list.json       ← 全部NPC运行时状态
            ├── player_dict.json        ← 玩家数据
            └── world_dict.json         ← 世界层数据

    这种结构与 data/ 目录的导入风格一致，各组件独立文件方便人工检查和修改。
    """

    def __init__(self):
        """确保存档根目录存在"""
        self.save_dir = os.path.join(BASE_PATH, SAVE_ROOT_PATH)
        os.makedirs(self.save_dir, exist_ok=True)

    # ===================== 公共接口 =====================

    def save(self, state: dict, name: str, extra_files: dict[str, str] = None) -> str:
        """
        保存游戏状态到存档文件夹。

        :param state:       完整游戏状态字典（由调用方 build_save_state() 构建）
        :param name:        存档名，如 'slot_1' / 'quick_save'
        :param extra_files: 额外要复制进存档文件夹的文件，{目标文件名: 源文件绝对路径}。
                            用于存档日志等非 JSON 文件。复制在 JSON 写入之后进行。
        :return:            存档文件夹的绝对路径

        每个顶层 key 拆成一个独立 JSON 文件写入 <存档文件夹>/<key>.json。
        安全写入：先将每个文件写入 .tmp，全部成功后统一 rename。
        """
        folder_path = os.path.join(self.save_dir, name)
        os.makedirs(folder_path, exist_ok=True)

        if extra_files is None:
            extra_files = {}

        # 收集待写文件：key 去掉后缀筛选，写入各自文件中
        tmp_files = []
        target_files = []
        try:
            for key, value in state.items():
                file_name = f"{key}.json"
                tmp_path = os.path.join(folder_path, f".{file_name}.tmp")
                target_path = os.path.join(folder_path, file_name)

                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(value, f, ensure_ascii=False, indent=2)

                tmp_files.append(tmp_path)
                target_files.append(target_path)

            # 全部写入成功 → rename 逐个覆盖
            for tmp, tgt in zip(tmp_files, target_files):
                os.replace(tmp, tgt)

        except Exception as e:
            # 失败时清理残留 .tmp 文件
            for tmp in tmp_files:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            logger.error(f"存档保存失败：{folder_path}，错误：{e}")
            raise

        # 复制额外文件（日志等）
        for dest_name, src_path in extra_files.items():
            if os.path.isfile(src_path):
                shutil.copy2(src_path, os.path.join(folder_path, dest_name))

        logger.info(f"存档已保存：{folder_path} ({len(state)} 个JSON + {len(extra_files)} 个额外文件)")
        return folder_path

    def load(self, name: str) -> dict | None:
        """
        加载指定存档文件夹。

        :param name: 存档名
        :return:     存档字典（与 build_save_state() 产出同构）；文件夹不存在返回 None
        """
        folder_path = os.path.join(self.save_dir, name)
        if not os.path.isdir(folder_path):
            logger.warn(f"存档文件夹不存在：{folder_path}")
            return None

        state = {}
        try:
            for file_name in os.listdir(folder_path):
                if not file_name.endswith(".json"):
                    continue
                file_path = os.path.join(folder_path, file_name)
                key = file_name[:-5]  # 去掉 .json 后缀即是顶层 key 名
                with open(file_path, "r", encoding="utf-8") as f:
                    state[key] = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"读取存档失败：{folder_path}，错误：{e}")
            return None

        if not state:
            logger.warn(f"存档文件夹为空：{folder_path}")
            return None

        logger.info(f"存档已加载：{folder_path} ({len(state)} 个文件)")

        # 版本检查：只告警，不拒绝加载（未来可收紧为抛 SaveVersionError）
        if "meta" in state and isinstance(state["meta"], dict):
            save_version = state["meta"].get("version", 0)
            if save_version != SAVE_VERSION:
                logger.warn(
                    f"存档版本 v{save_version} ≠ 当前 v{SAVE_VERSION}，"
                    f"可能存在 Schema 不兼容，请留意异常"
                )

        return state

    def list_saves(self) -> list[dict]:
        """
        列出所有存档及其元信息。

        :return: [{
            'name': 'slot_1',
            'path': 'E:\\...\\save\\slot_1',
            'mtime': '2026-07-19 14:30:00',
            'file_count': 8,
            'meta': { ... }   # meta.json 的内容
        }, ...]

        按文件夹修改时间降序排列（最新的在前）。
        若缺少 meta.json，则 meta 为 None。
        """
        saves = []
        if not os.path.isdir(self.save_dir):
            return saves

        for entry_name in os.listdir(self.save_dir):
            entry_path = os.path.join(self.save_dir, entry_name)
            if not os.path.isdir(entry_path):
                continue  # 跳过松散文件（兼容旧格式遗留）

            # 检查是否为存档文件夹（至少含一个 .json 文件）
            json_files = [f for f in os.listdir(entry_path) if f.endswith(".json")]
            if not json_files:
                continue

            # 读取元信息
            meta = None
            meta_path = os.path.join(entry_path, "meta.json")
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass

            mtime = os.path.getmtime(entry_path)
            saves.append({
                "name": entry_name,
                "path": entry_path,
                "mtime": self._ts_to_readable(mtime),
                "file_count": len(json_files),
                "meta": meta,
            })

        saves.sort(key=lambda s: os.path.getmtime(s["path"]), reverse=True)
        return saves

    def delete(self, name: str) -> bool:
        """
        删除指定存档文件夹。

        :param name: 存档名
        :return:     True 删除成功 / False 文件夹不存在
        """
        folder_path = os.path.join(self.save_dir, name)
        if not os.path.isdir(folder_path):
            logger.warn(f"删除存档失败，文件夹不存在：{folder_path}")
            return False

        shutil.rmtree(folder_path)
        logger.info(f"存档已删除：{folder_path}")
        return True

    # ===================== 快速存取 =====================

    def quick_save(self, state: dict, extra_files: dict[str, str] = None) -> str:
        """保存快速存档（自动覆盖 quick_save 文件夹）"""
        return self.save(state, "quick_save", extra_files)

    def quick_load(self) -> dict | None:
        """加载快速存档"""
        return self.load("quick_save")

    # ===================== 内部工具 =====================

    @staticmethod
    def _ts_to_readable(timestamp: float) -> str:
        """将时间戳转为可读字符串"""
        dt = datetime.datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
