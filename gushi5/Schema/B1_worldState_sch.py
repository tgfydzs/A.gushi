import math
import re
from collections import deque

from common.log import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════
#  地点节点的保留键
# ══════════════════════════════════════════════════════════════
#
# 地点树升级后，每个节点（不只叶节点）都可以带属性：
#
#     "东域": {
#         "_属性": {"灵气": "浓郁", "危险": "低"},
#         "天道宗": { ... 子地点 ... }
#     }
#
# 于是"这个键是子地点还是元数据"必须有个判据，否则遍历器会把 `_属性`
# 当成一个叫"_属性"的地点，把 `苍云大陆·东域·_属性` 写进已知地点列表，
# L3 拿到它之后**完全可能把玩家的位置写成这串**——位置一漂，
# 之后所有距离计算都跟着漂，且不会报任何错。
#
# 判据取 `_` 前缀 + `描述`。中文地名不会撞上这两个名字。
# ⚠️ 所有遍历器都必须走 `normalize_node()`，**不要直接读原始节点的键**。
_RESERVED_DESC_KEY = '描述'
_RESERVED_PREFIX = '_'

#: `类型` 里哪些值是**区域**（"面"：只有范围、不是落脚点），见 `地图格式.md` §9.1。
#: ⚠️ **不含 `山脉`/`冰原`**——那些一般**能停留**（能采药、能扎营），是地点。
#: 作者没写 `类型` 一律按**地点**算（不猜；天道宗有 7 个子地块，可它自己能停留）。
REGION_TYPES = frozenset({'大陆', '区域', '域', '州', '疆域'})


class B1WorldLayer:
    """
    base1 世界层
    职能：封装世界规则、状态
    world_dict: dict | 传入世界规则字典，内部init通过对应key提取
    """

    def __init__(self, world_dict: dict):
        self.dict = world_dict

    # ===================== 世界配置读取 =====================

    def get_start_time(self) -> str:
        """读取游戏开始时间，默认 2026-07-19 18:00"""
        return self.dict.get('游戏开始时间', '2026-07-19 18:00')

    # ===================== 世界配置读取（数据驱动） =====================

    def get_config(self, key: str, default=None):
        """
        读取世界配置项。

        从 world_dict['世界配置'] 中获取配置值，不存在则返回 default。
        通过统一入口读取，配置变更只需改 data/ 下的 JSON 文件。

        当前可用配置项：
            NPC_附近距离_段    — 附近NPC距离阈值（R3 候选筛选用）
            事件_最小上下文长度 — 新事件判断防死循环的最小上下文条数
            心跳间隔_小时      — 后台心跳触发间隔（游戏时间）
            NPC_上限_MAX_*     — 各 NPC 表的截断上限。键名由代码拼成
                                 `f'NPC_上限_{attr}'`，attr 取 `B3NPCLayer.CAP_MAP`
                                 的值（MAX_OBSERVATION / MAX_MEMORY / MAX_PENDING /
                                 MAX_PLAYER_OBSERVATION / MAX_BELIEF / MAX_GOALS /
                                 MAX_RELATIONSHIP），由 `init_all_npcs` 逐个覆写。
                                 ⚠️ 2026-10-07 前这里是 `NPC_观察上限` 这类旧键名，
                                 与代码查的键**零交集**，所以那几项从来没生效过——
                                 只因值恰好等于类默认值才没暴露。改键名时两边一起改。
            玩家初始数据       — 玩家JSON文件路径
            生命值体系         — "数值" / "文本"，决定像游戏还是像小说
            机缘_间隔_小时    — 同一地点两次机缘机会之间至少隔多少游戏时间（R4）
            机缘_每日上限     — 每个游戏日最多得手**几件**机缘之物。**设 0 就是关掉机缘**
                                 （比清空 15 张 `产出` 表省事）。键名由 R4 直接读，
                                 不经过 `NPC_上限_*` 那套拼接
        """
        config = self.dict.get('世界配置', {})
        return config.get(key, default)

    # ===================== 位置查询与路径工具 =====================

    def get_location_tree(self) -> dict:
        """
        返回完整的世界地点嵌套字典。
        """
        return self.dict.get('世界地点列表', {})

    # ===================== 节点归一化（唯一形状判定点）=====================

    @staticmethod
    def is_reserved_key(key) -> bool:
        """这个键是元数据（不是子地点名）吗？"""
        return (not isinstance(key, str)
                or key.startswith(_RESERVED_PREFIX)
                or key == _RESERVED_DESC_KEY)

    @staticmethod
    def normalize_node(raw) -> tuple:
        """
        把任意形状的地点节点归一成 `(属性, 描述, 子地点)`。

        三种合法输入：

            1. 字符串（老写法）      "炼气期外门弟子日常修炼之所"
               → ({}, '炼气期外门弟子日常修炼之所', {})

            2. 对象（新写法）        {"_属性": {...}, "描述": "...", "子地点名": {...}}
               → (属性, 描述, 子地点)

            3. 只有属性、没有子地点  → 叶节点

        「是不是叶节点」的判据是 **`not 子地点`**，不是"值是不是 dict"——
        后者会被 `{"_属性": {...}}` 这种只带属性的节点骗到（它是个非空 dict，
        会被当成"有子地点"，然后 `_属性` 就作为子节点被列出来）。

        **这是唯一的形状判定点。** 所有遍历器都走它。少改一处，
        `_属性` 就会作为地点漏进路径里——见 `src/test/location_test.py` 的断言。
        """
        if isinstance(raw, str):
            return {}, raw, {}
        if not isinstance(raw, dict):
            return {}, str(raw), {}
        attrs = raw.get('_属性')
        if not isinstance(attrs, dict):
            attrs = {}
        desc = raw.get(_RESERVED_DESC_KEY, '')
        kids = {k: v for k, v in raw.items()
                if not B1WorldLayer.is_reserved_key(k)}
        return dict(attrs), str(desc or ''), kids

    def list_location_paths(self, max_depth: int = 3) -> list:
        """
        把地点树压成路径字符串列表（到 max_depth 层为止）。

        用途：给结算层一份**已知地点参考**，免得它自己编一个不在树里的位置名
        （实测出现过玩家要去"青木山脉"，结果被写成"落霞坪外山道"——
        位置漂了，之后所有距离计算都跟着漂）。

        ⚠️ 遍历走 `normalize_node`，`_属性` 之类的保留键**不会**成为路径段。

        :return: ['苍云大陆', '苍云大陆·东域', ...]
        """
        out = []

        def walk(node, cur, depth):
            if depth > max_depth:
                return
            _, _, kids = B1WorldLayer.normalize_node(node)
            for key, sub in kids.items():
                p = cur + [key]
                out.append('·'.join(p))
                walk(sub, p, depth + 1)

        walk(self.get_location_tree(), [], 1)
        return out

    # ===================== 地段属性查询 =====================

    def _walk_path(self, path_str: str):
        """
        沿路径下钻，返回 `[(层名, 原始节点), ...]`（根 → 叶）。
        **任一段对不上就返回 None**——不是"能走多远算多远"的部分结果。

        ⚠️ 这里用**最长匹配**，不按 `·` 逐段匹配。原因：地点键本身可能含 `·`
        （`"上古试炼秘境·万象洞天"`、`"外门弟子修炼区·落霞坪"` 等 7 个）。
        `parse_path` 会把它们拆成两段，于是 `list_location_paths` **吐得出来**的路径
        `get_location_attrs` 却**解不回来**——同一棵树两套答案。

        `get_location_children` 不受影响：它收的是**原始的键名列表**
        （调用方从键上取的），`·` 在键名里只是一个普通字符。
        """
        segs = self.parse_path(path_str)
        node = self.get_location_tree()
        chain = []
        i = 0
        while i < len(segs):
            _, _, kids = self.normalize_node(node)
            hit = None
            for n in range(len(segs), i, -1):      # 从最长开始试
                cand = '·'.join(segs[i:n])
                if cand in kids:
                    hit, i = cand, n
                    break
            if hit is None:
                return None
            node = kids[hit]
            chain.append((hit, node))
        return chain

    def _resolve_node(self, path_str: str):
        """沿路径下钻到原始节点；整条路径不成立返回 None。"""
        chain = self._walk_path(path_str)
        return chain[-1][1] if chain else None

    def get_location_attrs(self, path_str: str) -> dict:
        """某个地点**自己**的属性（不含祖先）。找不到返回 {}。"""
        node = self._resolve_node(path_str)
        if node is None:
            return {}
        attrs, _, _ = self.normalize_node(node)
        return attrs

    def get_location_context(self, path_str: str) -> list:
        """
        从根到该地点的**属性链**，供 LLM 判断用：

            [{'层级': '苍云大陆', '属性': {...}},
             {'层级': '东域',     '属性': {...}},
             {'层级': '落霞坪',   '属性': {...}}]

        ⚠️ **刻意不做属性继承。** "东域灵气浓郁"对落霞坪意味着什么，
        由 LLM 看着整条链自行判断。写成继承规则（子节点自动获得父节点属性）
        就等于把判定权从 LLM 手里拿走——那正是 `不程序化叙事` 禁止的。

        链上某层没有属性时仍然保留该层级（LLM 需要看到地名本身）。
        路径不成立时返回空列表。
        """
        chain = self._walk_path(path_str) or []
        return [{'层级': name, '属性': self.normalize_node(n)[0]}
                for name, n in chain]

    # ===================== 几何（.map 的《几何》段） =====================
    #
    # ⚠️ **几何只用于客观判定**：他在哪、在不在场、前端怎么画。
    # 听不听得见 / 消息传多远 / 走多久 / 谁先动手——**一律仍归 LLM**。
    # 项目已经删过一轮"距离→结论"（R2 的段数折算、S1 旁路、R3 的距离过滤），
    # 别让它从几何这扇门爬回来。见 `地图格式.md` §1.3。

    def geometry(self, kind: str) -> dict:
        """读几何段。`kind` ∈ {'坐标', '范围', '路径线'}；缺了就是 `{}`。"""
        return (self.dict.get('地图几何') or {}).get(kind) or {}

    def roads(self) -> list:
        """路列表（地块之间的通路）。"""
        return self.dict.get('路列表') or []

    def _depth_map(self) -> dict:
        """地块名 → 层级深度（0 = 根）。用于"落在嵌套范围里时取最深那个"。"""
        if getattr(self, '_depths', None) is None:
            depths = {}

            def walk(node, depth):
                for k, v in (self.normalize_node(node)[2] or {}).items():
                    depths[k] = depth
                    walk(v, depth + 1)

            walk(self.get_location_tree() or {}, 0)
            self._depths = depths
        return self._depths

    def in_range(self, name: str, x: float, y: float) -> bool:
        """这个坐标在不在某个地块的范围内。没有范围信息 → False。"""
        shape = self.geometry('范围').get(name)
        center = self.geometry('坐标').get(name)
        if not shape or not center:
            return False
        kind, nums = shape[0], shape[1]
        dx, dy = x - center[0], y - center[1]
        if kind == '圆':
            return dx * dx + dy * dy <= nums[0] * nums[0]
        if kind == '矩形':
            return abs(dx) <= nums[0] / 2 and abs(dy) <= nums[1] / 2
        return False

    def nearest_place(self, x: float, y: float):
        """最近的**地点**（不含区域）→ (名, 距离)。没有几何信息 → (None, inf)。"""
        best, best_d = None, float('inf')
        for name, c in self.geometry('坐标').items():
            if self.is_region(name):
                continue
            d = math.hypot(c[0] - x, c[1] - y)
            if d < best_d:
                best, best_d = name, d
        return best, best_d

    def region_at(self, x: float, y: float):
        """脚下是哪个**区域**（面）——取**最小的那个圈**（域套在陆里面）。不在任何区域给 None。"""
        best, best_r = None, None
        for name, shape in self.geometry('范围').items():
            if not self.is_region(name) or not shape or shape[0] != '圆':
                continue
            if self.in_range(name, x, y):
                r = shape[1][0]
                if best_r is None or r < best_r:
                    best, best_r = name, r
        return best

    def locate(self, x: float, y: float) -> dict:
        """
        坐标 → `{'地块': 名, '在路上': bool, '距离': float, '区域': 名|None, '野外': bool}`。

        - 落在某个**地点**的范围里 → 就在那个地点（嵌套时取**最深**的那个，与旧的地段链同一语义）
        - 都不落 → **在路上**：退到最近的**地点**，`距离` 说明有多远
        - `野外` = 没落在任何地点的范围里，**而且离最近的地点比"站在那儿"还远**：
          人站在东域的旷野上，就不该被说成"在灵虚集"。`区域` 是脚下那个区域（面），
          "在野外"时它仍能给出灵气/危险——**区域是面，不是落脚点**

        "在路上"不是一个要另存的字段：它是"坐标没落在任何范围里"这个事实。

        ⚠️ **区域不参与"落在谁的范围里"**（`类型=区域` 的那些只提供 `区域` 这一项）。
        不这么分的话，"去东域"永远是去它的放射中心——那个点不代表任何地方。
        没写 `类型` 的地块照旧按地点算（**不猜**：天道宗有 7 个子地块，可它自己能停留）。
        """
        depths = self._depth_map()
        best, best_depth = None, -1
        for name in self.geometry('范围'):
            if self.is_region(name):
                continue
            if self.in_range(name, x, y) and depths.get(name, -1) > best_depth:
                best, best_depth = name, depths.get(name, -1)
        region = self.region_at(x, y)
        if best:
            return {'地块': best, '在路上': False, '距离': 0.0,
                    '区域': region, '野外': False}
        name, d = self.nearest_place(x, y)
        return {'地块': name, '在路上': True, '距离': round(d, 1),
                '区域': region, '野外': d > self.PLACE_CORE}

    def place_coord(self, name: str):
        """**地块名** → 坐标；没有就返回 None（迁移与落点用）。"""
        return self.geometry('坐标').get(name)

    # ---- 路：从"只给眼睛看"到"落点要用"（1A.1）----
    #
    # ⚠️ 边界（`地图格式.md` §1.3）：路在这里只用来回答**"他该走哪条路"**
    # 与**"他在这条线上的哪一点"**——都是**表示**问题，和 `locate()` 同类。
    # **绝不用它算"要走多久 / 谁先动手 / 传得多远"**：那些是叙事判断，
    # 归 LLM。这个项目为"距离→结论"删过一轮代码（R2 段数折算、S1 旁路、
    # R3 距离过滤），别让它从这扇门爬回来。

    def road_between(self, a: str, b: str):
        """连接 a、b 的路；没有就是 None。`双向=否` 时只认 `从→到` 的方向。"""
        for r in self.roads():
            x, y = r.get('从'), r.get('到')
            if x == a and y == b:
                return r
            if x == b and y == a and r.get('双向', True):
                return r
        return None

    def _adjacency(self) -> dict:
        """地块名 → [(邻居, 那条路)]。**这是 `路列表` 第一次进引擎判定**。"""
        if getattr(self, '_adj', None) is None:
            adj = {}
            for r in self.roads():
                a, b = r.get('从'), r.get('到')
                if not a or not b:
                    continue
                adj.setdefault(a, []).append((b, r))
                if r.get('双向', True):
                    adj.setdefault(b, []).append((a, r))
            self._adj = adj
        return self._adj

    def route(self, src: str, dst: str) -> list:
        """
        按**跳数**最短路的地块序列（含首尾）。走不通返回 `[]`。

        "远的地方不是被拒绝，而是要走、要走很多轮"——多轮怎么切成"每轮走一跳"，
        就落在这里：每轮只沿 `route()` 的**第一跳**往前挪。
        """
        if not src or not dst:
            return []
        if src == dst:
            return [src]
        adj = self._adjacency()
        prev = {src: None}
        q = deque([src])
        while q:
            cur = q.popleft()
            for nxt, _r in adj.get(cur, []):
                if nxt in prev:
                    continue
                prev[nxt] = cur
                if nxt == dst:
                    path = [dst]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return list(reversed(path))
                q.append(nxt)
        return []

    def road_point(self, road: dict, t: float):
        """
        `t ∈ [0,1]` 处的坐标。

        ⚠️ 三点必须走**二次贝塞尔**，和前端 `buildMapStatic` 生成
        `M…Q…` 用的是同一个公式——不一致的话，玩家在地图上就**不在那条线上**
        （`map_edit_test` 有一条常量比对盯着）。多于三点按折线等分。
        """
        pts = self.geometry('路径线').get((road or {}).get('名'))
        if not pts:
            a, b = self.place_coord((road or {}).get('从')), self.place_coord((road or {}).get('到'))
            if not a or not b:
                return None
            pts = [a, b]
        t = max(0.0, min(1.0, float(t)))
        if len(pts) >= 3:
            (x0, y0), (x1, y1), (x2, y2) = pts[0], pts[1], pts[2]
            u = 1.0 - t
            return [u * u * x0 + 2 * u * t * x1 + t * t * x2,
                    u * u * y0 + 2 * u * t * y1 + t * t * y2]
        n = len(pts) - 1
        seg = min(int(t * n), n - 1)
        lt = t * n - seg
        (x0, y0), (x1, y1) = pts[seg], pts[seg + 1]
        return [x0 + (x1 - x0) * lt, y0 + (y1 - y0) * lt]

    #: 投影时的采样密度。1/48 够叙事用（"刚上路/过半/将到"本就粗），
    #: 而再密一点就要在每轮每次 `on_road_at` 上多花几倍的乘法。
    ROAD_SAMPLES = 48
    #: 离路径线多近才算"在这条路上"。
    ROAD_CORRIDOR = 40.0
    #: 离地块中心多近就算"到了那个地方"（停留语义）。区域枢纽也得有"站的地方"。
    PLACE_CORE = 26.0

    def _road_table(self) -> list:
        """`[(路, t, x, y)]` 采样表。缓存——它只取决于几何，一局不变。"""
        if getattr(self, '_roadtbl', None) is None:
            tbl = []
            for r in self.roads():
                for i in range(self.ROAD_SAMPLES + 1):
                    t = i / self.ROAD_SAMPLES
                    p = self.road_point(r, t)
                    if p:
                        tbl.append((r, t, p[0], p[1]))
            self._roadtbl = tbl
        return self._roadtbl

    def _attrs_by_name(self) -> dict:
        """地块名 → 它自己的属性（不含祖先）。**一次走完树，别按名字逐个解析路径。**"""
        if getattr(self, '_attrsn', None) is None:
            out = {}

            def walk(node):
                for k, v in (self.normalize_node(node)[2] or {}).items():
                    out[k] = self.normalize_node(v)[0]
                    walk(v)

            walk(self.get_location_tree() or {})
            self._attrsn = out
        return self._attrsn

    def is_region(self, name: str) -> bool:
        """
        这块地是**区域**（面）还是**地点**（点）？

        判据只有一条：作者写的 `类型`（`地图格式.md` §3.1）。缺省 = **地点**。
        **不猜**——"非叶就是区域"是错的：天道宗有 7 个子地块，可它自己能停留。
        """
        return str((self._attrs_by_name().get(name) or {}).get('类型') or '').strip() \
            in REGION_TYPES

    def _leaf_names(self) -> set:
        """能**停下来**的那些地块（没有子地块的）。区域（东域/苍云大陆）不算。"""
        if getattr(self, '_leaves', None) is None:
            out = set()

            def walk(node):
                _, _, kids = self.normalize_node(node)
                for k, v in (kids or {}).items():
                    if self.normalize_node(v)[2]:
                        walk(v)
                    else:
                        out.add(k)

            walk(self.get_location_tree() or {})
            self._leaves = out
        return self._leaves

    def stop_in(self, name: str, x: float = None, y: float = None) -> str:
        """
        `name` 是**地区**时，落到里面**离 `(x,y)` 最近的地点**；是地点就原样返回。

        为什么非有这一步：地区的 `坐标` 只是那个圈的**圆心**（`地图格式.md` §9.1），
        落在它上面等于"站在旷野里"——**没有落脚点**。作者说"我去东域"，落地就该是
        东域里某个真地方。规则只有一条（先给最简单的）：**子树里离你最近的那个叶节点**。

        地区底下**一个叶都没有**（一整片没有具体地点的荒野）→ 返回 `''`：
        调用方照旧**不动 + 警告**，不编一个假位置。
        """
        if not name or not self.is_region(name):
            return name
        sub = self._subtree_names(name)
        best, bd = '', None
        for leaf in self._leaf_names():
            if leaf not in sub:
                continue
            c = self.place_coord(leaf)
            if not c:
                continue
            if x is None or y is None:
                return leaf                      # 没有参照点：取稳定序里的第一个
            d = (c[0] - x) ** 2 + (c[1] - y) ** 2
            if bd is None or d < bd:
                best, bd = leaf, d
        return best

    def _subtree_names(self, root: str) -> set:
        """`root` 自己 + 它底下所有节点的名字（`属于` 那棵树，不含路）。"""
        index = {}

        def walk(node):
            for k, v in (self.normalize_node(node)[2] or {}).items():
                index[k] = v
                walk(v)

        walk(self.get_location_tree() or {})
        out, stack = {root}, [root]
        while stack:
            n = stack.pop()
            for k in (self.normalize_node(index.get(n) or {})[2] or {}):
                out.add(k)
                stack.append(k)
        return out

    def on_road_at(self, x: float, y: float):
        """
        坐标 → **他在哪条路、走到第几成**（`{'路','t','段','距离'}`），不在路上给 `None`。

        这是 `地图格式.md` 说的"**由坐标派生**在哪条路哪一段"——`行进` **不另存字段**
        （少一个字段就少一处"坐标说在 A、行进说在路上"的不一致）。

        ⚠️ **不能直接用 `locate()['在路上']` 当判据。** 自动布局给的 `范围` 半径是
        **画图**用的（区域圆要罩住子地块，天道宗 r=104、东域 r=252），于是**路的整条
        走廊都落在某个区域的圈里**——用 `locate` 判的话"在路上"**永远不会为真**
        （实测：41 个地块坐标 0 个在路上，连路的中点也在某个区域的圈里）。
        所以这里用一套**停留语义**的判据：

            1. 落在某个**叶**地块范围内      → 就在那儿（叶 = 真能停下来的）
            2. 离任何地块中心 ≤ `PLACE_CORE`  → 就当在那儿（区域枢纽也有"站的地方"）
            3. 离某条路 ≤ `ROAD_CORRIDOR`    → **在那条路上**
            4. 都不满足                      → 不在路上（由 `location_desc` 退到"附近"）

        于是：短路（同门师兄弟之间那几十个单位）基本判成"在天道宗地界内"——
        那是对的，你确实没走多远；**长路**（跨域的几百个单位）中段才会判成"在路上"，
        而那正是"在路上"有意义的地方。

        ⚠️ 刻意与 `locate()` **分开**：`locate` 有 13 个调用点、每轮跑很多次，
        而这里要把坐标投影到 65 条路上。只有真要那个"语义"的地方（喂 L1/L3、
        画图）才调它。
        """
        leaves = self._leaf_names()
        for name in self.geometry('范围'):
            # **区域不算"站在这儿"**：人站在东域的旷野上，不该因为落在东域那个大圈里
            # 就判成"不在路上"。区域是面，落脚点只有地点。
            if name in leaves and not self.is_region(name) and self.in_range(name, x, y):
                return None
        _near, nd = self.nearest_place(x, y)
        if nd is not None and nd <= self.PLACE_CORE:
            return None
        best, bd = None, None
        for r, t, rx, ry in self._road_table():
            d = (rx - x) ** 2 + (ry - y) ** 2
            if bd is None or d < bd:
                best, bd = (r, t), d
        if not best or math.sqrt(bd) > self.ROAD_CORRIDOR:
            return None
        road, t = best
        return {'路': road.get('名', ''),
                '从': road.get('从'), '到': road.get('到'),
                't': round(t, 3), '段': self.segment_at(road, t),
                '距离': round(math.sqrt(bd), 1)}

    def segment_ranges(self, road: dict) -> list:
        """
        一条路的段 → `[(段名, t0, t1), …]`。**"段落在路径线的哪一截"只有这里说了算**：
        段可以自己写 `区间=[t0,t1]`；没写区间的按**段序等分**（`地图格式.md` §9 #4 定的）。

        谁在用：小地图叙述"正在穿过雪岭段"（`segment_at`）、`行程` 报段名时折算
        （`GameController.travel_frac_by_segment`）。**两处必须同一个答案**——
        各算各的就会出现"叙述说在雪岭、落点却在溪谷"。
        """
        segs = [s for s in ((road or {}).get('段') or []) if s.get('名')]
        n = len(segs)
        out = []
        for i, s in enumerate(segs):
            t0, t1 = i / n, (i + 1) / n
            rng = s.get('区间')
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                try:
                    t0, t1 = float(rng[0]), float(rng[1])
                except (TypeError, ValueError):
                    pass
            out.append((s.get('名'), t0, t1))
        return out

    def segment_range(self, road: dict, name: str):
        """段名 → `(t0, t1)`；这条路上没有这一段就给 `None`。"""
        for n, t0, t1 in self.segment_ranges(road):
            if n == name:
                return (t0, t1)
        return None

    def segment_at(self, road: dict, t: float):
        """
        这条路上 `t` 落在哪一段 → 段名；没有段就给 `None`。

        段是**作者写的**（"雪岭段"这种有语义的名字），所以它不是从几何生成的，
        而是查表（区间与等分的规矩见 `segment_ranges`）。
        当前 `.map` 里 0 条段，所以实际都返回 `None`——接口先留着，
        段等**修订地图**那一次铺（见 `地图实施计划.md` 阶段 4）。
        """
        rgs = self.segment_ranges(road)
        if not rgs:
            return None
        for name, t0, t1 in rgs:
            if t0 <= t <= t1:
                return name
        return rgs[-1][0]

    def road_desc(self, road_name: str, t: float = None) -> str:
        """
        路的**语义**描述（喂 LLM 用，**不含任何数字**）。

        `t` 是"走到第几成"，但出口只有"刚上路 / 半路 / 将到"三档——
        和 `失真感知` 同一条纪律：**给刻度就等于给公式**。
        """
        r = next((x for x in self.roads() if x.get('名') == road_name), None)
        if not r:
            return ''
        seg = self.segment_at(r, t) if t is not None else None
        # ⚠️ 措辞必须**方向中性**：路的 `从/到` 是书写顺序，跟学生实际往哪走无关。
        # 写成"A到B的路上"会让 L3 以为他在从 A 去 B，而实际可能正相反
        # （实测：从落霞坪走向天道宗，描述成了"天道宗到落霞坪的路上"）。
        where = seg or f'{r.get("从")}—{r.get("到")} 之间的路上'
        if t is None:
            return where
        how = '刚上路' if t < 0.3 else ('半路' if t < 0.75 else '将到')
        return f'{where}（{how}）'

    # ---- 位置：坐标是唯一真源，路径是它的派生 ----

    def coord_at(self, state: dict):
        """从一个 `当前状态CurrentState` 取坐标 → `(x, y)` 或 None。"""
        c = (state or {}).get('位置')
        if isinstance(c, (list, tuple)) and len(c) == 2:
            try:
                return float(c[0]), float(c[1])
            except (TypeError, ValueError):
                return None
        return None

    def name_at(self, state: dict) -> str:
        """→ **地块名**（短名，给人看/给测试用）。没有几何时回退到路径的末段。"""
        c = self.coord_at(state)
        if c is not None:
            return self.locate(*c).get('地块') or ''
        raw = (state or {}).get('当前所处位置', '') or ''
        return self.name_of(raw) if raw else ''

    def path_at(self, state: dict) -> str:
        """
        → **完整路径**。引擎内部一律用路径（与换坐标之前同一形状），
        所以 13 个调用点一行都不用改。

        坐标是**真源**；路径由它解析出来（`locate` → 地块名 → 路径）。
        落在所有范围之外（=在路上）时退到最近的地块——于是返回值**永远是个合法路径**：
        绝不会再出现"往真路径后面接话"那种半真半假的串。
        """
        c = self.coord_at(state)
        if c is not None:
            name = self.locate(*c).get('地块')
            return self.place_paths().get(name, '') if name else ''
        raw = (state or {}).get('当前所处位置', '') or ''      # 旧档回退
        if raw in self.place_paths().values():
            return raw
        return self.place_paths().get(self.name_of(raw), '')

    def location_desc(self, state: dict) -> str:
        """
        给 LLM 看的"他在哪"。**唯一一处允许出现"在路上"的地方。**

        为什么非要有它：`path_at()` 在"在路上"时会**静默退到最近地块**
        （它的返回值永远是合法路径），于是 L1 和 L3 根本**分不出
        "他在路上"和"他就在那个村子"**——走在雪岭上的人收到的地段链是最近那个
        村子的，而且没有任何人知道这件事。这个方法就是那个静默塌缩的**显式出口**。

        出口是**语义**不是坐标：`路名 + 刚上路/半路/将到`，不含任何数字。
        """
        c = self.coord_at(state)
        if c is None:
            return (state or {}).get('当前所处位置', '') or ''
        # ⚠️ 判"在路上"用 `on_road_at`，**不是** `locate()['在路上']`
        # ——后者被自动布局的大半径 `范围` 罩住了，永远为假（见 `on_road_at` 的注释）
        rd = self.on_road_at(*c)
        if rd:
            return self.road_desc(rd['路'], rd['t'])
        loc = self.locate(*c)
        # **在野外**：脚下一整片区域，但不在任何具体地点。不说出来的话，L3 会把他
        # 当成"就在最近那个村子里"——那正是 2026-10-08 那次事故的形状（静默借用）。
        # 注意这里给的是**语义**（哪个区域的旷野上），不是坐标也不是距离。
        if loc.get('野外'):
            return f'（{loc["区域"]}的旷野上，不在任何具体地点）' if loc.get('区域') \
                else '（旷野上，不在任何具体地点）'
        name = loc.get('地块')
        if not name:
            return '（不知何处）'
        return self.place_paths().get(name, '') or name

    def location_context_for(self, state: dict) -> list:
        """
        地段属性链。**"在路上"时不再静默借用最近地块。**

        在路上的链 = 路自己的属性（`自动生成` 这种生成标记不算内容，滤掉）
        + **最近那个端点**的链，而且第一条**明写着 `在路上: True`**。
        这样 L3 叙述时知道自己在路上，而不是以为他站在那个村子里。

        ⚠️ 为什么仍然带上最近端点的链：**链空掉正是 2026-10-08 那次事故的形状**
        （地段能力静默全失效，L3 把 `灵气：浓郁` 叙述成"偏薄"）。
        带上它、并且**说出来**，比空着强，也比假装他就在那里强。
        """
        c = self.coord_at(state)
        if c is None:
            return self.get_location_context((state or {}).get('当前所处位置', ''))
        # 同 `location_desc`：判"在路上"用 `on_road_at`
        rd = self.on_road_at(*c)
        if not rd:
            loc = self.locate(*c)
            # **在野外**（脚下一整片区域，不在任何具体地点）：链就是**那个区域**的链，
            # 外加一条明写的 `不在任何地点`。不给的话，这里会退回"最近那个地点"的链
            # ——站在东域旷野上的人，收到的是灵虚集的灵气与规矩（静默借用，老毛病）。
            if loc.get('野外'):
                region = loc.get('区域')
                head = {'层级': f'（在{region}的旷野上）' if region else '（旷野上）',
                        '属性': {}, '不在任何地点': True}
                tail = self.get_location_context(
                    self.place_paths().get(region, '')) if region else []
                return [head] + tail
            return self.get_location_context(
                self.place_paths().get(loc.get('地块'), ''))
        loc = self.locate(*c)
        road = next((x for x in self.roads() if x.get('名') == rd['路']), {})
        # ⚠️ `roads()` 给的是**扁平**形状（属性就摊在同一层，那是 `.map` 里的形状），
        # 而 `flatten_world()` 给编辑器的是 `{'属性': {...}}`。这里以前读的是后者，
        # 于是**永远取到空字典**——路的 `地形`/`隔阻`/`凡人脚程` **一个都没进过 L3**，
        # 而数据里 65 条路只有 `自动生成=是`（正好在滤除名单里），所以从没暴露。
        # prompt 里那句"`当前地段` 会告诉他这条路和两端的地段属性"曾经是**一句假话**。
        attrs = {k: v for k, v in road.items()
                 if k not in ('名', '从', '到', '双向', '段', '说明', '自动生成')}
        head = {'层级': f'（在路上）{rd["从"]} → {rd["到"]}',
                '属性': attrs, '在路上': True}
        # 正在穿过哪一段，也要说出口：段存在的理由就是"这一路穿过什么"
        # （`海拔=8000` 的雪岭、溪谷、渡口）——那是 L3 判"能不能过、几时到"的依据，
        # 而它以前连段名都没进过 L3（只有 `road_desc` 那句叙事里有）。
        seg = []
        if rd.get('段'):
            s = next((x for x in (road.get('段') or [])
                      if x.get('名') == rd['段']), {})
            seg = [{'层级': f'（正在穿过）{rd["段"]}',
                    '属性': {k: v for k, v in s.items()
                             if k not in ('名', '说明')}}]
        near = loc.get('地块')
        tail = self.get_location_context(self.place_paths().get(near, '')) if near else []
        return [head] + seg + [{**x, '说明': '路的这一端'} for x in tail]

    # ---- 名字 ↔ 路径：两套词汇之间的唯一桥 ----
    #
    # `.map` 的几何段按**地块名**索引（`坐标 落霞坪 (…)`），而引擎历来用**路径**
    # （`苍云大陆·东域·天道宗·外门弟子进修区·落霞坪`）。两套词汇并存就是漂的温床——
    # 项目栽过同款（`NPC_上限_MAX_MEMORY` 对不上 `NPC_记忆上限`，七项配置从来没生效过）。
    # 所以转换只走这两个函数，别再各处 `split('·')`。

    def place_paths(self) -> dict:
        """地块名 → 完整路径。名称全局唯一（校验器盯着），所以这是双射。"""
        if getattr(self, '_paths', None) is None:
            paths = {}

            def walk(node, prefix):
                for k, v in (self.normalize_node(node)[2] or {}).items():
                    p = f'{prefix}·{k}' if prefix else k
                    paths[k] = p
                    walk(v, p)

            walk(self.get_location_tree() or {}, '')
            self._paths = paths
        return self._paths

    def resolve_place(self, query: str) -> tuple:
        """
        模型给的地名 → **唯一**的地块名。返回 `(地块名, 依据)`；对不上返回 `('', 原因)`。

        为什么需要它：模型不会一字不差地抄 `南疆凡人国·云梦国`，它会写"云梦国"（人话就是
        这么说的）。只认全名 = 玩家说"我要去云梦国"就**原地不动**（实测日志：
        `去向「云梦国」不在已知地点里，位置不动`）。这不是模型的错，是匹配太死。

        容忍的差异**全是结构上的**，不做语义猜测、不维护词表：
          1. 精确（地块名或全路径）
          2. **末段**：`云梦国` → `南疆凡人国·云梦国`
          3. **任一段**：`古藤废墟` → `荒废传送阵·古藤废墟`
          4. 子串：`query in 地块名`（如 `云梦` —— 但会同时命中渔火渡）

        ⚠️ **只有唯一命中才采纳**；多个候选一律拒绝并回报"歧义"（宁可不动，也不猜）。
        ⚠️ 方位表达式（`天道宗上空`、`南面三里`）仍然对不上——那不是能落脚的地块。
        """
        q = (query or '').strip().strip('「」《》""''')
        if not q:
            return '', '空'
        paths = self.place_paths() or {}
        if q in paths:
            return q, '精确'
        # 全路径 → 地块名（`name_of` 查不到会原样返回，所以要比一下）
        by_path = self.name_of(q)
        if by_path != q and by_path in paths:
            return by_path, '全路径'
        q_segs = q.split('·')
        last = q_segs[-1]
        # 末段（模型最常写的形态）
        hit = [n for n in paths if n.split('·')[-1] == last]
        if len(hit) == 1:
            return hit[0], f'末段「{last}」'
        # 任一段
        hit = [n for n in paths if last in n.split('·')]
        if len(hit) == 1:
            return hit[0], f'段「{last}」'
        # 子串
        hit = [n for n in paths if last and last in n]
        if len(hit) == 1:
            return hit[0], f'子串「{last}」'
        if len(hit) > 1:
            return '', f'歧义「{q}」：' + '、'.join(hit[:4])
        return '', f'没有「{q}」这个地块'

    def name_of(self, path_str: str) -> str:
        """
        路径 → 地块名。

        ⚠️ **不能 `rsplit('·')`**：地块名自己就含 `·`（`外门弟子修炼区·落霞坪`），
        切末段只会得到 `落霞坪`。必须走反向索引——这正是本项目早记过的那个坑
        （Phase A 的 `_walk_path` 最长匹配就是为它写的）。
        """
        if not path_str:
            return ''
        rev = getattr(self, '_name_by_path', None)
        if rev is None:
            rev = {p: n for n, p in self.place_paths().items()}
            self._name_by_path = rev
        return rev.get(path_str, path_str)

    def chain_of_name(self, name: str) -> list:
        """**地块名** → 属性链（`get_location_context` 的名字版）。"""
        path = self.place_paths().get(name) or name
        return self.get_location_context(path)

    def get_location_children(self, path: list) -> dict | None:
        """
        从根节点沿 path 逐级下钻，返回当前层级的**子地点**。

        ⚠️ 返回的字典已经过归一化——`_属性` 之类的保留键**不在里面**。
        原实现直接返回原始 dict，升级地点节点后 `_属性` 会作为可选子地点
        出现在终端菜单里。

        :param path: 层级路径，如 ['苍云大陆', '东域']
        :return: 子地点字典，如 {'天道宗': {...}, '星辰阁': {...}}；
                 路径不存在、或落在老写法的字符串叶上，返回 None
        """
        node = self.get_location_tree()
        for step in path:
            _, _, kids = self.normalize_node(node)
            if step not in kids:
                return None
            node = kids[step]
        # 老写法的叶节点是字符串，它没有子地点（保持原行为：返回 None）
        if not isinstance(node, dict):
            return None
        _, _, kids = self.normalize_node(node)
        return kids

    @staticmethod
    def parse_path(path_str: str) -> list:
        """
        解析位置字符串为路径列表。
        "华国·东南沿海市·旧城区·街边炒粉摊，正吃着炒粉"
        → ['华国', '东南沿海市', '旧城区', '街边炒粉摊']

        规则：
        1. 先按 '·' 分割
        2. 清洗每段：去除逗号/句号后的附言、括号内容
        """
        if not path_str:
            return []
        parts = path_str.split('·')
        result = []
        for p in parts:
            # 去掉逗号、中文逗号后的描述附言
            cleaned = re.split(r'[,，。；;]', p)[0].strip()
            # 去掉括号及内容
            cleaned = re.sub(r'[（(][^）)]*[）)]', '', cleaned).strip()
            if cleaned:
                result.append(cleaned)
        return result

    @staticmethod
    def format_path(path_list: list) -> str:
        """
        路径列表转字符串：['华国', '东南沿海市'] → '华国·东南沿海市'
        """
        return '·'.join(path_list)

    # ===================== 交互式位置导航 =====================

    def navigate_interactive(self, current_path_str: str = None) -> str | None:
        """
        终端交互式层级位置选择器。

        流程：
        - 用户通过数字键选择子节点深入
        - [0] 手动输入自定义位置
        - [b] 返回上级
        - [c] 确认当前层级为目的地
        - [x] 取消，返回 None

        :param current_path_str: 当前位置字符串（用于面包屑展示和起始点）
        :return: 格式化路径字符串，如 '华国·东南沿海市·旧城区·城中村'；取消返回 None
        """
        # 起始路径
        if current_path_str:
            current_path = self.parse_path(current_path_str)
        else:
            current_path = []

        seg_len = 80
        # 检查当前路径是否有效（指向一个存在的字典节点或中间节点）
        children = self.get_location_children(current_path)

        while True:
            # ---------- 打印面包屑 ----------
            breadcrumb = '·'.join(current_path) if current_path else '(根)'
            print()
            print('=' * seg_len)
            print(f'📍 {breadcrumb}')
            print('-' * seg_len)

            # ---------- 列出子节点 ----------
            if children is None:
                # 路径在数据中不存在 → 当前是手动输入的自由位置
                print('  (当前为自定义位置，无预设子地点)')
                print()
                print('  [c] ✅ 确认当前位置为目标')
                print('  [0] ✏️  继续细化（输入更具体的位置）')
                print('  [b] 🔙 返回上级')
                print('  [x] ❌ 取消')
                choice = input('> ').strip().lower()
                if choice == 'c':
                    return '·'.join(current_path) if current_path else None
                elif choice == '0':
                    custom = input('  输入更具体的位置名称：\n> ').strip()
                    if not custom:
                        print('输入为空')
                        continue
                    current_path.append(custom.split('·')[0])
                    children = None  # 仍然是自定义位置
                    continue
                elif choice == 'b':
                    if current_path:
                        current_path.pop()
                        children = self.get_location_children(current_path)
                    else:
                        print('已在根节点，无法返回')
                    continue
                elif choice == 'x':
                    return None
                else:
                    print('无效选项')
                    continue

            if children and isinstance(children, dict) and len(children) > 0:
                # 有子节点 → 列出
                keys = list(children.keys())
                for i, key in enumerate(keys, 1):
                    # 叶/枝判定必须走归一化——直接看原始值会被 `{"_属性": …}` 骗到：
                    # 它是个非空 dict，会被当成"有子地点"，然后 `_属性` 就上了菜单
                    _, _desc, _kids = self.normalize_node(children[key])
                    if not _kids:
                        d = _desc[:30] + '...' if len(_desc) > 30 else _desc
                        desc = f'  — {d}' if d else '  — (叶)'
                    else:
                        desc = f'  [{len(_kids)}个子地点]'
                    print(f'  [{i}] {key}{desc}')
            else:
                # children 是空字典或字符串值 → 叶节点
                print('  (叶节点，无预设子地点)')
                print('  可用 [0] 输入更具体的位置')

            print()
            print('  [0] ✏️  手动输入自定义位置')
            if current_path:
                print('  [c] ✅ 确认当前位置为目标')
            print('  [b] 🔙 返回上级')
            print('  [x] ❌ 取消')

            # ---------- 用户选择 ----------
            choice = input('> ').strip().lower()

            if choice == 'x':
                return None
            elif choice == 'b':
                if current_path:
                    current_path.pop()
                    children = self.get_location_children(current_path)
                else:
                    print('已在根节点，无法返回')
                continue
            elif choice == 'c':
                if current_path:
                    return '·'.join(current_path)
                else:
                    print('根节点不能作为目标位置，请选择具体地点')
                    continue
            elif choice == '0':
                # 手动输入 — 允许任意深度
                custom = input('  输入自定义位置名称：\n> ').strip()
                if not custom:
                    print('输入为空，已取消')
                    continue
                # 追加到当前路径
                first_part = custom.split('·')[0]
                new_path = current_path + [first_part]
                target = '·'.join(new_path)
                print(f'  目标位置：{target}')
                confirm = input('  确认？[y/n/c(继续细化)] > ').strip().lower()
                if confirm == 'y':
                    return target
                elif confirm == 'c':
                    current_path = new_path
                    children = None  # 自定义位置，允许继续细化
                # n 或其他 → 留在当前菜单
                continue

            # 数字选择（上述命令都不匹配 → 尝试按数字选子节点）
            if children and isinstance(children, dict):
                keys = list(children.keys())
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(keys):
                        selected = keys[idx]
                        current_path.append(selected)
                        # 叶/枝判定同样走归一化：`_属性` 不是子地点
                        _, _desc, _kids = self.normalize_node(children[selected])
                        if not _kids:
                            target = '·'.join(current_path)
                            desc_str = f'（{_desc[:40]}）' if _desc else ''
                            print(f'  已选择叶节点：{target} {desc_str}')
                            print('  [y] ✅ 确认前往')
                            print('  [c] 📝 继续细化（在此之下创建更具体的位置）')
                            confirm = input('  > ').strip().lower()
                            if confirm == 'y':
                                return target
                            elif confirm == 'c':
                                custom = input('  输入更具体的位置名称：\n  > ').strip()
                                if custom:
                                    current_path.append(custom.split('·')[0])
                                    children = None
                                continue
                            else:
                                current_path.pop()
                            continue
                        else:
                            children = _kids
                            continue
                except ValueError:
                    pass
            print('无效选项，请重新输入')

    # ===================== 位置距离计算 =====================

    def location_depth(self, path_str: str) -> int:
        """
        计算位置在树中的深度（段数）。
        "华国·东南沿海市·旧城区·街边炒粉摊" → 4
        """
        return len(self.parse_path(path_str))

    def location_distance(self, from_path_str: str, to_path_str: str) -> int:
        """
        计算两个位置的最短路径段数。

        算法：找LCA（最近公共祖先），距离 = (a深度 - lca深度) + (b深度 - lca深度)

        例：街边炒粉摊 → 废弃造船厂 = 4段
            from: 华国·东南沿海市·旧城区·街边炒粉摊  (深度4)
            to:   华国·东南沿海市·港口区·废弃造船厂   (深度4)
            LCA:  华国·东南沿海市                      (深度2)
            距离 = (4-2) + (4-2) = 4 段

        :param from_path_str: 出发地完整路径字符串
        :param to_path_str:   目的地完整路径字符串
        :return: 路径段数（≥1）；两地相同返回0
        """
        a = self.parse_path(from_path_str)
        b = self.parse_path(to_path_str)

        if not a or not b:
            return max(len(a), len(b))

        # 找公共前缀长度
        common = 0
        for seg_a, seg_b in zip(a, b):
            if seg_a == seg_b:
                common += 1
            else:
                break

        return (len(a) - common) + (len(b) - common)

    # ===================== 位置NPC查找 =====================

    def find_npcs_at_location(self, path: list, all_npcs) -> 'B3NPCListLayer':
        """
        查找当前位置的所有NPC。
        匹配规则：NPC的"当前所处位置"的路径前缀与传入path匹配。

        例：path=['华国', '东南沿海市', '旧城区']
        - "华国·东南沿海市·旧城区·城中村" → 匹配（前缀一致）
        - "华国·东南沿海市·港口区·废弃造船厂" → 不匹配

        :param path: 玩家当前位置的路径列表
        :param all_npcs: B3NPCListLayer 全部NPC
        :return: B3NPCListLayer 当前位置匹配的NPC
        """
        from Schema.B3_npclist_sch import B3NPCListLayer
        result = B3NPCListLayer()
        if not path or not all_npcs:
            return result

        for npc in all_npcs:
            npc_loc = npc.current_state.get('当前所处位置', '')
            npc_path = self.parse_path(npc_loc)
            # 检查 path 是否为 npc_path 的前缀（允许npc路径更深）
            if len(npc_path) >= len(path) and npc_path[:len(path)] == path:
                result.add(npc)
        return result
