"""
N9 导演层 — 拆分为三个独立并行子任务：
  N9a 规则触发（低温度，严谨推理）
  N9b 环境叙事（高温度，创意写作）
  N9c NPC行为+剧情传播（中温度，调度型）

三个子任务互不依赖，并行执行，总耗时≈最慢子任务。
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

from common.log import get_logger
from common.small_tool import safe_json_loads, resolve_npc_name, apply_npc_delta, strip_empty_delta
from common.base_cnf import BASE_PATH
from common.LLMclient import LLMClient


class N9_Director:
    """N9 导演层 — 世界的潜意识，推动暗线前进"""

    TEMPERATURE_RULE = 0.3        # N9a：规则判定 → 低温度，高确定性
    TEMPERATURE_NARRATION = 0.9   # N9b：创意写作 → 高温度，多样性
    TEMPERATURE_BEHAVIOR = 0.6    # N9c：调度型 → 中温度

    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N9_Director')
        self.cli = LLMClient()

        # 已触发规则追踪：{规则序号: 当前进度}
        self.triggered_rules: dict[int, str] = {}

        # 最近环境叙事（精确文本，防逐字重复）
        self.recent_narrations: list[str] = []

        # 已叙述主题关键词（防同主题变体重复）
        self.narrated_themes: set[str] = set()

    # ===================== 主入口 =====================

    def process(self,
                game_controller,
                event_context: list,
                npc_responses: list,
                n7_result: dict = None):
        """
        导演层主入口 — 三条并行 → 合并校验 → 直接落地副作用。

        :param game_controller: GameController 实例（N9通过它读写世界/NPC/上下文）
        :param event_context: 事件上下文列表
        :param npc_responses: 本轮NPC回应列表
        :param n7_result: N7传播结果
        """
        gc = game_controller
        world_layer = gc.b1WorldLayer
        game_clock = gc.game_clock
        player_inst = gc.player
        all_npc_names = [n.name for n in gc.all_npc_list]

        world_rules = world_layer.dict.get('世界演变规则列表', [])
        world_state_list = world_layer.dict.get('世界状态列表', [])
        player_loc = player_inst.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', '未知')
        player_name = player_inst.player_dict.get('玩家名称name', '未知')
        player_power = player_inst.player_dict.get('当前状态CurrentState', {}).get('实力等级', '未知')

        event_summary = event_context[-5:] if len(event_context) > 5 else (event_context or [])
        dc_trimmed = gc.director_context.context[-3:] if gc.director_context.context else []

        # ---- 并行执行三个子任务 ----
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_a = pool.submit(
                self._process_rules,
                world_rules, world_state_list, event_summary,
                dc_trimmed, game_clock.now_str(),
                player_name, player_loc, player_power,
            )
            fut_b = pool.submit(
                self._process_narration,
                player_loc, game_clock.now_str(),
                world_state_list, event_summary,
            )
            fut_c = pool.submit(
                self._process_behavior,
                all_npc_names, event_summary, dc_trimmed,
                npc_responses, n7_result, world_state_list,
            )

            rule_result = fut_a.result()
            narration_result = fut_b.result()
            behavior_result = fut_c.result()

        # ========== 校验 + 落地副作用 ==========

        # ---- N9a 规则触发 → 写世界状态 + 打印 + 上下文 ----
        valid_rules = world_rules
        for t in (rule_result.get('规则触发', []) or []):
            rule_text = t.get('规则原文', '')
            if rule_text not in valid_rules:
                self.logger.warn(f'N9a 返回了不在规则列表中的规则，已过滤：{rule_text[:80]}')
                continue
            rule_idx = valid_rules.index(rule_text)
            if rule_idx in self.triggered_rules:
                self.logger.warn(f'N9a 规则 [{rule_idx}] 已触发过，跳过重复触发')
                continue

            self.triggered_rules[rule_idx] = t.get('进度', '初期')
            self.logger.info(f'N9a 触发规则 [{rule_idx}]：{rule_text[:60]}... 进度={t.get("进度", "初期")}')

            # 世界状态变更
            state_changes = t.get('世界状态变更', {}) or {}
            for new_state in (state_changes.get('增加', []) or []):
                if new_state and new_state not in world_layer.dict.get('世界状态列表', []):
                    world_layer.dict['世界状态列表'].append(new_state)
                    self.logger.info(f'🌍 世界状态新增：{new_state}')
            for old_state in (state_changes.get('删减', []) or []):
                state_list = world_layer.dict.get('世界状态列表', [])
                if old_state and old_state in state_list:
                    state_list.remove(old_state)
                    self.logger.info(f'🌍 世界状态移除：{old_state}')

            # 可见性：玩家可见 → 打印+global；幕后 → director_context
            progress = t.get('进度', '')
            public_view = t.get('玩家可见叙述', '')[:300]
            behind_view = t.get('幕后详情', '')[:300]
            if public_view and public_view.strip() and public_view != '暂无公开迹象':
                print(f'\n🌍 [世界事件 · {progress}] {public_view}')
                gc.add_context_all(f'世界事件（{progress}）：{public_view}')
                self.logger.info(f'🌍 世界事件公开：{public_view[:120]}')
            if behind_view:
                gc.add_director_context(f'世界事件幕后（{progress}）：{behind_view}')
                self.logger.info(f'🌍 幕后详情：{behind_view[:120]}')

        # ---- N9b 环境叙事 → 更新缓存 + 打印 + 全局上下文 ----
        narrations = narration_result if isinstance(narration_result, list) else []
        self.recent_narrations.extend(narrations)
        if len(self.recent_narrations) > 20:
            self.recent_narrations = self.recent_narrations[-20:]
        for n in narrations:
            self.narrated_themes.add(n[:15])
            if n:
                print(f'  \033[2m· {n}\033[0m')
                gc.add_context_all(f'旁白：{n}')
        if len(self.narrated_themes) > 50:
            self.narrated_themes = set(list(self.narrated_themes)[-25:])

        # ---- N9c NPC行为+传播 → 校验名称 → NPC增量 + 写observation + director_context ----
        npc_actions = behavior_result.get('NPC自主行为', {}) or {}
        # 兼容旧格式：str → {"动作": str}
        for npc_name in list(npc_actions.keys()):
            val = npc_actions[npc_name]
            if isinstance(val, str):
                npc_actions[npc_name] = {'动作': val}

        for npc_name, action_data in npc_actions.items():
            resolved = resolve_npc_name(npc_name, all_npc_names)
            if not resolved:
                self.logger.warn(f'N9c 返回了不存在的NPC [{npc_name}]，已过滤')
                continue
            action_text = action_data.get('动作', '') or ''
            npc_delta = action_data.get('NPC增量')

            npc = gc.all_npc_list.get_npc(resolved)
            if npc is None:
                continue

            # 应用NPC增量（位置变更、记忆添加等统一走apply_npc_delta）
            if npc_delta:
                npc_delta = strip_empty_delta(npc_delta)
                if npc_delta:
                    updated_npc = apply_npc_delta(npc, npc_delta)
                    gc.sync_npc_to_lists(updated_npc)
                    npc = updated_npc
                    self.logger.info(f'📦 [{resolved}] 增量应用：{npc_delta}')

            # 观察记录（增量可能已经包含了observation，自动同步无需重复写）
            if action_text:
                obs_entry = f'（幕后行动）{action_text}'
                if obs_entry not in npc.observation:
                    npc.observation.append(obs_entry)

            gc.add_director_context(f'幕后：{resolved} — {action_text}')
            self.logger.info(f'🎬 [{resolved}] 幕后行动：{action_text}')

        for spread in (behavior_result.get('剧情级传播', []) or []):
            info = spread.get('信息', '')
            method = spread.get('方式', '')
            raw_targets = spread.get('到达NPC', []) or []
            resolved_targets = []
            for n in raw_targets:
                resolved = resolve_npc_name(n, all_npc_names)
                if resolved:
                    resolved_targets.append(resolved)
                else:
                    self.logger.warn(f'N9c 剧情传播包含不存在的NPC [{n}]，已过滤')
            if info:
                gc.add_director_context(f'情报（{method}）：{info} → {resolved_targets}')
                self.logger.info(f'📡 情报（{method}）：{info} → {resolved_targets}')
                for npc_name in resolved_targets:
                    npc = gc.all_npc_list.get_npc(npc_name)
                    if npc:
                        obs_entry = f'（情报·{method}）{info}'
                        if obs_entry not in npc.observation:
                            npc.observation.append(obs_entry)

        self.logger.info(
            f'N9 并行结果：规则触发={sum(1 for _ in (rule_result.get("规则触发", []) or []))}条 '
            f'环境叙事={len(narrations)}条 '
            f'NPC行为={len(npc_actions)}个 '
            f'传播={len(behavior_result.get("剧情级传播", []) or [])}条'
        )

        # 简化摘要返回（主循环仅用于打时间）
        return {
            '规则触发数': len([t for t in (rule_result.get('规则触发', []) or []) if t.get('规则原文', '') in world_rules]),
            '环境叙事数': len(narrations),
            'NPC行为数': len(npc_actions),
            '传播数': len(behavior_result.get('剧情级传播', []) or []),
        }

    # ===================== N9a：规则触发 =====================

    def _process_rules(self, world_rules, world_state_list, event_summary,
                       dc_trimmed, time_str, player_name, player_loc, player_power) -> dict:
        """N9a：低温度，纯规则引擎模式"""
        sys_prompt = self._load_prompt('N9a_rule_sys.txt')

        user_input = {
            '世界演变规则列表': world_rules,
            '当前世界状态': world_state_list,
            '已触发规则': {str(k): v for k, v in self.triggered_rules.items()},
            '当前游戏时间': time_str,
            '玩家': {
                '名称': player_name,
                '位置': player_loc,
                '实力': player_power,
            },
            '最近事件摘要': event_summary,
            '导演历史（幕后情报参考）': dc_trimmed,
        }

        return self._llm_call(sys_prompt, user_input, self.TEMPERATURE_RULE, 'N9a')

    # ===================== N9b：环境叙事 =====================

    def _process_narration(self, player_loc, time_str,
                           world_state_list, event_summary) -> list:
        """N9b：高温度，纯创意写作模式"""
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

        # 确保返回list
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            # LLM偶尔会包成dict，尝试兜底
            for v in result.values():
                if isinstance(v, list):
                    return v
        self.logger.warn(f'N9b 返回格式异常，已重置为空：{type(result)}')
        return []

    # ===================== N9c：NPC行为+传播 =====================

    def _process_behavior(self, all_npc_names, event_summary, dc_trimmed,
                          npc_responses, n7_result, world_state_list) -> dict:
        """N9c：中温度，NPC调度+情报传播"""
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

        # 确保输出字段完整
        if not isinstance(result, dict):
            self.logger.warn(f'N9c 返回格式异常，已重置为空：{type(result)}')
            return {'NPC自主行为': {}, '剧情级传播': []}
        result.setdefault('NPC自主行为', {})
        result.setdefault('剧情级传播', [])
        return result

    # ===================== 公共工具 =====================

    @staticmethod
    def _load_prompt(filename: str) -> str:
        """读取 prompt 文件"""
        path = os.path.join(BASE_PATH, 'prompt', filename)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def _llm_call(self, sys_prompt: str, user_input: dict, temperature: float, label: str):
        """执行一次LLM调用，统一异常处理"""
        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]
        self.logger.debug(f'{label} 输入：{json.dumps(user_input, ensure_ascii=False)[:300]}')

        try:
            reply = self.cli.chat_by_context(all_context, label=label, temperature=temperature)
            return safe_json_loads(reply, label=label)
        except Exception as e:
            self.logger.error(f'{label} 调用失败：{e}')
            # 返回安全的空值，不中断整轮
            if label == 'N9a':
                return {'规则触发': []}
            elif label == 'N9b':
                return []
            else:
                return {'NPC自主行为': {}, '剧情级传播': []}

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
