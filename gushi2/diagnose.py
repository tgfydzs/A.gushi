"""诊断脚本 — 验证 N7→N18 数据流正确性.

用法:
    python diagnose.py            # Mock 模式快速验证
    python diagnose.py --real    # 真实 DeepSeek 验证

测试场景:
    玩家输入: "我来到冒险者公会，和艾琳打招呼"

验证:
    1. N7: 位置是否正确切换为"辉石城·冒险者公会"
    2. N7: involved_npc_ids 中不应包含酒馆 NPC
    3. WorldState.player_location 是否同步
    4. N11: 酒馆 NPC（老约翰等）是否标记为"无感知"
    5. N11: 公会 NPC（艾琳）是否感知到了事件
    6. N16: 只有艾琳产生了回应（老约翰不应回应）
"""

from __future__ import annotations

import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.memory_store import MemoryStore
from engine.world_engine import WorldEngine
from engine.llm_manager import MockLLMManager, LLMManager
from engine.loader import initialize_store
from engine.prompt_loader import PromptLoader
from nodes.event_node import EventNode
from nodes.perception_node import PerceptionNode
from nodes.reasoning_node import ReasoningNode
from nodes.response_node import ResponseNode
from nodes.director_node import DirectorNode
from nodes.propagation_node import PropagationNode


def diagnose(use_real: bool = False) -> dict:
    """运行诊断并返回结果."""
    results: dict = {
        "n7_location": "",
        "n7_involved": [],
        "player_location": "",
        "perceptions": {},
        "responses": [],
        "passes": [],
        "failures": [],
    }

    # 1. 加载数据
    store = initialize_store("cangyue", "cangyue_npcs")
    prompt_loader = PromptLoader()

    # 2. LLM 设置
    if use_real:
        llm_mgr = LLMManager.create_from_config()
    else:
        llm_mgr = MockLLMManager()
        # 注册合理的模拟响应
        llm_mgr.register_response(
            "event",
            '{"continue_context":false,'
            '"location":"辉石城·冒险者公会",'
            '"topic":"与艾琳打招呼",'
            '"addressed_npc_id":"guild_receptionist",'
            '"involved_npc_ids":["guild_receptionist"],'
            '"event_type":"dialogue","scope":"private"}',
        )
        llm_mgr.register_response(
            "perception",
            '{"npc_id":"","perceived_event_ids":["current"],'
            '"perceived_summary":"一个冒险者走进公会大厅向你打招呼",'
            '"missed_event_ids":[],"perception_confidence":1.0}',
        )
        llm_mgr.register_response(
            "reasoning",
            '{"npc_id":"","new_knowledge":[],"new_beliefs":[],'
            '"resolved_pending":[],"goals_updated":[],'
            '"reasoning_output":"nothing to reason about","emotion_change":""}',
        )
        llm_mgr.register_response(
            "response",
            '{"npc_id":"","npc_name":"","response_text":"你好！请问有什么事？",'
            '"response_type":"speech","target_id":"player",'
            '"emotion":"neutral","favorability_changes":[]}',
        )
        llm_mgr.register_response(
            "director",
            '{"narrative":"艾琳：\\「你好！请问有什么事？\\」",'
            '"scene_update":"","npc_responses_summary":[],'
            '"world_changes":[],"scene_notes":"","narration_type":"dialogue",'
            '"npc_speeches":[],"narrations":[]}',
        )
        llm_mgr.register_response(
            "propagation",
            '{"propagations":[],'
            '"global_event":{"should_create":false,'
            '"description":"","affected_area":"","reason":""}}',
        )

    # 3. 引擎
    engine = WorldEngine(store)
    engine.enable_debug()
    engine.set_nodes(
        event_node=EventNode(llm_manager=llm_mgr, prompt_loader=prompt_loader),
        perception_node=PerceptionNode(llm_manager=llm_mgr, prompt_loader=prompt_loader),
        reasoning_node=ReasoningNode(llm_manager=llm_mgr, prompt_loader=prompt_loader),
        response_node=ResponseNode(llm_manager=llm_mgr, prompt_loader=prompt_loader),
        director_node=DirectorNode(llm_manager=llm_mgr, prompt_loader=prompt_loader),
        propagation_node=PropagationNode(llm_manager=llm_mgr, prompt_loader=prompt_loader),
    )
    engine.initialize()

    # 4. 执行 Tick
    print("\n" + "=" * 60)
    print("诊断: 玩家输入 → '我来到冒险者公会，和艾琳打招呼'")
    print("=" * 60)

    result = engine.tick("我来到冒险者公会，和艾琳打招呼")
    ctx = engine.context

    # ── 检查 N7 ─────────────────────────────────────────
    ce = ctx.current_event
    results["n7_location"] = ce.location if ce else "N/A"
    results["n7_involved"] = ce.involved_npc_ids if ce else []
    results["player_location"] = (
        store.world_state.player_location
    )

    print(f"\n=== N7 事件层 ===")
    print(f"  location: {results['n7_location']}")
    print(f"  involved_npc_ids: {results['n7_involved']}")
    print(f"  player_location: {results['player_location']}")

    if results["n7_location"] == "辉石城·冒险者公会":
        results["passes"].append("N7 location switched to 冒险者公会")
    else:
        results["failures"].append(
            f"N7 location should be 冒险者公会, got: {results['n7_location']}"
        )

    if "innkeeper" not in results["n7_involved"]:
        results["passes"].append("N7: 酒馆NPC不在involved_npc_ids中")
    else:
        results["failures"].append("N7: 酒馆NPC错误出现在involved_npc_ids中")

    if results["player_location"] == "辉石城·冒险者公会":
        results["passes"].append("WorldState.player_location 正确同步")
    else:
        results["failures"].append(
            f"WorldState.player_location 应为冒险者公会，实际: {results['player_location']}"
        )

    # ── 检查 N11 ─────────────────────────────────────────
    print(f"\n=== N11 感知层 ===")
    if ctx.perception_results is not None:
        for p in ctx.perception_results.npc_perceptions:
            npc = ctx.get_npc(p.npc_id)
            npc_name = npc.name if npc else p.npc_id
            npc_loc = npc.current_state.location if npc else "?"
            has_p = "YES" if p.has_perception() else "NO"
            print(f"  [{npc_name}] @ {npc_loc} → 感知:{has_p} | {p.perceived_summary[:60]}...")
            results["perceptions"][p.npc_id] = {
                "name": npc_name,
                "location": npc_loc,
                "has_perception": p.has_perception(),
                "summary": p.perceived_summary[:80],
            }

            # 酒馆NPC应无感知
            if npc_loc and "酒馆" in npc_loc and p.has_perception():
                results["failures"].append(
                    f"酒馆NPC {npc_name} 错误地感知到了公会事件"
                )
            elif npc_loc and "酒馆" in npc_loc and not p.has_perception():
                results["passes"].append(f"酒馆NPC {npc_name} 正确标记为无感知")

            # 公会NPC应有感知
            if npc_loc and "公会" in npc_loc and p.has_perception():
                results["passes"].append(f"公会NPC {npc_name} 正确感知到事件")
            elif npc_loc and "公会" in npc_loc and not p.has_perception():
                results["failures"].append(
                    f"公会NPC {npc_name} 未能感知到事件"
                )

    # ── 检查 N16 ─────────────────────────────────────────
    print(f"\n=== N16 回应层 ===")
    if ctx.response_results is not None:
        for r in ctx.response_results.npc_responses:
            print(f"  [{r.npc_name}] → {r.response_text[:60]}...")
            results["responses"].append({
                "npc_id": r.npc_id,
                "npc_name": r.npc_name,
                "text": r.response_text[:80],
            })

        # 酒馆NPC不应出现在回应列表中
        responding_ids = {r.npc_id for r in ctx.response_results.npc_responses}
        for npc_id, npc in store.npcs.items():
            loc = npc.current_state.location
            if "酒馆" in (loc or "") and npc_id in responding_ids:
                results["failures"].append(
                    f"酒馆NPC {npc.name} 错误地产生了回应（它不在事件位置）"
                )
            elif "公会" in (loc or "") and npc_id in responding_ids:
                results["passes"].append(f"公会NPC {npc.name} 正确产生了回应")

    # ── 总结 ─────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"诊断结果: {len(results['passes'])} 通过 / {len(results['failures'])} 失败")
    print(f"{'=' * 60}")
    for p in results["passes"]:
        print(f"  [PASS] {p}")
    for f in results["failures"]:
        print(f"  [FAIL] {f}")

    return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--real", action="store_true", help="使用真实 DeepSeek API")
    args = p.parse_args()
    diagnose(use_real=args.real)
