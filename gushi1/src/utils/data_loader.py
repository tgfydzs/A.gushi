"""数据加载器 — 从 JSON 文件加载世界、NPC 等数据."""

import json
import os
from pathlib import Path
from typing import Any

from src.models.npc import (
    NPC,
    NPCRole,
    PersonalityData,
    CurrentStateData,
    Emotion,
    KnowledgeEntry,
    BeliefEntry,
    MemoryEntry,
    ObservationEntry,
    PendingEntry,
    RelationshipValue,
    RelationshipType,
    Item,
    GoalEntry,
)


# 项目根目录（E:\A.gushi）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def load_json(filename: str) -> dict[str, Any]:
    """加载JSON文件.

    Args:
        filename: 相对于项目根目录的文件路径.

    Returns:
        解析后的dict.
    """
    filepath = PROJECT_ROOT / filename
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def load_world(world_name: str) -> dict[str, Any]:
    """加载世界定义.

    Args:
        world_name: 世界名称（对应 data/worlds/{name}.json）.

    Returns:
        世界定义dict.
    """
    return load_json(f"data/worlds/{world_name}.json")


def load_npcs(npc_file: str) -> list[NPC]:
    """从JSON加载NPC列表.

    Args:
        npc_file: NPC文件名（对应 data/npcs/{npc_file}.json）.

    Returns:
        NPC对象列表.
    """
    data = load_json(f"data/npcs/{npc_file}.json")
    npcs: list[NPC] = []

    for raw in data.get("npcs", []):
        npc = NPC(
            npc_id=raw["npc_id"],
            name=raw["name"],
            role=NPCRole(raw.get("role", "commoner")),
            personality=_load_personality(raw.get("personality", {})),
            current_state=_load_current_state(raw.get("current_state", {})),
            knowledge=[
                KnowledgeEntry(
                    fact=k["fact"],
                    source=k.get("source", "unknown"),
                    confidence=k.get("confidence", 1.0),
                    timestamp=k.get("timestamp", ""),
                )
                for k in raw.get("knowledge", [])
            ],
            belief=[
                BeliefEntry(
                    belief=b["belief"],
                    basis=b.get("basis", ""),
                    strength=b.get("strength", 0.5),
                    timestamp=b.get("timestamp", ""),
                )
                for b in raw.get("belief", [])
            ],
            memory=[
                MemoryEntry(
                    content=m["content"],
                    importance=m.get("importance", 1),
                    related_event_id=m.get("related_event_id"),
                    timestamp=m.get("timestamp", ""),
                )
                for m in raw.get("memory", [])
            ],
            goal=[
                GoalEntry(
                    goal=g["goal"],
                    priority=g.get("priority", 5),
                    progress=g.get("progress", ""),
                    is_completed=g.get("is_completed", False),
                    timestamp=g.get("timestamp", ""),
                )
                for g in raw.get("goal", [])
            ],
            relationship={
                tid: RelationshipValue(
                    target_npc_id=tid,
                    relationship_type=RelationshipType(
                        r.get("relationship_type", "stranger")
                    ),
                    favorability=r.get("favorability", 0),
                    trust=r.get("trust", 50),
                    history=r.get("history", []),
                )
                for tid, r in raw.get("relationship", {}).items()
            },
            inventory=[
                Item(
                    item_id=i["item_id"],
                    name=i["name"],
                    description=i.get("description", ""),
                    quantity=i.get("quantity", 1),
                    is_equipped=i.get("is_equipped", False),
                )
                for i in raw.get("inventory", [])
            ],
        )
        npcs.append(npc)

    return npcs


def load_prompt(name: str) -> str:
    """加载Prompt模板.

    Args:
        name: Prompt名称（对应 prompts/{name}.md）.

    Returns:
        Prompt内容字符串.
    """
    filepath = PROJECT_ROOT / "prompts" / f"{name}.md"
    if not filepath.exists():
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def render_prompt(name: str, **kwargs: str) -> str:
    """加载并渲染Prompt模板.

    使用 {变量名} 占位符，与 Python format() 兼容。
    模板中的 JSON 示例不需要转义——只替换指定的变量。

    Args:
        name: Prompt名称（对应 prompts/{name}.md）.
        **kwargs: 模板变量.

    Returns:
        渲染后的Prompt字符串.
    """
    template = load_prompt(name)
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _load_personality(data: dict) -> PersonalityData:
    """加载性格数据."""
    return PersonalityData(
        openness=data.get("openness", 50),
        conscientiousness=data.get("conscientiousness", 50),
        extraversion=data.get("extraversion", 50),
        agreeableness=data.get("agreeableness", 50),
        neuroticism=data.get("neuroticism", 50),
        intelligence=data.get("intelligence", 50),
        traits=data.get("traits", []),
    )


def _load_current_state(data: dict) -> CurrentStateData:
    """加载当前状态."""
    return CurrentStateData(
        location=data.get("location", ""),
        emotion=Emotion(data.get("emotion", "neutral")),
        action=data.get("action", ""),
        health=data.get("health", 100),
        is_alive=data.get("is_alive", True),
    )
