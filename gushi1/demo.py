"""AI 多NPC世界模拟 — 交互式演示.

等同于: python main.py --world cangyue
"""

from main import create_engine, interactive_mode

if __name__ == "__main__":
    engine = create_engine("cangyue", "cangyue_npcs")
    interactive_mode(engine)
