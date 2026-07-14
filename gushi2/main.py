"""main.py — gushi2 程序入口.

启动流程:
    加载配置 → 创建 LLMManager → 加载世界数据
    → 创建 WorldEngine → CLI 交互循环

用法:
    python main.py                    # 交互模式
    python main.py --once "输入文本"   # 单次执行
    python main.py --world cangyue    # 指定世界
    python main.py --mock             # Mock 模式（测试用）

LLM 模式:
    config/runtime.yaml: llm_mode = mock | real
    mock: 使用 MockLLMManager（无需 API Key）
    real: 使用真实 LLM Provider（需设置环境变量）
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime

# 确保 gushi2 目录在 Python path 中
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.llm_manager import LLMManager, MockLLMManager
from engine.llm_client import LLMClientError
from engine.memory_store import MemoryStore
from engine.world_engine import WorldEngine
from engine.loader import initialize_store
from engine.logger import TickLogger
from engine.prompt_loader import PromptLoader
from config import load_llm_config, load_runtime_config, get_llm_mode

from nodes.event_node import EventNode
from nodes.perception_node import PerceptionNode
from nodes.reasoning_node import ReasoningNode
from nodes.response_node import ResponseNode
from nodes.director_node import DirectorNode
from nodes.propagation_node import PropagationNode


# =============================================================================
# 启动检查
# =============================================================================


def print_startup_banner(llm_mgr: LLMManager, store: MemoryStore) -> None:
    """打印启动信息横幅."""
    cfg = load_llm_config()
    mode = get_llm_mode()

    print()
    print("=" * 50)
    print("  gushi2 — AI 多NPC世界模拟框架")
    print("=" * 50)
    print(f"  世界:     {store.world_definition.name}")
    print(f"  时间:     第{store.world_state.day}天 "
          f"{store.world_state.time_of_day.value}")
    print(f"  NPC 数量: {len(store.npcs)}")
    print(f"  NPC 列表:")
    for npc in store.npcs.values():
        print(f"    - {npc.name} [{npc.identity.role.value}] "
              f"@ {npc.current_state.location}")
    print(f"  LLM 模式: {mode.upper()}")

    if mode == "real":
        print(f"  Provider:  {cfg.provider}")
        print(f"  Model:     {cfg.model}")
    else:
        print(f"  Provider:  Mock (测试)")

    if store.world_definition.rules:
        print(f"  世界规则:")
        for r in store.world_definition.rules[:5]:
            print(f"    - {r.description}")

    print("=" * 50)
    print()


def run_startup_checks(llm_mode: str) -> None:
    """执行启动前检查."""
    errors: list[str] = []

    # 1. Schema 可加载
    try:
        from Schema.world_schema import WorldState
        from Schema.npc_schema import NPC
    except ImportError as e:
        errors.append(f"Schema 加载失败: {e}")

    # 2. 世界数据
    world_path = _PROJECT_ROOT / "data" / "worlds" / "cangyue.json"
    if not world_path.exists():
        errors.append(f"世界数据不存在: {world_path}")

    npc_path = _PROJECT_ROOT / "data" / "npcs" / "cangyue_npcs.json"
    if not npc_path.exists():
        errors.append(f"NPC 数据不存在: {npc_path}")

    # 3. Prompt
    prompt_dir = _PROJECT_ROOT / "prompts"
    for name in ("event", "perception", "reasoning", "response", "director", "propagation"):
        if not (prompt_dir / f"{name}.md").exists():
            errors.append(f"Prompt 缺失: prompts/{name}.md")

    # 4. LLM 模式 + Key 检查
    if llm_mode == "real":
        cfg = load_llm_config()
        if cfg.provider == "deepseek":
            if not os.getenv("DEEPSEEK_API_KEY"):
                errors.append(
                    "真实模式(DeepSeek)需要 DEEPSEEK_API_KEY 环境变量.\n"
                    "  设置方式: setx DEEPSEEK_API_KEY \"your-key\"\n"
                    "  或切换到 Mock 模式: 修改 config/runtime.yaml "
                    "llm_mode=mock"
                )
        elif cfg.provider == "openai":
            if not os.getenv("OPENAI_API_KEY"):
                errors.append(
                    "真实模式(OpenAI)需要 OPENAI_API_KEY 环境变量."
                )

    if errors:
        print("\n[启动检查失败]")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)


# =============================================================================
# 引擎创建
# =============================================================================


def create_engine(
    world_name: str = "cangyue",
    npc_file: str = "cangyue_npcs",
    force_mock: bool = False,
) -> WorldEngine:
    """创建并初始化 WorldEngine.

    Args:
        world_name: 世界名称.
        npc_file: NPC 文件名.
        force_mock: 强制使用 Mock 模式.

    Returns:
        初始化好的 WorldEngine 实例.
    """
    llm_mode = "mock" if force_mock else get_llm_mode()

    # 启动检查
    run_startup_checks(llm_mode)

    # 1. LLMManager
    if force_mock or llm_mode == "mock":
        llm_mgr = MockLLMManager()
        if force_mock:
            _register_mock_defaults(llm_mgr)
    else:
        try:
            llm_mgr = LLMManager.create_from_config()
        except LLMClientError as e:
            print(f"\n[LLM 初始化失败] {e}")
            print("切换到 Mock 模式...")
            llm_mgr = MockLLMManager()
            _register_mock_defaults(llm_mgr)

    # 2. 加载世界
    store = initialize_store(world_name, npc_file)

    # 3. Prompt Loader
    prompt_loader = PromptLoader()

    # 4. WorldEngine
    engine = WorldEngine(store)

    # 5. Node 注入
    engine.set_nodes(
        event_node=EventNode(
            llm_manager=llm_mgr, prompt_loader=prompt_loader,
        ),
        perception_node=PerceptionNode(
            llm_manager=llm_mgr, prompt_loader=prompt_loader,
        ),
        reasoning_node=ReasoningNode(
            llm_manager=llm_mgr, prompt_loader=prompt_loader,
        ),
        response_node=ResponseNode(
            llm_manager=llm_mgr, prompt_loader=prompt_loader,
        ),
        director_node=DirectorNode(
            llm_manager=llm_mgr, prompt_loader=prompt_loader,
        ),
        propagation_node=PropagationNode(
            llm_manager=llm_mgr, prompt_loader=prompt_loader,
        ),
    )

    # 6. 初始化
    engine.initialize()

    # 7. Banner
    print_startup_banner(llm_mgr, store)

    return engine


def _register_mock_defaults(llm_mgr: MockLLMManager) -> None:
    """为 Mock 模式注册基础响应."""
    llm_mgr.register_response(
        "event",
        '{"continue_context":false,"location":"test_inn",'
        '"topic":"default","addressed_npc_id":"innkeeper",'
        '"involved_npc_ids":["innkeeper"],'
        '"event_type":"dialogue","scope":"private"}',
    )
    llm_mgr.register_response(
        "perception",
        '{"npc_id":"","perceived_event_ids":[],'
        '"perceived_summary":"nothing unusual noticed","missed_event_ids":[],'
        '"perception_confidence":0.5}',
    )
    llm_mgr.register_response(
        "reasoning",
        '{"npc_id":"","new_knowledge":[],"new_beliefs":[],'
        '"resolved_pending":[],"goals_updated":[],'
        '"reasoning_output":"nothing to reason","emotion_change":""}',
    )
    llm_mgr.register_response(
        "response",
        '{"npc_id":"","npc_name":"","response_text":"",'
        '"response_type":"speech","target_id":"player",'
        '"emotion":"neutral","favorability_changes":[]}',
    )
    llm_mgr.register_response(
        "director",
        '{"narrative":"","scene_update":"","npc_responses_summary":[],'
        '"world_changes":[],"scene_notes":"","narration_type":"dialogue",'
        '"npc_speeches":[],"narrations":[]}',
    )
    llm_mgr.register_response(
        "propagation",
        '{"propagations":[],'
        '"global_event":{"should_create":false,'
        '"description":"","affected_area":"","reason":""}}',
    )
    llm_mgr.set_default_response("{}")


# =============================================================================
# CLI 交互
# =============================================================================


def run_cli(engine: WorldEngine) -> None:
    """交互式 CLI 循环."""
    print("输入 'quit' 退出 | 'status' 查看状态 | 'save' 存档")
    print()

    logger = TickLogger(enabled=True)
    tick_count = 0

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n模拟结束.")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("模拟结束.")
            break

        if user_input.lower() == "status":
            print(engine.summary())
            continue

        if user_input.lower() == "save":
            from engine.persistence import save
            path = save(
                engine.memory_store,
                save_name=f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            )
            print(f"存档: {path}")
            continue

        # Tick
        tick_count += 1
        logger.start_tick(
            tick_id=tick_count,
            player_input=user_input,
        )
        result = engine.tick(user_input)
        logger.finish_tick(narrative=result.narrative)

        if result.success:
            narrative = result.narrative
            if narrative:
                print(f"\n{narrative}")
                print(f"  [{result.world_time}]")
            else:
                print(f"  (世界沉默...) [{result.world_time}]")
        else:
            print(f"\n  [错误] {result.error}")
            if result.error:
                print(f"  [{result.world_time}]")


def run_once(engine: WorldEngine, player_input: str) -> None:
    """单次执行."""
    logger = TickLogger(enabled=True)
    tick_count = engine.tick_number + 1

    logger.start_tick(tick_id=tick_count, player_input=player_input)
    result = engine.tick(player_input)
    logger.finish_tick(narrative=result.narrative)

    if result.success:
        if result.narrative:
            print(result.narrative)
        else:
            print("(世界沉默...)")
    else:
        print(f"[错误] {result.error}", file=sys.stderr)


# =============================================================================
# 入口
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="gushi2 — AI 多NPC世界模拟框架"
    )
    parser.add_argument(
        "--once", type=str, default="",
        help="单次执行（不进入交互模式）"
    )
    parser.add_argument(
        "--world", type=str, default="cangyue",
        help="世界名称"
    )
    parser.add_argument(
        "--npcs", type=str, default="cangyue_npcs",
        help="NPC 文件名"
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="强制使用 Mock 模式"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="开启 Debug 模式（输出每步中间结果）"
    )
    args = parser.parse_args()

    engine = create_engine(
        world_name=args.world,
        npc_file=args.npcs,
        force_mock=args.mock,
    )

    if args.debug:
        engine.enable_debug()
        print("[DEBUG] 详细诊断模式已开启\n")

    if args.once:
        run_once(engine, args.once)
    else:
        run_cli(engine)
