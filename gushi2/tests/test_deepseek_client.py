"""测试 DeepSeekClient — 不调用真实 API.

验证：
- 缺少 API Key 时报错
- 环境变量读取
- Provider 选择逻辑
- HTTP 错误处理
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from engine.llm_client import LLMClientError
from engine.providers.deepseek_client import (
    DeepSeekClient, DeepSeekConfig,
)


class TestDeepSeekConfig:
    def test_config_defaults(self):
        """默认配置来从环境变量."""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            config = DeepSeekConfig(
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            )
            assert config.api_key == "test-key"
            assert config.model == "deepseek-chat"
            assert config.base_url == "https://api.deepseek.com"
            assert config.temperature == 0.7
            assert config.max_tokens == 2048

    def test_missing_api_key_raises_error(self):
        """缺少 API Key 时创建 Client 应报错."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(LLMClientError) as exc_info:
                DeepSeekClient(DeepSeekConfig(api_key=""))
            assert "Missing environment variable" in str(exc_info.value)
            assert "DEEPSEEK_API_KEY" in str(exc_info.value)


class TestDeepSeekClient:
    def test_chat_success(self):
        """模拟成功 API 调用."""
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'{"choices":[{"message":{"content":"hello world"}}]}'
        )
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            client = DeepSeekClient(DeepSeekConfig(
                api_key="test-key",
                model="deepseek-chat",
            ))
            result = client.chat("system", "user")
            assert result == "hello world"

    def test_chat_count_tracks_calls(self):
        """调用计数正确."""
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'{"choices":[{"message":{"content":"ok"}}]}'
        )
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            client = DeepSeekClient(DeepSeekConfig(
                api_key="test-key",
            ))
            client.chat("s", "u")
            client.chat("s", "u")
            assert client.call_count == 2
            assert client.total_time > 0
            assert client.avg_time > 0

    def test_http_error_raises_llm_client_error(self):
        """HTTP 错误转为 LLMClientError."""
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 401, "Unauthorized", {}, None,
            ),
        ):
            client = DeepSeekClient(DeepSeekConfig(
                api_key="test-key",
            ))
            with pytest.raises(LLMClientError) as exc_info:
                client.chat("s", "u")
            assert "401" in str(exc_info.value)

    def test_network_error_raises_llm_client_error(self):
        """网络错误转为 LLMClientError."""
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            client = DeepSeekClient(DeepSeekConfig(
                api_key="test-key",
            ))
            with pytest.raises(LLMClientError) as exc_info:
                client.chat("s", "u")
            assert "timeout" in str(exc_info.value)


class TestLLMManagerProvider:
    """测试 LLMManager.create_from_config() 的 Provider 选择."""

    def test_mock_mode_returns_mock_manager(self):
        """llm_mode=mock 时返回 MockLLMManager."""
        with patch(
            "config.get_llm_mode", return_value="mock",
        ):
            from engine.llm_manager import LLMManager, MockLLMManager
            mgr = LLMManager.create_from_config()
            assert isinstance(mgr, MockLLMManager)

    def test_deepseek_mode_requires_api_key(self):
        """real+deepseek 模式下缺少 Key 应报错."""
        with patch(
            "config.get_llm_mode", return_value="real",
        ):
            with patch.dict(os.environ, {}, clear=True):
                from engine.llm_manager import LLMManager
                with pytest.raises(LLMClientError) as exc_info:
                    LLMManager.create_from_config()
                assert "DEEPSEEK_API_KEY" in str(exc_info.value)

    def test_deepseek_mode_with_key(self):
        """real+deepseek 模式下有 Key 可正常创建."""
        with patch(
            "config.get_llm_mode", return_value="real",
        ):
            with patch(
                "config.load_llm_config",
                return_value=type('obj', (object,), {
                    'provider': 'deepseek',
                    'model': 'deepseek-chat',
                    'base_url': '',
                    'temperature': 0.7,
                    'max_tokens': 2048,
                    'nodes': {},
                    'api_key': '',
                })(),
            ):
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
                    from engine.llm_manager import LLMManager
                    from engine.llm_manager import MockLLMManager
                    mgr = LLMManager.create_from_config()
                    assert not isinstance(mgr, MockLLMManager)

    def test_unsupported_provider_raises_error(self):
        """不支持的 Provider 报错."""
        with patch(
            "config.get_llm_mode", return_value="real",
        ):
            with patch(
                "config.load_llm_config",
                return_value=type('obj', (object,), {
                    'provider': 'unknown',
                    'model': '',
                    'base_url': '',
                    'temperature': 0.7,
                    'max_tokens': 1024,
                    'nodes': {},
                    'api_key': '',
                })(),
            ):
                with patch.dict(os.environ, {"UNKNOWN_API_KEY": "x"}):
                    from engine.llm_manager import LLMManager
                    with pytest.raises(LLMClientError):
                        LLMManager.create_from_config()
