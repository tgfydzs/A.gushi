"""基础 Result 对象 — 所有层输出继承自此."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BaseResult:
    """所有层Result的基类.

    Attributes:
        success: 处理是否成功.
        error: 错误信息（仅在失败时填充）.
        layer_name: 产生此Result的层名.
    """

    success: bool = True
    error: Optional[str] = None
    layer_name: str = ""

    def is_success(self) -> bool:
        """判断处理是否成功."""
        return self.success

    def is_error(self) -> bool:
        """判断处理是否有错误."""
        return not self.success
