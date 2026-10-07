import re

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
