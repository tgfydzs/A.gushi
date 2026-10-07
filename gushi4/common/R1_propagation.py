"""
R1 地段传播（**执行层，不做判断**，不调用 LLM）

把 L1 给的「地段传播{消息, 传到}」写进那些 NPC 的 `观察Observation`。

    这不是"感知"，是"听说"。差别在于**必然失真**——传闻不保证准确，
    也不保证送达。这和 §2.3 的失真感知是同一种世界观：信息在人群里流动会走形。

# 为什么"传到谁"由 L1 判断，而不是在这里算

原来这里是按 `level:int` 做**祖先前缀展开**（`path[:level]`），再用 `mask:float`
概率挑人。**两个都不成立**：

| 原来的做法 | 为什么不对 |
|-----------|-----------|
| `path[:level]` 前缀展开 | 只能覆盖"我祖先链上的**子树**"，**够不着兄弟节点**——「化神期一嗓子传到相邻地段」这种情形结构上根本表达不出来 |
| 每一级全有或全无 | 没法表达"传到天道宗和坊市、但没传到星辰阁" |
| `mask` 均匀概率 | 假设了"同一地段人人等可能听到"。都市里隔一堵墙就听不见，这个假设同样不成立 |

这三条其实是同一个毛病：**让 LLM 吐一个数字，再由 Python 用一张表把数字翻译成结论。**
数字太窄，表就是那堵墙。而传播范围恰恰是**没法用几十个键值对描述**的东西——
地段大小、距离远近、修为高低、建筑隔音、深夜还是闹市、当时街上有没有人……

所以改成 **L1 直接给名单**。本层只做三件事：

    名字防幻觉 · 排除已亲身经历者 · 写进观察表

**不做任何距离判断、不做任何概率判断。**
"""

from Schema.Event_sch import Scope
from common.log import get_logger

logger = get_logger('R1_Propagation')

# 不产生传闻的 scope。这是执行 L1 **自己的声明**，不是本层替它判断。
_NO_SPREAD_SCOPES = {Scope.HIDDEN, Scope.PRIVATE}


class R1_Propagation:
    """地段传播：L1 给的名单 → 传闻 → 写进观察表"""

    def propagate(self, ground_spread: dict, world_layer=None,
                  all_npc_list=None, experienced_names=None,
                  player_location: str = '', scope: Scope = None,
                  rumor_style: str = '（传闻）') -> dict:
        """
        :param ground_spread: L1 产出 `{"消息": str, "传到": [NPC完整名, ...]}`
        :param all_npc_list: 全部 NPC（用于名字校验）
        :param experienced_names: 本轮已**亲身感知**的 NPC——不再给传闻（同一件事不给两遍）
        :param scope: hidden/private 的事件不产生传播
        :param rumor_style: 传闻前缀
        :param world_layer: **已不再使用**，保留仅为兼容调用点
        :param player_location: **已不再使用**，同上
        :return: {npc_name: [传闻文本, ...]}
        """
        if not ground_spread:
            return {}

        if scope in _NO_SPREAD_SCOPES:
            logger.info(f'R1：scope={scope.value}，本轮不产生传闻')
            return {}

        if all_npc_list is None:
            logger.warn('R1：未传 all_npc_list，无法校验名字')
            return {}

        msg = (ground_spread.get('消息') or ground_spread.get('msg') or '').strip()
        targets = ground_spread.get('传到')

        # 没过 schema 校验就**不静默兜底**——旧结构还带着 level/mask 说明 L1 没按新格式输出，
        # 提示词可能失效了。报出来，别让它烂在日志里。
        if targets is None:
            if ground_spread.get('level') is not None:
                logger.warn('⚠️ R1：L1 输出的是旧的 level/mask 结构，没有"传到"名单。'
                            '本轮不产生传闻——检查提示词里的地段传播 schema 是否生效')
            return {}

        if not msg or not isinstance(targets, list):
            return {}

        valid_names = {n.name for n in all_npc_list.npc_list}
        exclude = set(experienced_names or [])
        rumor = f'{rumor_style}{msg}'

        results = {}
        for raw in targets:
            if not isinstance(raw, str):
                continue
            name = raw.strip()
            if not name:
                continue
            if name not in valid_names:
                logger.warn(f'R1：丢弃不存在的 NPC 名 {name!r}（防幻觉）')
                continue
            if name in exclude:
                continue                      # 已经亲身经历过，不再给传闻
            npc = all_npc_list.get_npc(name)
            if npc is None:
                continue
            if rumor not in npc.observation:
                npc.observation.append(rumor)
            npc.cap_fields()
            results.setdefault(name, []).append(rumor)

        preview = msg[:40] + ('...' if len(msg) > 40 else '')
        logger.info(f'📡 传闻："{preview}" 传到 {len(results)} 人 → '
                    f'{[n.split("、")[0] for n in list(results)[:6]]}')
        return results
