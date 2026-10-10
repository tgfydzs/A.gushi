"""
世界地图的读写（`.map` ↔ 世界字典）

    emit_world(world_dict) -> str      把世界字典吐成 .map 文本
    parse_world(text) -> dict          把 .map 解析回**同样的嵌套形状**

# 为什么解析回来要"同样的形状"

地点树那两个消费者（`B1WorldLayer.normalize_node` / 地段属性链）**一行都不用改**——
换格式这件事被关在 I/O 层里。评测标准就是 `src/test/map_migration_test.py` 的基线对照：
换了形状，语义必须逐条一致。见 `地图格式.md` §8。

# 只吐地理与配置

`世界规则列表` / `世界状态` / `演变规则` / `世界知识` **不在这份文件里**（它们没有几何、
也不常手改），留在原来的 JSON。见 `地图格式.md` §0「范围」。

跑法：
    python -m common.map_io --export       # 当前世界 → 迁移基线/世界.map
    python -m common.map_io --roundtrip    # 导出 → 解析回来 → 与原世界逐项对照
"""

import io
import math
import os
import re
import shutil
import sys
import time

from common.log import get_logger

logger = get_logger('地图IO')

#: 解析后不当作属性、要单独处理的键
RESERVED = ('属于', '描述', '产出')
#: 值里不许出现的字符（它们本身就是分隔符）
FORBIDDEN_IN_VALUE = ('|', '[', ']', '\n', '\r')

#: 引擎**按名字读**的键（`地图格式.md` §3.1 那张表在这里的落点）。
#: 值是"谁读它"（`文件` 或 `文件:符号`）；**`None` = 声明了但没人读**。
#:
#: 为什么要有这张表：`§3.1` 原来是**手写的声明**，而声明会撒谎——`类型` 写着
#: "引擎读"，代码里一个字都没有（`dead_reserved_keys()` 就是去核这件事的）。
#: 项目栽过同款：`NPC_上限_MAX_MEMORY` 对不上 `NPC_记忆上限`，七项配置从没生效过，
#: 只因值恰好等于默认值才没暴露。
RESERVED_KEYS = {
    '属于': 'common/map_io.py:nest_world',
    '描述': 'common/map_io.py:_normalize',
    '产出': 'common/R4_opportunity.py',
    '从': 'Schema/B1_worldState_sch.py:roads',
    '到': 'Schema/B1_worldState_sch.py:roads',
    '双向': 'Schema/B1_worldState_sch.py:roads',
    '段': 'Schema/B1_worldState_sch.py:segment_at',
    '说明': 'common/map_io.py:_check_desc',
    '坐标': 'Schema/B1_worldState_sch.py:coord_at',
    '范围': 'Schema/B1_worldState_sch.py:in_range',
    '路径线': 'Schema/B1_worldState_sch.py:road_point',
    # `类型` 曾经是**空头承诺**（§3.1 写着保留键，代码里一个字都不读，
    # `dead_reserved_keys()` 就是把它抓出来的那条）。2026-10-08 区域/地点分家时接上了：
    # `类型=区域` 的是"面"（只画圈、不画点、不当路端点）。数据里现在还是 0 条——
    # 等**修订地图**那一次补（见 地图格式.md §9.1）。
    '类型': 'Schema/B1_worldState_sch.py:is_region',
}

#: 只在**键名**上做近似检测的那些保留键。几何那几个（`坐标`/`范围`/`路径线`）不在此列——
#: 它们是**行首关键字**，拼错了解析器当场报"认不出的关键字"，本来就响。
NEAR_KEYS = ('属于', '描述', '类型', '产出', '从', '到', '双向', '说明')

#: 每个实体上**引擎真的会读**的保留键（除此之外的保留键写上去是"写歪了"）。
#: 地块的 `说明` 走行尾 `|`（存进 `描述`），所以不出现在属性里。
_ALLOWED_KEYS = {
    '地块': {'属于', '产出', '类型'},
    '路': {'从', '到', '双向', '产出'},
    '段': set(),
    '配置': set(),
}


def _edit_distance_le1(a: str, b: str) -> bool:
    """`a` 与 `b` 的编辑距离是否 ≤1（含一次相邻换位）。**只用来提示拼错。**"""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    s, l = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(l)):
        if l[:i] + l[i + 1:] == s:
            return True
    return False


def key_notices(edit: dict) -> list:
    """
    **键写歪了**的提醒（不是错误——不拦保存）。

    两类，正是 §3.1 那个坑的两副面孔：

    1. **像保留键**：`类型`→`类形`、`双向`→`双方`。写歪了它就是个自由键，
       解析不报错、能力静默没了（`NPC_上限_*` 那次就是这么栽的）。
       ⚠️ **同义近义词测不出来**（`上级`/`属于` 一个共同字都没有，编辑距离是 2）——
       所以还有下面第 3 条兜着。
    2. **保留键写在了引擎不读的地方**：比如 `地点 … : 描述=某处`——引擎只认行尾
       `| 描述`，写成键它就成了一条普通属性（还会被喂给 LLM 当成地段属性）。
    3. **值是一个已知地点名**：`上级=东域` 这种，多半是想要 `属于`/`从`/`到`。
       这条专治"同义词"——编辑距离抓不到它，但它**想干什么**是看得出来的。
    """
    out = []
    names = {str(p.get('名') or '') for p in edit.get('地块') or []}
    fams = []
    for p in edit.get('地块') or []:
        fams.append(('地块', str(p.get('名') or ''), p.get('属性') or {}))
    for r in edit.get('路') or []:
        fams.append(('路', str(r.get('名') or ''), r.get('属性') or {}))
        for s in r.get('段') or []:
            fams.append(('段', str(s.get('名') or ''),
                         {k: v for k, v in s.items() if k not in ('名', '说明')}))
    fams.append(('配置', '', edit.get('配置') or {}))

    for fam, name, attrs in fams:
        tag = f'{fam} {name!r}' if name else fam
        allowed = _ALLOWED_KEYS.get(fam, set())
        for k, v in attrs.items():
            k = str(k)
            for res in NEAR_KEYS:
                if _edit_distance_le1(k, res):
                    out.append(f'{tag} 的键 `{k}` 很像保留键 `{res}`——'
                               f'写成 `{res}` 才有用，写歪了它只是个自由键（能力会静默失效）')
            if k in RESERVED_KEYS and k not in allowed:
                hint = {'描述': '描述写在**行尾 `|`** 后面（编辑器里那块叫「说明」）',
                        '说明': '说明写在**行尾 `|`** 后面',
                        }.get(k, '引擎不会在这个位置读它')
                out.append(f'{tag} 的键 `{k}` 在这里没人读——{hint}')
            elif k not in allowed and isinstance(v, str) and v.strip() in names:
                out.append(f'{tag} 的 `{k}={v}` 是一个**已知地点名**——'
                           f'如果你要的是包含关系，键得写成 `属于`（别的写法它只是个自由键）')

    # **区域与地点**（`地图格式.md` §9.1）：`类型=区域` 的地块在图上**不画点**、
    # 也不当路的端点。判定"谁是区域"**不靠猜**（"非叶就是区域"是错的：天道宗
    # 有 7 个子地块，可它自己能停留），所以这里只做两件提醒：
    out += _region_notices(edit)

    # **几何一致性**：子节点的圈不能越出父节点的圈（`地图格式.md` §1.2）
    out += _geometry_notices(edit)

    # **`配置` 行整条丢了**——从零手写 `.map` 时最容易漏的就是它，
    # 而它里面装的是**引擎开关**（NPC 上限、机缘、生命值体系、玩家初始数据）。
    # 缺了不会报错，只会**悄悄退回代码默认值**（比如机缘会照开）。
    cfg = edit.get('配置') or {}
    have = [k for k in ENGINE_CONFIG_KEYS if k in cfg]
    if not have and not any(str(k).startswith('NPC_上限_') for k in cfg):
        out.append('`配置` 行里**引擎要读的键一个都没有**（'
                   + '、'.join(ENGINE_CONFIG_KEYS) + '、NPC_上限_*）——'
                   '缺了不会报错，只会**悄悄退回代码默认值**（比如机缘会照开）。'
                   '从零手写 .map 时最容易漏这一行，手写时把它整行抄过来')
    return out


def _geometry_notices(edit: dict) -> list:
    """
    **几何一致性**：子节点的圈不能越出父节点的圈（`地图格式.md` §1.2 / §9.1）。

    为什么这不是美观问题：`locate()` 判"在哪个地区"靠**范围包含**，而属性链靠
    `属于`。子节点的圈越出父节点，就会出现"他站在 A 里，可 A 脚下的地区不算它"
    ——两处真源对不上，正是那份文档点名要躲的鬼故事。文档里写着"校验器**必须**报"
    这一条，而 2026-10-10 之前**代码里没有它**（这条提醒是那天补的）。

    只在**两边都是圆**时判（矩形的包含另说；数据里现在一条矩形都没有）。
    宽容 1 个单位：`范围` 是画图用的数，差半个单位不值得叫人。
    """
    places = edit.get('地块') or []
    geo = edit.get('几何') or {}
    coord = geo.get('坐标') or {}
    shape = geo.get('范围') or {}
    out = []
    for p in places:
        nm = str(p.get('名') or '')
        par = str(p.get('属于') or '').strip()
        if not par or nm not in coord or par not in coord:
            continue
        cs, ps = shape.get(nm), shape.get(par)
        if not (cs and ps and cs[0] == '圆' and ps[0] == '圆'):
            continue
        d = math.dist(coord[nm], coord[par])
        rc, rp = float(cs[1][0]), float(ps[1][0])
        if d + rc > rp + 1.0:
            out.append(
                f'`{nm}` 的圈**越出了**它父节点 `{par}` 的圈（圆心距 {d:.0f} + 半径 {rc:.0f} '
                f'> {rp:.0f}，超出 {d + rc - rp:.0f}）——`locate()` 判"在哪个地区"靠**范围包含**，'
                f'属性链靠 `属于`；越出去之后两处对不上（`地图格式.md` §1.2）。'
                f'要么把 `{nm}` 挪回圈里、要么把 `{par}` 的 `范围` 放大')
    return out


def _region_notices(edit: dict) -> list:
    """地区相关的两条提醒（**不拦保存**，见 §9.1）。

    ⚠️ 2026-10-10 删掉了一条旧的："带子节点却没写 `类型`"。它当时的前提是
    "带子地块 = 看着像区域"，而**在正式术语下这个前提不成立**：节点由作者写的
    `类型` 分家，**地点完全可以有子节点**（天道宗有 7 个子地块，但它就是天道宗，
    是能站进去的地方）。那条提醒会在正经数据上一直叫。
    """
    from Schema.B1_worldState_sch import REGION_TYPES
    places = edit.get('地块') or []
    is_region = {str(p.get('名') or ''):
                 str((p.get('属性') or {}).get('类型') or '').strip() in REGION_TYPES
                 for p in places}
    out = []
    # ① 标了地区、却有 `产出`/`功能`：那是"能去的地方"才有的东西
    for p in places:
        nm = str(p.get('名') or '')
        if not is_region.get(nm):
            continue
        attrs = p.get('属性') or {}
        for k in ('产出', '功能'):
            if attrs.get(k):
                out.append(f'节点 {nm!r} 写了 `类型={attrs.get("类型")}`（地区）又有 `{k}`'
                           f'——地区是"面"、不是能去的地方，这条没人消费得到。'
                           f'要么去掉 `类型`（它是能站进去的地点），要么把 `{k}` 挪到里面的节点')
    # ② 标了地区、却被路段当成端点："从东域出发"该写它里面的**具体地点**
    ends = {}
    for r in edit.get('路') or []:
        for k in ('从', '到'):
            nm = str(r.get(k) or '').strip()
            if is_region.get(nm):
                ends.setdefault(nm, 0)
                ends[nm] += 1
    if ends:
        show = '、'.join(list(ends)[:4]) + ('…' if len(ends) > 4 else '')
        out.append(f'{len(ends)} 个地区被当成了路段的端点（{show}，共 {sum(ends.values())} 条）'
                   f'——地区是"面"，落脚点只能是地点：把这些路段改接到地区里的具体地点'
                   f'（旅行时 `去向` 给地区名会自动落到里面离你最近的地点，'
                   f'但**路段的端点**必须是地点，否则"从这头走进来"没有落脚点）')
    return out


def dead_reserved_keys(root: str = None) -> list:
    """
    保留键里**声明了却没人读**的（空头承诺）。返回 `[(键, 为什么)]`。

    核对方式：`RESERVED_KEYS` 说"谁读它"，就去那个文件里找这个字面量。
    这是个**弱检查**（字符串拼接读的键它查不到），但正好挡住最要命的那种谎：
    表里写着保留键、代码里一个消费者都没有——而那正是要报给用户的事。
    """
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = []
    for k, who in RESERVED_KEYS.items():
        if not who:
            out.append((k, '声明成保留键，但代码里**没有任何消费者**——'
                           '要么补上读它的地方，要么把它降成自由键'))
            continue
        path = os.path.join(root, who.split(':')[0])
        try:
            with io.open(path, encoding='utf-8') as f:
                text = f.read()
        except OSError as e:
            out.append((k, f'声明说 {who} 读它，但那个文件打不开：{e}'))
            continue
        if f"'{k}'" not in text and f'"{k}"' not in text:
            out.append((k, f'声明说 {who} 读它，但那个文件里搜不到 `{k}`'))
    return out


# ═══════════════════ 值 ↔ 文本 ═══════════════════

def _num(v) -> str:
    if isinstance(v, bool):
        return '是' if v else '否'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _fmt_value(v) -> str:
    """
    值 → 文本。**列表一律带方括号**。

    这条是被往返检查逼出来的：原来用"逗号分隔"表示列表，
    于是 `["天道宗"]` 吐成 `天道宗`、`[]` 吐成空——再解析回来就变成**字符串**，
    类型静默翻转，而且**单元素列表会悄悄降级**。方括号把这个歧义关掉：
    有括号就是列表，没有就是标量。
    """
    if isinstance(v, bool):
        return '是' if v else '否'
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, list):
        return '[' + ','.join(_fmt_value(x) for x in v) + ']'
    s = str(v)
    for ch in FORBIDDEN_IN_VALUE:
        if ch in s:                     # 值里混进分隔符 = 静默改义，必须出声
            logger.warn(f'值里出现了分隔符 {ch!r}，已替换以免解析错乱：{s[:40]}')
            s = s.replace(ch, '｜' if ch == '|' else ' ')
    return s


_KV_RE = re.compile(r'([^\s=|]+)=((?:(?!\s+[^\s=|]+=).)*)', re.S)
_INT_RE = re.compile(r'-?\d+')
_FLOAT_RE = re.compile(r'-?\d*\.\d+')


def _parse_scalar(s: str):
    s = s.strip()
    if s in ('是', '否'):
        return s == '是'
    if _INT_RE.fullmatch(s):
        return int(s)
    if _FLOAT_RE.fullmatch(s):
        return float(s)
    return s


def _parse_value(s: str):
    """与 `_fmt_value` 对称：**有括号才是列表**（空括号 = 空列表）。"""
    s = s.strip()
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1].strip()
        return [] if not inner else [_parse_scalar(x) for x in inner.split(',')]
    return _parse_scalar(s)


def _parse_kv(text: str) -> dict:
    out = {}
    for m in _KV_RE.finditer(text or ''):
        out[m.group(1)] = _parse_value(m.group(2))
    return out


def _parse_production(text: str) -> list:
    """`低阶丹药:0.3 灵草:0.15(深入山脉腹地)` → 产出数组"""
    out = []
    for tok in (text or '').split():
        cond = None
        m = re.match(r'^(.*?)\((.*)\)$', tok)
        if m:
            tok, cond = m.group(1), m.group(2)
        if ':' not in tok:
            logger.warn(f'产出条目缺 "物品:概率"：{tok!r}')
            continue
        item, chance = tok.rsplit(':', 1)
        try:
            chance_v = float(chance)
        except ValueError:
            logger.warn(f'产出概率不是数字：{tok!r}')
            continue
        e = {'物品': item, '概率': chance_v}
        if cond:
            e['条件'] = cond
        out.append(e)
    return out


def _fmt_production(items) -> str:
    parts = []
    for e in items or []:
        if not isinstance(e, dict):
            continue
        s = f'{e.get("物品", "")}:{_num(e.get("概率", 0))}'
        if e.get('条件'):
            s += f'({e["条件"]})'
        parts.append(s)
    return ' '.join(parts)


# ═══════════════════ 吐出（emit） ═══════════════════

def emit_world(world_dict: dict, world_name: str = '') -> str:
    """世界字典 → `.map` 文本。字段按**原顺序**输出（源顺序 = 顺序，见 §5）。"""
    L = []
    head = []
    for k in ('游戏开始时间', '时间格式'):
        if world_dict.get(k) is not None:
            head.append(f'{k}={_fmt_value(world_dict[k])}')
    L.append(f'世界 {world_name or world_dict.get("世界名") or "未命名"} : ' + ' '.join(head))
    L.append('')
    cfg = world_dict.get('世界配置') or {}
    if cfg:
        L.append('配置 : ' + ' '.join(f'{k}={_fmt_value(v)}' for k, v in cfg.items()))
        L.append('')

    L.append('# ── 地块 ──')
    L.append('')

    def walk(node, parent):
        attrs, desc, kids = _normalize(node)
        for key, child in (kids or {}).items():
            c_attrs, c_desc, c_kids = _normalize(child)
            seg = [f'地点 {key} :']
            if parent:
                seg.append(f'属于={parent}')
            for k, v in (c_attrs or {}).items():
                if k == '产出':
                    seg.append(f'产出={_fmt_production(v)}')
                else:
                    seg.append(f'{k}={_fmt_value(v)}')
            line = '  '.join(seg)
            if c_desc:
                line += f' | {c_desc}'
            L.append(line)
            if c_kids:
                walk(child, key)

    walk(world_dict.get('世界地点列表') or {}, '')

    roads = world_dict.get('路列表') or []
    if roads:
        L += ['', '# ── 路（地块之间）──', '']
        for r in roads:
            seg = [f'路 {r.get("名", "")} :',
                   f'从={r.get("从", "")}', f'到={r.get("到", "")}',
                   f'双向={"是" if r.get("双向", True) else "否"}']
            for k, v in r.items():
                if k in ('名', '从', '到', '双向', '段', '说明'):
                    continue
                seg.append(f'{k}={_fmt_value(v)}')
            line = '  '.join(seg)
            # 路的说明走**行尾 `|`**（与地块同一个写法），不写成 `说明=…`：
            # `说明=` 会被当成一条普通属性喂给 LLM，而它是写给**人**看的注解
            # （见 地图格式.md §4）。以前这一格是**解析进来就扔**——手工写上去的
            # 一句话，保存一次就没了（写盘三查也拦不住：模型里从头就没有它）。
            if (r.get('说明') or '').strip():
                line += f' | {str(r["说明"]).strip()}'
            L.append(line)
            for s in r.get('段') or []:
                sseg = [f'  段 {s.get("名", "")} :']
                for k, v in s.items():
                    if k in ('名', '说明'):
                        continue
                    sseg.append(f'{k}={_fmt_value(v)}')
                sline = '  '.join(sseg)
                if (s.get('说明') or '').strip():
                    sline += f' | {str(s["说明"]).strip()}'
                L.append(sline)

    geo = world_dict.get('地图几何') or {}
    if any(geo.get(k) for k in ('坐标', '范围', '路径线')):
        L += ['', '# ── 几何（**只给前端**：引擎侧一律不读，见 地图格式.md §0）──', '']
        for name, p in (geo.get('坐标') or {}).items():
            L.append(f'坐标 {name} ({_num(p[0])},{_num(p[1])})')
        for name, s in (geo.get('范围') or {}).items():
            kind, nums = s[0], s[1]
            L.append(f'范围 {name} {kind}(' + ','.join(_num(x) for x in nums) + ')')
        for name, pts in (geo.get('路径线') or {}).items():
            L.append(f'路径线 {name} [' +
                     ','.join(f'({_num(x)},{_num(y)})' for x, y in pts) + ']')
    return '\n'.join(L) + '\n'


def _normalize(node):
    """本模块不 import 世界层（避免循环依赖），自己判一次形状——与 normalize_node 同约定。"""
    if isinstance(node, str):
        return {}, node, {}
    if not isinstance(node, dict):
        return {}, '', {}
    attrs = node.get('_属性')
    attrs = dict(attrs) if isinstance(attrs, dict) else {}
    desc = str(node.get('描述', '') or '')
    kids = {k: v for k, v in node.items()
            if not (isinstance(k, str) and (k.startswith('_') or k == '描述'))}
    return attrs, desc, kids


# ═══════════════════ 解析（parse） ═══════════════════

def parse_world(text: str) -> dict:
    """
    `.map` 文本 → 世界字典（**与旧格式同样的嵌套形状** + 一个 `路列表`）。

    出错一律抛 `MapFormatError`（带行号）——静默降级会让我们丢掉"少了一个地点"
    这种致命但无声的错。
    """
    world = {'世界地点列表': {}, '路列表': [], '世界配置': {}}
    by_name = {}                 # 名 → 原始节点（dict），用于挂 `属于`
    parents = {}                 # 名 → 父名
    cur_road = None

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        kw, _, rest = line.partition(' ')
        rest = rest.strip()
        if kw not in ('世界', '配置', '地点', '路', '段', '坐标', '范围', '路径线'):
            raise MapFormatError(lineno, f'认不出的关键字 {kw!r}')

        if kw in ('坐标', '路径线', '范围'):
            # 名 = 第一个空格分隔的 token（地点名里没有空格），其余是几何写法
            head = (rest.split() or [''])[0].strip()
            if not head:
                raise MapFormatError(lineno, f'{kw} 后面没有名字')
            geo = world.setdefault('地图几何', {}).setdefault(kw, {})
            if kw == '范围':
                shape = _parse_shape(rest[len(head):])
                if not shape:
                    raise MapFormatError(lineno, f'范围写法认不出：{rest!r}')
                geo[head] = shape
            else:
                pts = _parse_points(rest[len(head):])
                if not pts:
                    raise MapFormatError(lineno, f'{kw} 后面没解析出坐标：{rest!r}')
                geo[head] = pts[0] if kw == '坐标' else pts
            continue

        name, _, body = rest.partition(':')
        name, body = name.strip(), body.strip()
        kv_text, desc = (body.split('|', 1) + [''])[:2]
        kv = _parse_kv(kv_text)

        if kw == '世界':
            # ⚠️ 名字必须存下来：不存的话 emit 会写成"未命名"——
            # 编辑器保存一次就把世界名改掉，这是最难发现的那类往返损坏。
            world['世界名'] = name
            world['游戏开始时间'] = _fmt_back(kv.get('游戏开始时间', ''))
            if '时间格式' in kv:
                world['时间格式'] = _fmt_back(kv['时间格式'])
        elif kw == '配置':
            world['世界配置'] = kv
        elif kw == '地点':
            prod = _parse_production(str(kv.pop('产出', '') or ''))
            attrs = {}
            for k, v in kv.items():
                if k == '属于':
                    parents[name] = str(v)
                    continue
                attrs[k] = v
            if prod:
                attrs['产出'] = prod
            raw_node = {}
            if attrs:
                raw_node['_属性'] = attrs
            if desc.strip():
                raw_node['描述'] = desc.strip()
            by_name[name] = raw_node
        elif kw == '路':
            cur_road = {'名': name}
            for k, v in kv.items():
                cur_road[k] = v
            cur_road.setdefault('双向', True)
            if desc.strip():
                cur_road['说明'] = desc.strip()
            world['路列表'].append(cur_road)
        elif kw == '段':
            if cur_road is None:
                raise MapFormatError(lineno, '`段` 出现在任何 `路` 之前')
            seg = {'名': name}
            seg.update(kv)
            if desc.strip():
                seg['说明'] = desc.strip()
            cur_road.setdefault('段', []).append(seg)

    # 挂层级：没有 属于 的是根
    for name, node in by_name.items():
        parent = parents.get(name)
        if not parent:
            world['世界地点列表'][name] = node
            continue
        holder = by_name.get(parent)
        if holder is None:
            raise MapFormatError(0, f'地点「{name}」的 属于=「{parent}」找不到')
        holder[name] = node
    return world


_POINT_RE = re.compile(r'\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)')


def _parse_points(text: str) -> list:
    """`(300,180)` 与 `[(300,180),(360,140)]` 两种写法都收 → [[x,y], …]"""
    return [[float(x), float(y)] for x, y in _POINT_RE.findall(text or '')]


_SHAPE_RE = re.compile(r'(圆|矩形)\s*\(\s*(-?[\d.,\s]+?)\s*\)')


def _parse_shape(text: str):
    """`圆(90)` / `矩形(60,40)` → ('圆', [90.]) / ('矩形', [60., 40.])"""
    m = _SHAPE_RE.search(text or '')
    if not m:
        return None
    nums = [float(x) for x in m.group(2).split(',') if x.strip()]
    if not nums:
        return None
    return (m.group(1), nums)


def _fmt_back(v):
    return v


class MapFormatError(Exception):
    def __init__(self, lineno: int, msg: str):
        self.lineno = lineno
        super().__init__(f'第 {lineno} 行：{msg}')


# ═══════════════════ 骨架路（迁移用） ═══════════════════

def skeleton_roads(tree: dict) -> list:
    """
    给一个只有地块的世界补一层**骨架路**（`地图实施计划.md` M：自动补，标 `自动生成=是`）。

    规则（比计划里那句更保守，见下）：
      - 父 ↔ 每个直接子：各一条（双向）
      - 同父下的**相邻兄弟**互连（链式，不是两两互连）

    为什么兄弟用"链式"而不是"两两互连"：两两互连是组合爆炸——`天道宗` 有 7 个子节点
    就是 21 条路，而链式只有 6 条。**脚程留空**，交 LLM 现场判断（不给换算表）。
    """
    roads = []

    def add(a, b):
        roads.append({'名': f'{a}-{b}', '从': a, '到': b, '双向': True, '自动生成': True})

    def walk(node, parent_name):
        _attrs, _desc, kids = _normalize(node)
        names = list((kids or {}).keys())
        for n in names:
            if parent_name:
                add(parent_name, n)
        for i in range(len(names) - 1):        # 相邻兄弟成链
            add(names[i], names[i + 1])
        for n, child in (kids or {}).items():
            walk(child, n)

    walk(tree or {}, '')
    return roads


# ═══════════════════ 自动布局（v0） ═══════════════════

#: 各层放射半径：根 → 域 → 宗/阁 → 殿/坪 …
_RADII = (0.0, 340.0, 130.0, 70.0, 40.0, 26.0)
_LEAF_R = 16.0        #: 叶地块的半径
_MARGIN = 18.0        #: 枝地块的范围在子节点之外再留这么多

#: 域名的方位偏置。屏幕坐标 y 向下，所以"北"是负角
_COMPASS = {
    '东域': 0.0,
    '南域': math.pi / 2,
    '西域': math.pi,
    '北域': -math.pi / 2,
    '中州': -math.pi / 4,
}


def auto_layout(tree: dict, roads: list = None) -> dict:
    """
    v0 自动布局 → `{'坐标': {名: (x,y)}, '范围': {名: ('圆',[r])}, '路径线': {路名: [[x,y],…]}}`。

    **放射状 + 方位偏置**：域按罗盘摆（东在右、南在下、西在左、北在上），
    域以下在自己节点周围**整圈均分**。纯函数、确定性——重跑不会让文件抖。

    它只是**初值**：够画出一张能用的地图、够 1A/1B 用。好不好看交给编辑器去调，
    而且**几何与语义是解耦的**（`地图格式.md` §1.2），挪动它不影响任何判定。

    路的线：给每条路一根**带一个拐点的折线**（中点在垂线上偏一点），
    这样地图上不是一堆笔直的辐条。前端把折线渲染成**平滑曲线**——
    存点而不是存贝塞尔控制点：手写时"过这几个点"，比"这四点是控制点"直观得多。
    """
    nodes = {}

    def index(node):
        for k, v in (_normalize(node)[2] or {}).items():
            nodes[k] = v
            index(v)

    index(tree or {})
    coords, ranges = {}, {}

    def place(name, cx, cy, depth, spin):
        """
        子节点绕父节点**整圈均分**，而不是挤在父节点继承来的窄扇区里。

        第一版按"扇区"分，结果 `天道宗` 的 7 个子节点挤在 ~0.7 弧度里、
        间距只有 9 个单位，而每个节点半径 16——**全糊在一起**。
        整圈均分时间距 = 2πr/n，r=70、n=7 就是 63 个单位，够用了。
        域那一层才用罗盘方位（东在右、南在下…），因为那一层是"地理"，
        再往下是"某地内部有什么"，方位没有意义。
        """
        coords[name] = (round(cx, 1), round(cy, 1))
        kids = list((_normalize(nodes.get(name, {}))[2] or {}).keys())
        if not kids:
            ranges[name] = ('圆', [_LEAF_R])
            return _LEAF_R
        r = _RADII[min(depth + 1, len(_RADII) - 1)]
        n = len(kids)
        reach = 0.0
        for i, k in enumerate(kids):
            ang = (_COMPASS[k] if (depth == 0 and k in _COMPASS)
                   else spin + 2 * math.pi * i / n)
            nx, ny = cx + r * math.cos(ang), cy + r * math.sin(ang)
            cr = place(k, nx, ny, depth + 1, ang + math.pi / n)
            reach = max(reach, math.hypot(nx - cx, ny - cy) + cr)
        ranges[name] = ('圆', [round(reach + _MARGIN, 1)])
        return reach + _MARGIN

    # **有多个根要给每个根都铺**（`属于` 为空的都算根）。
    # 以前只铺 `roots[0]`——第二个根**一个坐标都拿不到**，于是它
    # ① 不画在地图上（`map_view` 只画有坐标的）、② `set_position` 落不下去（走不进去）。
    # 手写新世界时多一个"域外/另一块大陆"是常事，所以这里必须支持。
    # 各团沿 x 轴排开（互不重叠）；**只有一个根时位移为 0，输出与从前逐字节一致**。
    roots = list((tree or {}).keys())
    cursor = 0.0
    for ri, r0 in enumerate(roots):
        before = set(coords)
        reach = place(r0, 0.0, 0.0, 0, 0.0)
        if cursor:
            for k in (set(coords) - before):
                coords[k] = (round(coords[k][0] + cursor, 1), coords[k][1])
        cursor += 2 * reach + 2 * _MARGIN

    lines = {}
    for r in (roads or []):
        a, b = r.get('从'), r.get('到')
        if a not in coords or b not in coords:
            continue
        (x1, y1), (x2, y2) = coords[a], coords[b]
        dx, dy = x2 - x1, y2 - y1
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        # 垂线方向偏一点，偏量随路长走（长短路看着都自然）
        k = 0.12
        lines[r.get('名') or f'{a}-{b}'] = [
            [round(x1, 1), round(y1, 1)],
            [round(mx - dy * k, 1), round(my + dx * k, 1)],
            [round(x2, 1), round(y2, 1)],
        ]
    return {'坐标': coords, '范围': ranges, '路径线': lines}


# ═══════════════════ 编辑模型（阶段 2/3）═══════════════════
#
# 为什么中间要夹一层"扁平可编辑模型"：
#
#   1. 编辑器（和将来的 AI 生成器）想改的是**实体的字段**，不是文本排版。
#      有了它，前端不必懂 `属于=` 嵌套与 emit 顺序，只改键值；
#   2. `emit_world` 一行都不用动——它已被往返测试钉死，nest 归这一层管；
#   3. **写盘前能自查**：emit → parse → flatten 必须回到**一模一样**。
#      不查的话，"改一条属性、顺手丢另一条"是查不出根因的那种坏
#      （同 `NPC_上限_*` 那次：改 A 坏 B，报错在很远的地方）。

#: 名字里不许出现的字符（它们本身就是 `.map` 的分隔符）
FORBIDDEN_IN_NAME = (' ', '\t', ':', '=', '|', '[', ']', '\n', '\r')
#: 属性键不许出现的字符（`_KV_RE` 的键是 `[^\s=|]+`）
FORBIDDEN_IN_KEY = (' ', '\t', '=', '|', '\n', '\r')
#: 每个实体家族一行怎么念（报错信息用）
FAMILY_LABEL = {'地块': '地块', '路': '路'}


class MapEditError(Exception):
    """编辑模型本身不合法（不是 `.map` 文本格式错，那是 `MapFormatError`）。"""


def _geo_as_json(geo: dict) -> dict:
    """
    几何统一成**纯 list**。解析器给的是 tuple（`(x,y)` / `('圆',[r])`），
    而编辑模型要来回一趟 JSON（浏览器 → 服务端），元组过 JSON 就变成列表了——
    不在这里对齐，保存时的往返自检会报"改了别的东西"，而它其实只是 list≠tuple。
    真被这条抓到过一次（第一次跑 `/api/map/save`）。
    """
    out = {}
    out['坐标'] = {k: [float(v[0]), float(v[1])]
                   for k, v in (geo.get('坐标') or {}).items()}
    out['范围'] = {k: [v[0], [float(x) for x in v[1]]]
                   for k, v in (geo.get('范围') or {}).items()}
    out['路径线'] = {k: [[float(p[0]), float(p[1])] for p in v]
                     for k, v in (geo.get('路径线') or {}).items()}
    return out


def flatten_world(world: dict) -> dict:
    """
    世界字典（嵌套）→ 扁平可编辑模型，**顺序 = emit 顺序 = 源文件顺序**。

        {'世界': {'名', '游戏开始时间', '时间格式'},
         '配置': {...},
         '地块': [{'名', '属于', '属性': {...}, '说明': ''}],
         '路':   [{'名', '从', '到', '双向', '属性': {...}, '段': [...]}],
         '几何': {'坐标': {}, '范围': {}, '路径线': {}}}
    """
    out = {
        '世界': {
            '名': world.get('世界名') or '',
            '游戏开始时间': world.get('游戏开始时间'),
            '时间格式': world.get('时间格式'),
        },
        '配置': dict(world.get('世界配置') or {}),
        '地块': [],
        '路': [],
        '几何': _geo_as_json(world.get('地图几何') or {}),
    }

    def walk(node, parent):
        attrs, desc, kids = _normalize(node)
        for name, child in (kids or {}).items():
            c_attrs, c_desc, _ = _normalize(child)
            out['地块'].append({'名': name, '属于': parent,
                                '属性': c_attrs, '说明': c_desc})
            walk(child, name)

    walk(world.get('世界地点列表') or {}, '')

    for r in world.get('路列表') or []:
        attrs = {k: v for k, v in r.items()
                 if k not in ('名', '从', '到', '双向', '段', '说明')}
        out['路'].append({
            '名': r.get('名', ''), '从': r.get('从', ''), '到': r.get('到', ''),
            '双向': bool(r.get('双向', True)), '说明': r.get('说明', ''),
            '属性': attrs,
            # 段的 `说明` 与路的同款：**先摆正中那几个键，再摆其余**。
            # 顺序必须与 `from_edit_form` 一致——不然"表单 → 模型"会看起来像
            # "键序变了"，写盘三查会把一次正常的保存拦下来（真拦过）。
            '段': [{'名': s.get('名', ''), '说明': s.get('说明', ''),
                    **{k: v for k, v in s.items() if k not in ('名', '说明')}}
                   for s in (r.get('段') or [])],
        })
    return out


def nest_world(edit: dict) -> dict:
    """扁平可编辑模型 → 世界字典（`emit_world` 的输入形状）。**只搬不校验**。"""
    w = edit.get('世界') or {}
    out = {
        '世界名': w.get('名') or '未命名',
        '世界配置': dict(edit.get('配置') or {}),
        '路列表': [],
        '地图几何': {k: dict((edit.get('几何') or {}).get(k) or {})
                     for k in ('坐标', '范围', '路径线')},
        '世界地点列表': {},
    }
    for k in ('游戏开始时间', '时间格式'):
        if w.get(k) is not None:
            out[k] = w[k]

    # 先把所有节点建出来，再挂——顺序即插入顺序，挂出来的树就是 DFS 前序，
    # 正好等于 emit 的遍历顺序（所以往返后行序不变，diff 才干净）。
    nodes = {}
    for p in edit.get('地块') or []:
        node = {'_属性': dict(p.get('属性') or {})}
        if (p.get('说明') or '').strip():
            node['描述'] = p['说明']
        nodes[p.get('名') or ''] = node
    for p in edit.get('地块') or []:
        name = p.get('名') or ''
        parent = (p.get('属于') or '').strip()
        if parent and parent in nodes:
            nodes[parent][name] = nodes[name]
        else:
            out['世界地点列表'][name] = nodes[name]

    for r in edit.get('路') or []:
        road = {'名': r.get('名', ''), '从': r.get('从', ''), '到': r.get('到', ''),
                '双向': bool(r.get('双向', True))}
        if (r.get('说明') or '').strip():
            road['说明'] = str(r['说明']).strip()
        road.update(dict(r.get('属性') or {}))
        segs = [dict(s) for s in (r.get('段') or [])]
        if segs:
            road['段'] = segs
        out['路列表'].append(road)
    return out


def validate_edit(edit: dict) -> list:
    """
    结构校验。返回问题列表（**空列表 = 可以写盘**）。

    只管"能不能落成合法的 `.map`"，**不管内容好不好**——语义好不好是 LLM 的事
    （用户要的正是"字段不定，交由 llm 判断"）。
    """
    bad = []
    places = edit.get('地块')
    roads = edit.get('路')
    if not isinstance(places, list) or not places:
        bad.append('一个地块都没有——空的 `.map` 会让所有地点消失')
        places = places if isinstance(places, list) else []
    roads = roads if isinstance(roads, list) else []

    names = []
    seen = {}
    for i, p in enumerate(places, 1):
        name = str(p.get('名') or '')
        tag = f'地块#{i}'
        if not name.strip():
            bad.append(f'{tag} 没有名字')
            continue
        tag = f'地块 {name!r}'
        if any(c in name for c in FORBIDDEN_IN_NAME):
            bad.append(f'{tag} 名字里有分隔符（空格 / : / = / | / [ ] 都不行）')
        if name in seen:
            bad.append(f'{tag} 与地块#{seen[name]} 重名')
        seen[name] = i
        names.append(name)
        if str(p.get('属于') or '').strip() == name:
            bad.append(f'{tag} 的 `属于` 指向自己')

    nameset = set(names)

    def chain_cycle(name):
        seen_p, cur = set(), (name or '').strip()
        while cur:
            if cur in seen_p:
                return True
            seen_p.add(cur)
            nxt = next((str(q.get('属于') or '').strip() for q in places
                        if str(q.get('名') or '') == cur), '')
            cur = nxt
        return False

    for p in places:
        name = str(p.get('名') or '')
        parent = str(p.get('属于') or '').strip()
        if parent and parent not in nameset:
            bad.append(f'地块 {name!r} 的 `属于={parent}` 不存在')
        elif parent and chain_cycle(name):
            bad.append(f'地块 {name!r} 的 `属于` 成环了（链上回到自己）')
        _check_kv(bad, f'地块 {name!r}', p.get('属性'),
                  allow_production=True)
        _check_desc(bad, f'地块 {name!r}', p.get('说明'))

    road_names, pairs = [], {}
    for i, r in enumerate(roads, 1):
        name = str(r.get('名') or '')
        tag = f'路#{i}'
        if not name.strip():
            bad.append(f'{tag} 没有名字')
            continue
        tag = f'路 {name!r}'
        if any(c in name for c in FORBIDDEN_IN_NAME):
            bad.append(f'{tag} 名字里有分隔符')
        if name in road_names:
            bad.append(f'{tag} 与其他路重名')
        road_names.append(name)
        a, b = str(r.get('从') or '').strip(), str(r.get('到') or '').strip()
        for end, val in (('从', a), ('到', b)):
            if val not in nameset:
                bad.append(f'{tag} 的 `{end}={val}` 不是已知地块')
        if a and a == b:
            bad.append(f'{tag} 的 `从` 与 `到` 是同一个地块')
        key = tuple(sorted((a, b)))
        if a and b and key in pairs:
            bad.append(f'{tag} 与 {pairs[key]!r} 是同一对地块的两条路')
        elif a and b:
            pairs[key] = name
        _check_kv(bad, tag, r.get('属性'), allow_production=True)
        _check_desc(bad, tag, r.get('说明'))
        for s in r.get('段') or []:
            sname = str(s.get('名') or '')
            if not sname.strip():
                bad.append(f'{tag} 有一段没有名字')
            seg_attrs = {k: v for k, v in s.items() if k not in ('名', '说明')}
            _check_kv(bad, f'{tag} 的段 {sname!r}', seg_attrs)
            _check_desc(bad, f'{tag} 的段 {sname!r}', s.get('说明'))

    geo = edit.get('几何') or {}
    for kind, pool in (('坐标', nameset), ('范围', nameset),
                       ('路径线', set(road_names))):
        for key in (geo.get(kind) or {}):
            if key not in pool:
                bad.append(f'几何 `{kind} {key}` 指向不存在的地块/路'
                           f'——要么补上它，要么删掉这行几何')
    for name, pts in (geo.get('路径线') or {}).items():
        if len(pts or []) < 2:
            bad.append(f'`路径线 {name}` 少于两个点，画不出路')
    return bad


def _check_desc(bad: list, tag: str, desc):
    """
    `说明`（`.map` 里行尾 `|` 后面那一段）的合法性。

    以前这里**一个字都不查**——于是 `说明` 里敲个回车，保存时会走到
    `parse_world` 才炸，报出来是「第 23 行：认不出的关键字 '第二行'」，
    完全指不到"是你在说明里按了回车"。校验器该在这儿拦住并说人话。
    """
    s = str(desc or '')
    if '\n' in s or '\r' in s:
        bad.append(f'{tag} 的 `说明` 里有换行——`.map` 里它是行尾 `|` 后面的一段，'
                   f'换行会把它劈成两行。请改成空格或分号')
    if '|' in s:
        bad.append(f'{tag} 的 `说明` 里有 `|`——那是"说明"的分隔符，'
                   f'写进去会把后半截变成新的键值')


def _check_kv(bad: list, tag: str, attrs, allow_production: bool = False):
    """键值本身能不能落成 `.map` 文本。"""
    if attrs is None:
        return
    if not isinstance(attrs, dict):
        bad.append(f'{tag} 的属性不是键值表')
        return
    for k, v in attrs.items():
        if not str(k).strip():
            bad.append(f'{tag} 有一个空键')
            continue
        if any(c in str(k) for c in FORBIDDEN_IN_KEY):
            bad.append(f'{tag} 的键 {k!r} 里有分隔符')
        if v is None:
            bad.append(f'{tag} 的 `{k}` 是空值（会写成字符串 None）')
            continue
        if k == '产出':
            if not allow_production:
                bad.append(f'{tag} 不该有 `产出`（只有地块与路才有产出）')
            elif not isinstance(v, list):
                bad.append(f'{tag} 的 `产出` 必须是列表')
            continue
        if isinstance(v, (list, tuple)):
            for x in v:
                if isinstance(x, (list, dict)):
                    bad.append(f'{tag} 的 `{k}` 是嵌套列表，写不进去')
                    break
        elif isinstance(v, dict):
            bad.append(f'{tag} 的 `{k}` 是字典，`.map` 表达不了（用列表）')
        else:
            s = str(v)
            if any(c in s for c in FORBIDDEN_IN_VALUE):
                bad.append(f'{tag} 的 `{k}` 里有分隔符 | [ ]')


def duplicate_names(edit: dict) -> list:
    """
    重名的实体。**必须在级联之前挡掉。**

    为什么单独拎出来当"前置检查"：`canonicalize` 与 `detect_renames` 都是
    **按名字配对**的，重名会让它们把 A 配成 B——于是级联改错东西，而校验器
    最后报出来的是一屏"成环"，跟真正的原因（重名）毫无关系。实测过：
    把 `东域` 改成 `苍云大陆`，报出来是 41 条「属于成环」，一条重名都没有。
    """
    bad = []
    for fam in ('地块', '路'):
        seen = {}
        for i, x in enumerate(edit.get(fam) or [], 1):
            n = str(x.get('名') or '')
            if not n.strip():
                continue
            if n in seen:
                bad.append(f'{fam} {n!r} 与第 {seen[n]} 个重名'
                           f'——重名会让"按名字配对"的改名/级联改错东西，请先改成一个不重名的')
            else:
                seen[n] = i
    return bad


def detect_renames(edit: dict, base: dict) -> dict:
    """
    按**列表位置**比出改名，返回 `{'地块': {旧: 新}, '路': {旧: 新}}`。

    为什么敢用位置当信号：编辑器**只能改名和删除，不能插入**（新建实体是阶段 3 的
    事，那时要换成显式声明）。所以同一位置上名字变了、且新旧两个名字各自只出现在
    一边，那就是改名，不是"删一个加一个"。

    为什么非要知道这是改名：不知道就得把引用（子地块的 `属于`、路的 `从/到`、几何的键）
    当成悬空引用报错——而用户只是改了个名字。
    """
    out = {}
    for fam, key in (('地块', '名'), ('路', '名')):
        b, e = base.get(fam) or [], edit.get(fam) or []
        bnames = {str(x.get(key) or '') for x in b}
        enames = {str(x.get(key) or '') for x in e}
        got = {}
        for i in range(min(len(b), len(e))):
            old = str(b[i].get(key) or '')
            new = str(e[i].get(key) or '')
            # 只有"旧的不在新表里、新的不在旧表里"才算改名，否则是插入/删除把位置错开了
            if old != new and old not in enames and new not in bnames:
                got[old] = new
        if got:
            out[fam] = got

    # 位置比对还漏得掉一种：**新建之后又改名**。新实体在 base 里根本没有对应位置，
    # 位置比对**轮不到它**——于是它的 `坐标` 键还挂着占位名，而占位名已经不是任何
    # 地块了，落盘时会被当成"悬空几何"**静默清掉**（新建的地块就此丢了坐标，
    # 图上不出现、`locate()` 也认不出）。
    #
    # 补一次配对：几何的键**就是实体名**，所以"一个没人认领的几何键 + 一个没有几何的
    # 新名字"就是一次改名。两个限定都不能松：
    #   · 只认**全新的**键（不在 base 里）——不然"删一个 + 改另一个"会被配成改名，
    #     路会被接到别的名字上（有损，而且看不出来）；
    #   · 只认 **1↔1**，多对多没有可靠的顺序信号，宁可让用户看到"连带删掉"的提示。
    geo_e = edit.get('几何') or {}
    for fam, kind in (('地块', '坐标'), ('路', '路径线')):
        e_names = {str(x.get('名') or '') for x in (edit.get(fam) or [])}
        b_names = {str(x.get('名') or '') for x in (base.get(fam) or [])}
        keys = {str(k) for k in (geo_e.get(kind) or {})}
        orphan = keys - e_names - b_names          # 新建的实体留下的几何键
        bare = e_names - b_names - keys            # 新建的、还没有几何的实体
        if len(orphan) == 1 and len(bare) == 1:
            old, new = next(iter(orphan)), next(iter(bare))
            if old not in (out.get(fam) or {}):
                out.setdefault(fam, {})[old] = new

    # 路名通常是"从-到"拼出来的（骨架路都是），地块改了名它也得跟着改——
    # 不然会留下 `路 天道宗-星辰阁 : 从=天道宗·改名试 到=星辰阁` 这种
    # **名字与端点对不上**的东西，而它的 `路径线` 键还挂在旧名上。
    pr = out.get('地块') or {}
    if pr:
        rr = dict(out.get('路') or {})
        for r in base.get('路') or []:
            a, b = str(r.get('从') or ''), str(r.get('到') or '')
            nm = str(r.get('名') or '')
            if not (a in pr or b in pr):
                continue
            na, nb = pr.get(a, a), pr.get(b, b)
            if nm == f'{a}-{b}':
                rr.setdefault(nm, f'{na}-{nb}')
            elif nm == f'{b}-{a}':
                rr.setdefault(nm, f'{nb}-{na}')
            # 名字是手写的（不是拼的）就不动——作者起的名字不该被我们改
        if rr:
            out['路'] = rr
    return out


def cascade(edit: dict, base: dict, renames: dict) -> tuple:
    """
    把改名/删除的**引用一起改掉**，免得用户保存不了（以前只能关掉编辑器改文件）。

    改名（无损）：子地块的 `属于`、路的 `从/到`、`几何.坐标/范围/路径线` 的键。
    删除地块：连带删掉它的 `坐标`/`范围`，**以及引用它的路**。
    删除路：连带删掉它的 `路径线`。

    ⚠️ 连带删路是**有损**的，所以它一定出现在返回的 `notes` 里、界面上必须显示出来。
    之所以不改成"先问一次再删"（那样更像样）：现在这个按钮的失败模式是**保存不了、
    也没有别的地方能删那行几何**——死路。**报出来的连带删除**比死路强。
    落盘前有备份、历史里有 git，真删错了能捞回来。

    :return: `(新编辑模型, notes)`
    """
    notes = []
    if not base:
        return edit, notes
    out = dict(edit)
    pr = renames.get('地块') or {}
    rr = renames.get('路') or {}

    for p in out.get('地块') or []:
        if str(p.get('属于') or '').strip() in pr:
            p['属于'] = pr[str(p['属于']).strip()]
    for r in out.get('路') or []:
        for k in ('从', '到'):
            if str(r.get(k) or '').strip() in pr:
                r[k] = pr[str(r[k]).strip()]
        # 路名是"从-到"拼的就跟着改（`detect_renames` 已经把它记进 rr 了）。
        # 不应用这一步的话：`renames` 说有改名、`canonicalize` 按新名字去配，
        # 而模型里还是旧名字——配不上，路就整条**没了**。
        nm = str(r.get('名') or '')
        if nm in rr:
            r['名'] = rr[nm]

    geo = {k: dict((out.get('几何') or {}).get(k) or {})
           for k in ('坐标', '范围', '路径线')}
    for kind in ('坐标', '范围', '路径线'):
        for old, new in pr.items():
            if old in geo[kind]:
                geo[kind][new] = geo[kind].pop(old)
        for old, new in rr.items():
            if kind == '路径线' and old in geo[kind]:
                geo[kind][new] = geo[kind].pop(old)

    # 删掉的实体：几何一定要跟着清，否则校验器会把保存挡下来（那正是死路的来源）
    alive_p = {str(p.get('名') or '') for p in out.get('地块') or []}
    alive_r = {str(r.get('名') or '') for r in out.get('路') or []}

    # **被删掉的枝地块，它的子地块怎么办**——这是"删一个区域"唯一让人意外的地方。
    # 选择"把子地块挂到爷层"（而不是连坐删掉整棵子树）：删掉 天道宗 的意思
    # 是"这个区域不要了"，不是"底下 7 个地方也一起没"。而且这是**无损**的。
    base_parent = {str(p.get('名') or ''): str(p.get('属于') or '').strip()
                   for p in (base.get('地块') or [])}
    for _ in range(12):                       # 爷层也可能被删了，往上走到活着的一层
        moved = False
        for p in out.get('地块') or []:
            par = str(p.get('属于') or '').strip()
            if par and par not in alive_p and par in base_parent:
                gp = base_parent[par]
                p['属于'] = gp
                notes.append(f'{p.get("名")} 的上层从 {par} 改成 {gp or "（顶层）"}'
                             f'——{par} 被删了')
                moved = True
        if not moved:
            break

    for kind in ('坐标', '范围'):
        for k in list(geo[kind]):
            if k not in alive_p:
                del geo[kind][k]
                notes.append(f'连带删掉 `{kind} {k}`（地块没了）')
    for k in list(geo['路径线']):
        if k not in alive_r:
            del geo['路径线'][k]
            notes.append(f'连带删掉 `路径线 {k}`（路没了）')

    # 路的两端都得是活着的地块；一端**没了**就连带删路。
    # ⚠️ 但"端点空着"不改：空是**填错**，不是"那块地没了"，该由校验器报错。
    # 把两者混起来的话，用户删到一半（端点暂时空着）会静默丢掉整条路。
    kept, dead, malformed = [], [], False
    for r in out.get('路') or []:
        a, b = str(r.get('从') or '').strip(), str(r.get('到') or '').strip()
        if not a or not b:
            malformed = True
            kept.append(r)                    # 留给校验器，别在这里做主张
            continue
        if a in alive_p and b in alive_p:
            kept.append(r)
        else:
            dead.append(str(r.get('名') or f'{a}-{b}'))
    if dead:
        gone = set(dead)
        for k in list(geo['路径线']):
            if k in gone:
                del geo['路径线'][k]
        notes.append('连带删掉 %d 条路（端点地块没了）：%s'
                     % (len(dead), '、'.join(dead[:6]) + ('…' if len(dead) > 6 else '')))
    out['路'] = kept

    out['几何'] = geo
    for old, new in pr.items():
        notes.append(f'改名 {old} → {new}（引用一起改了）')
    for old, new in rr.items():
        notes.append(f'改名 {old} → {new}（引用一起改了）')
    return out, notes


def order_places_dfs(places: list) -> list:
    """
    把地块列表重排成 **DFS 前序**（根按原相对顺序，子按原相对顺序）。

    为什么**必须**有这一步：`emit_world` 是按**树** DFS 吐的（`属于` 决定谁写在谁后面），
    所以扁平模型的顺序**必须**等于那个 DFS 序。少了它，"新建一个**子**地块"就出事——
    新条目被追加到列表末尾，可它其实是中间某个节点的孩子，于是
    `emit → parse → flatten` 回来的顺序对不上，往返自检报出一大段错位。
    （真踩到了：新建一个 `属于=东域` 的地块，自检报"地块[17].名 '西域' → '新地块1'"。）

    有环时（校验器会拦）走到重复节点就停——**这里不能死循环**，
    否则报错会变成挂死。
    """
    items = list(places or [])
    names = {str(p.get('名') or '') for p in items}
    kids, roots = {}, []
    for p in items:
        par = str(p.get('属于') or '').strip()
        if par and par in names:
            kids.setdefault(par, []).append(p)
        else:
            roots.append(p)

    out, seen = [], set()

    def walk(p):
        n = str(p.get('名') or '')
        if n in seen:
            return
        seen.add(n)
        out.append(p)
        for c in kids.get(n, []):
            walk(c)

    for r in roots:
        walk(r)
    for p in items:                      # 环里的、或父名被删了的，别丢
        if str(p.get('名') or '') not in seen:
            out.append(p)
    return out


def apply_geo_patch(model: dict, patch: dict) -> tuple:
    """
    把前端**拖出来的数值**盖到几何上（阶段 3：拖节点 / 拉曲线）。

    为什么走数值、不让前端把 `路径线` 自己拼成文本：`.map` 里坐标的写法是 Python 的
    `repr(float)`——`170.0` 写成 `170` 就变了类型（`_parse_value` 会把它解成 int）。
    在 JS 里再抄一份格式化规则就是**两处真相**，迟早漂。所以前端只报
    「我把它拖到了这儿」，**文本由这一层生成**。

    `patch` 形如 `{'坐标': {名: [x,y]}, '路径线': {路名: [[x,y],…]}}`。
    **只覆盖被点名的实体**，没拖过的一个都不动；新实体（新建的地块/路）也允许新增。

    ⚠️ 拖一个地块，**连着它的路那一端要跟着走**：`路径线` 的两端本来就**是**那两个
    地块的坐标（`auto_layout` 这么生成，`road_point` 也靠这个前提）。不跟着走的话，
    线会从空地上开始——图上就是断的。这是**派生**的改动（端点属于地块），
    所以自动做，但**一定要列出来**（`notes`）。

    :return: `(模型, notes)`
    """
    if not patch:
        return model, []
    geo = {k: dict((model.get('几何') or {}).get(k) or {})
           for k in ('坐标', '范围', '路径线')}
    moved = {}
    for name, pt in (patch.get('坐标') or {}).items():
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            geo['坐标'][str(name)] = [float(pt[0]), float(pt[1])]
            moved[str(name)] = [float(pt[0]), float(pt[1])]
    for name, pts in (patch.get('路径线') or {}).items():
        pts = list(pts or [])
        if len(pts) >= 2 and all(len(q) == 2 for q in pts):
            geo['路径线'][str(name)] = [[float(q[0]), float(q[1])] for q in pts]

    notes, dragged_roads = [], set((patch.get('路径线') or {}).keys())
    if moved:
        for r in model.get('路') or []:
            nm = str(r.get('名') or '')
            pts = geo['路径线'].get(nm)
            if not pts or nm in dragged_roads:
                continue                     # 这条路的线自己刚被拖过，别覆盖人家
            hit = None
            if r.get('从') in moved:
                pts[0] = list(moved[r['从']])
                hit = r.get('从')
            if r.get('到') in moved:
                pts[-1] = list(moved[r['到']])
                hit = r.get('到') if hit is None else f'{hit}、{r.get("到")}'
            if hit:
                notes.append(f'`路径线 {nm}` 的端点跟着 {hit} 走了（线的两端就是地块本身）')
    model['几何'] = geo
    return model, notes


def diff_edit(a: dict, b: dict, path: str = '') -> list:
    """两版扁平模型的第一批差异（只报前 12 条，够定位就行）。**键序也算差异。**"""
    out = []

    def rec(x, y, p):
        if len(out) >= 12:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            # 键序也查——`.map` 的属性是按书写顺序吐的，顺序变了 diff 就是满屏，
            # 而"内容一字没改"的情况最容易被误当成改了一大片（真踩过一次：
            # Flask 的 jsonify 默认按键排序）。
            if list(x.keys()) != list(y.keys()):
                out.append(f'≠ {p} 的键序变了：{list(x)[:6]} → {list(y)[:6]}')
            for k in list(x) + [k for k in y if k not in x]:
                if len(out) >= 12:
                    return
                if k not in x:
                    out.append(f'+ {p}.{k}')
                elif k not in y:
                    out.append(f'- {p}.{k}')
                else:
                    rec(x[k], y[k], f'{p}.{k}')
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                out.append(f'≠ {p}: {len(x)} 项 → {len(y)} 项')
                return
            for i, (xi, yi) in enumerate(zip(x, y)):
                if len(out) >= 12:
                    return
                rec(xi, yi, f'{p}[{i}]')
        elif x != y:
            out.append(f'≠ {p}: {str(x)[:50]!r} → {str(y)[:50]!r}')

    rec(a, b, path or '根')
    return out


def _reorder(d: dict, ref) -> dict:
    """按 `ref` 的键序重排 `d`；`ref` 里没有的键排到最后（相对顺序不变）。"""
    d = d or {}
    out = {k: d[k] for k in (ref or {}) if k in d}
    for k, v in d.items():
        out.setdefault(k, v)
    return out


def canonicalize(edit: dict, base: dict, renames: dict = None) -> dict:
    """
    把编辑器来的模型**按盘上文件的顺序对齐**（实体序 + 每个实体的属性键序）。

    为什么要这一道：编辑器不该有能力"顺手把整个文件重排"。没有它，任何一处
    `Object.keys()` 抖动/JSON 序列化排序都会让保存变成 159 行 diff——内容其实
    一字没改，但 review 时完全看不出改了哪一条（真发生过）。

    盘中不存在的新实体/新键**追加到末尾**，所以新增是加法，不动老行。

    `renames` 给了就认改名：改过名的实体**留在它原来的位置**，而不是被当成
    新实体甩到末尾（那会让 diff 从"一行"变成"整块搬家"）。
    """
    if not base:
        return edit
    out = dict(edit)
    rn = renames or {}

    def by_name(fam, items, ref_items, keys):
        ren = rn.get(fam) or {}
        ref = {r.get('名'): r for r in (ref_items or [])}
        cur = {i.get('名'): i for i in (items or [])}
        ordered, used = [], set()
        for r in (ref_items or []):
            old = r.get('名')
            now = ren.get(old, old)            # 改过名就按新名字去找
            i = cur.get(now)
            if i is None:
                continue                       # 删掉了
            used.add(now)
            n = dict(i)
            for k in keys:
                if k in n:
                    n[k] = _reorder(n[k], r.get(k))
            ordered.append(n)
        ordered += [i for i in (items or []) if i.get('名') not in used
                    and i.get('名') not in ref]        # 新增的
        return ordered

    out['地块'] = by_name('地块', edit.get('地块'), base.get('地块'), ('属性',))
    out['路'] = by_name('路', edit.get('路'), base.get('路'), ('属性',))

    # 路里的段也是有序的（`段 名 : k=v …` 依次吐），同样对齐
    road_ren = rn.get('路') or {}
    base_segs = {road_ren.get(r.get('名'), r.get('名')): (r.get('段') or [])
                 for r in (base.get('路') or [])}
    for road in out['路']:
        ref = base_segs.get(road.get('名'))
        if not ref:
            continue
        ref_by = {s.get('名'): s for s in ref}
        segs, done = [], set()
        for s in ref:
            cur = next((x for x in (road.get('段') or [])
                        if x.get('名') == s.get('名')), None)
            if cur is not None:
                segs.append(_reorder(cur, s))
                done.add(s.get('名'))
        segs += [x for x in (road.get('段') or []) if x.get('名') not in done]
        road['段'] = segs

    out['配置'] = _reorder(edit.get('配置'), base.get('配置'))
    geo_b, geo_e = {}, dict(edit.get('几何') or {})
    for kind in ('坐标', '范围', '路径线'):
        geo_b[kind] = _reorder(geo_e.get(kind), (base.get('几何') or {}).get(kind))
    out['几何'] = geo_b
    return out


def _fmt_points(pts) -> str:
    return '[' + ','.join(f'({_num(p[0])},{_num(p[1])})' for p in pts) + ']'


def _fmt_shape(shape) -> str:
    return f'{shape[0]}(' + ','.join(_num(x) for x in shape[1]) + ')'


def to_edit_form(model: dict) -> dict:
    """
    内部模型 → **线上形态**：每个值都写成它在 `.map` 里的样子（字符串）。

    为什么不直接送原生 JSON、让 JS 自己判类型：值的写法（`[a,b]` 是列表、
    `是/否` 是布尔、纯数字是数字）在 `.map` 里已经**只有一种正确写法**。
    编辑器照抄这套语法，就不必在 JS 里再写一份 `_fmt_value`/`_parse_value`
    ——抄一份就是两处真相，改一处另一处就悄悄漂（这个项目栽过好几次）。

    副作用还挺好：编辑器里的字段框显示的就是"这一条写进 `.map` 会是什么"。
    """
    def fields(attrs):
        # `产出` 走它自己的语法（`物品:概率(条件)`），不走通用的值语法——
        # 它是个"对象列表"，通用语法只会吐成 `[{'物品':…}]` 那种没人看得懂的东西
        return [{'k': str(k),
                 'v': _fmt_production(v) if k == '产出' else _fmt_value(v)}
                for k, v in (attrs or {}).items()]

    form = {
        '世界': dict(model.get('世界') or {}),
        '配置': fields(model.get('配置')),
        '地块': [{'名': p.get('名', ''), '属于': p.get('属于', ''),
                  '说明': p.get('说明', ''), '字段': fields(p.get('属性'))}
                 for p in model.get('地块') or []],
        '路': [{'名': r.get('名', ''), '从': r.get('从', ''), '到': r.get('到', ''),
                '双向': '是' if r.get('双向', True) else '否',
                '说明': r.get('说明', ''),
                '字段': fields(r.get('属性')),
                '段': [{'名': s.get('名', ''), '说明': s.get('说明', ''),
                        '字段': fields({k: v for k, v in s.items()
                                        if k not in ('名', '说明')})}
                       for s in r.get('段') or []]}
               for r in model.get('路') or []],
        '几何': {k: {n: (_fmt_points(v) if k == '路径线' else
                         _fmt_shape(v) if k == '范围' else
                         f'({_num(v[0])},{_num(v[1])})')
                     for n, v in (model.get('几何') or {}).get(k, {}).items()}
                 for k in ('坐标', '范围', '路径线')},
    }
    return form


def from_edit_form(form: dict) -> tuple:
    """
    线上形态 → 内部模型。**文本不合法就报问题，不猜**。

    :return: `(model, problems)`
    """
    bad = []
    # 键重复是"改完保存，另一个字段没了"的经典来源，先在这里挡掉
    def fields(rows, where, allow_production=True):
        attrs, seen = {}, set()
        for i, row in enumerate(rows or [], 1):
            k = str(row.get('k') or '').strip()
            txt = str(row.get('v') if row.get('v') is not None else '')
            if not k:
                bad.append(f'{where} 第 {i} 个字段没有名字')
                continue
            if k in seen:
                bad.append(f'{where} 的字段 `{k}` 出现了两次')
                continue
            seen.add(k)
            if k == '产出':
                if not allow_production:
                    bad.append(f'{where} 不该有 `产出`（只有地块与路才有）')
                    continue
                v = _parse_production(txt)
            else:
                v = _parse_value(txt)
            attrs[k] = v
        return attrs

    def one_point(txt, where):
        pts = _parse_points(str(txt or ''))
        if not pts:
            bad.append(f'{where} 的坐标写法认不出：{txt!r}（应是 `(x,y)`）')
            return None
        return [float(pts[0][0]), float(pts[0][1])]

    # 键序照 `flatten_world` 的来——两边不一致的话，`save_map` 的往返自检会报
    # "键序变了"，而它其实只是两个构造函数各写各的。（这条抓到过一次。）
    places = []
    for i, p in enumerate(form.get('地块') or [], 1):
        name = str(p.get('名') or '').strip()
        places.append({'名': name, '属于': str(p.get('属于') or '').strip(),
                       '属性': fields(p.get('字段'), f'地块 {name or f"#{i}"}'),
                       '说明': str(p.get('说明') or '')})

    roads = []
    for i, r in enumerate(form.get('路') or [], 1):
        name = str(r.get('名') or '').strip()
        roads.append({
            '名': name, '从': str(r.get('从') or '').strip(),
            '到': str(r.get('到') or '').strip(),
            '双向': str(r.get('双向') or '是').strip() != '否',
            '说明': str(r.get('说明') or ''),
            '属性': fields(r.get('字段'), f'路 {name or f"#{i}"}'),
            '段': [{'名': str(s.get('名') or '').strip(),
                    '说明': str(s.get('说明') or ''),
                    **fields(s.get('字段'), f'路 {name} 的段')}
                   for s in r.get('段') or []],
        })

    geo_in = form.get('几何') or {}
    geo = {'坐标': {}, '范围': {}, '路径线': {}}
    for name, txt in (geo_in.get('坐标') or {}).items():
        v = one_point(txt, f'`坐标 {name}`')
        if v:
            geo['坐标'][name] = v
    for name, txt in (geo_in.get('范围') or {}).items():
        sh = _parse_shape(' ' + str(txt or ''))
        if not sh:
            bad.append(f'`范围 {name}` 认不出：{txt!r}（应是 `圆(r)` 或 `矩形(w,h)`）')
            continue
        geo['范围'][name] = [sh[0], [float(x) for x in sh[1]]]
    for name, txt in (geo_in.get('路径线') or {}).items():
        pts = _parse_points(str(txt or ''))
        if len(pts) < 2:
            bad.append(f'`路径线 {name}` 少于两个点：{txt!r}')
            continue
        geo['路径线'][name] = [[float(p[0]), float(p[1])] for p in pts]

    model = {
        '世界': {k: form.get('世界', {}).get(k) for k in ('名', '游戏开始时间', '时间格式')},
        '配置': fields(form.get('配置'), '配置'),
        '地块': places, '路': roads, '几何': geo,
    }
    return model, bad


def read_base(path: str):
    """
    读盘上现有的 `.map` 当**基准**——改名与级联都靠它比对。

    没有文件、或者盘上那份自己就是坏的，都返回 `None`（那就没有"旧名字"可比，
    照常写，不是错误）。
    """
    if not (path and os.path.isfile(path)):
        return None
    try:
        with io.open(path, encoding='utf-8') as f:
            return flatten_world(parse_world(f.read()))
    except MapFormatError:
        return None


def prepare_edit(edit: dict, base: dict = None, geo: dict = None) -> tuple:
    """
    落盘前的**全部内存整理**：拖出来的几何 → 改名/删除级联 → 保序对齐 → 收尾排序。

    抽出来是因为它有**两个消费者**：`save_map`（写盘）和编辑器的
    `/api/map/preview`（只看不写，把"这一存会变成什么样"画出来）。
    两条路共用同一段代码，编辑器里那张图就不会与保存后的世界各说各话——
    否则 JS 那边得自己抄一份级联规则，而**抄的那份迟早和这里漂开**。

    :return: `(模型, notes, renames)`；`notes` 是"顺带改了什么"，界面上必须显示；
        `renames` 是认出来的改名映射，编辑器拿它去**同步自己那份表单**
        （预览只画图，不会把改过的引用塞回表单，不同步的话"改了名的那条路"
        在图上叫新名字、在表单里叫旧名字，点它就点不中了）。
    """
    notes, renames = [], {}
    # 顺序有讲究（与线上一致）：**先落拖拽几何，再级联**——这样"拖完又改名"的
    # 那份几何也会被改名那一步一起改掉（`cascade` 会改写几何的键）。
    if geo:
        edit, geo_notes = apply_geo_patch(edit, geo)
        notes += geo_notes
    if base and isinstance(edit.get('地块'), list):
        # 先认改名（按列表位置比），再顺着改名与删除把引用一起改掉
        renames = detect_renames(edit, base)
        edit, cs_notes = cascade(edit, base, renames)
        notes += cs_notes
        edit = canonicalize(edit, base, renames)
    if isinstance(edit.get('地块'), list):
        # 收尾：列表顺序必须是 **DFS 前序**，否则新建的子地块会与 emit 顺序错位
        # （`canonicalize` 把"新增的"追加到末尾，而它可能该出现在中间）
        edit['地块'] = order_places_dfs(edit['地块'])
    return edit, notes, renames


def save_map(path: str, edit: dict, backup_dir: str = None,
             geo: dict = None) -> dict:
    """
    扁平模型 → `.map` 落盘。**写盘前三查，任一不过就不写**：

        1. 结构（`validate_edit`）
        2. 往返（emit → parse → flatten 必须与原模型等值）
        3. 备份旧文件（带时间戳，放在 `_备份/`）

    :param geo: 编辑器拖出来的几何（**数值**，见 `apply_geo_patch`）。
        走这里而不是让前端拼文本：格式规则只该有一份。

    返回 `{'ok': bool, ...}`；失败时给 `problems` / `lost`（往返丢的东西）。
    """
    # 重名要**在最前面**挡掉：级联与保序对齐都是按名字配对的，重名会让它们配错，
    # 最后报出来的是一屏"成环"，跟真正的原因没关系（实测过）。
    dup = duplicate_names(edit)
    if dup:
        return {'ok': False, 'stage': '前置', 'problems': dup}

    # ⚠️ 顺序在这里同样关键：**先级联、后校验**。
    # 反过来的话，"删掉一个地块"会在级联之前就被校验器拦下（它的几何悬空了），
    # 而级联本来就是来修那些悬空的——于是按钮永远点不通（正是要修的死路）。
    base = read_base(path)
    edit, notes, _renames = prepare_edit(edit, base, geo)

    problems = validate_edit(edit)
    if problems:
        return {'ok': False, 'problems': problems,
                'stage': '校验（对齐后）' if base else '校验', 'cascade': notes}

    world = nest_world(edit)
    text = emit_world(world, (edit.get('世界') or {}).get('名') or '')
    try:
        back = flatten_world(parse_world(text))
    except MapFormatError as e:
        return {'ok': False, 'stage': '往返', 'cascade': notes,
                'problems': [f'吐出来的文本自己解析不回去：{e}']}

    lost = diff_edit(edit, back)
    if lost:
        return {'ok': False, 'stage': '往返', 'cascade': notes, 'problems': [
            '往返后与编辑前的模型不一致（写下去就会**悄悄改掉别的东西**，所以拒绝落盘）'
        ] + lost}

    backup = _backup(path, backup_dir)

    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)
    return {'ok': True, 'path': path, 'backup': backup, 'cascade': notes,
            'bytes': len(text.encode('utf-8')),
            'lines': len(text.splitlines())}


# ═══════════════════ 入口 ═══════════════════

def _current_world():
    from common.game_controller import GameController
    from common.base_cnf import WORLD_DATA_SELECT
    gc = GameController('mapio')
    gc.init_world()
    return gc.b1WorldLayer.dict, WORLD_DATA_SELECT


def _strip_geo(world: dict) -> dict:
    """只留 .map 覆盖的部分（地理 + 配置），供对照用。"""
    return {
        '世界地点列表': world.get('世界地点列表') or {},
        '世界配置': world.get('世界配置') or {},
        '游戏开始时间': world.get('游戏开始时间'),
        '时间格式': world.get('时间格式'),
    }


def load_world_map(path: str) -> dict:
    """读 `.map`。文件不存在/解析失败都抛异常——**不静默降级**：

    地理是游戏的骨架，读不到就该立刻停下并说清原因，而不是拿着空地图往下跑
    （那会让"所有地点都不存在"，而玩家看到的是 NPC 集体失忆）。
    """
    with io.open(path, encoding='utf-8') as f:
        return parse_world(f.read())


def apply_map(world: dict, map_world: dict) -> dict:
    """把 `.map` 的**地理与配置**盖到 JSON 世界字典上，其余键保持 JSON 的。"""
    out = dict(world)
    out['世界地点列表'] = map_world.get('世界地点列表') or {}
    out['世界配置'] = map_world.get('世界配置') or {}
    for k in ('游戏开始时间', '时间格式'):
        if map_world.get(k) is not None:
            out[k] = map_world[k]
    for k in ('路列表', '地图几何'):
        if map_world.get(k):
            out[k] = map_world[k]
    return out


#: 引擎**按名字读**的世界配置键（`世界配置` 行）。缺了会**退回代码默认值**——
#: 而默认值不是你写的那份（`机缘_每日上限` 的默认值不是 0，也就是**机缘照开**）。
#: 从零手写 `.map` 时最容易漏的就是这一行，所以 `key_notices` 会盯着它。
#: ⚠️ `NPC_上限_*` 是拼出来的（见 `B1WorldLayer.get_config` 的注释），单独判前缀。
ENGINE_CONFIG_KEYS = ('玩家初始数据', '生命值体系', 'NPC_附近距离_段',
                      '事件_最小上下文长度', '心跳间隔_小时',
                      '机缘_间隔_小时', '机缘_每日上限')


def _backup(path: str, backup_dir: str = None):
    """
    写盘前留一份（`save_map` 与命令行那几个动词共用这一个命名规则）。

    ⚠️ **同一秒里连着写两次不能互相盖掉**：重置时就是连着几条命令
    （`--drop-*` → `--skeleton` → `--layout`），秒级时间戳会撞名——
    那就成了"看起来有备份，其实只剩一份"。撞上就往后编号。
    """
    if not os.path.isfile(path):
        return None
    bdir = backup_dir or os.path.join(os.path.dirname(path), '_备份')
    os.makedirs(bdir, exist_ok=True)
    stem = f'{os.path.basename(path)}.{time.strftime("%Y%m%d-%H%M%S")}'
    dst, n = os.path.join(bdir, stem), 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(bdir, f'{stem}-{n}')
    shutil.copy2(path, dst)
    return dst


def main() -> int:
    args = sys.argv[1:]
    from common.base_cnf import BASE_PATH, WORLD_MAP_FILE
    out_path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
    if '--to' in args:
        out_path = args[args.index('--to') + 1]

    _groups = (('--drop-geo', '--drop-roads'), ('--skeleton',), ('--layout',),
               ('--export', '--roundtrip'))
    _used = [g for g in _groups if any(v in args for v in g)]
    if len(_used) > 1:
        print('⚠️ 一次只做一件事（你给了 '
              + '、'.join(' '.join(g) for g in _used) + '）——按第一个来，其余忽略。'
              '它们各自会写盘，分开跑更清楚。')

    if '--drop-geo' in args or '--drop-roads' in args:
        # **重置地图的第一步**：把"不是预期"的那几样清掉，留下有价值的那部分。
        # 用户要的正是这个形状——"连线、位置、范围都不是我的预期"，
        # 而 41 个地点的名字/属性/产出/规矩是**手写的、值钱的**。
        world = load_world_map(out_path)
        _backup(out_path)
        notes = []
        if '--drop-geo' in args:
            g = world.get('地图几何') or {}
            n = sum(len(g.get(k) or {}) for k in ('坐标', '范围', '路径线'))
            world['地图几何'] = {'坐标': {}, '范围': {}, '路径线': {}}
            notes.append(f'几何 {n} 行（坐标/范围/路径线）')
        if '--drop-roads' in args:
            notes.append(f"路 {len(world.get('路列表') or [])} 条")
            world['路列表'] = []
        io.open(out_path, 'w', encoding='utf-8', newline='\n').write(emit_world(world))
        print('✓ 已清掉：' + '、'.join(notes) + '。地点与 `配置` 原样留着。'
              '接着 `--skeleton`（铺草稿路）→ `--layout`（铺几何）→ 开编辑器')
        return 0

    if '--skeleton' in args:
        # **从零起一张图**的第一步：手写完地点树（名字/属于/属性），让代码铺一层
        # **骨架路**当草稿，再由人删改。树里相邻的兄弟/父子各连一条，标
        # `自动生成=是`——好让人一眼看出哪些是机器凑的，将来该重连的正是它们。
        world = load_world_map(out_path)
        if world.get('路列表') and '--force' not in args:
            print(f'✓ 已有 {len(world["路列表"])} 条路，未改动（要覆盖加 --force）')
            return 0
        world['路列表'] = skeleton_roads(world.get('世界地点列表') or {})
        _backup(out_path)
        io.open(out_path, 'w', encoding='utf-8', newline='\n').write(emit_world(world))
        print(f'✓ 已铺骨架路 {len(world["路列表"])} 条（全带 `自动生成=是`）。'
              f'接着跑 `--layout` 铺几何，再开编辑器拖')
        return 0

    if '--layout' in args:
        world = load_world_map(out_path)
        geo = world.setdefault('地图几何', {})
        if geo.get('坐标') and '--relayout' not in args:
            print('✓ 已有坐标，未重排（要重排加 --relayout）')
            return 0
        geo.update(auto_layout(world.get('世界地点列表') or {},
                               world.get('路列表') or []))
        _backup(out_path)
        io.open(out_path, 'w', encoding='utf-8', newline='\n').write(emit_world(world))
        print(f'✓ 已布局：坐标 {len(geo.get("坐标") or {})} 个，'
              f'范围 {len(geo.get("范围") or {})} 个')
        return 0

    if '--export' in args or '--roundtrip' in args:
        # 地理的真源已经换成 .map 之后，再导出就该**从 .map 读**（否则会把 JSON 里的
        # 陈旧地理又搬回来）。所以：.map 在 → 读它（幂等重排）；不在 → 从 JSON 首次导出。
        if os.path.isfile(out_path) and '--from-json' not in args:
            world = load_world_map(out_path)
            world.setdefault('世界配置', world.get('世界配置') or {})
            name = ''
        else:
            world, name = _current_world()
        if not world.get('路列表'):
            world = dict(world)
            world['路列表'] = skeleton_roads(world.get('世界地点列表') or {})
        text = emit_world(world, name)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        _backup(out_path)
        io.open(out_path, 'w', encoding='utf-8', newline='\n').write(text)
        lines = text.count('\n')
        print(f'✓ 已导出 {os.path.relpath(out_path, BASE_PATH)}（{lines} 行，'
              f'地点 {len(_flatten(world.get("世界地点列表") or {}))} 个、'
              f'路 {len(world.get("路列表") or [])} 条）')

        if '--roundtrip' in args:
            back = parse_world(text)
            a, b = _strip_geo(world), _strip_geo(back)
            if a == b:
                print('✓ 往返一致')
                return 0
            print('✗ 往返**不一致**：')
            _first_diff(a, b)
            return 1
        return 0
    print(__doc__)
    return 0


def _flatten(tree: dict) -> list:
    out = []

    def walk(node, prefix):
        _a, _d, kids = _normalize(node)
        for k, v in (kids or {}).items():
            p = f'{prefix}·{k}' if prefix else k
            out.append(p)
            walk(v, p)

    walk(tree, '')
    return out


def _first_diff(a, b, path=''):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                print(f'    + {path}.{k} 只在新的里')
            elif k not in b:
                print(f'    - {path}.{k} 只在旧的里')
            else:
                _first_diff(a[k], b[k], f'{path}.{k}')
    elif a != b:
        print(f'    ≠ {path}: {str(a)[:60]!r} → {str(b)[:60]!r}')


if __name__ == '__main__':
    sys.exit(main())
