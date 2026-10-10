"""
gushi4 LLM 客户端 —— 无状态、分层可配。

与 gushi3 的两点差别：

1. **分层配置**（`LLMClient('S1')`）
   每个调用点传层名，客户端去 `LAYER_MODEL_OVERRIDE` 查这一层的覆写：
   推理开关 / 模型 / 超时 / 重试 / 温度。未列出的字段回落全局默认值。
   这是设计方案 §3.8「每个调用点两个独立开关」的落点——阶段 1 的推理消融
   实验改配置即可，不需要动代码。

2. **推理开关**
   `thinking=False` → 请求体里塞 `payload['thinking'] = {'type': 'disabled'}`。
   推理 token 实测占输出 token 的 67~96%，**按输出计费**，关掉同时省延迟和成本。

其余（无状态、Session 池、token 日志、重试）与 gushi3 一致。
"""

import os
import time
from typing import List, Dict, Optional

import requests

from common.base_cnf import *
from common.log import get_logger

_llm_log = get_logger("LLM")


# ===================== 分层配置解析 =====================

class LayerCfg:
    """
    一个调用点的最终生效配置。由「全局默认」叠加「本层覆写」得到。

    覆写表里的键都是可选的，不填就回落全局值 —— 这样 base_cnf 里
    只需要写"这一层和别处不一样的地方"。
    """

    __slots__ = ('layer', 'model', 'base_url', 'api_key', 'timeout',
                 'max_retries', 'temperature', 'thinking')

    def __init__(self, layer: str = ''):
        ov = (LAYER_MODEL_OVERRIDE.get(layer) or {}) if layer else {}

        self.layer = layer or '-'

        # 模型 / 端点：留空表示"目前不区分模型"，见 base_cnf 的说明
        self.model = ov.get('model') or DEFAULT_MODEL
        self.base_url = ov.get('base_url') or DEFAULT_BASE_URL

        key_env = ov.get('api_key_env')
        if key_env:
            self.api_key = os.environ.get(key_env) or DEFAULT_API_KEY
        else:
            self.api_key = DEFAULT_API_KEY

        self.timeout = ov.get('timeout', DEFAULT_TIMEOUT)
        self.max_retries = ov.get('max_retries', DEFAULT_MAX_RETRIES)
        self.temperature = ov.get('temperature')      # None = 用 API 默认
        # 缺省 = 开推理。只有显式写 False 才关
        self.thinking = ov.get('thinking', True)

    def describe(self) -> str:
        return (f'{self.layer} | model={self.model} | think={"on" if self.thinking else "off"}'
                f' | timeout={self.timeout}s | retries={self.max_retries}'
                f' | temp={self.temperature if self.temperature is not None else "默认"}')


# ===================== 响应解析 =====================

def _is_retryable(e: Exception, statuses=RETRYABLE_STATUSES) -> bool:
    """判断异常是否可重试（网络/超时/限流/服务端错误）"""
    if isinstance(e, requests.ConnectionError):
        return True
    if isinstance(e, requests.Timeout):
        return True
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return e.response.status_code in statuses
    return False


def _extract_reply(res_data: dict) -> tuple:
    """
    从响应里取出正文和推理正文。

    :return: (reply: str, reasoning: str)
             关掉推理时 reasoning 恒为 ''
    """
    msg = (res_data.get("choices") or [{}])[0].get("message") or {}
    reply = (msg.get("content") or "").strip()
    reasoning = msg.get("reasoning_content") or ""
    return reply, reasoning


# ===================== 调试钩子：抓取推理正文 =====================
#
# 默认**不抓**。理由：推理正文很长（动辄几万字），而且是模型的内部思考，
# 逐条记进日志会把日志淹掉。
#
# 但排查"这一层为什么想这么久"时，**没有它就只能猜**。所以要有一个能开的开关。
# 用法见 src/test/diag_reasoning.py。
#
# 注意这不是"上下文存储"——它只是调试快照，不影响 LLMClient 无状态这条原则。

CAPTURE_REASONING = False
_captured: list = []


def enable_reasoning_capture(on: bool = True):
    """打开/关闭推理正文抓取。"""
    global CAPTURE_REASONING
    CAPTURE_REASONING = on


def drain_reasoning() -> list:
    """取走已抓到的推理记录并清空缓冲。"""
    out = list(_captured)
    _captured.clear()
    return out


def _fmt_usage(res_data: dict) -> str:
    """
    格式化 token 用量，能取到多少记多少。

    关键指标：
      in       prompt_tokens        —— 输入规模（大 → 砍输入，但收益很小）
      out      completion_tokens    —— 输出规模（大 → 砍输出字段）
      reason   reasoning_tokens     —— **隐藏思考**，实测占输出 67~96%。
                                        这项大意味着延迟主因在"想"，
                                        砍 prompt 没用，得关思考或换模型。
      cache_hit prompt 缓存命中     —— 省钱指标，不影响延迟
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


# ===================== 客户端 =====================

DEFAULT_SYSTEM_PROMPT = "你是专业智能助手，回答简洁准确，逻辑清晰"


class LLMClient:
    """
    大模型 API 调用客户端。

    **纯单次请求，不内部存储、不维护任何对话上下文** ——
    上下文完全由调用方通过入参传入管理。
    """

    def __init__(self, layer: str = ''):
        """
        :param layer: 层名（'L1'/'L2'/'L3'/'S1'/'B1a'/'B1b'/'B2'），
                      用于查 LAYER_MODEL_OVERRIDE。空串 = 全用全局默认。
        """
        self.cfg = LayerCfg(layer)
        self.layer = layer or ''
        self.default_system_prompt = DEFAULT_SYSTEM_PROMPT

        _llm_log.info(f'LLMClient 初始化 | {self.cfg.describe()} | url={self.cfg.base_url}')

        self.headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

        # 复用连接池：省掉每次调用的 TCP+TLS 握手。
        # urllib3 的 PoolManager 线程安全，L2 的并行线程共享同一实例。
        if USE_SESSION_POOL:
            self.session = requests.Session()
            self.session.headers.update(self.headers)
        else:
            self.session = None

    # ---------- 内部：发一次请求 ----------

    def _post(self, payload: dict, label: str) -> str:
        """
        发送请求并在失败时按配置重试。

        注意日志顺序：**先 `raise_for_status()` 再记"完成"**——
        gushi3 曾把失败请求也记成完成，污染了整个延迟统计。
        """
        t0 = time.time()
        tag = f' [{label}]' if label else ''
        last_err = None

        for attempt in range(self.cfg.max_retries):
            try:
                poster = self.session.post if self.session else requests.post
                resp = poster(url=self.cfg.base_url, json=payload, timeout=self.cfg.timeout)

                resp.raise_for_status()
                res_data = resp.json()

                elapsed = time.time() - t0
                reply, reasoning = _extract_reply(res_data)
                reason_info = f' reasoning={len(reasoning)}字' if reasoning else ''
                _llm_log.info(f'{self.cfg.layer}{tag} 完成 | {elapsed:.1f}s | '
                              f'{_fmt_usage(res_data)}{reason_info}')

                if CAPTURE_REASONING and reasoning:
                    _captured.append({
                        'layer': self.cfg.layer, 'label': label,
                        'seconds': elapsed, 'usage': _fmt_usage(res_data),
                        'reasoning': reasoning, 'reply': reply,
                    })
                return reply

            except Exception as e:
                last_err = e
                elapsed = time.time() - t0
                if attempt < self.cfg.max_retries - 1 and _is_retryable(e):
                    backoff = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                    _llm_log.warn(f'{self.cfg.layer}{tag} 第{attempt + 1}次失败'
                                  f'(耗时{elapsed:.1f}s)，{backoff}s后重试：{e}')
                    time.sleep(backoff)
                else:
                    _llm_log.error(f'{self.cfg.layer}{tag} 请求失败(耗时{elapsed:.1f}s)：{e}')
                    raise

        raise RuntimeError(f'{self.cfg.layer}{tag} 请求失败：{last_err}')

    def _build_payload(self, messages: list, temperature: float = None) -> dict:
        """组装请求体。这里是 `thinking` 开关的落点。"""
        payload = {
            'model': self.cfg.model,
            'messages': messages,
        }

        temp = temperature if temperature is not None else self.cfg.temperature
        if temp is not None:
            payload['temperature'] = temp

        if not self.cfg.thinking:
            # DeepSeek 兼容端点的关思考写法（裸 requests，不需要 SDK）
            payload['thinking'] = {'type': 'disabled'}

        return payload

    # ---------- 对外 ----------

    def chat(self, user_content: str, system_prompt: Optional[str] = None,
             context_messages: Optional[List[Dict[str, str]]] = None,
             label: str = '') -> str:
        """
        发起单次对话请求。

        :param user_content: 本轮用户提问
        :param system_prompt: 本次的系统提示词；不传用全局默认
        :param context_messages: 外部传入的完整上下文列表；传入时 system_prompt 失效
        """
        if context_messages is not None:
            send_msgs = list(context_messages)
            send_msgs.append({"role": "user", "content": user_content})
        else:
            use_sys = system_prompt if system_prompt is not None else self.default_system_prompt
            send_msgs = [
                {"role": "system", "content": use_sys},
                {"role": "user", "content": user_content},
            ]

        return self._post(self._build_payload(send_msgs), label or self.layer)

    def chat_by_context(self, context: list, label: str = '', temperature: float = None) -> str:
        """
        上下文由外部维护，最新消息放列表最后。

        :param context: [{"role": "system", "content": ...}, ...]
        :param label: 调用方标识（如 'L2·叶尘'），仅用于日志
        :param temperature: 可选，覆盖本层默认温度
        """
        return self._post(self._build_payload(context, temperature), label or self.layer)


# ===================== 自检 =====================

if __name__ == '__main__':
    for name in ('', 'S1', 'L1', 'L2', 'L3', 'B1a', 'B1b', 'B2'):
        print(LayerCfg(name).describe())
