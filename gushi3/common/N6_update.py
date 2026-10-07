import json

from Schema.B3_1_npc_sch import B3NPCLayer
from Schema.B4_player_sch import B4PlayerLayer
from common.log import get_logger
from common.small_tool import load_prompt, apply_npc_delta, apply_player_delta, safe_json_loads
from common.base_cnf import *
from common.LLMclient import LLMClient


class N6_Update:
    """
    N6 更新层（收尾层）
    角色：流水线最后一步，以客观视角做最终收尾——所有表都可能更新。
    限制：三层中唯一接受玩家实例的层。

    本层同时承担原 N8 的时间估算：收尾时顺带估出「本轮耗时分钟」，
    省掉一次独立的 LLM 往返（N8 已降级为纯规则聚合）。

    流水线：N45（理解→回应）→ N6（收尾 + 耗时）
    """

    # 耗时估算失败时的兜底分钟数
    DEFAULT_MINUTES = 1

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N6_Update')
        self.cli = LLMClient('N6')

    def update_for_npc(self,
                       npc_inst: B3NPCLayer,
                       player_inst: B4PlayerLayer,
                       perception_msg: str,
                       inner_thought: str = ''):
        """
        客观收尾：根据内心想法和客观条件，落实所有表更新，并估算本轮耗时。

        :param npc_inst: N45理解回应后的NPC实例（原封不动）
        :param player_inst: 玩家实例
        :param perception_msg: N3感知层分发的带方向消息
        :param inner_thought: N45产出的内心想法
        :return: (更新后的B3NPCLayer, 玩家增量dict, 旁白list, 本轮耗时分钟int)
        """
        sys_prompt = load_prompt('N6_update_sys.txt')

        all_context = [{'role': 'system', 'content': sys_prompt}]
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '感知输入': perception_msg,
            '玩家实例': player_inst.player_dict,
            '内心想法InnerThought': inner_thought,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N6_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context, label='N6')
        json_replay = safe_json_loads(reply, label='N6')

        npc_delta = json_replay.get('npc实例', {})
        player_delta = json_replay.get('玩家实例', {})

        narration = json_replay.get('旁白', []) or []
        minutes, reason = self._parse_minutes(json_replay, npc_inst.name)

        self.logger.info(f'更新结果：npc_delta={npc_delta} player_delta={player_delta} '
                         f'旁白={narration} 耗时={minutes}分钟({reason})')

        new_npc_inst = apply_npc_delta(npc_inst, npc_delta)

        return new_npc_inst, player_delta, narration, minutes

    @staticmethod
    def _parse_minutes(result: dict, label: str = '') -> tuple:
        """
        解析「本轮耗时分钟」，非法值兜底为 DEFAULT_MINUTES。
        :return: (minutes: int, reason: str)
        """
        raw = result.get('本轮耗时分钟')
        reason = str(result.get('耗时理由', '') or '')
        try:
            return max(1, int(float(raw))), reason
        except (TypeError, ValueError):
            get_logger('N6_Update').warn(f'[{label}] 耗时字段非法({raw!r})，兜底为 {N6_Update.DEFAULT_MINUTES} 分钟')
            return N6_Update.DEFAULT_MINUTES, reason

    # ===================== 玩家自主行动 =====================

    def process_player_action(self, player_inst, player_input: str,
                               game_time_str: str = '', has_move: bool = False) -> tuple:
        """
        处理玩家自主行动（无NPC参与时）。

        独处修炼、赶路、探索等行为——玩家状态变化 + 客观旁白 + 本轮耗时。

        时间跨度不再由外部传入，改为本层自行估算（原 N8 的职责）。

        :param player_inst: B4PlayerLayer 玩家实例
        :param player_input: 玩家原始输入
        :param game_time_str: 游戏时间字符串
        :param has_move: 本轮是否含移动（移动耗时由系统按距离单独算，避免重复计）
        :return: (player_delta, narration, minutes)
        """
        user_input = {
            '玩家名称': player_inst.player_dict.get('玩家名称name', '未知'),
            '玩家当前状态': player_inst.player_dict.get('当前状态CurrentState', {}),
            '玩家持有物': player_inst.player_dict.get('持有物Inventory', {}),
            '玩家描述': player_inst.player_dict.get('描述Description', []),
            '玩家输入': player_input,
            '本轮含移动': bool(has_move),
            '游戏时间': game_time_str,
        }

        sys_prompt = load_prompt('N6_player_sys.txt')

        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]

        reply = self.cli.chat_by_context(all_context, label='N6-Player')
        result = safe_json_loads(reply, label='N6-Player')

        player_delta = result.get('玩家状态变更', {})
        narration = result.get('旁白', []) or []
        minutes, reason = self._parse_minutes(result, 'N6-Player')

        self.logger.info(f'玩家自主更新：delta={player_delta} 旁白={narration} 耗时={minutes}分钟({reason})')
        return player_delta, narration, minutes
