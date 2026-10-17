"""
N9 导演层 — 三个独立子任务。
不再落地副作用，所有结果由调用方通过 DeferredQueue 消费。
"""

import json

from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads
from common.LLMclient import LLMClient


class N9_Director:
    """N9 导演层 — 世界的潜意识，推动暗线前进"""

    TEMPERATURE_RULE = 0.3        # N9a：规则判定 → 低温度
    TEMPERATURE_NARRATION = 0.9   # N9b：创意写作 → 高温度
    TEMPERATURE_BEHAVIOR = 0.6    # N9c：调度型 → 中温度

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N9_Director')
        self.cli = LLMClient('N9')

        # 已触发规则追踪（仅在主线程 consume 时写入）
        self.triggered_rules: dict[int, str] = {}
        self._last_rule_trigger_elapsed: int = 0

        # 环境叙事防重复
        self.recent_narrations: list[str] = []
        self.narrated_themes: set[str] = set()

    # ===================== 仅供后台 heartbeat 调用 =====================

    def run_rules(self, world_rules, world_state_list, event_summary,
                  dc_trimmed, time_str, player_name, player_loc, player_power,
                  world_knowledge=None) -> dict:
        """N9a：规则触发，返回原始LLM结果（触发节奏由心跳时间间隔控制）"""
        return self._process_rules(world_rules, world_state_list, event_summary,
                                   dc_trimmed, time_str, player_name, player_loc, player_power,
                                   world_knowledge)

    def run_narration(self, player_loc, time_str, world_state_list, event_summary) -> list:
        """N9b：环境叙事，返回原始LLM结果"""
        return self._process_narration(player_loc, time_str, world_state_list, event_summary)

    # ===================== N9a =====================

    def _process_rules(self, world_rules, world_state_list, event_summary,
                       dc_trimmed, time_str, player_name, player_loc, player_power,
                       world_knowledge=None) -> dict:
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
        if world_knowledge:
            # 秘辛（上古真相、谁握着谁的档案…）：**只在这层出现**，NPC 层永远看不到。
            user_input['世界知识（鲜有人知的底细）'] = list(world_knowledge)
        return self._llm_call(sys_prompt, user_input, self.TEMPERATURE_RULE, 'N9a')

    def _process_narration(self, player_loc, time_str, world_state_list, event_summary) -> dict:
        sys_prompt = self._load_prompt('N9b_narration_sys.txt')
        user_input = {
            '玩家当前位置': player_loc,
            '当前游戏时间': time_str,
            '当前世界状态': world_state_list,
            '最近事件摘要': event_summary,
            '最近环境叙事': self.recent_narrations[-5:],
            '已叙述过的主题': list(self.narrated_themes),
        }
        return self._llm_call(sys_prompt, user_input, self.TEMPERATURE_NARRATION, 'N9b')

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
        return self._llm_call(sys_prompt, user_input, self.TEMPERATURE_BEHAVIOR, 'N9c')

    # ===================== 公共工具 =====================

    @staticmethod
    def _load_prompt(filename: str) -> str:
        return load_prompt(filename)

    def _llm_call(self, sys_prompt: str, user_input: dict, temperature: float, label: str):
        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]
        reply = self.cli.chat_by_context(all_context, label=label, temperature=temperature)
        return safe_json_loads(reply, label=label)

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
