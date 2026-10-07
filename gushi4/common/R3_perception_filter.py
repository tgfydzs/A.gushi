"""
R3 感知校验（纯规则，**只做防幻觉与结构兜底**，不调用 LLM）

# 它现在不做什么了

R3 原来还有一道「按 scope + 段数算谁有权感知」的过滤（`_allowed_set`）。
**那道过滤已经删掉。**

理由：L1 已经在 `感知分发{名: 内容}` 里**直接回答了"谁听得到"**。
R3 再拿 `scope` 枚举和 `NPC_附近距离_段` 算一遍，是在**给 LLM 的答案做二次猜疑**——
而那个段数模型恰恰是错的：

| 段数模型假设 | 现实 |
|-------------|------|
| 感知范围 = 距玩家 N 段以内 | 修仙世界里修为高的人一声长啸能传到**相邻地段**（兄弟节点，段数模型够不着） |
| 同一地段内机会均等 | 都市里隔一堵墙就听不见 |

范围这种事**没法用几十个键值对描述**。所以判断权交回 L1，本层不再复核。

# 它现在做什么

| 保留的校验 | 为什么这不是"判断" |
|-----------|------------------|
| 名字防幻觉 | 丢弃名册里不存在的 NPC 名——纯正确性，不涉及叙事 |
| hidden 清空 | 执行 **L1 自己声明的**"无人感知"。是兑现它的声明，不是替它拿主意 |
| involved 漏感知补齐 | L1 自己把这人列进了名单却没给他写感知——补的是**它已声明要给的** |

补的内容是事实文本（玩家原话），不含叙事创作。

用法分两段：

    R3.candidates(...)   →  喂给 L1 的候选池（**只是提示**，L1 仍可点任何人）
    L1 产出 感知分发{...}
    R3.enforce(...)      →  防幻觉 + 执行声明
"""

from Schema.Event_sch import Scope
from Schema.B3_npclist_sch import B3NPCListLayer
from common.log import get_logger

logger = get_logger('R3_Filter')


class R3_PerceptionFilter:
    """感知硬约束：位置 · scope · involved"""

    def __init__(self, world_layer=None):
        self.world_layer = world_layer

    def set_world(self, world_layer):
        self.world_layer = world_layer

    # ===================== 工具 =====================

    def _near_segments(self) -> int:
        if self.world_layer is None:
            return 3
        return self.world_layer.get_config('NPC_附近距离_段', 3)

    # ===================== 第一段：候选池 =====================

    def candidates(self, player_location: str, all_npc_list: B3NPCListLayer,
                   involved: list = None, extra_names: list = None) -> list:
        """
        产出"可能感知到本轮事件"的 NPC 候选池，喂给 L1。

        **这一步不看 scope** —— scope 是 L1 这一轮才判定的，此时还不存在。
        它只做位置过滤：太远的人根本不该出现在 L1 的输入里，
        否则 L1 每轮都要为几十个无关 NPC 判断一次"他能不能听到"。

        但 `involved`（上一轮锁定的人）**无条件进候选**，
        否则会出现"上一轮说过话的人，这一轮因为走远了就突然断线"。

        :param player_location: 玩家当前位置
        :param all_npc_list: 全部 NPC
        :param involved: 当前事件锁定的 NPC 名（上轮的，来自 EventLayer）
        :param extra_names: 额外必须进候选的名字（如 S1/L1 显式点名的）
        :return: [{'名字','位置','距离','在场'}, ...]
        """
        near = self._near_segments()
        involved_set = set(involved or [])
        extra_set = set(extra_names or [])

        pool = []
        for npc in all_npc_list:
            npc_loc = npc.current_state.get('当前所处位置', '')
            if not npc_loc:
                continue

            forced = npc.name in involved_set or npc.name in extra_set
            try:
                d = self.world_layer.location_distance(player_location, npc_loc) \
                    if self.world_layer else 0
            except Exception as e:
                logger.warn(f'距离计算失败 [{npc.name}]：{e}')
                d = 0

            if forced or d <= near:
                pool.append({
                    '名字': npc.name,
                    '位置': npc_loc,
                    '距离': f'{d}段',
                    '在场': '是' if d == 0 else ('附近' if d <= near else '锁定'),
                })

        logger.debug(f'R3 候选池：{len(pool)}/{len(all_npc_list.npc_list)} '
                     f'（锁定{len(involved_set)}，附近阈值{near}段）')
        return pool

    # ===================== 第二段：校验产出 =====================

    def enforce(self, perceptions: dict, scope: Scope, involved: list,
                direct_target: list = None, player_location: str = '',
                all_npc_list=None, fallback_text: str = '') -> dict:
        """
        对 L1 产出的 `感知分发{名: 失真感知}` 做**防幻觉 + 声明兑现**。

        **不再按 scope/距离过滤。** L1 写谁就是谁——它已经在 `感知分发` 里
        回答了"谁听得到"，本层不替它复核（理由见模块 docstring）。

        :param perceptions: L1 产出，{NPC名: 感知文本}
        :param scope: L1 声明的事件可见性（**只用来执行 hidden 声明**）
        :param involved: L1 声明的名单（漏写感知的会被补齐）
        :param direct_target: **已不再使用**，保留仅为兼容调用点
        :param player_location: **已不再使用**，同上
        :param all_npc_list: 全部 NPC（用于名字校验）
        :param fallback_text: 补漏时用的事实文本（通常是玩家原话）
        """
        perceptions = dict(perceptions or {})
        involved = [n for n in (involved or []) if n]
        if all_npc_list is None:
            logger.warn('R3：未传 all_npc_list，跳过名字校验')
            return perceptions
        valid_names = {n.name for n in all_npc_list.npc_list}

        # ---- 1. hidden：执行 L1 自己声明的"无人感知" ----
        # 这是兑现它的声明，不是替它拿主意：说了 hidden 又写感知，是它自相矛盾。
        if scope == Scope.HIDDEN:
            if perceptions:
                logger.info(f'R3：L1 声明 scope=hidden，按其声明丢弃 {len(perceptions)} 份感知')
            return {}

        # ---- 2. 名字防幻觉：名册里没有的一律丢弃 ----
        dropped_unknown = [k for k in perceptions if k not in valid_names]
        for k in dropped_unknown:
            perceptions.pop(k, None)
        if dropped_unknown:
            logger.warn(f'R3：丢弃不存在的 NPC 名 {dropped_unknown}')

        # ---- 3. involved 漏感知补齐：兑现 L1 自己的名单 ----
        # 它把人列进了名单却没写感知，等于让这人这一轮"瞎了"、剧情断在这里。
        # 补的是事实文本（玩家原话），不含叙事创作。
        missing = [n for n in involved if n not in perceptions and n in valid_names]
        if missing and fallback_text:
            for nm in missing:
                perceptions[nm] = fallback_text
            logger.warn(f'R3：involved 漏感知 {missing}，已用事实文本补齐')

        return perceptions

    # ===================== 兼容 gushi2 的命名 =====================

    def is_hard_constraint(self, scope: Scope, npc_name: str, involved: list,
                           direct_target: list) -> bool:
        """
        单点查询：某个 NPC 对某个 scope 的事件是否**必然**能感知（无需距离判定）。
        供 L1 组织输入、以及将来别的调用点复用。
        """
        if scope == Scope.HIDDEN:
            return False
        if npc_name in (involved or []):
            return True
        if scope == Scope.PRIVATE:
            return npc_name in (direct_target or [])
        return False
