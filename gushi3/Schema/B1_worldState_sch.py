import re

from common.log import get_logger

logger = get_logger(__name__)


class B1WorldLayer:
    """
    base1 世界层
    职能：封装世界规则、状态
    world_dict: dict | 传入世界规则字典，内部init通过对应key提取
    """

    def __init__(self, world_dict: dict):
        self.dict = world_dict

    # ===================== 位置查询与路径工具 =====================

    def get_location_tree(self) -> dict:
        """
        返回完整的世界地点嵌套字典。
        """
        return self.dict.get('世界地点列表', {})

    def get_location_children(self, path: list) -> dict | None:
        """
        从根节点沿 path 逐级下钻，返回当前层级的所有子节点。
        :param path: 层级路径，如 ['华国', '东南沿海市']
        :return: 子节点字典，如 {'旧城区': {...}, '新城区': {...}}；路径不存在返回 None
        """
        node = self.get_location_tree()
        for step in path:
            if not isinstance(node, dict) or step not in node:
                return None
            node = node[step]
        if isinstance(node, dict):
            return node
        return None

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
                # 路径在数据中不存在 → 当前是手动输入的自由位置，只能确认或返回
                print('  (当前为自定义位置，无预设子地点)')
                print()
                print('  [c] ✅ 确认当前位置为目标')
                print('  [b] 🔙 返回上级')
                print('  [x] ❌ 取消')
                choice = input('> ').strip().lower()
                if choice == 'c':
                    return '·'.join(current_path) if current_path else None
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
                    # 判断是否为叶节点（值不是字典或空字典）
                    is_leaf = not isinstance(children[key], dict) or len(children[key]) == 0
                    desc = ''
                    if is_leaf and isinstance(children[key], str):
                        desc = children[key]
                        if len(desc) > 30:
                            desc = desc[:30] + '...'
                        desc = f'  — {desc}'
                    elif is_leaf:
                        desc = '  — (叶)'
                    else:
                        subs = len(children[key]) if isinstance(children[key], dict) else 0
                        desc = f'  [{subs}个子地点]'
                    print(f'  [{i}] {key}{desc}')
            else:
                # children 是空字典或字符串值 → 叶节点
                print('  (叶节点，无子地点)')

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
                # 手动输入
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
                    children = None  # 自定义位置无预设子节点
                # n 或其他 → 留在当前菜单
                continue
            else:
                # 数字选择
                if children and isinstance(children, dict):
                    keys = list(children.keys())
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(keys):
                            selected = keys[idx]
                            current_path.append(selected)
                            new_children = children[selected]
                            # 叶节点（字符串值或空dict）→ 自动确认
                            if not isinstance(new_children, dict) or len(new_children) == 0:
                                target = '·'.join(current_path)
                                desc_str = ''
                                if isinstance(new_children, str) and new_children:
                                    desc_str = f'（{new_children[:40]}）'
                                print(f'  已选择叶节点：{target} {desc_str}')
                                confirm = input('  确认前往？[y/n] > ').strip().lower()
                                if confirm == 'y':
                                    return target
                                else:
                                    current_path.pop()
                                continue
                            else:
                                children = new_children
                                continue
                    except ValueError:
                        pass
                print('无效选项，请重新输入')

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
