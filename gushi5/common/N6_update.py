import json

from Schema.B3_1_npc_sch import B3NPCLayer
from Schema.B4_player_sch import B4PlayerLayer
from common.log import get_logger
from common.small_tool import (load_prompt, apply_npc_delta, apply_player_delta,
                               strip_position_keys, safe_json_loads)
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
        #: 这个世界的**硬性常识**（`global.json` 的 `世界规则列表`：境界体系、寿元、
        #: 盟约约束…）。由引擎每轮灌进来（`set_world_rules`），两条链都会读到。
        #: 它和 `世界知识列表` 的区别：规则是**人人皆知**的常识，知识是**秘辛**——
        #: 后者只进 N9 导演，绝不喂给 NPC 层（否则就是信息泄漏）。
        self.world_rules: list = []

    def _check_output_shape(self, payload: dict, label: str,
                            required=('npc实例', '玩家实例', '旁白', '本轮耗时分钟'),
                            container='npc实例'):
        """
        解析成功**不代表结构对**。实测过一种半成功：模型把 `删减` 提到了根层，
        根对象提前闭合，`玩家实例 / 旁白 / 本轮耗时分钟` 全成了"尾随数据"——
        括号修复之后整段能解析，但那几个字段要么缺、要么层级错。

        所以这里只做**可见性**：缺哪个键、`npc实例` 里混进了什么，各打一条 WARN。
        不抛异常（少一个字段不该打死整轮），但要让人看得见。
        """
        payload = payload or {}
        missing = [k for k in required if k not in payload]
        if missing:
            self.logger.warn(f'{label} 输出缺顶层键 {missing}——多半是层级写错了，'
                             f'这些字段本轮按空处理（玩家增量/旁白可能丢）')
        npc = payload.get(container) if container else None
        if isinstance(npc, dict):
            stray = [k for k in npc if k not in ('增加', '删减')]
            if stray:
                self.logger.warn(f'{label} `{container}` 里出现 `增加/删减` 之外的键 {stray}'
                                 f'——层级放错了，这些会被丢弃')

    def set_world_rules(self, rules: list):
        """由引擎在每轮开始时灌入（幂等，灌同一份不会重复）。"""
        self.world_rules = list(rules or [])

    def update_for_npc(self,
                       npc_inst: B3NPCLayer,
                       player_inst: B4PlayerLayer,
                       perception_msg: str,
                       inner_thought: str = '',
                       ground=None,
                       task: dict = None,
                       tasks: list = None,
                       player_tasks: list = None,
                       where: str = '',
                       places: list = None,
                       world_facts: list = None):
        """
        客观收尾：根据内心想法和客观条件，落实所有表更新，并估算本轮耗时。

        :param npc_inst: N45理解回应后的NPC实例（原封不动）
        :param player_inst: 玩家实例
        :param perception_msg: N3感知层分发的带方向消息
        :param inner_thought: N45产出的内心想法
        :param ground: 可选落地回调 `(npc, delta) -> npc`。传了就用它应用增量——
            位置会走「地名 → 代码落坐标」（`GameController.apply_npc_grounded_delta`）；
            不传则退回"丢掉位置字段"的老行为（位置字段本来就该代码写）。
        :param task: 这个NPC**最老的一条**进行中任务（兼容旧写法，保留）。
        :param tasks: 这个NPC**手上在执行的全部任务**（一行一条、带 id）。
            比 `task` 重要：模型要用 id 去引用，才能准确了结某一条，而不是"最老的那条"。
        :param player_tasks: 玩家手上在执行的任务。这一层同时看得到双方，
            所以玩家自己接的任务要由它来据实了结。
        :param places: **地点名白名单**（全路径）。给了它，`去向`/`当前所处位置` 才有得挑；
            不给则模型只能凭记忆拼路径（实测把 `执法堂·天刑殿` 写成 `天道宗·天刑殿`）
            → `name_of` 查不到 → 位置静默不动。
        :param world_facts: **当前世界状态（公开事实）**。喂进来，"上一轮已经发生的事"
            才会约束这一轮的收尾（如"大阵已裂"）；不喂它，NPC 只能吃自己上一轮写的心情，
            于是永远停在"准备接"。
        :return: (更新后的B3NPCLayer, 玩家增量dict, 旁白list, 本轮耗时分钟int, 世界状态增量dict)
        """
        sys_prompt = load_prompt('N6_update_sys.txt')

        all_context = [{'role': 'system', 'content': sys_prompt}]
        user_input = {
            '你是谁': npc_inst.to_dict(),
            '感知输入': perception_msg,
            '玩家实例': player_inst.player_dict,
            '内心想法InnerThought': inner_thought,
        }
        if task:
            user_input['你手上在办的差事'] = task
        if tasks:
            user_input['你手上在执行的任务（可一次多条，用 id 引用）'] = list(tasks)
        if player_tasks:
            # 玩家的任务也要让这一层看见：**结算由"同时看得到双方"的这层判**。
            # 不给的话，玩家跟 NPC 在一起时没有任何一层能了结他自己的任务
            # （N6-Player 只在无 NPC 时才跑）——那正是"玩家侧被饿死"的老形状。
            user_input['玩家手上在执行的任务（你也能据实了结）'] = list(player_tasks)
        if where:
            # 只在"在路上"时给：它不说的话，这个NPC根本不知道自己已经走了一段，
            # 于是每轮都会重新"刚上路"，永远到不了。
            user_input['你现在的行程'] = where
        if world_facts:
            user_input['当前世界状态'] = list(world_facts)
        if self.world_rules:
            user_input['世界规则（这个世界的硬性常识）'] = list(self.world_rules)
        if places:
            user_input['可用地点名（写位置/去向只能从这里原样复制）'] = list(places)
        # 可塑性：这个人的身份四表各有多难改（`表更新成本UpdateCost`）。喂给模型**同时**
        # 由 `small_tool.apply_npc_delta` 限流——语义给 LLM，硬闸在代码。
        if hasattr(npc_inst, 'cost_summary'):
            summary = npc_inst.cost_summary()
            if summary:
                user_input['你的可塑性（数字越大越难改）'] = summary
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'N6_LLM消息：{all_context}')

        reply = self.cli.chat_by_context(all_context, label='N6')
        json_replay = safe_json_loads(reply, label='N6')
        self._check_output_shape(json_replay, 'N6')

        npc_delta = json_replay.get('npc实例', {})
        player_delta = json_replay.get('玩家实例', {})
        world_delta = json_replay.get('世界状态', {}) or {}
        # 任务通道是**回复级**的（与 npc实例 并列），不是 NPC 的某张表。
        # 注进 delta 一起交给 `ground`，由 `apply_npc_grounded_delta` 里的
        # `_apply_task_channel` 消费——`extract_position_dest` 只透传 增加/删减，
        # 所以 `任务` 不会被误当成 NPC 的字段名。
        task_delta = json_replay.get('任务') or {}

        narration = json_replay.get('旁白', []) or []
        minutes, reason = self._parse_minutes(json_replay, npc_inst.name)

        self.logger.info(f'更新结果：npc_delta={npc_delta} player_delta={player_delta} '
                         f'世界状态={world_delta} 任务={task_delta} '
                         f'旁白={narration} 耗时={minutes}分钟({reason})')

        if ground is not None:
            if isinstance(task_delta, dict) and task_delta:
                npc_delta = dict(npc_delta) if isinstance(npc_delta, dict) else {}
                npc_delta['任务'] = task_delta
            # 位置归代码：把 `当前所处位置` 当**地名**落坐标（NPC 没有别的位置通道）
            new_npc_inst = ground(npc_inst, npc_delta)
        else:
            new_npc_inst = apply_npc_delta(
                npc_inst, strip_position_keys(npc_delta, f'N6[{npc_inst.name}]'))

        return new_npc_inst, player_delta, narration, minutes, world_delta

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
                               game_time_str: str = '', has_move: bool = False,
                               world_facts: list = None, tasks: list = None) -> tuple:
        """
        处理玩家自主行动（无NPC参与时）。

        独处修炼、赶路、探索等行为——玩家状态变化 + 客观旁白 + 本轮耗时。

        时间跨度不再由外部传入，改为本层自行估算（原 N8 的职责）。

        :param player_inst: B4PlayerLayer 玩家实例
        :param player_input: 玩家原始输入
        :param game_time_str: 游戏时间字符串
        :param has_move: 本轮是否含移动（移动耗时由系统按距离单独算，避免重复计）
        :param world_facts: 当前世界状态（公开事实）。**无 NPC 时玩家的动作也得有个落盘处**——
            否则"我劈开这座山"只活在旁白里，下一轮谁都不记得。
        :param tasks: 玩家手上在执行的任务（一行一条、带 id）。
            独处时"我决定去把那颗石头要回来"也要能记成任务，否则只有跟 NPC 说话才建得出任务。
        :return: (player_delta, narration, minutes, world_delta, task_delta)
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
        if world_facts:
            user_input['当前世界状态'] = list(world_facts)
        if tasks:
            user_input['你手上在执行的任务（可一次多条，用 id 引用）'] = list(tasks)
        if self.world_rules:
            user_input['世界规则（这个世界的硬性常识）'] = list(self.world_rules)
        sys_prompt = load_prompt('N6_player_sys.txt')

        all_context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]

        reply = self.cli.chat_by_context(all_context, label='N6-Player')
        result = safe_json_loads(reply, label='N6-Player')
        self._check_output_shape(result, 'N6-Player',
                                 required=('玩家状态变更', '旁白'), container=None)

        player_delta = result.get('玩家状态变更', {})
        world_delta = result.get('世界状态', {}) or {}
        task_delta = result.get('任务') or {}
        narration = result.get('旁白', []) or []
        minutes, reason = self._parse_minutes(result, 'N6-Player')

        self.logger.info(f'玩家自主更新：delta={player_delta} 世界状态={world_delta} '
                         f'任务={task_delta} 旁白={narration} 耗时={minutes}分钟({reason})')
        return player_delta, narration, minutes, world_delta, task_delta
