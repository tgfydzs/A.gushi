"""
N9 导演层 — 三个独立子任务。
不再落地副作用，所有结果由调用方通过 DeferredQueue 消费。
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

from common.log import get_logger
from common.small_tool import safe_json_loads
from common.base_cnf import BASE_PATH
from common.LLMclient import LLMClient


class N9_Director:
    """N9 导演层 — 世界的潜意识，推动暗线前进"""

    TEMPERATURE_RULE = 0.3        # N9a：规则判定 → 低温度
    TEMPERATURE_NARRATION = 0.9   # N9b：创意写作 → 高温度
    TEMPERATURE_BEHAVIOR = 0.6    # N9c：调度型 → 中温度

    MIN_RULE_INTERVAL_MINUTES = 10080  # 7天

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N9_Director')
        self.cli = LLMClient()

        # 已触发规则追踪（仅在主线程 consume 时写入）
        self.triggered_rules: dict[int, str] = {}
        self._last_rule_trigger_elapsed: int = 0

        # 环境叙事防重复
        self.recent_narrations: list[str] = []
        self.narrated_themes: set[str] = set()

    # ===================== 仅供后台 heartbeat 调用 =====================

    def run_rules(self, world_rules, world_state_list, event_summary,
                  dc_trimmed, time_str, player_name, player_loc, player_power) -> dict:
        """N9a：规则触发，返回原始LLM结果"""
        # 时间间隔检查（硬防护）
        current_elapsed = 0
        try:
            current_elapsed = int(time_str.split('·')[0]) if '·' in time_str else 0
        except Exception:
            pass
        if self._last_rule_trigger_elapsed > 0:
            if current_elapsed - self._last_rule_trigger_elapsed < self.MIN_RULE_INTERVAL_MINUTES:
                self.logger.info('N9a 规则触发跳过（时间间隔不足7天）')
                return {'规则触发': []}

        return self._process_rules(world_rules, world_state_list, event_summary,
                                   dc_trimmed, time_str, player_name, player_loc, player_power)

    def run_narration(self, player_loc, time_str, world_state_list, event_summary) -> list:
        """N9b：环境叙事，返回原始LLM结果"""
        return self._process_narration(player_loc, time_str, world_state_list, event_summary)

    def run_behavior(self, all_npc_names, event_summary, dc_trimmed,
                     npc_responses, n7_result, world_state_list) -> dict:
        """N9c：NPC行为+传播，返回原始LLM结果"""
        return self._process_behavior(all_npc_names, event_summary, dc_trimmed,
                                      npc_responses, n7_result, world_state_list)

    def run_all(self, world_rules, world_state_list, event_summary,
                dc_trimmed, time_str, player_name, player_loc, player_power,
                all_npc_names, npc_responses, n7_result) -> dict:
        """三条并行，返回合并后的原始结果，不落地副作用"""
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_a = pool.submit(
                self.run_rules,
                world_rules, world_state_list, event_summary,
                dc_trimmed, time_str, player_name, player_loc, player_power,
            )
            fut_b = pool.submit(
                self.run_narration,
                player_loc, time_str, world_state_list, event_summary,
            )
            fut_c = pool.submit(
                self.run_behavior,
                all_npc_names, event_summary, dc_trimmed,
                npc_responses, n7_result, world_state_list,
            )
            rule_result = fut_a.result()
            narration_result = fut_b.result()
            behavior_result = fut_c.result()

        return {
            '规则触发': rule_result.get('规则触发', []),
            '环境叙事': narration_result if isinstance(narration_result, list) else [],
            'NPC自主行为': behavior_result.get('NPC自主行为', {}) or {},
            '剧情级传播': behavior_result.get('剧情级传播', []) or [],
        }

    # ===================== N9a =====================

    def _process_rules(self, world_rules, world_state_list, event_summary,
                       dc_trimmed, time_str, player_name, player_loc, player_power) -> dict:
        sys_prompt = self._load_prompt('N9a_rule_sys.txt')
        user_input = {
            '世界演变规则列表': world_rules,
            '当前世界状态': world_state_list,
            '已触发规则': {str(k): v for k, v in self.triggered_rules.items()},
            '当前游戏时间': time_str,
            '玩家': {'名称': player_name, '位置': player_loc, '实力': player_power},
            '最近事件摘要': event_summary,
            '导演历史（幕后情报参考）': dc_trimmed,
        }
        return self._llm_call(sys_prompt, user_input, self.TEMPERATURE_RULE, 'N9a')

    def _process_narration(self, player_loc, time_str, world_state_list, event_summary) -> list:
        sys_prompt = self._load_prompt('N9b_narration_sys.txt')
        user_input = {
            '玩家当前位置': player_loc,
            '当前游戏时间': time_str,
            '当前世界状态': world_state_list,
            '最近事件摘要': event_summary,
            '最近环境叙事': self.recent_narrations[-5:],
            '已叙述过的主题': list(self.narrated_themes),
        }
        result = self._llm_call(sys_prompt, user_input, self.TEMPERATURE_NARRATION, 'N9b')
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, list):
                    return v
        self.logger.warn(f'N9b 返回格式异常：{type(result)}')
        return []

    def _process_behavior(self, all_npc_names, event_summary, dc_trimmed,
                          npc_responses, n7_result, world_state_list) -> dict:
        sys_prompt = self._load_prompt('N9c_behavior_sys.txt')
        user_input = {
            '全部NPC名称列表': all_npc_names,
            '最近事件摘要': event_summary,
            '导演历史（近期幕后行动和情报）': dc_trimmed,
            '本轮NPC回应摘要': [
                {'name': r.get('name', ''), 'response': (r.get('response', '') or '')[:80]}
                for r in (npc_responses or [])[:5]
            ],
            'N7传播结果': n7_result or {},
            '当前世界状态': world_state_list,
        }
        result = self._llm_call(sys_prompt, user_input, self.TEMPERATURE_BEHAVIOR, 'N9c')
        if not isinstance(result, dict):
            return {'NPC自主行为': {}, '剧情级传播': []}
        result.setdefault('NPC自主行为', {})
        result.setdefault('剧情级传播', [])
        return result

    # ===================== 公共工具 =====================

    @staticmethod
    def _load_prompt(filename: str) -> str:
        with open(os.path.join(BASE_PATH, 'prompt', filename), 'r', encoding='utf-8') as f:
            return f.read()

    def _llm_call(self, sys_prompt: str, user_input: dict, temperature: float, label: str):
        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]
        try:
            reply = self.cli.chat_by_context(all_context, label=label, temperature=temperature)
            return safe_json_loads(reply, label=label)
        except Exception as e:
            self.logger.error(f'{label} 调用失败：{e}')
            if label == 'N9a':
                return {'规则触发': []}
            elif label == 'N9b':
                return []
            else:
                return {'NPC自主行为': {}, '剧情级传播': []}

    # ===================== 序列化 =====================

    def to_dict(self) -> dict:
        return {
            'triggered_rules': {str(k): v for k, v in self.triggered_rules.items()},
            'recent_narrations': self.recent_narrations[-20:],
            'narrated_themes': list(self.narrated_themes),
            'last_rule_trigger_elapsed': self._last_rule_trigger_elapsed,
        }

    def restore(self, d: dict):
        if d:
            self.triggered_rules = {int(k): v for k, v in d.get('triggered_rules', {}).items()}
            self.recent_narrations = d.get('recent_narrations', [])[-20:]
            self.narrated_themes = set(d.get('narrated_themes', []))
            self._last_rule_trigger_elapsed = d.get('last_rule_trigger_elapsed', 0)
