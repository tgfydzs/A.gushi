"""Persistence — 运行状态 <-> 文件序列化.

职责：将 WorldState/NPC状态/CurrentEvent/DialogueHistory/SceneMemory
序列化到 JSON 文件，以及从文件恢复。

不参与 Tick。不调用 LLM。不修改 Entity。不绕过 UpdateEngine。
只是状态序列化工具。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.memory_store import MemoryStore

from Schema.world_schema import WorldState, TimeOfDay, Weather
from Schema.npc_schema import NPC, Emotion
from Schema.event_schema import CurrentEvent
from Schema.dialogue_schema import DialogueHistory, DialogueHistoryEntry
from Schema.director_schema import SceneMemory
from Schema.relationship_schema import RelationshipValue


# 默认存档目录
_SAVE_DIR = Path(__file__).resolve().parent.parent / "saves"


def _ensure_save_dir() -> Path:
    _SAVE_DIR.mkdir(parents=True, exist_ok=True)
    return _SAVE_DIR


# =============================================================================
# 序列化辅助
# =============================================================================


def _npc_to_dict(npc: NPC) -> dict:
    """NPC → 可序列化的 dict（只保留关键状态）."""
    return {
        "npc_id": npc.npc_id,
        "name": npc.name,
        "role": npc.role.value,
        "location": npc.current_state.location,
        "emotion": npc.current_state.emotion.value,
        "action": npc.current_state.action,
        "health_value": npc.current_state.health_value,
        "is_alive": npc.current_state.is_alive,
        "is_conscious": npc.current_state.is_conscious,
        "personality": {
            "openness": npc.personality.openness,
            "conscientiousness": npc.personality.conscientiousness,
            "extraversion": npc.personality.extraversion,
            "agreeableness": npc.personality.agreeableness,
            "neuroticism": npc.personality.neuroticism,
            "intelligence": npc.personality.intelligence,
            "traits": npc.personality.traits,
        },
        "knowledge_count": len(npc.knowledge),
        "memory_count": len(npc.memory),
        "observation_count": len(npc.observation),
        "belief_count": len(npc.belief),
        "pending_count": len(npc.pending_reason),
        "goal_count": len(
            [g for g in npc.goal if not g.is_completed]
        ),
        "relationship_count": len(npc.relationship),
    }


def _npc_from_dict(data: dict, original: NPC) -> NPC:
    """从 dict 恢复 NPC 关键状态（保留不可序列化的维护表）."""
    original.current_state.location = data.get("location", "")
    original.current_state.emotion = Emotion(
        data.get("emotion", "neutral")
    )
    original.current_state.action = data.get("action", "")
    original.current_state.health_value = data.get("health_value", 100)
    original.current_state.is_alive = data.get("is_alive", True)
    original.current_state.is_conscious = data.get("is_conscious", True)
    return original


# =============================================================================
# 公共 API
# =============================================================================


def save(
    store: MemoryStore,
    scene_memory: dict[str, SceneMemory] | None = None,
    save_name: str = "",
) -> str:
    """保存当前状态到文件.

    Args:
        store: MemoryStore 实例.
        scene_memory: 场景记忆字典.
        save_name: 存档名称。为空则自动生成。

    Returns:
        存档文件路径.
    """
    if not save_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_name = f"save_{timestamp}"

    save_data = {
        "save_name": save_name,
        "saved_at": datetime.now().isoformat(),
        "world_state": {
            "day": store.world_state.day,
            "time_of_day": store.world_state.time_of_day.value,
            "weather": store.world_state.weather.value,
            "player_location": store.world_state.player_location,
        },
        "npcs": [
            _npc_to_dict(npc) for npc in store.npcs.values()
        ],
        "dialogue_history_count": len(
            store.dialogue_history.entries
        ),
        "scene_memory": (
            {
                loc: {"scene_description": mem.scene_description}
                for loc, mem in (scene_memory or {}).items()
            }
        ),
    }

    if store.current_event and store.current_event.is_active():
        save_data["current_event"] = {
            "location": store.current_event.location,
            "topic": store.current_event.topic,
            "addressed_npc_id": store.current_event.addressed_npc_id,
            "involved_npc_ids": store.current_event.involved_npc_ids,
        }

    save_dir = _ensure_save_dir()
    filepath = save_dir / f"{save_name}.json"

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    return str(filepath)


def load(
    store: MemoryStore,
    save_name: str,
) -> bool:
    """从文件恢复状态.

    只恢复关键状态字段。NPC 的维护表（knowledge/memory等）保持不变。

    Args:
        store: MemoryStore 实例（需要已经加载了 NPC）.
        save_name: 存档名称.

    Returns:
        是否加载成功.
    """
    filepath = _ensure_save_dir() / f"{save_name}.json"
    if not filepath.exists():
        return False

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 世界状态
    ws_data = data.get("world_state", {})
    store.world_state.day = ws_data.get("day", 1)
    store.world_state.time_of_day = TimeOfDay(
        ws_data.get("time_of_day", "morning")
    )
    store.world_state.weather = Weather(
        ws_data.get("weather", "sunny")
    )
    store.world_state.player_location = ws_data.get("player_location", "")

    # NPC 状态
    npcs_data = data.get("npcs", [])
    for npc_data in npcs_data:
        npc_id = npc_data.get("npc_id", "")
        npc = store.get_npc(npc_id)
        if npc is not None:
            _npc_from_dict(npc_data, npc)

    # CurrentEvent
    ce_data = data.get("current_event", {})
    if ce_data:
        store.current_event = CurrentEvent(
            location=ce_data.get("location", ""),
            topic=ce_data.get("topic", ""),
            addressed_npc_id=ce_data.get("addressed_npc_id", ""),
            involved_npc_ids=ce_data.get("involved_npc_ids", []),
        )

    return True


def list_saves() -> list[str]:
    """列出所有存档名称."""
    save_dir = _ensure_save_dir()
    if not save_dir.exists():
        return []
    return sorted(
        f.stem for f in save_dir.glob("*.json")
    )
