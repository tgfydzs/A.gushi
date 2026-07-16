from datetime import datetime
from typing import Optional, TextIO
import sys
import os

def get_project_root() -> str:
    env_root = os.getenv("PROJECT_ROOT")
    if env_root and os.path.isdir(env_root):
        return env_root
    return os.getcwd()

DEFAULT_LOG_PATH = os.path.join(get_project_root(), "../log/run.log")

# 日志级别对应分数
LEVEL_SCORE = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "ERROR": 40
}


class SimpleLogger:
    """
    日志级别 DEBUG：10，默认步进10

    :param session_id: 日志会话标识
    :param module_name: 模块名称
    :param log_file_path: 日志文件路径
    :param file_min_score: 文件写入最低级别分数，低于该分不写入文件
    :param console_min_score: 控制台打印最低级别分数，低于该分不打印控制台
    :param console_enable: 控制台总开关，False 直接不输出控制台
    :param file_append: True=追加写入日志；False=每次实例化覆盖清空原日志文件
    """
    def __init__(
        self,
        session_id: str = 'test001',
        module_name: str = "DEFAULT",
        log_file_path: str = DEFAULT_LOG_PATH,
        file_min_score: int = 10,
        console_min_score: int = 10,
        console_enable: bool = True,
        file_append: bool = False  # 新增：是否追加，False则覆盖
    ):
        self.sid = session_id
        self.module = module_name
        self.log_path = log_file_path
        self.fp: Optional[TextIO] = None

        # 文件日志阈值
        self.file_min_score = file_min_score
        # 控制台日志阈值
        self.console_min_score = console_min_score
        # 控制台总启停开关
        self.console_enable = console_enable
        # 文件写入模式：追加 / 覆盖
        self.file_append = file_append

        # 创建日志目录
        log_dir = os.path.dirname(self.log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # 打开文件：a 追加 / w 覆盖清空
        open_mode = "a" if self.file_append else "w"
        self.fp = open(self.log_path, open_mode, encoding="utf-8")

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _write_line(self, level: str, msg: str):
        score = LEVEL_SCORE[level]
        line = f"[{self._now_str()}] [{level:6s}] [SID:{self.sid}] [{self.module}] {msg}\n"

        # 1. 控制台输出判断：总开关开启 + 级别达标才打印
        if self.console_enable and score >= self.console_min_score:
            sys.stdout.write(line)
            sys.stdout.flush()

        # 2. 文件写入判断：级别达标才落盘
        if score >= self.file_min_score:
            # Linux/mac 文件锁防多进程错乱
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(self.fp, fcntl.LOCK_EX)

            self.fp.write(line)
            self.fp.flush()

            if sys.platform != "win32":
                fcntl.flock(self.fp, fcntl.LOCK_UN)

    def debug(self, msg: str):
        self._write_line("DEBUG", msg)

    def info(self, msg: str):
        self._write_line("INFO", msg)

    def warn(self, msg: str):
        self._write_line("WARN", msg)

    def error(self, msg: str):
        self._write_line("ERROR", msg)

    # 动态修改配置方法
    def set_console_switch(self, enable: bool):
        """一键开启/关闭控制台"""
        self.console_enable = enable

    def set_file_level(self, score: int):
        """修改文件日志过滤级别"""
        self.file_min_score = score

    def set_console_level(self, score: int):
        """修改控制台打印过滤级别"""
        self.console_min_score = score

    def close(self):
        if self.fp:
            try:
                self.fp.flush()
                self.fp.close()
            except Exception:
                pass
        self.fp = None

    def __del__(self):
        self.close()