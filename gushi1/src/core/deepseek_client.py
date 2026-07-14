"""DeepSeek LLM 客户端 — 对接 DeepSeek API.

DeepSeek API 兼容 OpenAI 格式。
Base URL: https://api.deepseek.com
"""

from dataclasses import dataclass, field

from openai import OpenAI

from src.core.config import LLMConfig
from src.core.llm_client import LLMClient, LLMResponse
from src.utils.json_utils import parse_json, sanitize_text


@dataclass
class DeepSeekLLMClient(LLMClient):
    """DeepSeek LLM 客户端.

    使用 OpenAI 兼容接口调用 DeepSeek API。

    Attributes:
        config: LLM配置（provider自动设为deepseek）.
    """

    config: LLMConfig = field(default_factory=LLMConfig)
    _client: OpenAI | None = None

    def __post_init__(self) -> None:
        """初始化后创建 OpenAI 客户端."""
        self.config.provider = "deepseek"
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url or "https://api.deepseek.com",
        )

    def send(self, prompt: str) -> LLMResponse:
        """发送 Prompt 到 DeepSeek API.

        Args:
            prompt: 完整的 Prompt 内容.

        Returns:
            LLMResponse 包含原始文本和解析后的JSON.
        """
        try:
            resp = self._client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            text = sanitize_text(resp.choices[0].message.content or "")
            tokens = resp.usage.total_tokens if resp.usage else 0

            return LLMResponse(
                raw_text=text,
                parsed_json=parse_json(text),
                success=True,
                tokens_used=tokens,
            )
        except Exception as e:
            return LLMResponse(
                success=False,
                error=str(e),
            )
