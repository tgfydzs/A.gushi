"""
N8 时间层 — 判断每轮输入后的游戏时间跨度。

两种模式：
1. 规则计算（移动模式）：距离段数 × 基准分钟 × 能力系数 × 方式系数
2. LLM 自动判断（常规模式）：轻量调用，读本轮上下文和NPC回应估算耗时

流水线位置：N6 完成后，与 N7 并行调用。
"""

import json
import os

from common.log import get_logger
from common.small_tool import safe_json_loads
from common.base_cnf import BASE_PATH
from common.LLMclient import LLMClient
from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B4_player_sch import B4PlayerLayer
from common.game_clock import GameClock


# ===================== 移动计算参数 =====================

# 每段距离基准耗时（分钟），普通人步行
BASE_MINUTES_PER_SEGMENT = 15

# 常规移动方式 → 方式系数（纯物理，不涉及能力）
METHOD_COEFF = {
    '步行': 1.00,
    '跑步': 0.50,
    '骑电动车': 0.40,
    '公共交通': 0.30,
}

# 移动方式选项（终端交互使用，"异能移动"不在规则系数表里，走LLM判断）
MOVE_METHODS = ['步行', '跑步', '骑电动车', '公共交通', '异能移动']

# 默认移动方式
DEFAULT_METHOD = '步行'


class N8_Time:
    """
    N8 时间层

    三种模式：
    - 规则模式：常规移动（步行/跑步/骑车/公交），纯物理公式计算
    - 异能移动LLM模式：玩家用能力跑路，LLM读上下文判断意图和耗时
    - 对话LLM模式：常规轮次，LLM估算对话耗时
    """

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N8_Time')
        self.cli = LLMClient()

    # ===================== 规则模式：常规移动 =====================

    def estimate_move(self,
                      world_layer: B1WorldLayer,
                      player_inst,
                      from_loc: str,
                      to_loc: str,
                      method: str = '步行') -> float:
        """
        纯规则计算常规移动耗时（步行/跑步/骑车/公交）。

        :param world_layer: 世界地点树
        :param player_inst: B4PlayerLayer 玩家实例
        :param from_loc: 出发地字符串
        :param to_loc: 目的地字符串
        :param method: 移动方式
        :return: 耗时分钟数（浮点）
        """
        segments = world_layer.location_distance(from_loc, to_loc)
        if segments == 0:
            return 0.0

        method_coeff = METHOD_COEFF.get(method, 1.0)
        minutes = segments * BASE_MINUTES_PER_SEGMENT * method_coeff

        self.logger.info(
            f'[规则] 移动耗时 | {from_loc} → {to_loc} | '
            f'段数={segments} 方式={method}(×{method_coeff}) | '
            f'耗时={minutes:.1f}分钟'
        )
        return minutes

    # ===================== LLM 模式：异能移动 =====================

    def estimate_ability_move(self,
                              world_layer: B1WorldLayer,
                              player_inst,
                              from_loc: str,
                              to_loc: str,
                              event_context: list,
                              player_input: str = '') -> float:
        """
        LLM 判断异能移动耗时。

        异能移动不是固定速度——玩家可能：
        - 逃命时全力冲刺（极快，但有暴露风险）
        - 想在人前显圣（故意放慢，让大家看清是他在用能力）
        - 隐藏身份时压着跑（介于普通和全速之间）
        - 赶时间但不想太引人注目（收着用）

        :param world_layer: 世界地点树
        :param player_inst: 玩家实例（取实力等级）
        :param from_loc: 出发地
        :param to_loc: 目的地
        :param event_context: 本轮事件上下文
        :param player_input: 玩家本轮原始输入
        :return: 耗时分钟数
        """
        segments = world_layer.location_distance(from_loc, to_loc)
        ability = player_inst.player_dict.get('当前状态CurrentState', {}).get('实力等级', '未知')
        base_hours = segments * BASE_MINUTES_PER_SEGMENT / 60.0  # 普通人步行小时数

        summary = {
            '玩家实力': ability,
            '出发地': from_loc,
            '目的地': to_loc,
            '普通人步行耗时参考': f'{base_hours:.1f}小时（{segments}段×15分钟）',
            '异能移动理论极快': f'{base_hours * 10:.0f}分钟（能力者全速，但高空/高速有目击风险）',
            '本轮对话': event_context[-3:] if len(event_context) > 3 else event_context,
            '玩家当前输入': player_input,
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
            minutes = float(result.get('elapsed_minutes', base_hours * 60 * 0.02))
            reason = result.get('reason', '')
            # 下限：普通人步行时间的 2%（霸主全速的物理极限感）
            minutes = max(base_hours * 60 * 0.02, min(minutes, base_hours * 60))
        except Exception:
            self.logger.warn('N8 异能移动LLM失败，回退默认（普通人步行×0.02）')
            minutes = base_hours * 60 * 0.02
            reason = '（估算失败，使用默认值）'

        self.logger.info(
            f'[LLM-异能] 移动耗时 | {from_loc} → {to_loc} | '
            f'段数={segments} 实力={ability} | '
            f'耗时={minutes:.1f}分钟 | {reason}'
        )
        return minutes

    # ===================== LLM 模式：常规对话 =====================

    def estimate_auto(self,
                      event_context: list,
                      npc_responses: list,
                      minutes_since_prev: int = 5) -> int:
        """
        LLM 读取本轮上下文和NPC回应，估算对话过了几分钟。

        :param event_context: 本轮事件上下文
        :param npc_responses: NPC回应列表
        :param minutes_since_prev: 上轮距今分钟数（回退值）
        :return: 整数分钟
        """
        summary = {
            '对话': event_context[-3:] if len(event_context) > 3 else event_context,
            'NPC回应': [r.get('response', '')[:80] for r in npc_responses[:3]],
        }

        with open(os.path.join(BASE_PATH, r'prompt\N8_time_dialogue_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(summary, ensure_ascii=False)},
        ]

        try:
            reply = self.cli.chat_by_context(all_context, label='N8')
            result = safe_json_loads(reply, label='N8')
            minutes = int(result.get('elapsed_minutes', minutes_since_prev))
            # 玩家可能描述长时间行为（打坐修炼、长途跋涉等），不设上限
            minutes = max(1, minutes)
        except Exception:
            self.logger.warn('N8 LLM估算失败，回退默认值')
            minutes = minutes_since_prev

        self.logger.info(f'[LLM] 自动估算耗时 → {minutes}分钟')
        return minutes

    # ===================== 统一入口 =====================

    def process(self,
                game_clock: GameClock,
                world_layer: B1WorldLayer = None,
                player_inst=None,
                move_info: dict = None,
                event_context: list = None,
                npc_responses: list = None,
                player_input: str = '') -> float:
        """
        统一入口，根据移动方式分派模式。

        :param game_clock: 游戏时钟
        :param world_layer: B1世界层
        :param player_inst: 玩家实例
        :param move_info: 移动信息 {"from", "to", "method"}；None→对话模式
        :param event_context: 事件上下文
        :param npc_responses: NPC回应列表
        :param player_input: 玩家本轮原始输入（异能移动需要）
        :return: 耗时分钟数
        """
        if move_info and move_info.get('from') and move_info.get('to'):
            method = move_info.get('method', DEFAULT_METHOD)

            if method == '异能移动':
                # ---- LLM 模式：异能移动 ----
                minutes = self.estimate_ability_move(
                    world_layer, player_inst,
                    move_info['from'], move_info['to'],
                    event_context or [],
                    player_input,
                )
            else:
                # ---- 规则模式：常规移动 ----
                minutes = self.estimate_move(
                    world_layer, player_inst,
                    move_info['from'], move_info['to'],
                    method,
                )
        else:
            # ---- LLM 模式：常规对话 ----
            minutes = self.estimate_auto(
                event_context or [],
                npc_responses or [],
            )

        game_clock.advance(round(minutes))
        self.logger.info(f'⏰ 游戏时间 → {game_clock.now_str()}（+{minutes:.0f}分钟）')
        return minutes
