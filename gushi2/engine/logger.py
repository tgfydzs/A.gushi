"""TickLogger — 运行日志记录.

记录每次 Tick 的关键信息：节点执行详情、prompt、model、response、时间消耗。
日志格式为 JSON，存储在 logs/ 目录下。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _ensure_log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


# =============================================================================
# Tick 日志
# =============================================================================


class TickLogger:
    """Tick 级别日志记录器.

    用法:
        logger = TickLogger()
        logger.start_tick(tick_id=1, player_input="hello")
        logger.log_node("N7", node_output={"continue_context": False})
        ...
        logger.finish_tick(narrative="some text")
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._tick_id: int = 0
        self._start_time: float = 0.0
        self._player_input: str = ""
        self._nodes: list[dict] = []
        self._narrative: str = ""
        self._error: str = ""

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start_tick(self, tick_id: int, player_input: str) -> None:
        """开始记录一次 Tick."""
        if not self._enabled:
            return
        self._tick_id = tick_id
        self._start_time = time.time()
        self._player_input = player_input
        self._nodes.clear()
        self._narrative = ""
        self._error = ""

    def log_node(
        self,
        node_name: str,
        success: bool = True,
        raw_input: str = "",
        raw_output: str = "",
        error: str = "",
    ) -> None:
        """记录一个节点的执行详情."""
        if not self._enabled:
            return
        self._nodes.append({
            "node": node_name,
            "success": success,
            "input_preview": raw_input[:200] if raw_input else "",
            "output_preview": raw_output[:500] if raw_output else "",
            "error": error,
            "timestamp": datetime.now().isoformat(),
        })

    def finish_tick(
        self,
        narrative: str = "",
        error: str = "",
    ) -> Optional[str]:
        """完成一次 Tick，写入日志文件."""
        if not self._enabled:
            return None

        elapsed = time.time() - self._start_time

        log_entry = {
            "tick_id": self._tick_id,
            "timestamp": datetime.now().isoformat(),
            "player_input": self._player_input,
            "elapsed_seconds": round(elapsed, 3),
            "success": not bool(error),
            "error": error,
            "narrative": narrative,
            "nodes": self._nodes,
        }

        log_dir = _ensure_log_dir()
        filename = f"tick_{self._tick_id:04d}.json"
        filepath = log_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)

        return str(filepath)
