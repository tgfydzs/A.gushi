"""gushi2 测试套件.

测试顺序（按世界状态一致性优先）：
1. test_schema.py — Schema序列化/反序列化
2. test_update_engine.py — 唯一写入口正确性
3. test_world_engine.py — 完整Tick流程
4. test_event_node.py 等 — 单节点LLM行为
"""
