"""
N9 导演层 — 世界暗线推动、环境叙事、NPC自主行为调度、剧情级传播。

在每轮末尾（N7+N8完成后）调用一次LLM，判断：
1. 世界演变规则是否应该触发
2. 生成环境叙事（旁白）
3. NPC幕后行为
4. 跨地区/跨势力的剧情级信息传播

不负责：玩家动作判断、NPC对玩家回应、移动时间计算（这些是N2-N8的职责）。
"""

import json
import os

from common.log import get_logger
from common.small_tool import safe_json_loads, resolve_npc_name
from common.base_cnf import BASE_PATH
from common.LLMclient import LLMClient


class N9_Director:
    """N9 导演层 — 世界的潜意识，推动暗线前进"""

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N9_Director')
        self.cli = LLMClient()

        # 已触发规则追踪：{规则序号: 当前进度}
        # 例如 {0: "初期", 2: "发展中"}
        self.triggered_rules: dict[int, str] = {}

        # 最近环境叙事（精确文本，防逐字重复）
        self.recent_narrations: list[str] = []

        # 已叙述主题关键词（防同主题变体重复，如"电视新闻播斗殴"出来多次）
        self.narrated_themes: set[str] = set()

    # ===================== 主入口 =====================

    def process(self,
                world_layer,          # B1WorldLayer
                game_clock,           # GameClock
                player_inst,          # B4PlayerLayer
                event_context: list,
                director_context: list,
                npc_responses: list,
                all_npc_names: list,
                n7_result: dict = None) -> dict:
        """
        导演层主入口。

        :param world_layer: B1世界层（含规则、状态、地点）
        :param game_clock: 游戏时钟
        :param player_inst: 玩家实例
        :param event_context: 事件上下文（最近几轮）
        :param director_context: 导演历史上下文（前几轮N9产出的幕后信息）
        :param npc_responses: 本轮NPC回应列表 [{"name": str, "response": str}]
        :param all_npc_names: 全部NPC名称列表（防幻觉）
        :param n7_result: N7传播层结果 {npc_name: [rumor_strings]}
        :return: {
            "规则触发": [...],
            "环境叙事": [...],
            "NPC自主行为": {...},
            "剧情级传播": [...]
        }
        """
        with open(os.path.join(BASE_PATH, r'prompt\N9_director_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        # 组装输入
        user_input = {
            '世界演变规则列表': world_layer.dict.get('世界演变规则列表', []),
            '当前世界状态': world_layer.dict.get('世界状态列表', []),
            '已触发规则': {str(k): v for k, v in self.triggered_rules.items()},
            '当前游戏时间': game_clock.now_str(),
            '玩家': {
                '名称': player_inst.player_dict.get('玩家名称name', '未知'),
                '位置': player_inst.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', '未知'),
                '实力': player_inst.player_dict.get('当前状态CurrentState', {}).get('实力等级', '未知'),
            },
            '最近事件摘要': event_context[-5:] if len(event_context) > 5 else event_context,
            '导演历史（前几轮幕后变化）': director_context[-3:] if director_context else [],
            '本轮NPC回应摘要': [
                {'name': r.get('name', ''), 'response': (r.get('response', '') or '')[:80]}
                for r in (npc_responses or [])[:5]
            ],
            'N7传播结果': n7_result or {},
            '全部NPC名称列表': all_npc_names,
            '最近环境叙事': self.recent_narrations[-5:],
            '已叙述过的主题': list(self.narrated_themes),
        }

        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]

        self.logger.debug(f'N9 导演层输入：{json.dumps(user_input, ensure_ascii=False)[:500]}')

        reply = self.cli.chat_by_context(all_context, label='N9')
        result = safe_json_loads(reply, label='N9')

        # ========== 代码层防幻觉 & 后处理 ==========

        # 1. 过滤规则触发：只保留"世界演变规则列表"中存在的规则
        valid_rules = world_layer.dict.get('世界演变规则列表', [])
        filtered_triggers = []
        for t in result.get('规则触发', []) or []:
            rule_text = t.get('规则原文', '')
            if rule_text in valid_rules:
                rule_idx = valid_rules.index(rule_text)
                if rule_idx not in self.triggered_rules:
                    self.triggered_rules[rule_idx] = t.get('进度', '初期')
                    filtered_triggers.append(t)
                    self.logger.info(f'N9 触发规则 [{rule_idx}]：{rule_text[:60]}... 进度={t.get("进度", "初期")}')
                else:
                    self.logger.warn(f'N9 规则 [{rule_idx}] 已触发过，跳过重复触发')
            else:
                self.logger.warn(f'N9 返回了不在规则列表中的规则，已过滤：{rule_text[:80]}')

        result['规则触发'] = filtered_triggers

        # 2. 过滤NPC自主行为：子串匹配NPC名称
        npc_actions = result.get('NPC自主行为', {}) or {}
        filtered_actions = {}
        for npc_name, action in npc_actions.items():
            resolved = resolve_npc_name(npc_name, all_npc_names)
            if resolved:
                filtered_actions[resolved] = action
            else:
                self.logger.warn(f'N9 返回了不存在的NPC [{npc_name}]，已过滤')
        result['NPC自主行为'] = filtered_actions

        # 3. 过滤剧情级传播中的NPC名称（子串匹配）
        for spread in (result.get('剧情级传播', []) or []):
            raw_targets = spread.get('到达NPC', []) or []
            resolved_targets = []
            for n in raw_targets:
                resolved = resolve_npc_name(n, all_npc_names)
                if resolved:
                    resolved_targets.append(resolved)
                else:
                    self.logger.warn(f'N9 剧情传播包含不存在的NPC [{n}]，已过滤')
            spread['到达NPC'] = resolved_targets

        # 4. 更新最近环境叙事缓存 + 主题追踪
        narrations = result.get('环境叙事', []) or []
        self.recent_narrations.extend(narrations)
        if len(self.recent_narrations) > 20:
            self.recent_narrations = self.recent_narrations[-20:]
        # 提取首句关键词作为主题（取前15字，用于后续防重复提示LLM）
        for n in narrations:
            self.narrated_themes.add(n[:15])
        if len(self.narrated_themes) > 50:
            # 保留最近25条的主题
            self.narrated_themes = set(list(self.narrated_themes)[-25:])

        self.logger.info(
            f'N9 导演层结果：触发规则={len(filtered_triggers)}条 '
            f'环境叙事={len(narrations)}条 '
            f'NPC行为={len(filtered_actions)}个 '
            f'传播={len(result.get("剧情级传播", []) or [])}条'
        )

        return result

    # ===================== 序列化（存档用） =====================

    def to_dict(self) -> dict:
        """导出触发状态（存档时使用）"""
        return {
            'triggered_rules': {str(k): v for k, v in self.triggered_rules.items()},
            'recent_narrations': self.recent_narrations[-20:],
            'narrated_themes': list(self.narrated_themes),
        }

    def restore(self, d: dict):
        """从存档恢复触发状态"""
        if d:
            self.triggered_rules = {int(k): v for k, v in d.get('triggered_rules', {}).items()}
            self.recent_narrations = d.get('recent_narrations', [])[-20:]
            self.narrated_themes = set(d.get('narrated_themes', []))
