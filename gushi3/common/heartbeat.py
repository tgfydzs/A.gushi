"""
世界心跳管理器 — 后台线程运行，不阻塞主循环。

职责：
1. N9a 规则触发（全局一次）
2. N9b 环境叙事（全局一次）
3. 规则过滤+关联分组+N9c并行（每组一次）
4. 全部产出推入 DeferredQueue

触发条件：
- 主循环每完成3轮
- 游戏时间累计推进 ≥ 12小时
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.log import get_logger
from common.small_tool import safe_json_loads
from common.base_cnf import BASE_PATH
from common.LLMclient import LLMClient
from common.deferred_queue import DeferredQueue

logger = get_logger("Heartbeat")


class HeartbeatManager:
    """
    世界心跳管理器。

    使用方式：
        hb = HeartbeatManager(deferred_queue, n9_director)
        hb.try_trigger(round_count, game_clock, ...)
    """

    ROUND_INTERVAL = 3
    TIME_INTERVAL_HOURS = 12

    def __init__(self, deferred_queue: DeferredQueue, n9_director, logger=None):
        self.logger = logger if logger else get_logger('Heartbeat')
        self.queue = deferred_queue
        self.n9 = n9_director  # N9_Director 实例（只读调用 run_x 方法）
        self.cli = LLMClient()

        self._last_trigger_round = 0
        self._last_trigger_elapsed_minutes = 0
        self._npc_last_tick: dict[str, int] = {}

    # ===================== 主入口 =====================

    def try_trigger(self, current_round: int, game_clock, world_layer, all_npc_list,
                    director_context: list, world_state_list: list,
                    event_context: list, npc_responses: list,
                    player_input: str, player_inst, n7_result: dict = None) -> bool:
        """检查触发条件，满足则触发一轮心跳。"""
        elapsed = getattr(game_clock, '_elapsed', 0)
        reason = None

        if current_round - self._last_trigger_round >= self.ROUND_INTERVAL:
            reason = f'已过{self.ROUND_INTERVAL}轮'
        elif elapsed - self._last_trigger_elapsed_minutes >= self.TIME_INTERVAL_HOURS * 60:
            reason = f'游戏时间推进≥{self.TIME_INTERVAL_HOURS}小时'

        if reason is None:
            return False

        self._last_trigger_round = current_round
        self._last_trigger_elapsed_minutes = elapsed
        self.logger.info(f'⏰ 心跳触发：{reason}（轮次{current_round}）')

        import threading
        t = threading.Thread(
            target=self._heartbeat_worker,
            args=(reason, all_npc_list, world_state_list, director_context,
                  event_context, npc_responses, player_input, player_inst, n7_result or {}),
            daemon=True,
        )
        t.start()
        return True

    # ===================== 后台工作线程 =====================

    def _heartbeat_worker(self, reason: str, all_npc_list, world_state_list: list,
                          director_context: list, event_context: list,
                          npc_responses: list, player_input: str,
                          player_inst, n7_result: dict):
        """后台线程：N9a+N9b+N9c 全部在此完成，结果推入队列。"""
        try:
            # 组装共享输入
            player_name = player_inst.player_dict.get('玩家名称name', '未知')
            player_loc = player_inst.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', '未知')
            player_power = player_inst.player_dict.get('当前状态CurrentState', {}).get('实力等级', '未知')
            event_summary = event_context[-5:] if len(event_context) > 5 else (event_context or [])
            dc_trimmed = director_context[-3:] if director_context else []
            all_npc_names = [n.name for n in (all_npc_list.npc_list if hasattr(all_npc_list, 'npc_list') else all_npc_list)]
            time_str = getattr(getattr(player_inst, '_game_clock', None), 'now_str', lambda: '?')()

            # ---- ① NPC规则过滤 + 关联分组 ----
            candidates = self._filter_candidates(all_npc_list, world_state_list, n7_result)
            groups = self._group_candidates(candidates) if candidates else []
            if candidates:
                self.logger.info(f'心跳：{len(candidates)} 候审NPC → {len(groups)} 组')

            # ---- ② 并行执行 N9a + N9b + N9c 各组 ----
            # 限制后台并发，避免和主线程抢API额度
            BG_MAX_WORKERS = min(3, 2 + len(groups))
            with ThreadPoolExecutor(max_workers=BG_MAX_WORKERS) as pool:
                futures = {}

                # N9a 规则触发（全局一次）
                if world_state_list:
                    fut = pool.submit(
                        self.n9.run_rules,
                        world_state_list, world_state_list, event_summary,
                        dc_trimmed, time_str, player_name, player_loc, player_power,
                    )
                    futures[fut] = ('N9a', None)

                # N9b 环境叙事（全局一次）
                fut = pool.submit(
                    self.n9.run_narration,
                    player_loc, time_str, world_state_list, event_summary,
                )
                futures[fut] = ('N9b', None)

                # N9c 每组分一组
                for i, g in enumerate(groups):
                    fut = pool.submit(self._n9c_for_group, g, i)
                    futures[fut] = ('N9c', i)

                # 收集结果，推入队列
                for future in as_completed(futures):
                    ftype, idx = futures[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        self.logger.error(f'N9{ftype} 组{idx} 异常：{e}')
                        continue

                    if ftype == 'N9a' and result:
                        self.queue.put({
                            'source': 'heartbeat',
                            'trigger_reason': reason,
                            'data_type': 'N9a',
                            'N9a_规则触发': result.get('规则触发', []),
                        })
                    elif ftype == 'N9b' and result:
                        narrs = result if isinstance(result, list) else []
                        self.queue.put({
                            'source': 'heartbeat',
                            'trigger_reason': reason,
                            'data_type': 'N9b',
                            'N9b_环境叙事': narrs,
                        })
                    elif ftype == 'N9c' and result:
                        self.queue.put({
                            'source': 'heartbeat',
                            'trigger_reason': reason,
                            'data_type': 'N9c',
                            'NPC自主行为': result.get('NPC自主行为', {}),
                            '剧情级传播': result.get('剧情级传播', []),
                        })

            self.logger.info(f'心跳完成：已入队 N9a+N9b+{len(groups)}组N9c')

        except Exception as e:
            self.logger.error(f'心跳异常：{e}', exc_info=True)

    # ===================== ① 规则过滤器 =====================

    def _filter_candidates(self, all_npc_list, world_state_list: list, n7_result: dict) -> list:
        candidates = []
        state_regions = set()
        for s in (world_state_list or []):
            for kw in ['旧城区', '新城区', '港口区', '工业区', '商业区', '城中村',
                        '散修联盟', '万魔殿', '天道宗', '黑市', '市场', '城北']:
                if kw in s:
                    state_regions.add(kw)

        npc_iter = all_npc_list.npc_list if hasattr(all_npc_list, 'npc_list') else all_npc_list
        for npc in npc_iter:
            reasons = []
            name = npc.name
            npc_loc = npc.current_state.get('当前所处位置', '')

            if npc.goals:
                last_tick = self._npc_last_tick.get(name, 0)
                if last_tick == 0:
                    reasons.append('目标推进（新NPC）')
                elif last_tick < self._last_trigger_round - 5:
                    reasons.append('目标推进')

            if npc.pending_reasoning:
                reasons.append('待推理')

            if world_state_list:
                for region in state_regions:
                    if region in npc_loc:
                        reasons.append('区域事件')
                        break

            if n7_result and name in n7_result:
                reasons.append('新情报')

            if reasons:
                candidates.append({'name': name, 'npc': npc, 'reasons': reasons})
                self._npc_last_tick[name] = self._last_trigger_round

        return candidates

    _REGION_KEYWORDS = {
        '旧城区': ['旧城区', '城中村'],
        '新城区': ['新城区', '商业区'],
        '港口区': ['港口区', '工业区'],
        '城北': ['城北'],
        '散修联盟': ['散修联盟'],
        '万魔殿': ['万魔殿', '魔道'],
        '天道宗': ['天道宗', '正道'],
        '黑市': ['黑市', '地下'],
    }

    def _group_candidates(self, candidates: list) -> list[list]:
        if not candidates:
            return []
        region_event = [c for c in candidates if '区域事件' in c['reasons']]
        goal_npcs = [c for c in candidates if '目标推进' in c['reasons']]
        other_npcs = [c for c in candidates if c not in region_event and c not in goal_npcs]
        groups = []
        if region_event:
            groups.extend(self._split_by_location(region_event))
        if goal_npcs:
            groups.extend(self._split_by_location(goal_npcs))
        for c in other_npcs:
            groups.append([c])
        return groups

    def _split_by_location(self, npc_list: list, max_size: int = 5) -> list[list]:
        if not npc_list:
            return []
        buckets: dict[str, list] = {}
        for c in npc_list:
            loc = (c['npc'].current_state.get('当前所处位置', '')) if hasattr(c['npc'], 'current_state') else ''
            assigned = False
            for region, keywords in self._REGION_KEYWORDS.items():
                if any(k in loc for k in keywords):
                    buckets.setdefault(region, []).append(c)
                    assigned = True
                    break
            if not assigned:
                buckets.setdefault('其他', []).append(c)
        groups = []
        for bucket in buckets.values():
            for i in range(0, len(bucket), max_size):
                groups.append(bucket[i:i + max_size])
        return groups

    # ===================== N9c 单组调用 =====================

    def _n9c_for_group(self, group: list, idx: int) -> dict | None:
        group_data = []
        for c in group:
            npc_dict = c['npc'].to_dict()
            npc_dict['待处理原因'] = '、'.join(c['reasons'])
            group_data.append(npc_dict)

        with open(os.path.join(BASE_PATH, r'prompt\N9c_behavior_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        locations = [n.get('当前状态CurrentState', {}).get('当前所处位置', '') for n in group_data]
        shared_context = {
            '这批NPC': group_data,
            '导演历史相关情报': [],
            '共同区域': '、'.join(set(locations)),
        }

        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(shared_context, ensure_ascii=False)},
        ]

        try:
            reply = self.cli.chat_by_context(all_context, label=f'N9c-组{idx}')
            result = safe_json_loads(reply, label=f'N9c-组{idx}')
            npc_actions = result.get('NPC自主行为', {}) or {}
            for name in list(npc_actions.keys()):
                if isinstance(npc_actions[name], str):
                    npc_actions[name] = {'动作': npc_actions[name]}
            result['NPC自主行为'] = npc_actions
            result.setdefault('剧情级传播', [])
            return result
        except Exception as e:
            self.logger.error(f'N9c 组{idx} LLM失败：{e}')
            return None
