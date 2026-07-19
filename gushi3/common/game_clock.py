"""
游戏时钟 — 独立于现实时间，由流水线每轮更新。

功能：
- 从 B1 世界规则读取起始时间
- 每轮由 N8 时间层推进
- 提供格式化的游戏时间给上下文系统
- 支持存档/读档
"""

from datetime import datetime, timedelta


class GameClock:
    """管理游戏内时间。现实世界的钟不动，这个钟负责推进。"""

    def __init__(self, start_time_str: str = "2026-07-19 18:00",
                 start_datetime: datetime = None,
                 elapsed_minutes: int = 0):
        """
        :param start_time_str: "YYYY-MM-DD HH:MM" 格式的起始时间
        :param start_datetime: 直接传入 datetime（读档时使用，覆盖 start_time_str）
        :param elapsed_minutes: 已流逝分钟数（读档时使用）
        """
        if start_datetime is not None:
            self._start = start_datetime
        else:
            self._start = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        self._elapsed = elapsed_minutes

    # ===================== 时间操作 =====================

    def now(self) -> datetime:
        """当前游戏时间"""
        return self._start + timedelta(minutes=self._elapsed)

    def advance(self, minutes: int):
        """推进 N 分钟"""
        if minutes > 0:
            self._elapsed += minutes

    # ===================== 格式化输出 =====================

    def now_str(self) -> str:
        """当前游戏时间的可读字符串：2026-07-19 18:23"""
        return self.now().strftime("%Y-%m-%d %H:%M")

    def now_hhmm(self) -> str:
        """短格式：18:23"""
        return self.now().strftime("%H:%M")

    def now_date(self) -> str:
        """日期：2026-07-19"""
        return self.now().strftime("%Y-%m-%d")

    def is_night(self) -> bool:
        """是否夜间（22:00-06:00）"""
        h = self.now().hour
        return h >= 22 or h < 6

    def is_daytime(self) -> bool:
        return not self.is_night()

    # ===================== 序列化 =====================

    def to_dict(self) -> dict:
        return {
            "start": self._start.strftime("%Y-%m-%d %H:%M"),
            "elapsed_minutes": self._elapsed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameClock":
        return cls(start_time_str=d["start"], elapsed_minutes=d.get("elapsed_minutes", 0))

    @classmethod
    def from_world(cls, world_dict: dict) -> "GameClock":
        """从 B1 世界字典读取起始时间"""
        start = world_dict.get("游戏开始时间", "2026-07-19 18:00")
        return cls(start_time_str=start)
