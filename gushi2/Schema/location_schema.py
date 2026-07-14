"""位置 Schema — location_schema
====================================

Location 是世界模拟中的一等实体。
当前流程中，location 只是 NPC 和 Event 上的一个字符串字段。
将其提升为独立实体后，可以支持：
- 位置之间的连接关系（路径、距离）
- 位置属性（室内/室外、公开/私密）
- 位置状态（上锁、破坏、被占领）
- 位置感知规则（不同位置的感知范围不同）
"""

from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# 枚举定义
# =============================================================================


class LocationType(str, Enum):
    """位置类型."""

    CITY = "city"               # 城市
    TOWN = "town"               # 城镇
    VILLAGE = "village"         # 村庄
    BUILDING = "building"       # 建筑
    ROOM = "room"               # 房间
    OUTDOOR = "outdoor"         # 户外（森林、平原等）
    DUNGEON = "dungeon"         # 地下城/洞穴
    ROAD = "road"               # 道路
    WILDERNESS = "wilderness"   # 荒野
    CUSTOM = "custom"           # 自定义


class LocationAccess(str, Enum):
    """位置访问权限."""

    PUBLIC = "public"           # 公开 — 任何人都可以进入
    RESTRICTED = "restricted"   # 受限 — 需要特定条件（身份、钥匙等）
    PRIVATE = "private"         # 私密 — 仅特定NPC/玩家可进入
    HIDDEN = "hidden"           # 隐藏 — 未被发现的位置
    LOCKED = "locked"           # 上锁 — 需要解锁


# =============================================================================
# 位置定义
# =============================================================================


@dataclass
class Location:
    """位置实体.

    世界中的一个具体地点。

    Attributes:
        location_id: 位置唯一标识（如 "inn", "guild_hall", "north_gate"）.
        name: 位置显示名称（如 "辉石城·蜂蜜酒馆"）.
        parent_location_id: 父位置ID（如 "inn" 属于 "cangyue_city"）.
            空字符串表示顶级位置。
        location_type: 位置类型.
        description: 位置描述（自然语言）.
        access: 访问权限.
        notable_features: 显著特征列表（不变的场景元素）.
        connected_locations: 相连位置ID列表（可以从此处到达的位置）.
        npcs_present: 当前在场的 NPC ID 列表.
    """

    location_id: str = ""
    name: str = ""
    parent_location_id: str = ""            # AI可修改: 否 | 程序可修改: 是(初始化)
    location_type: LocationType = LocationType.CUSTOM
    description: str = ""                   # AI可修改: 否 | 程序可修改: 是(初始化)
    access: LocationAccess = LocationAccess.PUBLIC  # AI可修改: 否 | 程序可修改: 是
    notable_features: list[str] = field(default_factory=list)  # AI可修改: 是 | 程序可修改: 是
    connected_locations: list[str] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是(初始化)
    npcs_present: list[str] = field(default_factory=list)  # AI可修改: 否 | 程序可修改: 是
    # 说明: Location 的静态属性在初始化时设定。
    # npcs_present 由程序在NPC移动时自动维护。
    # notable_features 可由导演层LLM在首次访问时生成，后续保持不变。

    def is_public(self) -> bool:
        """判断是否为公开位置."""
        return self.access == LocationAccess.PUBLIC

    def is_accessible_by(self, npc_id: str) -> bool:
        """判断指定NPC是否可以进入此位置（当前简化为所有人可进入）."""
        # 后续可基于NPC的faction/key_items等扩展
        if self.access == LocationAccess.PUBLIC:
            return True
        return False

    def get_path(self) -> str:
        """获取完整路径名（父位置/子位置）."""
        if self.parent_location_id:
            return f"{self.parent_location_id}/{self.name}"
        return self.name


# =============================================================================
# 位置表（所有位置的集合）
# =============================================================================


@dataclass
class LocationTable:
    """位置表.

    管理世界中所有位置及其关系。

    Attributes:
        locations: location_id → Location 的映射.
        root_locations: 顶级位置ID列表（没有父位置的位置）.
    """

    locations: dict[str, Location] = field(default_factory=dict)
    root_locations: list[str] = field(default_factory=list)

    def add(self, location: Location) -> None:
        """添加位置."""
        self.locations[location.location_id] = location
        if not location.parent_location_id:
            self.root_locations.append(location.location_id)

    def get(self, location_id: str) -> Location | None:
        """获取位置."""
        return self.locations.get(location_id)

    def get_npcs_at(self, location_id: str) -> list[str]:
        """获取指定位置的所有NPC."""
        loc = self.locations.get(location_id)
        return loc.npcs_present if loc else []

    def move_npc(self, npc_id: str, from_location_id: str, to_location_id: str) -> bool:
        """移动NPC从一个位置到另一个位置.

        Args:
            npc_id: NPC ID.
            from_location_id: 原位置ID.
            to_location_id: 目标位置ID.

        Returns:
            是否移动成功.
        """
        from_loc = self.locations.get(from_location_id)
        to_loc = self.locations.get(to_location_id)
        if not from_loc or not to_loc:
            return False
        if npc_id in from_loc.npcs_present:
            from_loc.npcs_present.remove(npc_id)
        if npc_id not in to_loc.npcs_present:
            to_loc.npcs_present.append(npc_id)
        return True

    def get_connected(self, location_id: str) -> list[Location]:
        """获取与指定位置相连的所有位置."""
        loc = self.locations.get(location_id)
        if not loc:
            return []
        return [
            self.locations[cid]
            for cid in loc.connected_locations
            if cid in self.locations
        ]

    def get_same_location_npcs(self, location_id: str) -> list[str]:
        """获取与指定位置同位置的所有NPC.

        包括：该位置自身的NPC + 所有子位置的NPC。

        Args:
            location_id: 位置ID.

        Returns:
            同位置的NPC ID列表.
        """
        loc = self.locations.get(location_id)
        if not loc:
            return []
        npcs: list[str] = list(loc.npcs_present)
        # 也包含子位置的NPC
        for child_loc in self.locations.values():
            if child_loc.parent_location_id == location_id:
                npcs.extend(child_loc.npcs_present)
        return npcs


# =============================================================================
# 位置状态（运行时可变）
# =============================================================================


@dataclass
class LocationState:
    """位置运行时状态.

    与 Location（静态定义）不同，LocationState 记录位置的动态变化。

    Attributes:
        location_id: 位置ID.
        is_destroyed: 是否已被破坏.
        is_on_fire: 是否着火 — 预留扩展.
        is_flooded: 是否被淹 — 预留扩展.
        controlling_faction: 控制此位置的势力.
        last_visited_tick: 最后一次有玩家/NPC活动的tick.
    """

    location_id: str = ""
    is_destroyed: bool = False                  # AI可修改: 否 | 程序可修改: 是
    is_on_fire: bool = False                    # AI可修改: 否 | 程序可修改: 是
    is_flooded: bool = False                    # AI可修改: 否 | 程序可修改: 是
    controlling_faction: str = ""               # AI可修改: 否 | 程序可修改: 是
    last_visited_tick: int = 0                  # AI可修改: 否 | 程序可修改: 是
