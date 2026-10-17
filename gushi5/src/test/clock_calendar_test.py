# -*- coding: utf-8 -*-
"""
GameClock 历法自检（不联网、不写存档）。

两层：① 现有世界的时辰制行为**一字不能变**；② 换一套 `历法` 数据就能换纪年
（公历风、干支、年号），**代码零改动**——这才是"引擎可迁移"的验收点。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.game_clock import GameClock, int_to_cn, cn_to_int

ok = fail = 0


def check(name, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f'  [OK] {name}: {got}')
    else:
        fail += 1
        print(f'  [FAIL] {name}\n        got ={got!r}\n        want={want!r}')


D, H = 1440, 60
START = '太初历三千七百四十二年·仲夏·午时'

print('== 1. 中文数字往返（通用工具，不是历法知识）==')
for n in (0, 1, 5, 10, 11, 20, 21, 99, 100, 101, 110, 123, 1000, 1001, 3742, 12000, 100000, 37420000):
    s = int_to_cn(n)
    check(f'{n} → {s}', cn_to_int(s), n)
check('10 不写一十', int_to_cn(10), '十')
check('3742 写法', int_to_cn(3742), '三千七百四十二')
check('解析阿拉伯', cn_to_int('2026'), 2026)
check('解析中文', cn_to_int('三千七百四十二'), 3742)

print('== 2. 现有世界的时辰制：行为一字不变 ==')
c = GameClock(START, '时辰制')
check('起点', c.now_str(), '太初历三千七百四十二年·仲夏·午时初刻')
c.advance(213)
check('+213分（原实测值）', c.now_str(), '太初历三千七百四十二年·仲夏·未时三刻')
check('elapsed_minutes', c.elapsed_minutes, 213)
check('is_night(未时)', c.is_night(), False)

c = GameClock(START, '时辰制')
c.advance(2 * D)
check('+2日 → 出现日', c.now_str(), '太初历三千七百四十二年·仲夏·三日·午时初刻')

c = GameClock(START, '时辰制')
c.advance(30 * D)
check('+30日 → 月名进一', c.now_str(), '太初历三千七百四十二年·季夏·午时初刻')

c = GameClock(START, '时辰制')
c.advance(360 * D)
check('+1年（30日/月 × 12月）', c.now_str(), '太初历三千七百四十三年·仲夏·午时初刻')

c = GameClock(START, '时辰制')
c.advance(2 * 360 * D)
check('+2年', c.now_str(), '太初历三千七百四十四年·仲夏·午时初刻')

c = GameClock(START, '时辰制')
c.advance(30 * D + 2 * 360 * D)
check('+2年1月', c.now_str(), '太初历三千七百四十四年·季夏·午时初刻')

print('== 3. 24h 制回归 ==')
c = GameClock('2026-07-19 18:00', '24h')
check('24h 起点', c.now_str(), '2026-07-19 18:00')
c.advance(90)
check('24h +90分', c.now_str(), '2026-07-19 19:30')
c.advance(6 * H)
check('24h 跨日', c.now_str(), '2026-07-20 01:30')
check('24h is_night', c.is_night(), True)
check('auto 识别 24h', GameClock('2026-07-19 18:00')._time_format, '24h')

print('== 4. 解析不出年份 → 降级显示，且降级原因是可见的 ==')
c = GameClock('某界·午时', '时辰制')
check('无年份起点', c.now_str(), '某界')
c.advance(2 * D)
check('无年份 +2日（降级样式）', c.now_str(), '某界（2日后）')
check('降级有 calendar_error', '解析不出年份' in c.calendar_error, True)

print('== 5. 旧档兼容（dict 里没有历法键）==')
old = {'start_str': START, 'elapsed_minutes': 213, 'time_format': '时辰制',
       'start_shichen_idx': 6, 'start_ke': 0}
c = GameClock.from_dict(old)
check('旧档 now_str', c.now_str(), '太初历三千七百四十二年·仲夏·未时三刻')
check('旧档 elapsed', c.elapsed_minutes, 213)

print('== 6. 存档往返一致 ==')
c = GameClock(START, '时辰制')
c.advance(2 * 360 * D + 5 * D)
c2 = GameClock.from_dict(c.to_dict())
check('往返 now_str', c2.now_str(), c.now_str())
check('往返 elapsed', c2.elapsed_minutes, c.elapsed_minutes)

print('== 7. str_at 可对任意时刻求值（任务期限要用）==')
c = GameClock(START, '时辰制')
c.advance(213)
check('now', c.now_str(), '太初历三千七百四十二年·仲夏·未时三刻')
check('+2年后的绝对时间', c.str_at(c.elapsed_minutes + 2 * 360 * D),
      '太初历三千七百四十四年·仲夏·未时三刻')

print('== 8. span_to_minutes 的"月/年"跟着历法走 ==')
c = GameClock(START, '时辰制')
check('1 日', c.span_to_minutes(1, '日'), D)
check('1 月 = 30 日', c.span_to_minutes(1, '月'), 30 * D)
check('1 年 = 12 月', c.span_to_minutes(1, '年'), 360 * D)
check('1 时辰', c.span_to_minutes(1, '时辰'), 120)
check('1 刻', c.span_to_minutes(1, '刻'), 30)
check('未知单位 → None', c.span_to_minutes(3, '光年'), None)


class FakeWorld:
    """只提供 `dict`，模拟 B1WorldLayer 的最小面。"""

    def __init__(self, d):
        self.dict = d


print('== 9. 可迁移验收①：换成公历风（阿拉伯数字 / 无纪元 / 时·分）==')
SPEC_CS = {
    "模板": "{年}-{月}-{日} {时}:{分}",
    "数字": "阿拉伯",
    "起始": {"年": 2026, "月": 7, "日": 19, "时": 18, "分": 0},
    "单位": [
        {"名": "分", "进制": 1, "起点": 0},     # 最底层 = 多少个分钟
        {"名": "时", "进制": 60, "起点": 0},
        {"名": "日", "进制": 24},
        {"名": "月", "进制": 30},
        {"名": "年", "进制": 12},
    ],
}
c = GameClock('无关的起始串', '自定义')
c.apply_calendar_config(FakeWorld({'历法': SPEC_CS}))
check('公历风 起点', c.now_str(), '2026-7-19 18:0')
c.advance(90)
check('公历风 +90分', c.now_str(), '2026-7-19 19:30')
c.advance(6 * H)
check('公历风 跨日', c.now_str(), '2026-7-20 1:30')
check('公历风 calendar_error 为空', c.calendar_error, '')
check('公历风 1年 = 360日（数据说了算）', c.span_to_minutes(1, '年'), 360 * D)

print('== 10. 可迁移验收②：干支纪年（循环名表）==')
STEMS = [f'{s}{b}' for s in '甲乙丙丁戊己庚辛壬癸' for b in '子丑寅卯辰巳午未申酉戌亥'][:60]
SPEC_GZ = {
    "模板": "{年}年·{月}·{日}日·{时辰}{刻}刻",
    "数字": "中文",
    "起始": {"年": 3742, "月": 5, "日": 1, "时辰": 6, "刻": 0},
    "单位": [
        {"名": "刻", "进制": 30, "名表": ["初", "一", "二", "三"], "起点": 0},
        {"名": "时辰", "进制": 4, "起点": 0,
         "名表": ["子时", "丑时", "寅时", "卯时", "辰时", "巳时",
                  "午时", "未时", "申时", "酉时", "戌时", "亥时"]},
        {"名": "日", "进制": 12, "值为1时省略": True},
        {"名": "月", "进制": 30, "起点": 1,
         "名表": ["孟春", "仲春", "季春", "孟夏", "仲夏", "季夏",
                  "孟秋", "仲秋", "季秋", "孟冬", "仲冬", "季冬"]},
        {"名": "年", "进制": 12, "循环名表": STEMS, "循环周期": 60, "循环起点": 1},
    ],
}
gz_year = STEMS[(3742 - 1) % 60]
c = GameClock('无关的起始串', '自定义')
c.apply_calendar_config(FakeWorld({'历法': SPEC_GZ}))
check('干支年', c.now_str().startswith(gz_year + '年·'), True)
check('干支不凭空写太初历', c.now_str()[0] != '太', True)

print('== 11. 可迁移验收③：年号（显示偏移）==')
SPEC_NH = {
    "模板": "{纪元}{年}年·{月}月·{日}日",
    "数字": "中文",
    "纪元": "贞观",
    "起始": {"年": 628, "月": 3, "日": 1},
    "单位": [
        {"名": "日", "进制": 1440},          # 最底层 = 多少个分钟（1 日 = 1440 分）
        {"名": "月", "进制": 30},
        {"名": "年", "进制": 12, "显示偏移": -626},   # 绝对 627 年 = 贞观元年
    ],
}
c = GameClock('无关的起始串', '自定义')
c.apply_calendar_config(FakeWorld({'历法': SPEC_NH}))
c.advance(30 * D)          # 进一个月
check('年号', c.now_str(), '贞观二年·四月·一日')

print('== 12. 历法块写错 → 回落内置 + 原因可见（不许静默）==')
c = GameClock(START, '时辰制')
c.apply_calendar_config(FakeWorld({'历法': {'模板': '{年}年', '单位': [{'名': '年', '进制': 0}]}}))
check('非法历法回落内置', c.now_str(), '太初历三千七百四十二年·仲夏·午时初刻')
check('非法历法有 calendar_error', '不合法' in c.calendar_error, True)

print(f'\n== 结果：{ok} 通过 / {fail} 失败 ==')
sys.exit(1 if fail else 0)
