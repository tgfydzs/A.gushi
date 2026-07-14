"""gushi2 Engine — 世界模拟引擎核心.

包含：
- WorldContext: 运行时上下文容器
- PromptLoader: Prompt模板加载器
- LLMClient: LLM调用抽象接口
- StateValidator: 状态修改校验器
- UpdateEngine: 唯一数据写入口 (N12)
- WorldEngine: 主调度器 + Tick驱动
- MemoryStore: 内存存储
- TickResult: 单次 Tick 输出

依赖方向（单向，无循环）：
    Schema/  ←  nodes/  ←  engine/
"""

from .context import WorldContext
from .llm_client import LLMClient, MockLLMClient, LLMClientError
from .llm_manager import LLMManager, MockLLMManager, NodeLLMConfig
from .prompt_loader import PromptLoader, PromptNotFoundError
from .validation import StateValidator, ValidationResult
from .update_engine import UpdateEngine
from .memory_store import MemoryStore
from .world_engine import WorldEngine
from .tick_result import TickResult

__all__ = [
    "WorldContext",
    "LLMClient",
    "MockLLMClient",
    "LLMClientError",
    "LLMManager",
    "MockLLMManager",
    "NodeLLMConfig",
    "PromptLoader",
    "PromptNotFoundError",
    "StateValidator",
    "ValidationResult",
    "UpdateEngine",
    "MemoryStore",
    "WorldEngine",
    "TickResult",
]
