"""BaseLayer — 所有层的抽象基类.

每层必须实现:
- layer.py: Layer类
- schema.py: 输入输出Schema
- parser.py: JSON → Python对象解析
- result.py: 本层Result对象
- prompt.md: LLM Prompt模板（仅需要LLM的层）

层次顺序固定不可改变：
WorldLayer → WorldStateLayer → EventLayer → PerceptionLayer
→ UpdateLayer → ReasoningLayer → UpdateLayer → ResponseLayer
→ DirectorLayer → PropagationLayer

原则：
- 每层只能负责自己的职责，禁止越层
- 输入固定 → Prompt → LLM → JSON → Parser → Result
- 所有状态修改必须经过UpdateLayer
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LayerResult:
    """层处理结果的通用包装.

    Attributes:
        success: 处理是否成功.
        data: 处理结果数据.
        error: 错误信息.
    """

    success: bool = True
    data: Any = None
    error: str = ""


class BaseLayer(ABC):
    """所有层的抽象基类.

    每个Layer必须实现:
    - process(): 处理输入，返回Result对象.
    - layer_name: 层名称属性.

    约束：
    - 禁止直接修改其他层的数据
    - 禁止跨层读取或修改状态
    - 单一职责：每层只做一件事
    """

    @property
    @abstractmethod
    def layer_name(self) -> str:
        """返回此层的名称."""
        ...

    @abstractmethod
    def process(self, input_data: Any, context: dict[str, Any]) -> LayerResult:
        """处理输入并返回结果.

        Args:
            input_data: 上一层的输出Result数据.
            context: 上下文数据（如NPC列表、世界状态等）.

        Returns:
            LayerResult 包含处理结果.
        """
        ...

    def validate_input(self, input_data: Any) -> bool:
        """验证输入数据是否合法.

        子类可以覆盖此方法添加具体的验证逻辑。

        Args:
            input_data: 待验证的输入数据.

        Returns:
            验证是否通过.
        """
        return True

    def __repr__(self) -> str:
        """返回层的字符串表示."""
        return f"{self.__class__.__name__}(name='{self.layer_name}')"
