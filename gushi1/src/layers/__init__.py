"""分层架构模块 — 每层一个文件，只包含逻辑和解析器.

层次顺序固定不可改变：
WorldLayer → WorldStateLayer → EventLayer → PerceptionLayer
→ UpdateLayer → ReasoningLayer → UpdateLayer → ResponseLayer
→ DirectorLayer → PropagationLayer

原则：
- 层不包含实际数据（数据在 data/ 目录）
- 层不包含Prompt（Prompt在 prompts/ 目录）
- 所有状态修改必须经过UpdateLayer
"""
