"""DeepSeekClient — DeepSeek API LLM Provider.

实现 LLMClient 抽象接口，对接 DeepSeek OpenAI-compatible API。

职责：
- 携带 API Key 调用 DeepSeek API
- 将 system/user prompt 组装为 Chat Completions 请求
- 返回原始响应文本

不参与：
- 业务逻辑
- JSON 解析
- Schema 转换
- 世界数据处理

用法:
    client = DeepSeekClient(
        api_key="sk-xxx",
        model="deepseek-chat",
    )
    response = client.chat(
        system_prompt="你是事件判断者",
        user_prompt="玩家输入: 你好",
    )
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Optional

from engine.llm_client import LLMClient, LLMClientError

logger = logging.getLogger(__name__)

# DeepSeek API 默认地址
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


@dataclass
class DeepSeekConfig:
    """DeepSeek API 配置.

    Attributes:
        api_key: API Key（来自环境变量 DEEPSEEK_API_KEY）.
        model: 模型名称.
        base_url: API 基础地址.
        temperature: 采样温度 (0.0-2.0).
        max_tokens: 最大生成 token 数.
        timeout_seconds: 请求超时秒数.
    """

    api_key: str = ""
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_seconds: int = 120


class DeepSeekClient(LLMClient):
    """DeepSeek API LLM 客户端.

    使用 OpenAI-compatible Chat Completions API。
    支持自定义 base_url（可切换到其他兼容服务）。
    """

    def __init__(self, config: Optional[DeepSeekConfig] = None) -> None:
        """初始化 DeepSeek Client.

        Args:
            config: DeepSeek 配置。为 None 时从环境变量读取。
        """
        if config is None:
            config = DeepSeekConfig(
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
                base_url=os.getenv(
                    "DEEPSEEK_BASE_URL", DEFAULT_BASE_URL
                ),
            )

        if not config.api_key:
            raise LLMClientError(
                "Missing environment variable DEEPSEEK_API_KEY. "
                "设置方式: setx DEEPSEEK_API_KEY \"your-key\""
            )

        self._config = config
        self._call_count: int = 0
        self._total_time: float = 0.0

    # ── 公开接口 ──────────────────────────────────────────

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """调用 DeepSeek Chat Completions API.

        Args:
            system_prompt: 系统提示.
            user_prompt: 用户提示.

        Returns:
            响应文本（通常是 JSON 字符串）.

        Raises:
            LLMClientError: API 调用失败时抛出.
        """
        start = time.time()

        try:
            messages: list[dict] = []
            if system_prompt:
                messages.append({
                    "role": "system",
                    "content": system_prompt,
                })
            messages.append({
                "role": "user",
                "content": user_prompt or " ",
            })

            request_body = {
                "model": self._config.model,
                "messages": messages,
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
                "stream": False,
            }

            url = f"{self._config.base_url}/v1/chat/completions"
            data = json.dumps(request_body).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Accept": "application/json",
                },
            )

            with urllib.request.urlopen(
                req, timeout=self._config.timeout_seconds
            ) as resp:
                response_body = resp.read().decode("utf-8")
                result = json.loads(response_body)

            elapsed = time.time() - start
            self._call_count += 1
            self._total_time += elapsed

            # 提取响应文本
            choices = result.get("choices", [])
            if not choices:
                raise LLMClientError(
                    "DeepSeek API 返回空 choices"
                )

            content = (
                choices[0]
                .get("message", {})
                .get("content", "")
            )

            logger.info(
                f"[DeepSeek] model={self._config.model} "
                f"prompt_len={len(system_prompt) + len(user_prompt)} "
                f"response_len={len(content)} "
                f"time={elapsed:.2f}s"
            )

            return content

        except urllib.error.HTTPError as e:
            elapsed = time.time() - start
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise LLMClientError(
                f"DeepSeek API HTTP {e.code}: {error_body[:300]} "
                f"(time={elapsed:.2f}s)"
            ) from e
        except urllib.error.URLError as e:
            elapsed = time.time() - start
            raise LLMClientError(
                f"DeepSeek API 网络错误: {e.reason} (time={elapsed:.2f}s)"
            ) from e
        except json.JSONDecodeError as e:
            elapsed = time.time() - start
            raise LLMClientError(
                f"DeepSeek API 响应 JSON 解析失败: {e} (time={elapsed:.2f}s)"
            ) from e

    # ── 统计 ──────────────────────────────────────────────

    @property
    def call_count(self) -> int:
        """API 调用次数."""
        return self._call_count

    @property
    def total_time(self) -> float:
        """总调用耗时（秒）."""
        return self._total_time

    @property
    def avg_time(self) -> float:
        """平均每次调用耗时（秒）."""
        return self._total_time / max(self._call_count, 1)

    @property
    def model(self) -> str:
        """当前使用的模型."""
        return self._config.model
