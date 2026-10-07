"""
L3 结算层（结算域，1×/轮）

**唯一持有玩家全量实况、也是唯一的客观状态写入口。** 合并了 gushi3 的 N6。

    L2 所有 NPC 的动作意图 + 对白摘要
      ↓
    L3：同时看到双方 → 判定客观世界实际变成什么样
      ↓
    玩家增量 + 各 NPC 客观增量 + 全局旁白 + 本轮耗时

# 为什么客观结算必须是上帝视角

**物品转移、战斗伤害、位置变化本质上是「跨实体」的**，从任何单方视角看都不完整。

具体到交易：玩家说"我给一百灵石，你把剑给我"——
  - L2 看不到玩家实况，无法验证玩家有没有一百灵石，只能信
  - 如果 L2 擅自把剑扣了，L3 事后发现玩家付不起，**剑已经出去了，没人能收回来**

所以正确切法是**按信息性质分，不按实体分**：

| 信息性质 | 例子 | 谁写 | 为什么 |
|---------|------|------|--------|
| **主观** | 记忆·观察·信念·好感度·待解释·对白·内心想法 | L2 | "我怎么想"，只有本人视角该碰 |
| **客观** | 持有物·当前位置·伤势 | **L3** | "世界实际怎样"，跨实体，必须上帝视角 |

这样交易校验天然解决：**L3 同时看到双方持有物，判定成立与否，双方一起变或一起不变。**

# 输入为什么能从 11 张表砍到 2 张

客观结算时，主观表只需要**写**不需要**读**——不需要知道这人的性格信念才去扣他一把剑。
而"这人会不会真给"的判断，L2 已经通过 `内心想法` 做完了，L3 直接读结论。

# 输出为什么不泄漏

**L3 不产出任何自由文本形式的 NPC 内容**——没有对白，没有内心想法。
`旁白` 是对世界的外部描述，会打印给玩家看，本来就不是秘密。
唯一的例外是 `破裂观察`（见下），它是**事实记录**不是心理描写。

# 破裂观察：交易失败后 NPC 怎么反应

L3 判定交易不成立时，产出旁白 + 给该 NPC 注入**一条观察**
（"玩家掏不出灵石，交易当场破裂"），NPC 的反应在**下一轮**由 L2 自然发生。

复用已有的「观察 → 感知」通道，**零新机制**。让 L3 去改 NPC 的情绪表反而更糟——
那需要它理解 NPC 的心理状态，是 L2 的活。
"""

import json
from copy import deepcopy

from common.LLMclient import LLMClient
from common.log import get_logger
from common.small_tool import (load_prompt, safe_json_loads, pick_field,
                               canonical_table, strip_empty_values)
from common.L2_character import OBJECTIVE_TABLES

logger = get_logger('L3_Settlement')


def sanitize_objective_delta(delta: dict) -> dict:
    """
    **代码层强制**：L3 只能写客观表。

    L3 拿不到 NPC 的主观表，所以它**没有依据**改主观表——它要是写了
    `好感度Relationship`，那是凭空捏造。统一丢弃。
    """
    if not isinstance(delta, dict):
        return {'增加': {}, '删减': {}}

    out = {'增加': {}, '删减': {}}
    dropped = []
    for section in ('增加', '删减'):
        body = pick_field(delta, section, default={}) or {}
        if not isinstance(body, dict):
            continue
        for field, value in body.items():
            # 模型常把 `当前状态CurrentState` 写成 `当前状态`，必须过规范化
            canon = canonical_table(field, OBJECTIVE_TABLES)
            if canon:
                out[section][canon] = value
            else:
                dropped.append(field)

    if dropped:
        logger.warn(f'L3 越权字段被丢弃（属角色域）：{sorted(set(dropped))}')
    # 同 L2：空壳增量不该算作"变更"
    out = {sec: strip_empty_values(body) for sec, body in out.items()}
    return out


# ===================== 生命值体系（世界配置可切）=====================
#
# 用户指出的一条设计判断：**有数值更像游戏，没数值更像小说，能两者切换本身就是
# 这个引擎的优势**——它既能往游戏引擎迁移，也能停在一个多人小说演化器上。
#
# 所以数值**不硬编码进引擎**，而是由世界配置 `生命值体系` 决定：
#
#     生命值体系: "数值"  → 追加下面这段条款，L3 维护 生命/灵力 两个整数
#     生命值体系: "文本"  → 不追加，维持原样（只有 体力状态/当前心情 两行文字）
#
# ⚠️ 条款里**没有**任何"数值低于 X 就不能如何"的判定规则。数值只记录**结果**，
# 不参与叙事判断——越级战斗必须始终可能，这条不能因为加了数值就退化。

_HEALTH_CLAUSE_NUMERIC = """

# 生命值体系：本世界启用**数值**（世界配置 `生命值体系` = 数值）

`当前状态CurrentState` 里除了 `体力状态`、`当前心情`，还要维护两个整数：

| 字段 | 范围 | 含义 |
|------|------|------|
| `生命` | 0–100 | 肉身与生机的完整度。100 = 毫发无伤；跌破 30 算重伤（行动受限）；0 = 死亡 |
| `灵力` | 0–100 | 可用灵力存量。100 = 满盈；施法、御剑、炼丹、激斗都消耗，静修与丹药恢复 |

## 硬规则

1. **只在数值真的变了的时候才写**。没受伤、没施法就不要输出这两个字段的增量——
   每轮无条件重写会让 UI 一直在动，而实际上什么都没发生。
2. **数值必须和文字对得上**。`体力状态` 写"重伤"而 `生命` 还是 90，是自相矛盾的输出，
   视为错误。数值是文字描述的**量化**，不是另一套独立状态。
3. **玩家和 NPC 一视同仁**。你的伤害与消耗判定对双方同时生效，不能只扣一边。
4. **数值只记录结果，不参与判定**。谁打得过谁、能不能越级取胜，仍然由你的叙事判断决定——
   修为低的人完全可以打伤修为高的人，然后 `生命` 扣减如实记录这个结果。
   **不要**引入"等级差多少就打不动"这类规则。
5. 回复与恢复同理：调息、服药、时间流逝都可以回血回灵，同样要有叙事依据。

## 输出示例

```json
{"增加": {"当前状态CurrentState": {"生命": 88, "灵力": 72, "体力状态": "左肩挨了一记，抬手略滞，气息还稳"}}, "删减": {}}
```
"""


class L3_Settlement:
    """结算层：跨实体客观状态判定 + 全局旁白 + 本轮耗时"""

    def __init__(self):
        self.cli = LLMClient('L3')
        self.world_layer = None

    def set_world(self, world_layer):
        """
        绑定世界层，用来读 `世界配置`。

        和 `L1_Perception.set_world()` 同一个模式——层对象构造时不持有世界，
        由引擎在装配阶段注入。
        """
        self.world_layer = world_layer

    def _location_context(self, location: str) -> list:
        """
        地段属性链（根 → 当前地点），供结算判断用。

        属性是**描述**不是**规则**：坊市讲价合理、禁地乱走有后果——
        这些是 L3 看着属性做的叙事判断，代码不写任何"某属性 → 某结果"的映射。

        用 `getattr` 而不是直接调：测试里的 `_FakeWorld` 只实现了 `get_config`，
        没绑世界时也得能跑（和 `_health_clause` 一样的容错姿态）。
        """
        if self.world_layer is None or not location:
            return []
        fn = getattr(self.world_layer, 'get_location_context', None)
        return fn(location) if fn else []

    def _health_clause(self) -> str:
        """按世界配置决定要不要追加数值条款。默认数值。"""
        mode = '数值'
        if self.world_layer is not None:
            mode = self.world_layer.get_config('生命值体系', '数值')
        if str(mode).strip() != '数值':
            logger.info(f'生命值体系 = {mode}，走纯文本（不输出 生命/灵力）')
            return ''
        return _HEALTH_CLAUSE_NUMERIC

    # ===================== 输入组装 =====================

    @staticmethod
    def _player_full(player) -> dict:
        """
        玩家全量实况。**这是整条流水线里唯一能看到 `持有物Inventory` 和
        `描述Description` 的地方**——L1 只看得到 `当前状态CurrentState`。
        """
        pd = player.player_dict if hasattr(player, 'player_dict') else (player or {})
        return {
            '玩家名称': pd.get('玩家名称name', '玩家'),
            '当前状态CurrentState': pd.get('当前状态CurrentState', {}),
            '持有物Inventory': pd.get('持有物Inventory', {}),
            '描述Description': pd.get('描述Description', []),
        }

    @staticmethod
    def _objective_side(npc) -> dict:
        """NPC 的客观侧：2 张表，不是 11 张。"""
        st = npc.current_state or {}
        return {
            '名字': npc.name,
            '当前状态CurrentState': {
                '当前所处位置': st.get('当前所处位置', ''),
                '实力等级': st.get('实力等级', ''),
                '当前身份': st.get('当前身份', ''),
                '体力状态': st.get('体力状态', ''),
            },
            '持有物Inventory': npc.inventory or {},
        }

    def _build_input(self, player, npcs, l2_results, player_input, move_hint=None,
                     location=None, world_state=None, recent_context=None,
                     known_locations=None) -> dict:
        intents = []
        for r in (l2_results or []):
            if r.get('动作意图', {}).get('类型') in (None, '无') and not r.get('回应Response'):
                continue
            intents.append({
                'NPC': r['name'],
                '对白': (r.get('回应Response') or '')[:200],
                '内心想法': (r.get('内心想法InnerThought') or '')[:150],
                '动作意图': r.get('动作意图', {}),
                '他感知到的': (r.get('感知输入') or '')[:200],
            })

        return {
            '玩家实况': self._player_full(player),
            '参与NPC客观侧': [self._objective_side(n) for n in (npcs or [])],
            '各NPC的动作意图': intents,
            '玩家本轮输入': player_input,
            '当前位置': location or '',
            '当前地段': self._location_context(location),
            '世界状态': list(world_state or []),
            '移动信息': move_hint or {},
            # ⚠️ 必须给：**结算层没有别的记忆**。它看不到 NPC 的主观表，
            # 只看得到本轮的意图。上一轮"丹药到底给没给"只存在于旁白和上下文里，
            # 不给它就会出现"上一轮明明没给，这一轮旁白写成他收下了"。
            '最近经过': list(recent_context or [])[-10:],
            # 已知地点参考：位置字符串一旦编造，之后所有距离计算都跟着漂
            '已知地点': list(known_locations or []),
        }

    # ===================== 主入口：有 NPC =====================

    def settle_with_npcs(self, player, npcs, l2_results, player_input: str,
                         location: str = '', move_hint: dict = None,
                         world_state: list = None,
                         recent_context: list = None,
                         known_locations: list = None) -> dict:
        """
        有 NPC 参与时结算。

        :return: {
            '玩家实例': 已净化的增量,
            'NPC实例': {名: 已净化的客观增量},
            '旁白': [str],
            '本轮耗时分钟': int,
            '破裂观察': {名: 观察文本},
            'raw': 原始输出
        }
        """
        user_input = self._build_input(player, npcs, l2_results, player_input,
                                       move_hint, location, world_state,
                                       recent_context, known_locations)
        raw = self._call(user_input, 'L3_settlement_sys.txt', 'L3')

        result = self._normalize(raw)
        logger.info(f'L3 结算：玩家增量={list(result["玩家实例"]["增加"]) + list(result["玩家实例"]["删减"])} '
                    f'| NPC增量={ {k: list(v["增加"]) + list(v["删减"]) for k, v in result["NPC实例"].items()} } '
                    f'| 旁白{len(result["旁白"])}条 | {result["本轮耗时分钟"]}分')
        return result

    # ===================== 主入口：玩家独处 =====================

    def settle_solo(self, player, player_input: str, location: str = '',
                    game_time: str = '', has_move: bool = False,
                    move_hint: dict = None,
                    world_state: list = None,
                    recent_context: list = None,
                    known_locations: list = None) -> dict:
        """
        玩家无 NPC 时的自主行动（原 N6-Player）。

        同一层、同一契约的另一分支——它同样只看得到玩家实况，
        同样只写客观状态，只是没有别人的动作意图要结算。
        """
        user_input = {
            '玩家实况': self._player_full(player),
            '玩家本轮输入': player_input,
            '当前位置': location,
            '当前地段': self._location_context(location),
            '游戏时间': game_time,
            '本轮含移动': bool(has_move),
            # 有 `{移动}` 标记时给出起点终点；自由文本移动时为空，
            # 那时靠"当前位置"和 L3 自己写的新位置对照。见 prompt 第六部分。
            '移动信息': move_hint or {},
            '世界状态': list(world_state or []),
            '最近经过': list(recent_context or [])[-10:],
            '已知地点': list(known_locations or []),
        }
        raw = self._call(user_input, 'L3_solo_sys.txt', 'L3·独处')
        result = self._normalize(raw)
        logger.info(f'L3 独处：玩家增量={list(result["玩家实例"]["增加"]) + list(result["玩家实例"]["删减"])} '
                    f'| 旁白{len(result["旁白"])}条 | {result["本轮耗时分钟"]}分')
        return result

    # ===================== 内部 =====================

    def _call(self, user_input: dict, prompt_file: str, label: str) -> dict:
        # 数值条款**追加**在 system prompt 末尾，不走 `load_prompt` 的占位符机制——
        # 那个机制是给 `data/prompt/*_custom/` 注入用的，混在一起会互相打架
        sys_prompt = load_prompt(prompt_file) + self._health_clause()
        context = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ]
        reply = self.cli.chat_by_context(context, label=label)
        return safe_json_loads(reply, label=label) or {}

    def _normalize(self, raw: dict) -> dict:
        """
        统一净化：玩家增量、各 NPC 客观增量、旁白、耗时、破裂观察。

        全部走 `pick_field`——模型稳定地漏写英文后缀（返回 `"旁白"` 没问题，
        但 `"破裂观察"` 也常被写成各种变体），直接 `raw.get(完整名)` 会静默拿到空。
        """
        return {
            '玩家实例': sanitize_objective_delta(
                pick_field(raw, '玩家实例', default={})),
            'NPC实例': self._normalize_npc_map(
                pick_field(raw, 'NPC实例', 'NPC实例列表', default={})),
            '旁白': self._normalize_narration(pick_field(raw, '旁白', default=None)),
            '本轮耗时分钟': self._parse_minutes(
                pick_field(raw, '本轮耗时分钟', '耗时分钟', default=None)),
            '耗时理由': str(pick_field(raw, '耗时理由', default='') or '')[:30],
            '破裂观察': self._normalize_broken(
                pick_field(raw, '破裂观察', default=None)),
            'raw': raw,
        }

    def _normalize_npc_map(self, mp) -> dict:
        if not isinstance(mp, dict):
            return {}
        out = {}
        for name, delta in mp.items():
            if not isinstance(name, str) or not name.strip():
                continue
            clean = sanitize_objective_delta(delta or {})
            if clean['增加'] or clean['删减']:
                out[name.strip()] = clean
        return out

    @staticmethod
    def _normalize_narration(raw) -> list:
        if isinstance(raw, str):
            raw = [raw] if raw.strip() else []
        if not isinstance(raw, list):
            return []
        out = []
        for line in raw:
            if isinstance(line, str) and line.strip() and line.strip() not in out:
                out.append(line.strip()[:80])
        return out[:3]

    @staticmethod
    def _parse_minutes(raw) -> int:
        """
        解析本轮耗时。**任何异常都回落 1 分钟**——
        耗时是直接推进时钟的，一个 null 就能让游戏时间崩掉。
        """
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return 1
        return max(1, v)

    @staticmethod
    def _normalize_broken(raw) -> dict:
        """
        破裂观察：{NPC名: 观察文本}。

        这是 L3 写进 NPC `观察Observation` 的**唯一通道**（正常增量里没有这张表）。
        它是事实记录（"玩家掏不出灵石"），不是心理描写——所以不违反
        「L3 不产出自由文本形式的 NPC 内容」。
        """
        out = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str) and v.strip():
                    out[k.strip()] = v.strip()[:80]
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    nm = str(pick_field(item, 'NPC', '名字', default='') or '').strip()
                    ob = str(pick_field(item, '观察', default='') or '').strip()
                    if nm and ob:
                        out[nm] = ob[:80]
        return out
