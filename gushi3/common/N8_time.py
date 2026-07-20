"""
N8 时间层 — 统一LLM判断每轮输入后的游戏时间跨度。

不再做任何硬性公式计算（距离段数×系数等），所有时间估算全部交给LLM，
读玩家输入 + 上下文 + 可选移动信息，统一判断。

流水线位置：N6 完成后，与 N7 并行调用。
"""

import json
import os

from common.log import get_logger
from common.small_tool import safe_json_loads
from common.base_cnf import BASE_PATH
from common.LLMclient import LLMClient


class N8_Time:
    """
    N8 时间层 — 单一路径：全部LLM判断

    无论移动还是对话，都走 estimate_time()，把完整输入喂给LLM。
    """

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N8_Time')
        self.cli = LLMClient()

    # ===================== 统一LLM耗时估算 =====================

    def estimate_time(self,
                      player_input: str,
                      event_context: list,
                      npc_responses: list,
                      move_info: dict = None) -> int:
        """
        LLM 统一判断本轮耗时分钟数。

        :param player_input: 玩家本轮原始输入（含全部上下文）
        :param event_context: 事件对话上下文
        :param npc_responses: NPC回应列表
        :param move_info: 可选，{"from": "出发地", "to": "目的地"} 表示有位置移动
        :return: 整数分钟
        """
        summary = {
            '玩家输入': player_input,
            '对话上下文': event_context[-3:] if len(event_context) > 3 else event_context,
            'NPC回应摘要': [r.get('response', '')[:80] for r in npc_responses[:3]],
        }
        if move_info and move_info.get('from') and move_info.get('to'):
            summary['移动'] = {
                '从': move_info['from'],
                '到': move_info['to'],
            }

        with open(os.path.join(BASE_PATH, r'prompt\N8_time_ability_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(summary, ensure_ascii=False)},
        ]

        try:
            reply = self.cli.chat_by_context(all_context, label='N8')
            result = safe_json_loads(reply, label='N8')
            minutes = int(result.get('elapsed_minutes', 5))
            minutes = max(1, minutes)
            reason = result.get('reason', '')
        except Exception:
            self.logger.warn('N8 耗时估算LLM失败，回退5分钟')
            minutes = 5
            reason = '（估算失败，使用默认值）'

        loc_info = ''
        if move_info:
            loc_info = f' {move_info["from"]} → {move_info["to"]} |'
        self.logger.info(f'[LLM] 耗时{loc_info} → {minutes}分钟 | {reason}')
        return minutes

    # ===================== 统一入口 =====================

    def process(self,
                game_clock,
                player_input: str = '',
                event_context: list = None,
                npc_responses: list = None,
                move_info: dict = None) -> float:
        """
        统一入口，全部走LLM判断。

        :param game_clock: 游戏时钟
        :param player_input: 玩家本轮原始输入
        :param event_context: 事件上下文
        :param npc_responses: NPC回应列表
        :param move_info: 可选，{"from", "to"}；None→无移动
        :return: 耗时分钟数
        """
        minutes = self.estimate_time(
            player_input=player_input,
            event_context=event_context or [],
            npc_responses=npc_responses or [],
            move_info=move_info,
        )

        game_clock.advance(round(minutes))
        self.logger.info(f'⏰ 游戏时间 → {game_clock.now_str()}（+{minutes:.0f}分钟）')
        return minutes
