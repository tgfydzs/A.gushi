"""LLMClient — LLM 调用抽象接口.

提供统一的 LLM 调用抽象和 Mock 实现。

设计原则：
- 不绑定任何具体厂商（OpenAI/Claude/Dashscope 等）
- 通过子类继承扩展具体实现
- MockLLMClient 用于测试，支持预注册响应
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# 抽象接口
# =============================================================================


class LLMClientError(Exception):
    """LLM 调用异常."""


class LLMClient:
    """LLM 调用抽象基类.

    所有 LLM 客户端必须继承此类并实现 chat()。

    用法:
        client = SomeLLMClient()
        response = client.chat(
            system_prompt="你是事件判断者",
            user_prompt="玩家输入: ..."
        )
    """

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """发送 Prompt 到 LLM 并返回原始响应文本.

        Args:
            system_prompt: 系统提示（设定角色和规则）.
            user_prompt: 用户提示（具体要处理的内容）.

        Returns:
            LLM 的原始响应文本（未解析的 JSON 字符串）.

        Raises:
            NotImplementedError: 基类方法，需要子类实现.
            LLMClientError: LLM 调用失败时抛出.
        """
        raise NotImplementedError(
            "LLMClient.chat() 是抽象方法，需要具体实现。"
        )


# =============================================================================
# Mock 实现（用于测试）
# =============================================================================


@dataclass
class MockLLMClient(LLMClient):
    """Mock LLM 客户端，用于测试.

    不调用真实 API。通过 register_response() 预注册每个 Prompt 的期望响应。

    用法:
        mock = MockLLMClient()
        mock.register_response("event", '{"continue_context": false, ...}')
        result = mock.chat(system_prompt="...", user_prompt="...")
        # → '{"continue_context": false, ...}'

    支持两种匹配模式：
    1. 精确匹配：通过 prompt_name 注册
    2. 关键词匹配：通过 register_keyword_match() 注册（兜底）
    """

    # prompt_name → 返回的 JSON 字符串
    _responses: dict[str, str] = field(default_factory=dict)

    # (keyword_in_user_prompt) → 返回的 JSON 字符串
    _keyword_responses: list[tuple[str, str]] = field(default_factory=list)

    # 未匹配时的默认响应
    _default_response: str = "{}"

    def register_response(self, prompt_name: str, response: str) -> None:
        """注册一个 Prompt 的预定义响应.

        Args:
            prompt_name: Prompt 名称（如 "event", "perception"）。
                用于精确匹配 system_prompt 中的 Prompt 标识。
            response: 要返回的 JSON 字符串。
        """
        self._responses[prompt_name] = response

    def register_keyword_match(self, keyword: str, response: str) -> None:
        """注册基于关键词的响应（兜底匹配）.

        当精确匹配失败时，检查 user_prompt 是否包含关键词。

        Args:
            keyword: 触发此响应的关键词。
            response: 要返回的 JSON 字符串。
        """
        self._keyword_responses.append((keyword, response))

    def set_default_response(self, response: str) -> None:
        """设置无匹配时的默认响应."""
        self._default_response = response

    def clear_responses(self) -> None:
        """清除所有注册的响应."""
        self._responses.clear()
        self._keyword_responses.clear()
        self._default_response = "{}"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """返回预注册的响应（Mock 实现）.

        匹配顺序：
        1. 精确匹配：检查 system_prompt 或 user_prompt 中是否包含
           已注册的 prompt_name
        2. 检查用户提示中是否包含已注册的关键词
        3. 返回默认响应

        Args:
            system_prompt: 系统提示.
            user_prompt: 用户提示.

        Returns:
            预注册的响应字符串.
        """
        combined = (system_prompt + " " + user_prompt).lower()

        # 1. 精确匹配
        for name, response in self._responses.items():
            if name in combined:
                return response

        # 2. 关键词匹配
        for keyword, response in self._keyword_responses:
            if keyword.lower() in user_prompt.lower():
                return response

        # 3. 默认
        return self._default_response
