"""LLMManager — 统一 LLM 调用管理层.

职责：
- 管理 provider/api_key/base_url/model 等配置
- 支持每个 Node 使用独立的模型和参数
- 统一 chat() 接口：llm_manager.chat(node_name, system_prompt, user_prompt)

Node 只能通过 LLMManager 调用 LLM。禁止 Node 直接持有 LLMClient。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import logging
from engine.llm_client import LLMClient, MockLLMClient, LLMClientError

logger = logging.getLogger(__name__)


# =============================================================================
# 节点级 LLM 配置
# =============================================================================


@dataclass
class NodeLLMConfig:
    """单个节点的 LLM 配置.

    Attributes:
        model: 模型名称。为空则使用默认模型.
        temperature: 温度参数。为 None 则使用默认值.
        max_tokens: 最大 token 数。为 0 则使用默认值.
    """

    model: str = ""
    temperature: Optional[float] = None
    max_tokens: int = 0


# =============================================================================
# LLMManager
# =============================================================================


class LLMManager:
    """统一 LLM 调用管理器.

    管理所有 LLM Client 实例和节点级配置。
    每个节点可以有独立的 LLMClient（不同的 model/provider）。

    用法:
        mgr = LLMManager(default_client=some_client)
        mgr.set_node_client("event", event_client)
        response = mgr.chat("event", system_prompt, user_prompt)
    """

    def __init__(
        self,
        default_client: Optional[LLMClient] = None,
    ) -> None:
        """初始化.

        Args:
            default_client: 默认 LLM Client。节点未单独配置时使用。
        """
        self._default_client: LLMClient = default_client or MockLLMClient()
        self._node_clients: dict[str, LLMClient] = {}
        self._node_configs: dict[str, NodeLLMConfig] = {}

    # ── 配置 ──────────────────────────────────────────────

    def set_node_client(self, node_name: str, client: LLMClient) -> None:
        """为指定节点绑定 LLM Client.

        Args:
            node_name: 节点名称（如 "event", "perception"）.
            client: LLM Client 实例.
        """
        self._node_clients[node_name] = client

    def set_node_config(self, node_name: str, config: NodeLLMConfig) -> None:
        """为指定节点设置 LLM 参数.

        Args:
            node_name: 节点名称.
            config: 节点 LLM 配置.
        """
        self._node_configs[node_name] = config

    def set_default_client(self, client: LLMClient) -> None:
        """设置默认 LLM Client.

        Args:
            client: LLM Client 实例.
        """
        self._default_client = client

    # ── 调用 ──────────────────────────────────────────────

    def chat(self, node_name: str, system_prompt: str, user_prompt: str = "") -> str:
        """调用 LLM 并返回原始响应文本.

        按优先级查找客户端：
        1. 节点专属 LLMClient
        2. 默认 LLMClient

        Args:
            node_name: 节点名称（如 "event", "perception"）.
            system_prompt: 系统提示.
            user_prompt: 用户提示.

        Returns:
            LLM 原始响应文本.
        """
        client = self._node_clients.get(node_name, self._default_client)
        # 将 node_name 注入 system_prompt 首行，供 MockLLMClient 做 prompt 名称匹配
        tagged_prompt = f"[node:{node_name}]\n{system_prompt}"
        return client.chat(
            system_prompt=tagged_prompt,
            user_prompt=user_prompt,
        )

    def get_node_config(self, node_name: str) -> NodeLLMConfig:
        """获取节点 LLM 配置.

        Args:
            node_name: 节点名称.

        Returns:
            NodeLLMConfig.
        """
        return self._node_configs.get(node_name, NodeLLMConfig())

    def get_default_client(self) -> LLMClient:
        """获取默认 LLM Client."""
        return self._default_client

    # ── 工厂方法 ──────────────────────────────────────────

    @staticmethod
    def create_from_config() -> LLMManager:
        """根据 config/llm.yaml 创建 LLMManager.

        自动选择 Provider 并根据节点覆盖配置设置每个节点的
        独立 LLMClient（如果需要不同参数）。

        Returns:
            配置好的 LLMManager.

        Raises:
            LLMClientError: Provider 配置或 API Key 缺失时抛出.
        """
        from config import load_llm_config, get_llm_mode
        import os

        llm_cfg = load_llm_config()
        mode = get_llm_mode()

        if mode == "mock":
            return MockLLMManager()

        # 真实模式：根据 provider 创建客户端
        provider = llm_cfg.provider

        if provider == "deepseek":
            from engine.providers.deepseek_client import (
                DeepSeekClient, DeepSeekConfig,
            )
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            if not api_key:
                raise LLMClientError(
                    "真实模式需要 DEEPSEEK_API_KEY 环境变量."
                )
            client = DeepSeekClient(DeepSeekConfig(
                api_key=api_key,
                model=llm_cfg.model,
                base_url=llm_cfg.base_url or "https://api.deepseek.com",
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
            ))
            mgr = LLMManager(default_client=client)
            logger.info(
                f"LLMManager: DeepSeek {llm_cfg.model}"
            )
        elif provider == "openai":
            # OpenAI 使用同一 API 格式，复用 DeepSeekClient
            from engine.providers.deepseek_client import (
                DeepSeekClient, DeepSeekConfig,
            )
            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                raise LLMClientError(
                    "真实模式需要 OPENAI_API_KEY 环境变量."
                )
            client = DeepSeekClient(DeepSeekConfig(
                api_key=api_key,
                model=llm_cfg.model,
                base_url=llm_cfg.base_url or "https://api.openai.com/v1",
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
            ))
            mgr = LLMManager(default_client=client)
            logger.info(
                f"LLMManager: OpenAI {llm_cfg.model}"
            )
        else:
            raise LLMClientError(
                f"Unsupported provider: {provider}"
            )

        # 应用节点级温度覆盖（如果需要不同温度）
        # 当前简化：所有节点使用同一客户端

        return mgr


# =============================================================================
# MockLLMManager（测试用）
# =============================================================================


class MockLLMManager(LLMManager):
    """Mock LLMManager，用于测试.

    支持 register_response(node_name, json_string) 预注册响应。
    与 MockLLMClient 的 register_response 接口兼容。
    """

    def __init__(self) -> None:
        self._mock = MockLLMClient()
        super().__init__(default_client=self._mock)

    def register_response(self, node_name: str, response: str) -> None:
        """注册节点响应（兼容旧 MockLLMClient 接口）.

        Args:
            node_name: 节点名称（对应 prompt_name）.
            response: 要返回的 JSON 字符串.
        """
        self._mock.register_response(node_name, response)

    def register_keyword_match(self, keyword: str, response: str) -> None:
        """注册关键词匹配响应."""
        self._mock.register_keyword_match(keyword, response)

    def set_default_response(self, response: str) -> None:
        """设置默认响应."""
        self._mock.set_default_response(response)

    def clear_responses(self) -> None:
        """清除所有注册的响应."""
        self._mock.clear_responses()
