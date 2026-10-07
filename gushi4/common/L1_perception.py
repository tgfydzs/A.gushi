"""
L1 感知过滤层（世界域，1×/轮）

**这是"信息过滤"本体。** 合并了 gushi3 的 N2（事件层）+ N3（感知层），
并把 N3 从"分发消息"升级为**产出经过失真的感知**。

    决定：这轮发生了什么 · 谁在场 · **每个NPC感知到什么**（失真后）

# 为什么能合并

N2 与 N3 的输入信息集本就相同（事件上下文 + 全NPC名单 + 玩家位置 + 附近NPC），
N3 只多一个"附近NPC的档案"。合并没有把任何受限信息喂给不该看的层——
符合设计原则第 9 条「合并层级按信息集判定」。

# ⚠️ L1 必须拿玩家的**真实**实力

这点最容易搞反，但它是硬要求：**不知道真值就没法失真。**

要让 L1 写出"低阶修士看化神期只觉得深不可测"，它就必须知道对方真是化神期。
所以 L1 **持有真值**，但**输出里只有失真后的版本**——真值不落进 `感知分发`。

**这不是泄漏**，因为它不产出任何自由文本形式的 NPC 内容（没有对白、没有内心想法）。
它产出的 `感知分发` 是**代码可逐条检查的结构化文本**，且内容本身就是失真的。

# 关于「附近NPC 的实力/身份」

ℹ️ 设计方案 §3.2 的输入表只写了 `附近NPC{名,位,距离}`，但**失真的判定需要观察者自身的
修为**——"看不透"这件事只有相对某个观察者才成立。这与 L1 需要玩家真值是同一个道理：
不知道观察者的深浅，就写不出"他能不能看透你"。

因此候选池里带了 `实力` 和 `身份` 两个字段。它们取自 NPC 的 `当前状态CurrentState`
——**这与 L3 拿的"客观侧"是同一类切片**（客观可观察，非主观心理），
不涉及记忆/信念/好感度/待解释这些真正属于角色域的表。

# 硬约束归 R3

位置、scope 可见性、involved 必感知，全部由 R3 用代码执行。
L1 只做**软判断**：谁参与了、事件多公开、每个人注意到什么、措辞。
产出后由 `R3.enforce()` 校验：**写多了删、写少了补**。
"""

import json

from Schema.Event_sch import Scope
from common.base_cnf import L1_THINKING_RETRY_ENABLED
from common.LLMclient import LLMClient
from common.R3_perception_filter import R3_PerceptionFilter
from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads, pick_field

logger = get_logger('L1_Perception')


class L1_Perception:
    """感知过滤层：事件判断 + 失真感知分发"""

    def __init__(self):
        self.cli = LLMClient('L1')
        # 兜底通道：**强制开推理**，只在检测到"逐字复述真值"时用一次。
        # 配置见 base_cnf 的 L1_retry。
        self.cli_retry = LLMClient('L1_retry')
        self.filter = R3_PerceptionFilter()

    # ===================== LLM 调用 =====================

    def _call_llm(self, user_input: dict, retry: bool = False) -> dict:
        cli = self.cli_retry if retry else self.cli
        label = 'L1·重试' if retry else 'L1'
        sys_prompt = load_prompt('L1_perception_sys.txt')
        context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]
        reply = cli.chat_by_context(context, label=label)
        return safe_json_loads(reply, label=label) or {}

    # ===================== 真值复述检测 =====================

    @staticmethod
    def _truth_of(player) -> str:
        return (player.player_dict.get('当前状态CurrentState', {}) or {}).get('实力等级', '')

    def _has_truth_echo(self, raw: dict, player) -> bool:
        """
        检测"感知分发里逐字出现了玩家的真实实力值"。

        **这是唯一一种代码能自动检测的失真失败**——它意味着把这个字段从输入
        抄到了输出，等于没有失真。其他形态的退化（措辞变单调、误判变少）
        检测不了，只能靠人读 perception_audit.md。
        """
        truth = self._truth_of(player)
        if not truth:
            return False
        per = pick_field(raw, '感知分发', default={}) or {}
        if not isinstance(per, dict):
            return False
        return any(truth in str(v) for v in per.values())

    def set_world(self, world_layer):
        self.filter.set_world(world_layer)

    # ===================== 输入组装 =====================

    @staticmethod
    def _player_view(player) -> dict:
        """
        玩家交给 L1 的部分。

        **只给 `当前状态CurrentState`**（位置/实力/身份——在场就能观察到的），
        扣掉 `持有物Inventory`（口袋里的东西）和 `描述Description`（背景秘密）。
        这两样只给 L3。
        """
        pd = player.player_dict if hasattr(player, 'player_dict') else (player or {})
        state = pd.get('当前状态CurrentState', {}) or {}
        return {
            '名称': pd.get('玩家名称name', '玩家'),
            '位置': state.get('当前所处位置', ''),
            '实力': state.get('实力等级', ''),
            '身份': state.get('当前身份', ''),
        }

    def _nearby(self, world_layer, all_npc_list, player_location, involved):
        """候选池：R3 按距离粗筛 + 锁定名单豁免，并补上观察者自身的深浅。"""
        pool = self.filter.candidates(player_location, all_npc_list, involved)
        # 补 实力/身份 —— 失真判定需要观察者视角，见模块 docstring 的说明
        by_name = {n.name: n for n in all_npc_list.npc_list}
        for item in pool:
            npc = by_name.get(item['名字'])
            if npc is None:
                continue
            st = npc.current_state or {}
            item['实力'] = st.get('实力等级', '')
            item['身份'] = st.get('当前身份', '')
        return pool

    @staticmethod
    def _roster(all_npc_list) -> list:
        """
        全员名册：{名字, 实力, 位置}。

        为什么不止给名字：**"点名必达"要求 L1 能为任何一个被点名的人写失真感知**，
        而失真的程度取决于观察者自己的修为——不知道对方是炼气还是化神，
        就写不出"他能不能看透你"。

        这和 L1 需要玩家真值是同一个道理（§3.2）：持有真值是为了产生失真，
        而不是为了泄漏。真正的 11 张表（记忆/信念/好感度…）依然一张都不给。
        """
        return [{
            '名字': n.name,
            '实力': (n.current_state or {}).get('实力等级', ''),
            '位置': (n.current_state or {}).get('当前所处位置', ''),
        } for n in all_npc_list]

    # ===================== 主入口 =====================

    def judge(self, event_context, event_layer, all_npc_list, player,
              director_context, world_layer, player_input: str = '') -> dict:
        """
        :param event_context: ContextHistory 事件上下文
        :param event_layer: EventLayer 当前事件状态（scope/involved 会被更新）
        :param all_npc_list: B3NPCListLayer
        :param player: B4PlayerLayer
        :param director_context: ContextHistory 幕后情报（只读，用于参考）
        :param world_layer: B1WorldLayer
        :param player_input: 玩家本轮原话（R3 补漏时用作文本）
        :return: {
            'is_new_event': bool, 'scope': Scope,
            'event_npc': [...],      # 本轮主动互动的 NPC（已过 R3 校验）
            'related_npc': [...],    # 本轮行为关联的 NPC
            '锁定名单': [...],        # 上面两者的并集，= 要去 L2 的 NPC
            '直接对象': [...],        # 被直接对话/作用的对象（scope=private 时决定谁能感知）
            '感知分发': {名: 失真文本},
            '地段传播': {'msg','level','mask'},
            'raw': 原始 LLM 输出（调试用）
        }
        """
        player_loc = player.player_dict['当前状态CurrentState'].get('当前所处位置', '')
        cur_event = list(event_layer.involved)

        user_input = {
            '当前事件NPC': cur_event,
            '当前事件范围': event_layer.scope.value,
            '世界观所有NPC': self._roster(all_npc_list),
            '玩家': self._player_view(player),
            '玩家附近NPC': self._nearby(world_layer, all_npc_list, player_loc, cur_event),
            '对话上下文': list(event_context.context),
            '幕后情报': list(director_context.context)[-10:],
            '世界状态': world_layer.dict.get('世界状态列表', []),
        }

        raw = self._call_llm(user_input)

        # ---- 触发式重试 ----
        # L1 关着推理时（快），偶尔会把玩家的**真实实力逐字抄进感知**——那等于没失真。
        # 检测到就单独把这层用**开推理**的通道重跑一次。
        # 触发率实测约 8%，平均代价 ≈0.8s/轮；换来的是硬失败不再出现。
        if (L1_THINKING_RETRY_ENABLED and not self.cli.cfg.thinking
                and self._has_truth_echo(raw, player)):
            logger.warn(f'⚠️ L1 逐字复述了玩家真实实力（{self._truth_of(player)}）'
                        f'——用开推理通道重跑一次')
            try:
                raw2 = self._call_llm(user_input, retry=True)
                if raw2:
                    raw = raw2
            except Exception as e:
                # 重试失败就用原来那份——总比整轮崩掉强
                logger.error(f'L1 重试失败，沿用原结果：{type(e).__name__}: {e}')

        title = str(pick_field(raw, '事件标题', default='') or '')

        # ---- 事件边界（代码层防死循环）----
        is_new = self._resolve_is_new(raw, event_context, event_layer, world_layer)
        if is_new:
            event_layer.reset(title=title[:30])
            logger.info(f'🆕 新事件：{event_layer.title or "(无题)"}')

        # ---- scope 与名单 ----
        # 「主动互动」与「行为关联」两类合并成**同一份锁定名单**：
        # L2 对两者处理方式完全相同（都要理解+回应），区别只在 L3 结算时
        # 谁被作用了。名单分开维护只会多一个不同步的出错点。
        scope = Scope.parse(pick_field(raw, 'scope', '事件范围', default=None),
                            default=event_layer.scope)
        add_npc = self._dedup(pick_field(raw, 'add_npc', 'add_npc列表', default=[]))
        del_npc = self._dedup(pick_field(raw, 'del_npc', default=[]))
        add_rel = self._dedup(pick_field(raw, 'add_related_npc', default=[]))
        del_rel = self._dedup(pick_field(raw, 'del_related_npc', default=[]))
        # 记录本轮新加入的两类，供 L3 区分"被作用对象"与"在场旁观"
        event_names = list(add_npc)
        related_names = list(add_rel)
        direct = self._dedup(pick_field(raw, '直接对象', default=[]))

        names = list(event_layer.involved)
        for nm in add_npc + add_rel:
            if nm not in names:
                names.append(nm)
        for nm in del_npc + del_rel:
            if nm in names:
                names.remove(nm)
        locked = names

        event_layer.apply(scope_raw=scope, involved=locked, direct_target=direct,
                          title=title)
        event_layer.tick()

        # ---- 失真感知（软产出）→ R3 硬约束校验 ----
        perceptions = pick_field(raw, '感知分发', default={}) or {}
        if not isinstance(perceptions, dict):
            logger.warn(f'L1 感知分发格式异常（{type(perceptions).__name__}），按空处理')
            perceptions = {}
        perceptions = {str(k): str(v) for k, v in perceptions.items() if v}

        fallback = f'{self._player_view(player)["名称"]}（玩家）对你说：{player_input}' \
            if player_input else ''
        perceptions = self.filter.enforce(
            perceptions, scope, locked, direct, player_loc, all_npc_list, fallback)

        self._warn_on_truth_leak(perceptions, player, all_npc_list)

        ground = pick_field(raw, '地段传播', default={}) or {}
        if not isinstance(ground, dict):
            logger.warn(f'L1 地段传播格式异常（{type(ground).__name__}），按空处理')
            ground = {}

        logger.info(f'L1 结果：scope={scope.value} 锁定={len(locked)} '
                    f'感知={len(perceptions)} 传闻传到={len(ground.get("传到") or [])}人')

        # ---- 锁定名单 = 本轮**实际参与**的人 ----
        #
        # 原来这里取 `locked ∩ perceptions`，会让两个下游对不上：
        #   L2 用 `感知分发` 跑（谁收到感知谁回应）
        #   L3 用 `锁定名单` 拿客观状态（结算时能看到谁）
        # L1 完全可能给某个 NPC 写了感知、却忘了把他列进 `add_npc`——实测 3/12 轮如此。
        # 那时 L2 让这人开口说话了，L3 却看不到他的持有物——**交易校验会缺一边**。
        #
        # 以 R3 校验**之后**的 `感知分发` 为准：谁真收到感知，谁就是本轮参与者。
        # 注意这与 `event_layer.involved` 是两回事——那个是 L1 **声明**的名册，
        # 负责跨轮沿用（add/del 的基准），不是本轮的结算范围。
        participants = list(perceptions.keys())
        if set(participants) != set(locked):
            logger.info(f'本轮参与者({len(participants)}) 与 L1 声明名单({len(locked)}) 不同：'
                        f'多={[n.split("、")[0] for n in set(participants) - set(locked)]} '
                        f'缺={[n.split("、")[0] for n in set(locked) - set(participants)]}')

        return {
            'is_new_event': is_new,
            'scope': scope,
            'event_npc': [n for n in event_names if n in perceptions],
            'related_npc': [n for n in related_names if n in perceptions],
            '锁定名单': participants,
            '直接对象': [n for n in direct if n in perceptions],
            '感知分发': perceptions,
            '地段传播': ground,
            'raw': raw,
        }

    # ===================== 内部工具 =====================

    def _resolve_is_new(self, raw: dict, event_context, event_layer, world_layer) -> bool:
        """
        事件边界判定 + **代码层防死循环**。

        上下文很短却宣布"新事件"、且已有锁定 NPC 时，强制延续——
        否则 LLM 每轮都新建事件，事件上下文永远只有一两行，所有 NPC 都失忆。
        """
        is_new = bool(pick_field(raw, 'is_new_event', '新事件', default=False))
        if not is_new:
            return False

        ctx_len = len(event_context)
        min_ctx = world_layer.get_config('事件_最小上下文长度', 2) if world_layer else 2
        if ctx_len <= min_ctx and event_layer.involved:
            logger.warn(f'L1 is_new_event=true 但上下文仅{ctx_len}条且有锁定NPC，强制延续')
            return False
        return True

    @staticmethod
    def _warn_on_truth_leak(perceptions: dict, player, all_npc_list=None):
        """
        守卫：`感知分发` 里**逐字出现**玩家的真实实力等级时，记一条警告。

        **为什么不直接拦掉**：高阶修士看低阶修士，一眼看穿是**正确**的失真结果——
        "炼气中期"对一个金丹长老来说本来就是能读出来的东西。硬拦会把正确行为也杀掉。

        所以只留痕，**并附上观察者自己的修为**，让人一眼判断这是"看穿了"还是"复述了"：

            观察者是金丹后期 → 读出炼气中期是正常的
            观察者是炼气后期 → 读出炼气中期也可信
            观察者是练气初期 → 逐字说出"炼气中期"就值得怀疑了

        判断留给看日志的人，因为"谁该看透谁"恰恰是本设计**刻意不给刻度**的地方。
        """
        truth = (player.player_dict.get('当前状态CurrentState', {}) or {}).get('实力等级', '')
        if not truth:
            return
        echoed = [nm for nm, txt in (perceptions or {}).items() if truth in str(txt)]
        if not echoed:
            return

        by_name = {n.name: n for n in all_npc_list} if all_npc_list else {}
        who = []
        for nm in echoed:
            npc = by_name.get(nm)
            lv = (npc.current_state or {}).get('实力等级', '?') if npc else '?'
            who.append(f'{nm.split("、")[0]}({lv})')
        logger.warn(f'ℹ️ 感知分发里逐字出现了玩家真实实力「{truth}」——'
                    f'观察者：{"、".join(who)}。'
                    f'观察者修为远高于玩家时这是正确的；相近或更低时说明 L1 在复述而非失真')

    @staticmethod
    def _dedup(names) -> list:
        """去重 + 只保留非空字符串。LLM 偶尔会把名字写成 dict 或数字，一律丢弃。"""
        out = []
        for n in (names or []):
            if not isinstance(n, str):
                continue
            n = n.strip()
            if n and n not in out:
                out.append(n)
        return out


