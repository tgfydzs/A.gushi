"""WorldLoader — 世界数据加载器.

职责：从 JSON 文件加载世界和 NPC 数据，
转换为 Schema dataclass 并填充到 MemoryStore。

不修改 Schema。不绕过 UpdateEngine。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from Schema.world_schema import (
    WorldDefinition, WorldRule, HiddenEvent, KnownEvent,
    WorldState, TimeOfDay, Weather,
)
from Schema.npc_schema import (
    NPC, NPCIdentity, NPCRole, PersonalityData, CurrentStateData,
    Emotion, HealthStatus,
)
from Schema.memory_schema import KnowledgeEntry, MemoryEntry
from Schema.belief_schema import BeliefEntry
from Schema.relationship_schema import (
    RelationshipValue, RelationshipType,
)
from Schema.action_schema import GoalEntry, Item
from engine.memory_store import MemoryStore

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"


# =============================================================================
# 公共 API
# =============================================================================


def load_world(world_name: str) -> WorldDefinition:
    """加载世界定义.

    Args:
        world_name: 世界名称（对应 data/worlds/{name}.json）.

    Returns:
        WorldDefinition 对象.
    """
    filepath = _DATA_DIR / "worlds" / f"{world_name}.json"
    if not filepath.exists():
        raise FileNotFoundError(f"世界定义文件不存在: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    wd = data.get("world_definition", data)

    rules = [
        WorldRule(
            rule_id=r.get("rule_id", ""),
            description=r.get("description", ""),
            applies_to=r.get("applies_to", "all"),
            penalty=r.get("penalty", ""),
        )
        for r in wd.get("rules", [])
    ]

    hidden_events = [
        HiddenEvent(
            event_id=e.get("event_id", ""),
            trigger_condition=e.get("trigger_condition", ""),
            event_content=e.get("event_content", ""),
        )
        for e in wd.get("hidden_events", [])
    ]

    known_events: list[KnownEvent] = []
    for i, e in enumerate(wd.get("known_events", [])):
        if isinstance(e, str):
            known_events.append(KnownEvent(
                event_id=f"known_{i}",
                description=e,
            ))
        elif isinstance(e, dict):
            known_events.append(KnownEvent(
                event_id=e.get("event_id", f"known_{i}"),
                description=e.get("description", ""),
            ))

    return WorldDefinition(
        world_id=wd.get("world_id", ""),
        name=wd.get("name", ""),
        description=wd.get("description", ""),
        rules=rules,
        hidden_events=hidden_events,
        known_events=known_events,
    )


def load_npcs(npc_file: str) -> list[NPC]:
    """加载 NPC 列表.

    Args:
        npc_file: NPC 文件名（对应 data/npcs/{file}.json）.

    Returns:
        NPC 对象列表.
    """
    filepath = _DATA_DIR / "npcs" / f"{npc_file}.json"
    if not filepath.exists():
        raise FileNotFoundError(f"NPC 数据文件不存在: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    npcs: list[NPC] = []
    for raw in data.get("npcs", []):
        # 性格
        pers = raw.get("personality", {})
        personality = PersonalityData(
            openness=pers.get("openness", 50),
            conscientiousness=pers.get("conscientiousness", 50),
            extraversion=pers.get("extraversion", 50),
            agreeableness=pers.get("agreeableness", 50),
            neuroticism=pers.get("neuroticism", 50),
            intelligence=pers.get("intelligence", 50),
            traits=pers.get("traits", []),
        )

        # 当前状态
        state = raw.get("current_state", {})
        current_state = CurrentStateData(
            location=state.get("location", ""),
            emotion=_parse_emotion(state.get("emotion", "neutral")),
            action=state.get("action", ""),
            health_value=state.get("health", 100),
            is_alive=state.get("is_alive", True),
        )

        # 知识
        knowledge = [
            KnowledgeEntry(
                fact=k["fact"],
                source=k.get("source", "unknown"),
                confidence=k.get("confidence", 1.0),
            )
            for k in raw.get("knowledge", [])
        ]

        # 信念
        belief = [
            BeliefEntry(
                belief=b["belief"],
                basis=b.get("basis", ""),
                strength=b.get("strength", 0.5),
            )
            for b in raw.get("belief", [])
        ]

        # 记忆
        memory = [
            MemoryEntry(
                content=m["content"],
                importance=m.get("importance", 1),
            )
            for m in raw.get("memory", [])
        ]

        # 目标
        goals = [
            GoalEntry(
                goal=g["goal"],
                priority=g.get("priority", 5),
            )
            for g in raw.get("goal", [])
        ]

        # 关系
        relationship: dict[str, RelationshipValue] = {}
        for tid, r in raw.get("relationship", {}).items():
            relationship[tid] = RelationshipValue(
                target_npc_id=tid,
                relationship_type=_parse_rel_type(
                    r.get("relationship_type", "stranger")
                ),
                favorability=r.get("favorability", 0),
                trust=r.get("trust", 50),
                history=r.get("history", []),
            )

        # 物品
        inventory = [
            Item(
                item_id=i.get("item_id", ""),
                name=i.get("name", ""),
                description=i.get("description", ""),
                quantity=i.get("quantity", 1),
            )
            for i in raw.get("inventory", [])
        ]

        npc = NPC(
            identity=NPCIdentity(
                npc_id=raw["npc_id"],
                name=raw["name"],
                role=_parse_role(raw.get("role", "commoner")),
            ),
            personality=personality,
            current_state=current_state,
            knowledge=knowledge,
            belief=belief,
            memory=memory,
            goal=goals,
            relationship=relationship,
            inventory=inventory,
        )
        npcs.append(npc)

    return npcs


def initialize_store(
    world_name: str = "cangyue",
    npc_file: str = "cangyue_npcs",
) -> MemoryStore:
    """一次性加载世界和 NPC 数据到 MemoryStore.

    Args:
        world_name: 世界名称.
        npc_file: NPC 文件名.

    Returns:
        填充好数据的 MemoryStore.
    """
    world_def = load_world(world_name)
    npcs = load_npcs(npc_file)

    store = MemoryStore(
        world_definition=world_def,
        world_state=WorldState(day=1, player_location=""),
    )

    for npc in npcs:
        store.add_npc(npc)

    # 设置初始玩家位置（取第一个 NPC 的位置）
    if npcs:
        store.world_state.player_location = npcs[0].current_state.location

    return store


# =============================================================================
# 枚举解析辅助
# =============================================================================

def _parse_role(value: str) -> NPCRole:
    try:
        return NPCRole(value)
    except ValueError:
        return NPCRole.COMMONER


def _parse_emotion(value: str) -> Emotion:
    try:
        return Emotion(value)
    except ValueError:
        return Emotion.NEUTRAL


def _parse_rel_type(value: str) -> RelationshipType:
    try:
        return RelationshipType(value)
    except ValueError:
        return RelationshipType.STRANGER
