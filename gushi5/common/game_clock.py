"""
游戏时钟 — 独立于现实时间，由流水线每轮更新。

内部统一以**分钟**为最小单位推进（`_elapsed` 是**时间的唯一真源**），显示由世界的**历法**决定。

⚠️ **历法必须可迁移**：这一层不许知道"时辰""仲夏""刻"是什么。
先前版本把 12 个时辰名、12 个月名、30 分/刻、30 日/月、12 月/年 全写成模块常量，
于是**换个纪年就得改代码**（干支、年号、自定义进制都过不去）。
现在这些全从世界的 `历法` 数据块读（形状见 `_DEFAULT_CALENDAR`）：

    `单位`：从最底层往上排的阶梯
      · `进制`      多少个**下一级单位**凑成 1 个本级；最底层是"多少个分钟"
      · `名表`      该级用名字显示（子时/孟春…）；没有名表就显示数字
      · `起点`      名表**下标 0 对应的显示值**（子时=0、孟春=1）；数字级默认 1
      · `循环名表` + `循环周期` + `循环起点`  该级按周期取名（如 60 甲子）
      · `显示偏移`  显示时加上它（年号：绝对年 627 → 显示 1）
      · `值为1时省略`  该级等于 1 时不写（"月初不写日"）
    `模板`：`"{纪元}{年}年·{月}·{日}日·{时辰}{刻}刻"`，占位符名对上 `单位.名`
    `数字`：中文 / 阿拉伯（只影响没有名表的级）
    `纪元`：模板里 `{纪元}` 的值（不写就从 `游戏开始时间` 串里取）
    `夜间`：哪些名字算夜里（`is_night` 用）

内置一份 `_DEFAULT_CALENDAR`（就是原来的时辰制），世界没写 `历法` 时用它，
所以老世界、老存档照旧跑。

⚠️ **真农历做不到**：月长 29/30 不定 + 闰月，需要万年历表/算法，不是阶梯能表达的。
需要"按年给月长表"时那是另一层数据，本层不假装支持。

⚠️ 本模块**不 import 日志**（`common.log` 会连带拉 `base_cnf`，那要求 API_KEY 在 import 时就存在）。
历法配错时把原因写进 `calendar_error`，由调用方去记。
"""

import re
from datetime import datetime, timedelta

# ===================== 中文数字 ⇄ 整数 =====================
# 这是**通用数字写法**，不是历法知识：只负责"三千七百四十二" ⇄ 3742。
# 哪个级用中文写、哪个用阿拉伯写，由 `历法.数字` 决定。

_CN_DIGITS = "零一二三四五六七八九"
_CN_SMALL_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_BIG_UNIT = {"万": 10000, "亿": 100000000}

#: 解析起始串时用来抓数字
_NUM_PAT = r'[0-9零〇一二三四五六七八九十百千两]+'

_MINUTES_PER_DAY = 1440
_TOK = re.compile(r'\{([^{}]+)\}')


def int_to_cn(n: int) -> str:
    """整数 → 中文数字（支持到亿级）。0 → "零"，10 → "十"（不写"一十"）。"""
    if n < 0:
        return "负" + int_to_cn(-n)
    if n == 0:
        return "零"

    groups = []
    remain = n
    while remain > 0:
        groups.append(remain % 10000)
        remain //= 10000

    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            if parts and not parts[-1].endswith("零"):
                parts.append("零")
            continue
        if parts and g < 1000 and not parts[-1].endswith("零"):
            parts.append("零")
        parts.append(_four_to_cn(g) + ["", "万", "亿", "万亿"][gi])

    out = "".join(parts).rstrip("零")
    if out.startswith("一十"):
        out = out[1:]
    return out or "零"


def _four_to_cn(g: int) -> str:
    """0 < g < 10000 的四位组 → 中文（不含"万/亿"后缀）"""
    chars = []
    pending_zero = False
    for i in (3, 2, 1, 0):
        d = (g // (10 ** i)) % 10
        if d == 0:
            if chars:
                pending_zero = True
            continue
        if pending_zero:
            chars.append("零")
            pending_zero = False
        chars.append(_CN_DIGITS[d] + ["", "十", "百", "千"][i])
    return "".join(chars)


def cn_to_int(text: str) -> int | None:
    """中文数字 → 整数；解析不出返回 None。纯阿拉伯数字也接受。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digit_val = {c: i for i, c in enumerate(_CN_DIGITS)}
    digit_val["〇"] = 0
    digit_val["两"] = 2

    total = section = num = 0
    seen = False
    for ch in text:
        if ch in digit_val:
            num = digit_val[ch]
            seen = True
        elif ch in _CN_SMALL_UNIT:
            section += (num or 1) * _CN_SMALL_UNIT[ch]   # "十二" 的十前面没有数
            num = 0
            seen = True
        elif ch in _CN_BIG_UNIT:
            section = (section + num) * _CN_BIG_UNIT[ch]
            total += section
            section = 0
            num = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + section + num


# ===================== 内置历法（= 原来的时辰制）=====================
# 世界在 `global.json` 写 `历法` 块就覆盖它；不写就用这份，所以老世界不用改数据。
# 这里**没有** `起始`：起始点从世界的 `游戏开始时间` 串里按这份阶梯解析。

_DEFAULT_CALENDAR = {
    "模板": "{纪元}{年}年·{月}·{日}日·{时辰}{刻}刻",
    "数字": "中文",
    "夜间": ["亥时", "子时", "丑时", "寅时", "卯时"],
    "单位": [
        {"名": "刻",   "进制": 30, "名表": ["初", "一", "二", "三"], "起点": 0},
        {"名": "时辰", "进制": 4, "起点": 0,
         "名表": ["子时", "丑时", "寅时", "卯时", "辰时", "巳时",
                  "午时", "未时", "申时", "酉时", "戌时", "亥时"]},
        {"名": "日",   "进制": 12, "值为1时省略": True},
        {"名": "月",   "进制": 30, "起点": 1,
         "名表": ["孟春", "仲春", "季春", "孟夏", "仲夏", "季夏",
                  "孟秋", "仲秋", "季秋", "孟冬", "仲冬", "季冬"]},
        {"名": "年",   "进制": 12},
    ],
}


def _parse_template(tpl: str):
    """
    `"{纪元}{年}年·{月}·…"` → `(前缀, [[单位名, 后缀], ...])`

    **后缀归前一个单位**：`{月}·{日}日·` 里 `·` 是「月」的后缀、`日·` 是「日」的后缀。
    这样"省略某个单位"只丢它自己的后缀，分隔符跟着前一级走，不会留下多余的 `·`。
    """
    slots, prefix, pos = [], '', 0
    for m in _TOK.finditer(tpl or ''):
        lit = tpl[pos:m.start()]
        if not slots and not prefix:
            prefix = lit
        elif slots:
            slots[-1][1] += lit
        pos = m.end()
        slots.append([m.group(1), ''])
    if slots:
        slots[-1][1] += tpl[pos:]
    return prefix, slots


def _normalize_spec(raw) -> dict | None:
    """把世界给的 `历法` 块校一遍；不合法返回 None（调用方回落内置历法 + 报出来）。"""
    if not isinstance(raw, dict):
        return None
    units = [u for u in (raw.get('单位') or []) if isinstance(u, dict) and u.get('名')]
    if not units:
        return None

    clean = []
    for u in units:
        try:
            radix = int(u.get('进制') or 0)
        except (TypeError, ValueError):
            return None
        if radix <= 0:
            return None
        item = {'名': str(u['名']), '进制': radix}
        names = u.get('名表')
        try:
            if isinstance(names, list) and names:
                item['名表'] = [str(x) for x in names]
                item['起点'] = int(u.get('起点', 0))
            else:
                item['起点'] = int(u.get('起点', 1))     # 数字级默认从 1 起（日/年）
            cycle = u.get('循环名表')
            if isinstance(cycle, list) and cycle:
                item['循环名表'] = [str(x) for x in cycle]
                item['循环周期'] = int(u.get('循环周期') or len(cycle))
                item['循环起点'] = int(u.get('循环起点', 1))
            if '显示偏移' in u:
                item['显示偏移'] = int(u.get('显示偏移') or 0)
        except (TypeError, ValueError):
            return None
        if u.get('值为1时省略'):
            item['值为1时省略'] = True
        clean.append(item)

    return {
        '模板': str(raw.get('模板') or _DEFAULT_CALENDAR['模板']),
        '数字': str(raw.get('数字') or '中文'),
        '纪元': str(raw.get('纪元') or ''),
        '夜间': [str(x) for x in (raw.get('夜间') or [])],
        '起始': raw.get('起始') if isinstance(raw.get('起始'), dict) else None,
        '单位': clean,
    }


class GameClock:
    """管理游戏内时间。"""

    #: 历法配错时的原因（空串=没问题）。本模块不写日志，由调用方读它去记。
    calendar_error = ''

    def __init__(self, start_time_str: str = "2026-07-19 18:00",
                 time_format: str = "auto",
                 start_datetime: datetime = None,
                 elapsed_minutes: int = 0):
        """
        :param start_time_str: 任意格式的起始时间字符串（非 24h 制下按历法阶梯解析）
        :param time_format: "auto" / "24h" / 其它（其它都走历法阶梯）
        :param start_datetime: 直接传入 datetime（24h 读档兼容）
        :param elapsed_minutes: 已流逝分钟数
        """
        self._start_str = start_time_str
        self._elapsed = elapsed_minutes
        self._time_format = time_format

        self._start_dt: datetime = None
        if start_datetime is not None:
            self._start_dt = start_datetime
        else:
            try:
                self._start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
            except ValueError:
                pass
        if self._time_format == "auto":
            self._time_format = "24h" if self._start_dt is not None else "时辰制"

        self._spec = _normalize_spec(_DEFAULT_CALENDAR)
        self._rebuild()

    # ===================== 历法装载 =====================

    def apply_calendar_config(self, world_layer) -> "GameClock":
        """
        从世界层读 `历法` 块。

        ⚠️ 读档路径上时钟**先于**世界层构造，所以不能指望构造函数自己拿到它——
        调用点都在 world_layer 就绪之后。重复调用幂等。
        """
        if world_layer is None:
            return self
        raw = None
        try:
            raw = world_layer.dict.get('历法')
        except AttributeError:
            raw = None

        spec = _normalize_spec(raw)
        if raw is not None and spec is None:
            # 写了但写错 → 回落内置，并**把原因说出来**（不许静默按旧历法跑）
            spec = _normalize_spec(_DEFAULT_CALENDAR)
            self.calendar_error = ('世界配置的 `历法` 块不合法'
                                   '（`单位` 每级要有 `名` 与 `进制≥1`），已回落内置历法')
        else:
            self.calendar_error = ''
        self._spec = spec or _normalize_spec(_DEFAULT_CALENDAR)
        self._rebuild()
        return self

    def _rebuild(self):
        """把历法编译成：模板槽位 / 各级等于多少分钟 / 起始分钟偏移。"""
        spec = self._spec
        self._prefix, self._slots = _parse_template(spec['模板'])
        self._units = spec['单位']
        self._by_name = {u['名']: u for u in self._units}
        self._suffix = {name: suffix for name, suffix in self._slots}

        # 每级 = 多少分钟（下标 0 最底层）
        self._cum, acc = [], 1
        for u in self._units:
            acc *= u['进制']
            self._cum.append(acc)

        explicit = spec.get('起始') or {}
        parsed, era_parsed = {}, ''
        if self._time_format != '24h' and self._start_str:
            parsed, era_parsed = self._parse_start_str(self._start_str)
        self._era = spec.get('纪元') or era_parsed

        start = {}
        for u in self._units:
            raw = explicit.get(u['名'])
            if raw is None:
                raw = parsed.get(u['名'])
            try:
                start[u['名']] = int(u['起点'] if raw is None else raw)
            except (TypeError, ValueError):
                start[u['名']] = int(u['起点'])
        self._start = start

        self._start_minutes = 0
        for i, u in enumerate(self._units):
            self._start_minutes += (start[u['名']] - u['起点']) * self._cum[i]

        # 历法可用吗：非 24h 制，且（没有「年」这一级，或者「年」解析出来了）
        year = next((u for u in self._units if u['名'] == '年'), None)
        self._usable = self._time_format != '24h'
        if year is not None:
            self._usable = self._usable and ('年' in explicit or '年' in parsed)
        if self._time_format != '24h' and not self._usable:
            # 显示会退化成"前缀 +（N日后）"。**这是降级，要能看见**，不能悄悄生效。
            self.calendar_error = (self.calendar_error + '；' if self.calendar_error else '') + \
                (f'起始串「{self._start_str}」里解析不出年份，显示降级为「前缀（N日后）」。'
                 f'要么在 `历法.起始` 里直接给年，要么让起始串带上 `年`')

    # ===================== 起始串解析（按阶梯，不写死名字）=====================

    def _parse_start_str(self, s: str):
        """
        从 `游戏开始时间` 里按**本历法的名表/模板**取值 → `({单位名: 显示值}, 纪元)`。

        规则不含历法知识：
          · 有名表的级 → 找 `<名><后缀>`（后缀是分隔符如 `·` 时只找 `<名>`）
          · 无名的级   → 找 `<数字><后缀>`（如 `…42年`、`…3日`）
        返回值是**显示值**（名表下标 + 起点），与 `起始` 的写法一致。
        """
        values = {}
        for u in self._units:
            name, names = u['名'], u.get('名表')
            suffix = (self._suffix.get(name) or '').strip()
            if suffix and not suffix.strip('·-/: '):
                suffix = ''        # 后缀只是个分隔符 → 只用名字本身匹配
            if names:
                for idx, nm in enumerate(names):
                    if nm and f'{nm}{suffix}' in s:
                        values[name] = idx + u['起点']
                        break
                continue
            if suffix:
                m = re.search(f'({_NUM_PAT}){re.escape(suffix)}', s)
                if m:
                    v = cn_to_int(m.group(1))
                    if v is not None:
                        values[name] = v

        # 纪元 = 年那一级前面的文字（`太初历三千七百四十二年` → `太初历`）
        year = next((u for u in reversed(self._units) if '名表' not in u), None)
        era = ''
        if year is not None:
            suffix = (self._suffix.get(year['名']) or '').strip()
            m = re.search(f'({_NUM_PAT}){re.escape(suffix)}', s) if suffix else None
            if m:
                era = s[:m.start()].strip('·-/: ')
        return values, era

    # ===================== 时间操作 =====================

    @property
    def elapsed_minutes(self) -> int:
        """已流逝的分钟数（**时间的唯一真源**）"""
        return self._elapsed

    def now_datetime(self) -> datetime | None:
        """当前游戏时间 datetime 对象。非 24h 制返回 None"""
        if self._start_dt:
            return self._start_dt + timedelta(minutes=self._elapsed)
        return None

    def advance(self, minutes: int):
        """推进 N 分钟"""
        if minutes > 0:
            self._elapsed += minutes

    # ===================== 渲染 =====================

    def _parts(self, elapsed: int) -> dict:
        """分钟 → `{单位名: 显示值}`（自顶向下按进制剥离）"""
        rem = self._start_minutes + int(elapsed or 0)
        out = {}
        for i in range(len(self._units) - 1, -1, -1):
            u, cum = self._units[i], self._cum[i]
            out[u['名']] = rem // cum + u['起点']
            rem %= cum
        return out

    def _render_value(self, u: dict, value: int) -> str:
        """单个单位的显示值 → 文字：名表 / 循环名表 / 数字 三选一。"""
        names = u.get('名表')
        if names:
            idx = value - u['起点']
            return names[idx] if 0 <= idx < len(names) else str(value)
        cycle = u.get('循环名表')
        if cycle:
            period = u.get('循环周期') or len(cycle)
            return cycle[(value - u.get('循环起点', 1)) % period]
        shifted = value + u.get('显示偏移', 0)
        return int_to_cn(shifted) if self._spec['数字'] == '中文' else str(shifted)

    def _render(self, elapsed: int) -> str:
        parts = self._parts(elapsed)
        out = [self._prefix]
        for name, suffix in self._slots:
            if name == '纪元':
                out.append(self._era)
                continue
            u = self._by_name.get(name)
            if u is None:
                # 模板里写了不存在的单位：原样留着，配错了要看得见（不许静默吞掉）
                out.append('{' + name + '}' + suffix)
                continue
            value = parts.get(name)
            if u.get('值为1时省略') and value == 1:
                continue
            out.append(self._render_value(u, value))
            out.append(suffix)
        return ''.join(out)

    def _fmt_legacy(self, elapsed: int) -> str:
        """解析不出历法时的旧行为：起始串前缀 +（N日后）。只为不崩，不追求准。"""
        prefix = self._start_str or ''
        cut = len(prefix)
        for u in self._units:
            for nm in (u.get('名表') or []):
                if nm and nm in prefix:
                    cut = min(cut, prefix.index(nm))
        prefix = prefix[:cut].rstrip('·-/: ')
        days = int(elapsed or 0) // _MINUTES_PER_DAY
        return prefix + (f'（{days}日后）' if days > 0 else '')

    def str_at(self, elapsed_minutes: int) -> str:
        """
        把"已流逝 N 分钟"渲染成可读时间串。

        任务期限要对任意时刻求值（`str_at(now + 跨度)`），
        所以显示必须能算任意时刻，不是只能渲染"现在"。
        """
        elapsed = int(elapsed_minutes or 0)
        if self._time_format == "24h" and self._start_dt:
            return (self._start_dt + timedelta(minutes=elapsed)).strftime("%Y-%m-%d %H:%M")
        if self._usable:
            return self._render(elapsed)
        return self._fmt_legacy(elapsed)

    def now_str(self) -> str:
        """当前游戏时间的可读字符串"""
        return self.str_at(self._elapsed)

    def is_night(self) -> bool:
        """夜晚判定：24h 用时钟，其余用历法里的 `夜间` 名表。"""
        if self._start_dt:
            h = (self._start_dt + timedelta(minutes=self._elapsed)).hour
            return h >= 22 or h < 6
        night = self._spec.get('夜间') or []
        if not night:
            return False
        parts = self._parts(self._elapsed)
        for u in self._units:
            if not u.get('名表'):
                continue
            value = parts.get(u['名'])
            names = u['名表']
            idx = None if value is None else value - u['起点']
            if idx is not None and 0 <= idx < len(names):
                return names[idx] in night
        return False

    def is_daytime(self) -> bool:
        return not self.is_night()

    # ===================== 时间跨度换算 =====================

    def unit_minutes(self) -> dict:
        """`{单位名: 多少分钟}`（由阶梯推导，不含任何历法常量）"""
        return {u['名']: self._cum[i] for i, u in enumerate(self._units)}

    def span_to_minutes(self, value, unit: str = '分钟') -> int | None:
        """
        把「(数值, 单位)」换算成分钟。单位认不出返回 None（调用方记 WARN，不许静默当 0）。

        单位表来自**本世界的历法阶梯**：`月` 就是"1 个月多少分钟"，
        所以"修炼一年"跳出来的日历恰好回落到同一处，不会漂成 5 天。
        """
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return 0

        u = str(unit or '分钟').strip()
        table = self.unit_minutes()
        if u in ('分钟', '分'):
            per = 1
        elif u in ('周', '星期'):
            per = 7 * _MINUTES_PER_DAY
        elif u in ('天', '日') and '日' not in table:
            per = _MINUTES_PER_DAY
        else:
            per = table.get(u)
        if per is None:
            return None
        return int(round(v * per))

    # ===================== 序列化 =====================

    def to_dict(self) -> dict:
        d = {
            "start_str": self._start_str,
            "elapsed_minutes": self._elapsed,
            "time_format": self._time_format,
        }
        if self._start_dt:
            d["start_dt"] = self._start_dt.strftime("%Y-%m-%d %H:%M")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GameClock":
        dt = None
        if "start_dt" in d:
            dt = datetime.strptime(d["start_dt"], "%Y-%m-%d %H:%M")
        # ⚠️ 历法（年/月/日）**不单独存**：由 start_str 按世界历法解析、由 elapsed_minutes 派生。
        # 存第二份就等于给"现在几点"开第二个真源。旧档的 start_shichen_idx / start_ke
        # 一并作废（本来就是 start_str 的冗余），读档时忽略。
        return cls(
            start_time_str=d.get("start_str", "2026-07-19 18:00"),
            time_format=d.get("time_format", "auto"),
            start_datetime=dt,
            elapsed_minutes=d.get("elapsed_minutes", 0),
        )

    @classmethod
    def from_world(cls, world_dict: dict) -> "GameClock":
        """从 B1 世界字典读取起始时间和时间格式"""
        return cls(
            start_time_str=world_dict.get("游戏开始时间", "2026-07-19 18:00"),
            time_format=world_dict.get("时间格式", "auto"),
        )
