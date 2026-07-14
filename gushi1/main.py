"""AI 多NPC世界模拟 — 主入口.

使用方式：
    python main.py                    # 交互式运行（苍月大陆）
    python main.py --world cangyue    # 指定世界
    python main.py --once "你好"      # 单次运行
"""

import argparse
import sys

# 强制 UTF-8 输出，解决 Windows 终端 GBK 编码问题
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.core.config import Config, LLMConfig
from src.core.deepseek_client import DeepSeekLLMClient
from src.core.engine import Engine
from src.utils.data_loader import load_world, load_npcs

# ============================================================
# 默认配置
# ============================================================

DEFAULT_WORLD = "cangyue"
DEFAULT_NPCS = "cangyue_npcs"
DEFAULT_API_KEY = "sk-6801df4624fa4a10b61d4669d5804f60"


def create_engine(world_name: str, npc_file: str, api_key: str = "") -> Engine:
    """创建并初始化引擎.

    Args:
        world_name: 世界名称.
        npc_file: NPC数据文件名.
        api_key: DeepSeek API密钥.

    Returns:
        初始化后的Engine.
    """
    if not api_key:
        api_key = DEFAULT_API_KEY

    # LLM客户端
    llm_client = DeepSeekLLMClient(config=LLMConfig(
        model="deepseek-chat",
        api_key=api_key,
        max_tokens=2048,
        temperature=0.7,
    ))

    # 配置
    config = Config(llm=llm_client.config)

    # 引擎
    engine = Engine(config=config, llm_client=llm_client)

    # 加载世界
    world_def = load_world(world_name)
    engine.initialize_world(world_def)

    # 加载NPC
    for npc in load_npcs(npc_file):
        engine.add_npc(npc)

    return engine


def interactive_mode(engine: Engine) -> None:
    """交互式运行模式."""
    print("=" * 55)
    print("   🏰  AI 多NPC世界模拟")
    print("=" * 55)
    print()
    print(f"世界: {engine.world_definition.name}")
    print(f"NPC数量: {len(engine.npcs)}")
    print()
    print("在场NPC:")
    for npc in engine.npcs.values():
        print(f"  [{npc.role.value}] {npc.name} — {npc.current_state.location}")
    print()
    print("输入你的行动，输入 quit 退出，输入 status 看状态")
    print("-" * 55)

    while True:
        try:
            user_input = input("\n🎮 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("再见！")
            break
        if user_input.lower() == "status":
            engine._print_status()
            continue

        print("\n⏳ 世界运转中...\n")
        result = engine.tick(user_input)

        if result.success:
            narrative = result.get_narrative()
            if narrative:
                print(f"📖 {narrative}")
            else:
                print("📖 （世界沉默...）")
        else:
            print(f"\n❌ [错误] {result.get_error_context()}")


def once_mode(engine: Engine, player_input: str) -> None:
    """单次运行模式."""
    print(f"🎮 你: {player_input}")
    print()
    result = engine.tick(player_input)
    if result.success:
        narrative = result.get_narrative()
        if narrative:
            print(f"📖 {narrative}")
        else:
            print("（世界沉默...）")
    else:
        print(f"❌ {result.get_error_context()}")


# ============================================================
# 命令行入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="AI 多NPC世界模拟")
    parser.add_argument("--world", default=DEFAULT_WORLD, help=f"世界名称 (默认: {DEFAULT_WORLD})")
    parser.add_argument("--npcs", default=DEFAULT_NPCS, help=f"NPC文件名 (默认: {DEFAULT_NPCS})")
    parser.add_argument("--once", type=str, default=None, help="单次运行模式，传入玩家输入")
    parser.add_argument("--api-key", type=str, default=None, help="API密钥")

    args = parser.parse_args()

    engine = create_engine(args.world, args.npcs, api_key=args.api_key or DEFAULT_API_KEY)

    if args.once:
        once_mode(engine, args.once)
    else:
        interactive_mode(engine)


if __name__ == "__main__":
    main()
