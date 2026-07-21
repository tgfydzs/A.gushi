import json
import re

from Schema.B3_1_npc_sch import B3NPCLayer
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient


class N5_Response:
    """
    N5 回应层
    角色：结合NPC内部所有表进行主观推断，生成回应和内心想法。
    关键约束：本层不修改NPC实例，只产出 response_text + inner_thought。
    限制：不接受玩家实例，防止隐藏事实暴露。
    """

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N5_Response')
        self.cli = LLMClient()

    def response_say(self, npc_inst: B3NPCLayer, perception_msg: str,
                     context_history: list = None):
        """
        生成NPC回应和内心想法。

        :param npc_inst: N4推理后的NPC实例（本层不改它）
        :param perception_msg: N3感知层分发的带方向消息
        :param context_history: 全局对话上下文
        :return: (response_text: str, inner_thought: str)
        """
        sys_prompt = load_prompt('N5_response_sys.txt')

        all_context = [{'role': 'system', 'content': sys_prompt}]
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '对话上下文': context_history if context_history else [],
            '感知输入': perception_msg,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N5_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context, label='N5')

        try:
            json_replay = safe_json_loads(reply, label='N5')
            response_text = json_replay.get('回应Response', '')
            inner_thought = json_replay.get('内心想法InnerThought', '')
        except Exception:
            # 兜底：LLM 返回了纯文本而非 JSON，尝试正则提取
            self.logger.warn(f'N5 JSON解析失败，尝试纯文本兜底提取')
            response_text, inner_thought = self._parse_plain_text(reply)

        # 代码层兜底：空回应重试一次
        if not response_text.strip():
            self.logger.warn(f'N5 返回空回应，追加约束重试一次')
            all_context.append({
                'role': 'user',
                'content': '你刚刚输出了空回应。你必须说至少一句话。哪怕只是"……"表示沉默、或一句呵斥如"聒噪"。不能输出空字符串。请重新输出完整JSON。'
            })
            try:
                retry_reply = self.cli.chat_by_context(all_context, label='N5-Retry')
                retry_json = safe_json_loads(retry_reply, label='N5-Retry')
                response_text = retry_json.get('回应Response', '') or response_text
                inner_thought = retry_json.get('内心想法InnerThought', '') or inner_thought
            except Exception as e:
                self.logger.error(f'N5 重试也失败了：{e}')

        self.logger.info(f'回应：{response_text} | 内心想法：{inner_thought}')
        return response_text.strip(), inner_thought.strip()

    @staticmethod
    def _parse_plain_text(text: str):
        """
        兜底解析 LLM 返回的纯文本，提取回应和内心想法。
        期望格式：
            称呼：回应内容
            内心想法：xxx
        或：
            称呼：回应内容\n\n内心想法：xxx
        """
        # 尝试匹配 "内心想法" 分割
        m = re.split(r'[（(]?内心想法[）)]?\s*[：:]\s*', text, maxsplit=1)
        if len(m) == 2:
            response_text = m[0].strip()
            inner_thought = m[1].strip()
        else:
            # 没有内心想法标记，整段当作回应
            response_text = text.strip()
            inner_thought = ''
        return response_text, inner_thought

