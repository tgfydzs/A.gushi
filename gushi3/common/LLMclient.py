import requests
from typing import List, Dict, Optional

# ================== 模型基础配置 ======================
# 接口密钥
api_key = 'ms-206a1a90-061a-4029-9926-948d1c626fb9'
# OpenAI兼容接口地址
base_url = 'https://api-inference.modelscope.cn/v1/chat/completions'
# 指定调用模型名称
model_name = 'Qwen/Qwen3.5-27B'
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
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.default_system_prompt = DEFAULT_SYSTEM_PROMPT

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

        try:
            resp = requests.post(
                url=self.base_url,
                headers=self.headers,
                json=payload,
                timeout=90
            )
            resp.raise_for_status()
            res_data = resp.json()
            reply = res_data["choices"][0]["message"]["content"].strip()
            return reply
        except Exception as e:
            return f"请求失败：{str(e)}"


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