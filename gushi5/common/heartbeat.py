"""
世界心跳管理器 — 后台线程运行，不阻塞主循环。

职责：
1. N9a 规则触发（全局一次）
2. N9b 环境叙事（全局一次）
3. 规则过滤+关联分组+N9c并行（每组一次）
4. 全部产出推入 DeferredQueue

触发条件（满足任一）：
- 游戏时间累计推进 ≥ `心跳间隔_小时`（默认 12 小时；来源 `world_layer` 配置）
- 连续 ≥ `心跳_最少轮次`（默认 8）轮没触发过

⚠️ **轮次兜底不能省**：只有时间条件时，只要时间推进得慢（时辰制下正常对话一轮
才 2~5 分钟），后台就永远等不到 12 小时。实测一整档 93 轮游戏时间只走了 213 分钟，
`⏰ 心跳#` 一条都没出现——N9a/N9b/N9c 三路**一次都没跑过**，世界不会自己演进。
这条兜底原本就写在模块说明里（"主循环每完成3轮"），但代码里从来没实现过。
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads
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

    关键词**不写死**：区域名与"区域→地点关键词"在首次触发时从地图派生
        （见 `_ensure_regions`）。确实需要覆盖时才显式传：
        hb = HeartbeatManager(q, n9, state_region_kw=[...], region_keywords={...})
    """

    TIME_INTERVAL_HOURS = 12

    #: 轮次兜底：连续这么多轮没触发过心跳就强制一次。
    #: 没有它，时间推进慢的世界（时辰制下每轮才几分钟）后台永远跑不起来。
    ROUND_FALLBACK = 8

    # ⚠️ 这里原来有两张写死的表：`DEFAULT_STATE_REGION_KW`（东域/天道宗/落霞坪…）和
    # `DEFAULT_REGION_KEYWORDS`（东域→[天道宗, 星辰阁…]）。那是**把这个世界当成引擎常识**：
    # 换个世界，`_filter_candidates` 一个区域都匹配不上（N9c 的"区域相关"理由全失效），
    # `_group_by_region` 会把所有 NPC 倒进"其他"一个桶。
    # 现在区域名与关键词**全部从地图派生**（见 `_ensure_regions`）：地图本来就是地理的真源。

    def __init__(self, deferred_queue: DeferredQueue, n9_director, logger=None,
                 state_region_kw=None, region_keywords=None):
        self.logger = logger if logger else get_logger('Heartbeat')
        self.queue = deferred_queue
        self.n9 = n9_director
        self.cli = LLMClient('N9c')

        self._last_trigger_elapsed_minutes = 0
        self._last_trigger_round = 0
        self._heartbeat_count = 0
        self._npc_last_hb: dict[str, int] = {}

        # 区域名与关键词。显式给了就用给的，否则首次触发时从地图派生一次。
        self.state_region_kw = list(state_region_kw or [])
        self.region_keywords = dict(region_keywords or {})
        self._regions_ready = bool(self.state_region_kw and self.region_keywords)

    # ===================== 区域信息（从地图派生，不写死） =====================

    def _ensure_regions(self, world_layer):
        """
        区域名 / 区域→地点关键词 从**地图**派生一次。

        规则不含世界观知识：
          · 区域 = `is_region()` 为真、且**路径层数最小**的那些节点（= 一级区域，如东域）
          · 关键词 = 该区域子树里的全部地块名
        派生不出来（世界没有区域概念）→ 只告警一次，之后所有 NPC 归到"其他"。
        """
        if getattr(self, '_regions_ready', False) or world_layer is None:
            return
        self._regions_ready = True
        try:
            paths = world_layer.place_paths() or {}
            regions = {n: p for n, p in paths.items() if world_layer.is_region(n)}
            # 排除最外层（大陆/世界根）——它的路径层数最小
            if regions:
                min_depth = min(len(str(p).split('·')) for p in regions.values())
                tops = {n: p for n, p in regions.items()
                        if len(str(p).split('·')) == min_depth + 1}
                for name, path in tops.items():
                    self.state_region_kw.append(name)
                    self.region_keywords[name] = [
                        n for n, p in paths.items()
                        if p == path or str(p).startswith(str(path) + '·')]
        except Exception as e:
            self.logger.warn(f'区域信息派生失败：{type(e).__name__}: {e}')

        if self.state_region_kw:
            self.logger.info(f'🗺 区域信息来自地图：{len(self.state_region_kw)} 个区域'
                             f'（{"、".join(self.state_region_kw[:6])}）')
        else:
            # 不静默：没有区域概念的世界，N9c 只能把所有人当一个桶
            self.logger.warn('地图里没有"区域"层，N9c 将把所有 NPC 归到同一组'
                             '（世界配置可用 state_region_kw / region_keywords 显式给出）')

    # ===================== 主入口 =====================

    def try_trigger(self, game_clock, world_layer, all_npc_list,
                    director_context: list, world_state_list: list,
                    event_context: list, npc_responses: list,
                    player_input: str, player_inst, n7_result: dict = None,
                    round_no: int = 0) -> bool:
        """检查触发条件，满足则触发一轮心跳。"""
        # 区域信息第一次触发时从地图派生（不在构造期，因为构造期还没有世界层）
        self._ensure_regions(world_layer)
        elapsed = getattr(game_clock, '_elapsed', 0)
        reason = None

        # 条件一：游戏时间推进够了
        # 从世界配置读取心跳间隔，默认12小时
        interval_hours = world_layer.get_config('心跳间隔_小时', self.TIME_INTERVAL_HOURS) if world_layer else self.TIME_INTERVAL_HOURS
        try:
            interval_hours = float(interval_hours)
        except (TypeError, ValueError):
            interval_hours = self.TIME_INTERVAL_HOURS
        if elapsed - self._last_trigger_elapsed_minutes >= interval_hours * 60:
            reason = f'游戏时间推进≥{interval_hours:g}小时'

        # 条件二：轮次兜底（时间推得慢的世界靠这条，否则后台永远不跑）
        if reason is None and round_no:
            fallback = world_layer.get_config('心跳_最少轮次', self.ROUND_FALLBACK) if world_layer else self.ROUND_FALLBACK
            try:
                fallback = int(fallback)
            except (TypeError, ValueError):
                fallback = self.ROUND_FALLBACK
            gap = round_no - self._last_trigger_round
            if fallback > 0 and gap >= fallback:
                reason = f'连续 {gap} 轮没触发过（时间只推进了 {elapsed - self._last_trigger_elapsed_minutes} 分钟）'

        if reason is None:
            return False

        self._last_trigger_elapsed_minutes = elapsed
        self._last_trigger_round = round_no
        self._heartbeat_count += 1
        self.logger.info(f'⏰ 心跳#{self._heartbeat_count}：{reason}')

        import threading
        t = threading.Thread(
            target=self._heartbeat_worker,
            args=(reason, all_npc_list, world_state_list, director_context,
                  event_context, npc_responses, player_input, player_inst,
                  n7_result or {}, game_clock, world_layer),
            daemon=True,
        )
        t.start()
        return True

    # ===================== 后台工作线程 =====================

    def _heartbeat_worker(self, reason: str, all_npc_list, world_state_list: list,
                          director_context: list, event_context: list,
                          npc_responses: list, player_input: str,
                          player_inst, n7_result: dict,
                          game_clock=None, world_layer=None):
        """后台线程：N9a+N9b+N9c 全部在此完成，结果推入队列。"""
        try:
            # 组装共享输入
            player_name = player_inst.player_dict.get('玩家名称name', '未知')
            player_loc = player_inst.player_dict.get('当前状态CurrentState', {}).get('当前所处位置', '未知')
            player_power = player_inst.player_dict.get('当前状态CurrentState', {}).get('实力等级', '未知')
            event_summary = event_context[-5:] if len(event_context) > 5 else (event_context or [])
            dc_trimmed = director_context[-3:] if director_context else []
            all_npc_names = [n.name for n in (all_npc_list.npc_list if hasattr(all_npc_list, 'npc_list') else all_npc_list)]
            time_str = game_clock.now_str() if game_clock else '?'
            world_rules = world_layer.dict.get('世界演变规则列表', []) if world_layer else []
            # `世界知识列表` 是**秘辛**（上古大战真相、谁掌握谁的档案…）：只给后台导演，
            # 绝不喂 NPC 层，否则就是信息泄漏（见 AGENTS.md §二）。
            world_knowledge = world_layer.dict.get('世界知识列表', []) if world_layer else []

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
                fut = pool.submit(
                    self.n9.run_rules,
                    world_rules, world_state_list, event_summary,
                    dc_trimmed, time_str, player_name, player_loc, player_power,
                    world_knowledge,
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
                        narrs = result.get('旁白', []) if isinstance(result, dict) else []
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
            for kw in self.state_region_kw:
                if kw in s:
                    state_regions.add(kw)

        npc_iter = all_npc_list.npc_list if hasattr(all_npc_list, 'npc_list') else all_npc_list
        for npc in npc_iter:
            reasons = []
            name = npc.name
            npc_loc = npc.current_state.get('当前所处位置', '')

            if npc.goals:
                last_hb = self._npc_last_hb.get(name, 0)
                if last_hb == 0:
                    reasons.append('目标推进（新NPC）')
                elif self._heartbeat_count - last_hb >= 2:
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
                self._npc_last_hb[name] = self._heartbeat_count

        return candidates

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
            for region, keywords in self.region_keywords.items():
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

        sys_prompt = load_prompt('N9c_behavior_sys.txt')

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
            result.setdefault('剧情级传播', [])
            return result
        except Exception as e:
            self.logger.error(f'N9c 组{idx} LLM失败：{e}')
            return None
