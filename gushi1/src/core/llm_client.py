"""LLM API 抽象客户端.

封装LLM调用，负责：
- 发送Prompt
- 接收原始响应
- 确保返回JSON

Python不参与NPC思考，LLM的输出必须是JSON。
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.config import LLMConfig


@dataclass
class LLMResponse:
    """LLM响应包装.

    Attributes:
        raw_text: 原始响应文本.
        parsed_json: 解析后的JSON dict（成功时）.
        success: 调用是否成功.
        error: 错误信息.
        tokens_used: 消耗的token数.
    """

    raw_text: str = ""
    parsed_json: Optional[dict[str, Any]] = None
    success: bool = True
    error: str = ""
    tokens_used: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        """从解析后的JSON中获取值."""
        if self.parsed_json is None:
            return default
        return self.parsed_json.get(key, default)


@dataclass
class LLMClient:
    """LLM客户端抽象.

    封装与大语言模型的交互。
    实际使用时替换为具体的API调用实现。

    Attributes:
        config: LLM配置.
    """

    config: LLMConfig = field(default_factory=LLMConfig)

    def send(self, prompt: str) -> LLMResponse:
        """发送Prompt到LLM并返回响应.

        这是抽象接口，实际实现需要对接具体LLM API。
        子类需要实现具体的API调用逻辑。

        Args:
            prompt: 完整的Prompt内容.

        Returns:
            LLMResponse对象.

        Raises:
            NotImplementedError: 此基类方法需子类实现.
        """
        raise NotImplementedError(
            "LLMClient.send() 是抽象方法，需要根据具体LLM API实现。"
            "示例实现请参考具体的 provider 子类。"
        )

    def send_and_parse(self, prompt: str) -> LLMResponse:
        """发送Prompt并自动解析JSON响应.

        调用 send() 获取响应后，自动解析JSON。
        这是推荐的使用方式。

        Args:
            prompt: 完整的Prompt内容.

        Returns:
            LLMResponse对象，parsed_json已填充.
        """
        from src.utils.json_utils import parse_json

        response = self.send(prompt)
        if response.success and response.raw_text:
            parsed = parse_json(response.raw_text)
            if parsed is not None:
                response.parsed_json = parsed
            else:
                response.success = False
                response.error = "LLM响应JSON解析失败"
        return response
