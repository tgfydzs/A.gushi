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


class SimpleLogger:
    def __init__(
        self,
        session_id: str = 'test001',
        module_name: str = "DEFAULT",
        log_file_path: str = DEFAULT_LOG_PATH
    ):
        self.sid = session_id
        self.module = module_name
        self.log_path = log_file_path
        self.fp: Optional[TextIO] = None
        # 自动创建日志所在文件夹
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self.fp = open(self.log_path, "a", encoding="utf-8")

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _write_line(self, level: str, msg: str):
        line = f"[{self._now_str()}] [{level:6s}] [SID:{self.sid}] [{self.module}] {msg}\n"
        # 控制台输出
        sys.stdout.write(line)
        sys.stdout.flush()

        # 仅非Windows系统才加载fcntl加锁
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

    def close(self):
        if self.fp:
            try:
                self.fp.close()
            except Exception:
                pass

    def __del__(self):
        self.close()