import time
from datetime import datetime











def get_minute_ts() -> int:
    """获取当前时间 精确到分钟的int时间戳"""
    return int(time.time() // 60)


def ts_to_readable(ts: int) -> str:
    """分钟int时间戳 转 可读时间字符串：2026-07-14 20:30"""
    sec = ts * 60
    return datetime.fromtimestamp(sec).strftime("%Y-%m-%d %H:%M")