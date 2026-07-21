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

# ================== 模型基础配置 ======================
# 默认系统提示词
DEFAULT_SYSTEM_PROMPT = "你是专业智能助手，回答简洁准确，逻辑清晰"


class LLMClient:
    """
    大模型API基础调用客户端
    默认行为：纯单次请求，**不内部存储、不维护任何对话上下文**
    上下文完全由调用方通过入参传入管理，无自动记忆会话逻辑
    """

    def __init__(self):
        """初始化客户端基础鉴权信息与请求头"""
        self.api_key = API_KEY
        self.base_url = BASE_URL
        self.model_name = MODEL_NAME
        self.default_system_prompt = DEFAULT_SYSTEM_PROMPT

        _llm_log.info(f"LLMClient 初始化 | url={self.base_url} | model={self.model_name} | "
                      f"key={self.api_key[:12] if self.api_key else 'None'}...")

        # 请求固定头部
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

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
                resp = requests.post(
                    url=self.base_url,
                    headers=self.headers,
                    json=payload,
                    timeout=90
                )
                elapsed = time.time() - t0
                _llm_log.info(f"chat({self.model_name}) 请求完成，耗时 {elapsed:.1f}s")
                resp.raise_for_status()
                res_data = resp.json()
                reply = res_data["choices"][0]["message"]["content"].strip()
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
                    return f"请求失败：{str(e)}"
        return f"请求失败：{str(last_err)}"


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
                resp = requests.post(
                    url=self.base_url,
                    headers=self.headers,
                    json=payload,
                    timeout=90
                )
                elapsed = time.time() - t0
                _llm_log.info(f"chat_by_context({self.model_name}){tag} 请求完成，耗时 {elapsed:.1f}s")
                resp.raise_for_status()
                res_data = resp.json()
                reply = res_data["choices"][0]["message"]["content"].strip()
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
                    return f'请求失败{str(e)}'
        return f'请求失败{str(last_err)}'









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