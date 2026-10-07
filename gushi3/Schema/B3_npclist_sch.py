from common.log import get_logger
from Schema.B3_1_npc_sch import B3NPCLayer

logger = get_logger(__name__)


class B3NPCListLayer:
    """
    base3 NPC列表层
    职能：统一封装NPC实体列表，对上层屏蔽单个NPC对象的直接操作
    内部管理多个 B3NPCLayer 实例，提供增删和序列化能力。

    方法：
        all(npc):  添加一个NPC实体到列表中
        delete(npc):  从列表中移除一个NPC实体（注：Python中 del 为保留关键字，故命名为 delete）
    """

    def __init__(self):
        """初始化空的NPC列表"""
        self.npc_list: list[B3NPCLayer] = []

    def add(self, npc: B3NPCLayer) -> None:
        """
        添加一个NPC实体到列表中
        :param npc: B3NPCLayer 实例
        """
        if npc is None:
            logger.warn(f'添加npc实例进入列表错误，传参实际为空')
            return
        elif not isinstance(npc, B3NPCLayer):
            logger.warn(f"增加npc错误，期望 B3NPCLayer 实例，实际收到 {type(npc)}")
            return
        self.npc_list.append(npc)
        logger.debug(f"NPC [{npc.name}] 已添加到列表，当前列表长度: {len(self.npc_list)}")

    def delete(self, npc: B3NPCLayer) -> None:
        """
        从列表中移除一个NPC实体
        注：因 Python 中 del 为保留关键字，方法名使用 delete

        按名字匹配移除：读档/撤回后 event_npc 与 all_npc_list 会被分别重建为
        独立实例，此时按对象身份 list.remove() 会抛 ValueError。改为按名字
        定位移除，兼容跨列表引用。
        :param npc: 要移除的 B3NPCLayer 实例
        """
        if npc is None:
            logger.warn(f'删除npc实例在列表错误，传参实际为空')
            return
        elif not isinstance(npc, B3NPCLayer):
            logger.warn(f"删除npc错误，期望 B3NPCLayer 实例，实际收到 {type(npc)}")
            return
        for i, n in enumerate(self.npc_list):
            if n.name == npc.name:
                del self.npc_list[i]
                logger.debug(f"NPC [{npc.name}] 已从列表移除，当前列表长度: {len(self.npc_list)}")
                return
        logger.warn(f"删除npc [{npc.name}] 失败：列表中不存在同名NPC，当前列表长度: {len(self.npc_list)}")

    @staticmethod
    def _strip_brackets(s: str) -> str:
        """取左括号前的内容："血手（殷九娘）" → "血手"；"韩铁柱" → "韩铁柱" """
        idx = s.find('（')
        if idx == -1:
            idx = s.find('(')
        return s[:idx].strip() if idx != -1 else s.strip()

    def get_npc(self, name: str) -> B3NPCLayer:
        """
        通过 name 查找NPC实例。

        匹配规则（统一）：
        - 如果 name 包含 "（"，取左括号前的内容做 in 匹配
        - 如果 name 不含括号，直接用原内容做 in 匹配

        :param name: NPC名称（完整名或部分名均可）
        :return: 匹配的 B3NPCLayer 实例，未找到返回 None
        """
        key = self._strip_brackets(name)
        for n in self.npc_list:
            if key in n.name:
                return n

        logger.warn(f'获取npc实例失败，索引值：{name}')
        return None

    def to_dict(self) -> list:
        """将NPC列表序列化为列表"""
        return [npc.to_dict() for npc in self.npc_list]

    def new_list(self) -> None:
        """
        清空npc列表
        :return: None，实际为 list[B3NPCLayer] 的空列表
        """
        self.npc_list: list[B3NPCLayer] = []




    def __len__(self) -> int:
        """返回NPC数量"""
        return len(self.npc_list)

    def __iter__(self):
        """迭代NPC列表"""
        return iter(self.npc_list)

    def __getitem__(self, index: int) -> B3NPCLayer:
        """按索引获取NPC"""
        return self.npc_list[index]
