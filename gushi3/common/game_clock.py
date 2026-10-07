"""
游戏时钟 — 独立于现实时间，由流水线每轮更新。

内部统一以分钟为最小单位推进，显示格式由世界层的"时间格式"字段决定。
支持：现代24小时制、修仙时辰制、或任意自定义格式。
"""

from datetime import datetime, timedelta

# 时辰名称（修仙制默认使用，可被世界配置覆盖）
_DEFAULT_SHICHEN = [
    "子时", "丑时", "寅时", "卯时",
    "辰时", "巳时", "午时", "未时",
    "申时", "酉时", "戌时", "亥时",
]
_MINUTES_PER_SHICHEN = 120   # 一个时辰 = 120分钟
_MINUTES_PER_KE = 30          # 一刻 = 30分钟


class GameClock:
    """管理游戏内时间。"""

    def __init__(self, start_time_str: str = "2026-07-19 18:00",
                 time_format: str = "auto",
                 start_datetime: datetime = None,
                 elapsed_minutes: int = 0):
        """
        :param start_time_str: 任意格式的起始时间字符串
        :param time_format: 时间格式。可选："auto"（自动检测）、"24h"、"时辰制"、或自定义格式描述
        :param start_datetime: 直接传入 datetime（读档兼容）
        :param elapsed_minutes: 已流逝分钟数
        """
        self._start_str = start_time_str
        self._elapsed = elapsed_minutes
        self._time_format = time_format

        # 内部 datetime（仅 24h 制使用）
        self._start_dt: datetime = None
        if start_datetime is not None:
            self._start_dt = start_datetime
        else:
            try:
                self._start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
            except ValueError:
                pass

        # 自动检测格式
        if self._time_format == "auto":
            if self._start_dt is not None:
                self._time_format = "24h"
            else:
                self._time_format = "时辰制"

        # 时辰制：解析起始时辰和刻
        self._start_shichen_idx = 0   # 起始时辰索引（0=子时, 11=亥时）
        self._start_ke = 0            # 起始刻数（0-3）
        if self._time_format == "时辰制":
            self._parse_shichen_start(start_time_str)

    def _parse_shichen_start(self, s: str):
        """从起始时间字符串中提取初始时辰和刻数。如 '午时' → idx=6, ke=0；'午时三刻' → idx=6, ke=3"""
        for i, name in enumerate(_DEFAULT_SHICHEN):
            if name in s:
                self._start_shichen_idx = i
                # 尝试解析刻数
                s2 = s[s.index(name):]
                for ke in range(4):
                    ke_names = ["初", "一", "二", "三"]
                    if f"{ke_names[ke]}刻" in s2 or f"{ke}刻" in s2:
                        self._start_ke = ke
                        break
                break

    # ===================== 时间操作 =====================

    def now_datetime(self) -> datetime | None:
        """当前游戏时间 datetime 对象。非24h制返回 None"""
        if self._start_dt:
            return self._start_dt + timedelta(minutes=self._elapsed)
        return None

    def advance(self, minutes: int):
        """推进 N 分钟"""
        if minutes > 0:
            self._elapsed += minutes

    # ===================== 格式化输出 =====================

    def now_str(self) -> str:
        """当前游戏时间的可读字符串，由时间格式决定显示方式"""
        if self._time_format == "24h" and self._start_dt:
            return (self._start_dt + timedelta(minutes=self._elapsed)).strftime("%Y-%m-%d %H:%M")

        if self._time_format == "时辰制":
            total_ke = self._start_shichen_idx * 4 + self._start_ke + self._elapsed // _MINUTES_PER_KE
            shichen_idx = total_ke // 4 % 12
            ke = total_ke % 4
            day_shift = total_ke // 48

            ke_name = ["初", "一", "二", "三"][ke]
            shichen_name = _DEFAULT_SHICHEN[shichen_idx]

            # 取起始时间中最后一个时辰标记之前的部分作为前缀
            prefix = self._start_str
            for name in _DEFAULT_SHICHEN:
                if name in prefix:
                    prefix = prefix[:prefix.index(name)]
                    break
            prefix = prefix.rstrip("·")

            result = f"{prefix}·{shichen_name}{ke_name}刻"
            if day_shift > 0:
                result += f"（{day_shift}日后）"
            return result

        # 兜底：原样显示起始 + 经过时间
        hours = self._elapsed // 60
        mins = self._elapsed % 60
        if hours > 0:
            return f"{self._start_str}（已过{hours}时{mins}分）"
        elif mins > 0:
            return f"{self._start_str}（已过{mins}分）"
        return self._start_str

    def is_night(self) -> bool:
        if self._start_dt:
            h = (self._start_dt + timedelta(minutes=self._elapsed)).hour
            return h >= 22 or h < 6
        # 时辰制：与 24h 分支对齐（22:00–06:00 为夜晚）
        # 对应时辰：亥(11)、子(0)、丑(1)、寅(2)、卯(3)
        # 注意 idx 模 12 回绕，深夜子丑寅会回到 0-2，必须用 `or idx <= 3` 覆盖
        if self._time_format == "时辰制":
            total_ke = self._start_shichen_idx * 4 + self._start_ke + self._elapsed // _MINUTES_PER_KE
            idx = total_ke // 4 % 12
            return idx >= 11 or idx <= 3  # 亥子丑寅卯 → 深夜/凌晨
        return False

    def is_daytime(self) -> bool:
        return not self.is_night()

    # ===================== 序列化 =====================

    def to_dict(self) -> dict:
        d = {
            "start_str": self._start_str,
            "elapsed_minutes": self._elapsed,
            "time_format": self._time_format,
            "start_shichen_idx": self._start_shichen_idx,
            "start_ke": self._start_ke,
        }
        if self._start_dt:
            d["start_dt"] = self._start_dt.strftime("%Y-%m-%d %H:%M")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GameClock":
        dt = None
        if "start_dt" in d:
            dt = datetime.strptime(d["start_dt"], "%Y-%m-%d %H:%M")
        return cls(
            start_time_str=d.get("start_str", "2026-07-19 18:00"),
            time_format=d.get("time_format", "auto"),
            start_datetime=dt,
            elapsed_minutes=d.get("elapsed_minutes", 0),
        )._restore_shichen(d)

    def _restore_shichen(self, d: dict) -> "GameClock":
        """从存档恢复时辰偏移"""
        if self._time_format == "时辰制":
            self._start_shichen_idx = d.get("start_shichen_idx", 0)
            self._start_ke = d.get("start_ke", 0)
        return self

    @classmethod
    def from_world(cls, world_dict: dict) -> "GameClock":
        """从 B1 世界字典读取起始时间和时间格式"""
        start = world_dict.get("游戏开始时间", "2026-07-19 18:00")
        fmt = world_dict.get("时间格式", "auto")
        return cls(start_time_str=start, time_format=fmt)
