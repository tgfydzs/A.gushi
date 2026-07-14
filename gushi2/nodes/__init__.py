"""gushi2 Nodes — LLM节点层.

每个节点对应流程图中的一个LLM处理步骤：
- N7:  EventNode (event_node) — 判断事件延续/新建
- N11: PerceptionNode (perception_node) — 判断每个NPC的感知
- N15: ReasoningNode (reasoning_node) — NPC推理
- N16: ResponseNode (response_node) — NPC回应
- N17: DirectorNode (director_node) — 叙事整合
- N18: PropagationNode (propagation_node) — 信息传播

约束：
- 只读取 Schema 和 WorldContext，不修改任何 Entity
- 所有修改必须通过 engine.UpdateEngine (N12)
"""

from .base import BaseNode, NodeResult
from .event_node import EventNode, EventNodeOutput
from .perception_node import PerceptionNode
from .reasoning_node import ReasoningNode
from .response_node import ResponseNode
from .director_node import DirectorNode, DirectorNodeOutput
from .propagation_node import (
    PropagationNode,
    PropagationSuggestion,
    GlobalEventSuggestion,
    PropagationNodeOutput,
)

__all__ = [
    "BaseNode",
    "NodeResult",
    "EventNode",
    "EventNodeOutput",
    "PerceptionNode",
    "ReasoningNode",
    "ResponseNode",
    "DirectorNode",
    "PropagationNode",
    "PropagationSuggestion",
    "GlobalEventSuggestion",
    "PropagationNodeOutput",
]
