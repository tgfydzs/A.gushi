"""多次连续对话调试脚本 — 在同一引擎实例上连续运行多轮对话."""
import sys
sys.path.insert(0, '.')

from main import create_engine

# 创建引擎（只创建一次，保持跨tick状态）
engine = create_engine("cangyue", "cangyue_npcs")

# 多轮连续对话
conversations = [
    "你好，请问这里是哪里？",
    "我叫小明，是一名剑客。",
    "最近有什么有趣的事情发生吗？",
]

for i, user_input in enumerate(conversations):
    print(f"\n{'='*50}")
    print(f"第 {i+1} 轮: {user_input}")
    print('='*50)
    result = engine.tick(user_input)
    if result.success:
        print(f"叙事: {result.get_narrative()}")
    else:
        print(f"错误: {result.get_error_context()}")

print("\n全部对话完成！")
