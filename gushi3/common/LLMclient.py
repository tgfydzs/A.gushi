import time
import requests
from typing import List, Dict, Optional
from common.base_cnf import *

from common.log import get_logger
_llm_log = get_logger("LLM")

# 重试配置
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = [1, 3, 8]
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable(e: Exception) -> bool:
    """判断异常是否可重试（网络/超时/限流/服务端错误）"""
    if isinstance(e, requests.ConnectionError):
        return True
    if isinstance(e, requests.Timeout):
        return True
    if isinstance(e, requests.HTTPError):
        return e.response.status_code in _RETRYABLE_STATUSES
    return False


def _extract_reply(res_data: dict) -> tuple:
    """
    从响应里取出正文和推理内容。

    :return: (reply: str, reasoning_chars: int)
             reasoning_chars > 0 说明该模型是推理模型，思考过程不计入 content
    """
    msg = (res_data.get("choices") or [{}])[0].get("message") or {}
    reply = (msg.get("content") or "").strip()
    reasoning = msg.get("reasoning_content") or ""
    return reply, len(reasoning)


def _fmt_usage(res_data: dict) -> str:
    """
    格式化 token 用量。各家字段名不完全一致，能取到多少记多少。

    关键指标：
      in       prompt_tokens —— 输入规模（大 → 优化 prompt/裁剪表）
      out      completion_tokens —— 输出规模（大 → 砍输出字段）
      reason   reasoning_tokens —— **推理模型的隐藏思考**，不计入 out，
               但实实在在耗时。这一项大意味着砍 prompt 没用，得换模型。
      cache_hit prompt 缓存命中 —— 省钱指标，不影响延迟
    """
    usage = res_data.get("usage") or {}
    if not usage:
        return 'tokens: (无usage字段)'

    parts = [f"in={usage.get('prompt_tokens', '?')}",
             f"out={usage.get('completion_tokens', '?')}"]

    details = usage.get("completion_tokens_details") or {}
    reason = details.get("reasoning_tokens") or usage.get("reasoning_tokens")
    if reason:
        parts.append(f"reason={reason}")

    hit = usage.get("prompt_cache_hit_tokens")
    if hit is not None:
        parts.append(f"cache_hit={hit}")

    return 'tokens: ' + ' '.join(parts)

# ================== 模型基础配置 ======================
# 默认系统提示词
DEFAULT_SYSTEM_PROMPT = "你是专业智能助手，回答简洁准确，逻辑清晰"


class LLMClient:
    """
    大模型API基础调用客户端
    默认行为：纯单次请求，**不内部存储、不维护任何对话上下文**
    上下文完全由调用方通过入参传入管理，无自动记忆会话逻辑
    """

    def __init__(self, layer: str = ''):
        """
        :param layer: 层名（如 'N2'/'N3'/'N45'/'N9'），用于查 LAYER_MODEL_OVERRIDE 选模型。
                      未配置覆写时回落到全局 MODEL_NAME。
        """
        self.layer = layer or ''
        self.api_key = API_KEY
        self.base_url = BASE_URL
        self.model_name = (LAYER_MODEL_OVERRIDE.get(self.layer) if self.layer else None) or MODEL_NAME
        self.default_system_prompt = DEFAULT_SYSTEM_PROMPT

        _llm_log.info(f"LLMClient 初始化 | layer={self.layer or '-'} | url={self.base_url} | "
                      f"model={self.model_name} | key={self.api_key[:12] if self.api_key else 'None'}...")

        # 请求固定头部
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # 复用连接池：省掉每次调用的 TCP+TLS 握手（一轮流水线有十几次调用）
        # urllib3 的 PoolManager 是线程安全的，N4-N6 的并行线程可共享
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def chat(
        self,
        user_content: str,
        system_prompt: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """
        发起单次对话请求，无内置历史记录，所有上下文依赖外部传入
        :param user_content: 必填，本轮用户提问内容
        :param system_prompt: 可选，本次请求使用的系统提示词；不传则使用全局默认prompt
        :param context_messages: 可选，外部传入完整对话上下文列表
            格式示例：[{"role":"system","content":"xxx"},{"role":"user","content":"xxx"},{"role":"assistant","content":"xxx"}]
            若传入该参数，会直接基于该列表追加user_content发送请求，system_prompt参数失效
        :return: 模型返回的文本结果，异常时返回错误信息字符串
        """
        # 场景1：调用方传入完整上下文列表，直接拼接用户消息发送
        if context_messages is not None:
            send_msgs = context_messages.copy()
            send_msgs.append({"role": "user", "content": user_content})
        # 场景2：无外部上下文，仅使用系统提示词 + 当前用户单轮提问
        else:
            use_sys = system_prompt if system_prompt is not None else self.default_system_prompt
            send_msgs = [
                {"role": "system", "content": use_sys},
                {"role": "user", "content": user_content}
            ]

        payload = {
            "model": self.model_name,
            "messages": send_msgs
        }

        t0 = time.time()
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.post(
                    url=self.base_url,
                    json=payload,
                    timeout=90
                )
                # 先判成败再记日志——失败请求不该留下"完成"记录，会污染延迟统计
                resp.raise_for_status()
                res_data = resp.json()
                elapsed = time.time() - t0
                reply, reason_chars = _extract_reply(res_data)
                reason_info = f" reasoning={reason_chars}字" if reason_chars else ''
                _llm_log.info(f"chat({self.model_name}) 完成 | {elapsed:.1f}s | "
                              f"{_fmt_usage(res_data)}{reason_info}")
                return reply
            except Exception as e:
                last_err = e
                elapsed = time.time() - t0
                if attempt < _MAX_RETRIES - 1 and _is_retryable(e):
                    backoff = _RETRY_BACKOFF_SECONDS[attempt]
                    _llm_log.warn(f"chat 第{attempt+1}次失败(耗时{elapsed:.1f}s)，{backoff}s后重试：{str(e)}")
                    time.sleep(backoff)
                else:
                    _llm_log.error(f"chat 请求失败(耗时{elapsed:.1f}s)：{str(e)}")
                    raise
        raise RuntimeError(f"chat 请求失败：{str(last_err)}")


    def chat_by_context(self, context: list, label: str = '', temperature: float = None):
        """
        上下文由外部维护，最新消息放到列表最后
        :param context: 上下文列表，例：[{"role": "system", "content": use_sys}, ...]
        :param label: 调用方标识，如 N2/N3/N4/N5/N6，用于日志
        :param temperature: 可选，覆盖模型默认温度（0.0-2.0）。None表示使用API默认值
        :return:
        """
        payload = {
            'model': self.model_name,
            'messages': context
        }
        if temperature is not None:
            payload['temperature'] = temperature
        t0 = time.time()
        tag = f' [{label}]' if label else ''
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.post(
                    url=self.base_url,
                    json=payload,
                    timeout=90
                )
                # 先判成败再记日志——失败请求不该留下"完成"记录，会污染延迟统计
                resp.raise_for_status()
                res_data = resp.json()
                elapsed = time.time() - t0
                reply, reason_chars = _extract_reply(res_data)
                reason_info = f" reasoning={reason_chars}字" if reason_chars else ''
                _llm_log.info(f"chat_by_context({self.model_name}){tag} 完成 | {elapsed:.1f}s | "
                              f"{_fmt_usage(res_data)}{reason_info}")
                return reply
            except Exception as e:
                last_err = e
                elapsed = time.time() - t0
                if attempt < _MAX_RETRIES - 1 and _is_retryable(e):
                    backoff = _RETRY_BACKOFF_SECONDS[attempt]
                    _llm_log.warn(f"chat_by_context{tag} 第{attempt+1}次失败(耗时{elapsed:.1f}s)，{backoff}s后重试：{str(e)}")
                    time.sleep(backoff)
                else:
                    _llm_log.error(f"chat_by_context({self.model_name}){tag} 请求失败(耗时{elapsed:.1f}s)：{str(e)}")
                    raise
        raise RuntimeError(f"chat_by_context{tag} 请求失败：{str(last_err)}")









# 调用示例
if __name__ == "__main__":
    cli = LLMClient()

    # 用法1：纯单次对话，不带任何历史
    print(cli.chat("1+1等于几"))

    # 用法2：自定义本次系统prompt
    # print(cli.chat("介绍自己", system_prompt="只用一句话回答"))

    # 用法3：外部手动传入多轮上下文，由调用方维护记忆
    # history = [
    #     {"role": "system", "content": "你是翻译助手"},
    #     {"role": "user", "content": "apple"},
    #     {"role": "assistant", "content": "苹果"}
    # ]
    # print(cli.chat("再翻译一遍", context_messages=history))