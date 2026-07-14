"""多场景连续对话调试 — 不用断点，直接运行收集数据."""

import json
import sys
import os
from datetime import datetime

sys.path.insert(0, '.')

from main import create_engine


def run_scenario(name: str, conversations: list[str], extra_info: str = ""):
    """运行一个场景的多轮对话，收集所有tick的debug日志.

    Returns:
        每轮的 {tick, input, narrative, success, error}
    """
    print(f"\n{'#'*60}")
    print(f"#  场景: {name}")
    if extra_info:
        print(f"#  {extra_info}")
    print(f"{'#'*60}")

    engine = create_engine("cangyue", "cangyue_npcs")
    results = []

    for i, user_input in enumerate(conversations):
        print(f"\n--- 第{i+1}轮 ---")
        print(f"🎮 你: {user_input}")

        result = engine.tick(user_input)
        record = {
            "tick": i + 1,
            "input": user_input,
            "narrative": "",
            "success": result.success,
            "error": "",
        }

        if result.success:
            narrative = result.get_narrative()
            record["narrative"] = narrative
            print(f"📖 {narrative}")

            # 收集每层的详细数据
            record["event"] = {
                "events": [
                    {"id": e.event_id, "desc": e.description,
                     "location": e.location, "scope": str(e.scope),
                     "involved": e.involved_npc_ids}
                    for e in (result.event_result.events if result.event_result else [])
                ]
            }
            record["perception"] = [
                {"npc": p.npc_id, "perceived": p.perceived_event_ids,
                 "missed": p.missed_event_ids, "summary": p.perceived_summary[:80]}
                for p in (result.perception_result.npc_perceptions if result.perception_result else [])
            ]
            record["responses"] = [
                {"npc": r.npc_id, "name": r.npc_name, "text": r.response_text[:80]}
                for r in (result.response_result.npc_responses if result.response_result else [])
            ]

            # 收集NPC状态快照
            record["npc_snapshots"] = {}
            for npc_id, npc in engine.npcs.items():
                record["npc_snapshots"][npc_id] = {
                    "name": npc.name,
                    "location": npc.current_state.location,
                    "emotion": npc.current_state.emotion.value,
                    "obs_count": len(npc.observation),
                    "knowledge_count": len(npc.knowledge),
                    "belief_count": len(npc.belief),
                    "pending_count": len(npc.pending_reason),
                }

        else:
            record["error"] = result.get_error_context()
            print(f"❌ {record['error']}")

        record["engine_state"] = {
            "tick_count": engine.tick_count,
            "player_location": engine.world_state.player_location if engine.world_state else "",
            "dialogue_history_len": len(engine.dialogue_history),
            "current_event": {
                "location": engine.event_layer.current_event.location,
                "topic": engine.event_layer.current_event.topic,
                "addressed_npc_id": engine.event_layer.current_event.addressed_npc_id,
                "involved_npc_ids": engine.event_layer.current_event.involved_npc_ids,
            } if engine.event_layer.current_event else None,
        }

        results.append(record)

    return results


# ============================================================
# 场景设计
# ============================================================

all_scenarios = {}

# ---- 场景A: 长篇对话——酒馆聊天 (7轮) ----
all_scenarios["A-酒馆长篇对话"] = [
    "你好，请问这里是哪里？",
    "我叫小明，是一名四处游历的剑客。",
    "老约翰，你在这里开店多久了？",
    "最近辉石城有什么新鲜事吗？",
    "商路袭击的事情，能详细说说吗？",
    "那个古代遗迹，你知道具体位置吗？",
    "谢谢你，我打算去冒险者公会看看。",
]

# ---- 场景B: 跨场景对话——从酒馆到公会 (7轮) ----
all_scenarios["B-酒馆到公会"] = [
    "你好，老板，有客房吗？",
    "我叫小明，我需要一个落脚的地方。",
    "好的，先给我来一杯麦酒。冒险者公会怎么走？",
    "我去冒险者公会。",  # 切换场景
    "你好，我想看看有什么任务可以接。",
    "那个在森林里发现发光花朵的任务，能详细介绍一下吗？",
    "好，我接下这个任务。需要准备什么吗？",
]

# ---- 场景C: 多NPC交互——酒馆里和兜帽人搭话 (7轮) ----
all_scenarios["C-与沉默旅人搭话"] = [
    "你好老板，来一杯酒。",
    "谢谢。角落里那个独自喝酒的是谁？",
    "老板，我想认识一下那个兜帽人，能帮我引荐吗？",
    "你好，朋友，一个人喝酒不无聊吗？",  # 尝试与兜帽人对话
    "我是一名剑客，看你的样子，也像是在外面跑的人。",
    "说说看，也许我能帮上忙。你是不是在找什么东西？",
    "古代遗迹？有点意思……我正好缺个向导。",
]

# ---- 场景D: 与守卫互动 (7轮) ----
all_scenarios["D-城门守卫"] = [
    "你好守卫大哥，这里就是辉石城吗？",
    "我是外地来的冒险者，想进城。",
    "城里有什么规矩我需要知道的？",
    "听说最近有可疑的人在城墙附近徘徊？",
    "我是冒险者，如果遇到可疑的人我可以帮忙。",
    "你和酒馆的老约翰很熟吗？他刚提到过你。",
    "好，我先去酒馆落脚，晚点再来找你了解巡逻的事。",
]

# ---- 场景E: 跑腿任务——酒馆→公会→再回酒馆 (7轮) ----
all_scenarios["E-跑腿任务循环"] = [
    "你好老约翰，今天有什么需要帮忙的吗？",
    "送信？可以，我正好要去公会。信件给谁？",
    "好，我现在就去冒险者公会。",
    "艾琳你好，老约翰让我送一封信给你，还说你这里有任务？",
    "我对城外强盗的任务感兴趣，赏金500金币是真的吗？",
    "我先回酒馆跟老约翰说一声。",
    "老约翰，信送到了，艾琳还给了我一个任务信息。城外强盗的赏金任务。",
]

# ============================================================
# 运行所有场景
# ============================================================

all_results = {}

for scenario_name, conversations in all_scenarios.items():
    print(f"\n\n{'*'*60}")
    print(f"开始运行场景: {scenario_name}")
    print(f"{'*'*60}")

    results = run_scenario(scenario_name, conversations)
    all_results[scenario_name] = results

    # 在该场景结束后输出NPC状态汇总
    print(f"\n--- {scenario_name} 完结 ---")
    last_tick = results[-1]
    if last_tick["success"]:
        print(f"  对话历史条数: {last_tick['engine_state']['dialogue_history_len']}")
        print(f"  玩家位置: {last_tick['engine_state']['player_location']}")
        for npc_id, snap in last_tick["npc_snapshots"].items():
            print(f"  {snap['name']} | {snap['location']} | obs:{snap['obs_count']} "
                  f"know:{snap['knowledge_count']} belief:{snap['belief_count']} pend:{snap['pending_count']}")

# ============================================================
# 保存完整报告
# ============================================================

report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "scenario_report.json")
os.makedirs(os.path.dirname(report_path), exist_ok=True)

with open(report_path, "w", encoding="utf-8") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)

print(f"\n\n{'='*60}")
print(f"完整报告已保存到: {report_path}")
print(f"共 {len(all_scenarios)} 个场景, 每个场景 7 轮对话")
print(f"{'='*60}")
