"""
后台（心跳触发，独立线程）

    世界线 B1a 规则触发（温度 0.3，严谨因果链） ‖ B1b 环境叙事（温度 0.9，创意写作）
    角色线 B2  幕后行为 + 剧情级传播（与 L2 **同域同契约**）

# B1a 与 B1b 为什么不合并

它们**并行跑，合并不省任何延迟**，只会让 0.3 和 0.9 的温度需求互相打架。
这不是信息过滤问题，是收益问题（设计方案 D9）。

# B2 与 L2 的关系

**同一套输出 Schema 和落盘代码**，prompt 分两份（在场需要"回应"，幕后不需要）。
这是"职能整合"的落点——不是把两个层合并成一次调用，
而是让**同一个层在不同触发时机复用同一份契约**。

# 并发限制

后台线程池比主链小（`BACKGROUND_MAX_WORKERS`），避免和主线程抢 API 额度。
心跳跑得慢没关系——它是背景，反正不阻塞玩家。
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.base_cnf import BACKGROUND_MAX_WORKERS, MEMORY_CONSOLIDATION_ENABLED
from common.LLMclient import LLMClient
from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads, pick_field

logger = get_logger('Background')


class BackgroundManager:
    """
    心跳管理器。使用方式：

        bg = BackgroundManager(deferred_queue, l2_character, memory_manager)
        bg.try_trigger(game_clock=..., world_layer=..., all_npc_list=..., ...)
    """

    TIME_INTERVAL_HOURS = 12
    GROUP_MAX_SIZE = 5          # 一组最多几个 NPC（一次调用里塞太多会稀释注意力）

    def __init__(self, deferred_queue, l2_character, memory_manager=None):
        self.queue = deferred_queue
        self.l2 = l2_character
        self.memory = memory_manager

        self.cli_rule = LLMClient('B1a')
        self.cli_narr = LLMClient('B1b')

        self._last_trigger_elapsed = 0
        self.heartbeat_count = 0
        self._npc_last_hb: dict = {}

    # ===================== 触发 =====================

    def try_trigger(self, game_clock, world_layer, all_npc_list,
                    director_context=None, world_state_list=None,
                    event_context=None, l2_results=None, player_input='',
                    player=None, propagation_result=None) -> bool:
        """检查触发条件，满足则**另起线程**跑一轮心跳（立即返回，不阻塞主循环）。"""
        elapsed = getattr(game_clock, '_elapsed', 0) if game_clock else 0
        interval_h = world_layer.get_config('心跳间隔_小时', self.TIME_INTERVAL_HOURS) \
            if world_layer else self.TIME_INTERVAL_HOURS

        if elapsed - self._last_trigger_elapsed < interval_h * 60:
            return False

        self._last_trigger_elapsed = elapsed
        self.heartbeat_count += 1
        logger.info(f'⏰ 心跳#{self.heartbeat_count}（游戏时间推进 ≥{interval_h}小时）')

        threading.Thread(
            target=self._worker,
            args=(all_npc_list, world_layer, game_clock,
                  list(director_context or []), list(world_state_list or []),
                  list(event_context or []), list(l2_results or []),
                  player_input, player, dict(propagation_result or {})),
            daemon=True,
            name=f'heartbeat-{self.heartbeat_count}',
        ).start()
        return True

    # ===================== 工作线程 =====================

    def _worker(self, all_npc_list, world_layer, game_clock, director_context,
                world_state_list, event_context, l2_results, player_input,
                player, propagation_result):
        try:
            time_str = game_clock.now_str() if game_clock else '?'
            world_rules = world_layer.dict.get('世界演变规则列表', []) if world_layer else []
            event_summary = event_context[-5:]
            dc_trimmed = director_context[-5:]

            # ---- ① 筛出"该被推演"的 NPC，并按地点分组 ----
            candidates = self._filter_candidates(all_npc_list, world_state_list,
                                                 propagation_result)
            groups = self._group_by_location(candidates)
            if candidates:
                logger.info(f'心跳：{len(candidates)} 个候选 NPC → {len(groups)} 组')

            # ---- ② 并行跑 B1a ‖ B1b ‖ B2各分组 ----
            workers = min(BACKGROUND_MAX_WORKERS, 2 + len(groups))
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futures = {}

                futures[pool.submit(self._run_rules, world_rules, world_state_list,
                                    event_summary, dc_trimmed, time_str,
                                    player, player_input)] = ('B1a', None)
                futures[pool.submit(self._run_narration, player, time_str,
                                    world_state_list, event_summary)] = ('B1b', None)
                for i, g in enumerate(groups):
                    futures[pool.submit(self._run_group, g, i, world_state_list,
                                        dc_trimmed, l2_results)] = ('B2', i)

                for fut in as_completed(futures):
                    ftype, idx = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as e:
                        logger.error(f'{ftype} 组{idx} 异常：{type(e).__name__}: {e}')
                        continue
                    if result:
                        self.queue.put(result)

            logger.info(f'心跳#{self.heartbeat_count} 完成：已入队 B1a + B1b + {len(groups)}组B2')

            # ---- ③ 记忆巩固（同一线程顺带跑，不额外开线程）----
            if MEMORY_CONSOLIDATION_ENABLED and self.memory is not None:
                try:
                    self.memory.run_round(all_npc_list)
                except Exception as e:
                    logger.error(f'记忆巩固异常：{e}')

        except Exception as e:
            logger.error(f'心跳异常：{type(e).__name__}: {e}')

    # ===================== B1a 规则触发 =====================

    def _run_rules(self, world_rules, world_state_list, event_context,
                   director_context, time_str, player, player_input) -> dict:
        sys_prompt = load_prompt('B1a_rule_sys.txt')
        pd = player.player_dict if hasattr(player, 'player_dict') else (player or {})
        st = pd.get('当前状态CurrentState', {})
        payload = {
            '世界演变规则列表': world_rules,
            '当前世界状态': world_state_list,
            '最近事件': event_context,
            '幕后情报': director_context,
            '当前时间': time_str,
            '玩家': {
                '名称': pd.get('玩家名称name', ''),
                '位置': st.get('当前所处位置', ''),
                '实力': st.get('实力等级', ''),
            },
            '玩家最近输入': player_input,
        }
        reply = self.cli_rule.chat_by_context(
            [{'role': 'system', 'content': sys_prompt},
             {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            label='B1a')
        data = safe_json_loads(reply, label='B1a') or {}
        rules = pick_field(data, '规则触发', '规则', default=[]) or []
        if not isinstance(rules, list) or not rules:
            return None
        return {
            'source': 'heartbeat',
            'data_type': 'B1a',
            'B1a_规则触发': rules,
        }

    # ===================== B1b 环境叙事 =====================

    def _run_narration(self, player, time_str, world_state_list,
                       event_context) -> dict:
        sys_prompt = load_prompt('B1b_narration_sys.txt')
        pd = player.player_dict if hasattr(player, 'player_dict') else (player or {})
        payload = {
            '当前位置': pd.get('当前状态CurrentState', {}).get('当前所处位置', ''),
            '当前时间': time_str,
            '世界状态': world_state_list,
            '最近事件': event_context,
        }
        reply = self.cli_narr.chat_by_context(
            [{'role': 'system', 'content': sys_prompt},
             {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            label='B1b')
        data = safe_json_loads(reply, label='B1b') or {}
        narrs = pick_field(data, '旁白', default=[]) or []
        if isinstance(narrs, str):
            narrs = [narrs]
        narrs = [n for n in narrs if isinstance(n, str) and n.strip()]
        if not narrs:
            return None
        return {
            'source': 'heartbeat',
            'data_type': 'B1b',
            'B1b_环境叙事': narrs,
        }

    # ===================== B2 幕后行为（复用 L2 契约） =====================

    def _run_group(self, group: list, idx: int, world_state_list,
                   director_context, l2_results) -> dict:
        """
        一组 NPC 的幕后推演。

        **逐个调用 L2 的幕后模式**（而不是一次调用塞整组）——
        因为 L2 的契约是"一次调用只看得到一个 NPC 的表"，
        把整组塞进一次调用就等于让它们互相看到了不该看的表。
        """
        onstage = [r['name'] for r in (l2_results or [])]
        actions, spreads = {}, []

        for npc in group:
            try:
                res = self.l2.act_offstage(
                    npc,
                    world_state=world_state_list,
                    director_context=director_context,
                    onstage_names=onstage,
                )
            except Exception as e:
                logger.error(f'B2 [{npc.name}] 失败：{type(e).__name__}: {e}')
                continue

            action_text = (res.get('回应Response') or '').strip()
            if not action_text:
                continue

            actions[npc.name] = {
                '动作': action_text,
                'NPC增量': res.get('npc_instance') or {},
                '内心想法': res.get('内心想法InnerThought', ''),
                '动作意图': res.get('动作意图', {}),
            }

            for sp in (pick_field(res.get('raw') or {}, '剧情级传播', default=[]) or []):
                if isinstance(sp, dict) and pick_field(sp, '信息', default=''):
                    spreads.append(sp)

        if not actions and not spreads:
            return None
        return {
            'source': 'heartbeat',
            'data_type': 'B2',
            'NPC自主行为': actions,
            '剧情级传播': spreads,
        }

    # ===================== ① 候选筛选 =====================

    def _filter_candidates(self, all_npc_list, world_state_list, propagation_result) -> list:
        """
        谁该被后台推演？

        判据（任一命中即可）：
          - 有未完成的目标，且距上次推演 ≥2 次心跳（别每轮都推同一个人）
          - 有待解释事件（心里有事没想通）
          - 刚收到新情报（R1 传播到了他）
        """
        candidates = []
        for npc in all_npc_list:
            reasons = []
            last = self._npc_last_hb.get(npc.name, 0)

            if npc.goals and (last == 0 or self.heartbeat_count - last >= 2):
                reasons.append('目标推进')
            if npc.pending_reasoning:
                reasons.append('待推理')
            if propagation_result and npc.name in propagation_result:
                reasons.append('新情报')

            if reasons:
                candidates.append(npc)
                self._npc_last_hb[npc.name] = self.heartbeat_count
        return candidates

    def _group_by_location(self, candidates: list) -> list:
        """
        按**地点前缀**分组。

        gushi3 这里硬编码了一张"东域/西域/南域…"的关键词表——换了世界观就失效。
        改成直接取每个 NPC 位置的前两段做桶：同一块地方的归一起，
        不需要任何世界观专属配置。
        """
        buckets: dict = {}
        for npc in candidates:
            loc = (npc.current_state or {}).get('当前所处位置', '')
            key = '·'.join(loc.split('·')[:2]) or '未知'
            buckets.setdefault(key, []).append(npc)

        groups = []
        for bucket in buckets.values():
            for i in range(0, len(bucket), self.GROUP_MAX_SIZE):
                groups.append(bucket[i:i + self.GROUP_MAX_SIZE])
        return groups
