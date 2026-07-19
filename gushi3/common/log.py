"""
日志系统 — 单例引擎 + 代理模式

全局只有一个文件句柄，各处通过 get_logger(name) 获取轻量代理对象，
无需传递 logger 实例。

用法:
    # ===== 程序入口（可选，但推荐调用一次以明确配置）=====
    from common import log
    log.setup(session_id="test001", file_min_score=10, console_min_score=30)

    # ===== 各模块中使用 =====
    from common.log import get_logger
    logger = get_logger(__name__)   # 或 get_logger("MyModule")
    logger.debug("调试信息")
    logger.info("正常信息")
    logger.warn("警告信息")
    logger.error("错误信息")

    # ===== 运行时动态调整 =====
    from common import log
    log.set_console_switch(False)       # 关闭控制台输出
    log.set_file_level(20)              # 文件只写 INFO 及以上
    log.set_console_level(30)           # 控制台只打印 WARN 及以上
"""

import sys
import os
import threading
from datetime import datetime
from typing import Optional

from common.base_cnf import *

DEFAULT_LOG_PATH = os.path.join(BASE_PATH, LOG_PATH)

LEVEL_SCORE = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "ERROR": 40
}


class _LogEngine:
    """内部日志引擎——全局唯一实例，持有唯一文件句柄"""

    def __init__(self):
        self.sid = "default"
        self.module = "DEFAULT"
        self.log_path = DEFAULT_LOG_PATH
        self.fp: Optional[object] = None
        self.file_min_score: int = LOG_FILE_LEVEL
        self.console_min_score: int = LOG_CONSOLE_LEVEL
        self.console_enable: bool = True
        self._lock = threading.Lock()
        self._opened = False

    # ==================== 初始化 ====================

    def setup(
        self,
        session_id: str = "test001",
        log_file_path: str = DEFAULT_LOG_PATH,
        file_min_score: int = LOG_FILE_LEVEL,
        console_min_score: int = LOG_CONSOLE_LEVEL,
        console_enable: bool = True,
        file_append: bool = False,
    ):
        """
        初始化/重配置日志引擎（程序入口处调用一次即可）。
        未调用 setup() 时首次写日志会自动以默认参数懒初始化。
        """
        self.sid = session_id
        self.log_path = log_file_path
        self.file_min_score = file_min_score
        self.console_min_score = console_min_score
        self.console_enable = console_enable

        self._open_file(file_append)

    def _lazy_init(self):
        """懒初始化：使用 base_cnf 里的默认值"""
        self.setup(
            session_id="default",
            log_file_path=DEFAULT_LOG_PATH,
            file_min_score=LOG_FILE_LEVEL,
            console_min_score=LOG_CONSOLE_LEVEL,
            console_enable=True,
            file_append=False,
        )

    def _open_file(self, file_append: bool):
        """打开/重开日志文件（线程安全）"""
        with self._lock:
            if self.fp:
                try:
                    self.fp.flush()
                    self.fp.close()
                except Exception:
                    pass
                self.fp = None

            log_dir = os.path.dirname(self.log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            open_mode = "a" if file_append else "w"
            self.fp = open(self.log_path, open_mode, encoding="utf-8")
            self._opened = True

    # ==================== 写入 ====================

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def write(self, level: str, msg: str, module: str):
        """统一写入入口（所有代理最终调这里）"""
        if not self._opened:
            self._lazy_init()

        score = LEVEL_SCORE[level]
        line = f"[{self._now_str()}] [{level:6s}] [SID:{self.sid}] [{module}] {msg}\n"

        with self._lock:
            # 控制台
            if self.console_enable and score >= self.console_min_score:
                sys.stdout.write(line)
                sys.stdout.flush()

            # 文件
            if score >= self.file_min_score and self.fp:
                self.fp.write(line)
                self.fp.flush()

    # ==================== 动态配置 ====================

    def set_console_switch(self, enable: bool):
        self.console_enable = enable

    def set_file_level(self, score: int):
        self.file_min_score = score

    def set_console_level(self, score: int):
        self.console_min_score = score

    def close(self):
        with self._lock:
            if self.fp:
                try:
                    self.fp.flush()
                    self.fp.close()
                except Exception:
                    pass
                self.fp = None
            self._opened = False


# ==================== 全局单例引擎 ====================
_engine = _LogEngine()


# ==================== 轻量代理 ====================

class _LoggerProxy:
    """
    日志代理——只存模块名，所有写入委托给全局 _engine。
    对调用方而言，接口与旧 SimpleLogger 完全兼容。
    """
    __slots__ = ("_module",)

    def __init__(self, module_name: str):
        self._module = module_name

    def debug(self, msg: str):
        _engine.write("DEBUG", msg, self._module)

    def info(self, msg: str):
        _engine.write("INFO", msg, self._module)

    def warn(self, msg: str):
        _engine.write("WARN", msg, self._module)

    def error(self, msg: str):
        _engine.write("ERROR", msg, self._module)

    @property
    def module(self):
        return self._module


# ==================== 公开 API ====================

def get_logger(module_name: str = "DEFAULT") -> _LoggerProxy:
    """
    获取一个日志代理对象。

    参数:
        module_name: 模块标识，会出现在每条日志的 [module] 字段中。
                     推荐传入 __name__ 或功能名称。

    返回:
        _LoggerProxy — 有 debug/info/warn/error 四个方法
    """
    return _LoggerProxy(module_name)


def setup(
    session_id: str = "test001",
    log_file_path: str = DEFAULT_LOG_PATH,
    file_min_score: int = LOG_FILE_LEVEL,
    console_min_score: int = LOG_CONSOLE_LEVEL,
    console_enable: bool = True,
    file_append: bool = False,
):
    """
    初始化日志引擎。

    不调用也可以——首次写日志时会用 base_cnf 默认值自动初始化。
    但推荐在程序入口显式调用一次，确保配置可控。

    参数:
        session_id:   会话标识，出现在日志 [SID:xxx] 字段
        log_file_path: 日志文件路径
        file_min_score: 文件最低级别分数（DEBUG=10, INFO=20, WARN=30, ERROR=40）
        console_min_score: 控制台最低级别分数
        console_enable: True=启用控制台输出
        file_append: True=追加写入；False=覆盖
    """
    _engine.setup(
        session_id=session_id,
        log_file_path=log_file_path,
        file_min_score=file_min_score,
        console_min_score=console_min_score,
        console_enable=console_enable,
        file_append=file_append,
    )


def set_console_switch(enable: bool):
    """运行时开关控制台输出"""
    _engine.set_console_switch(enable)


def set_file_level(score: int):
    """运行时修改文件最低级别"""
    _engine.set_file_level(score)


def set_console_level(score: int):
    """运行时修改控制台最低级别"""
    _engine.set_console_level(score)


def get_log_path() -> str:
    """返回当前日志文件的绝对路径"""
    return _engine.log_path


def close():
    """关闭日志文件（程序退出前可选调用）"""
    _engine.close()


# ==================== 旧版兼容（SimpleLogger 别名） ====================
# 让那些还不能立刻改的旧代码依然能跑（但会打一条 warning 到 stderr）
# 建议尽快迁移到 get_logger()

class SimpleLogger(_LoggerProxy):
    """
    旧版兼容别名 —— 已废弃，请改用 get_logger()。

    这不再是一个独立实例，只是 _LoggerProxy 的子类。
    session_id / module_name 等构造参数将被忽略；请使用 log.setup() 和 get_logger()。
    """
    def __init__(self, *args, **kwargs):
        import warnings
        warnings.warn(
            "SimpleLogger 已废弃，请改用: from common.log import get_logger; "
            "logger = get_logger(__name__)",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(module_name=kwargs.get("module_name", "DEFAULT"))
