"""
记忆管理（后台，不占关键路径）

# 先纠正一个预期：**优化记忆不是为了提速**

token 数据显示输入只占很小一部分（1600~3900 token，处理很快），
砍输入最多省 0.5 秒。记忆管理的真实收益是**三条别的**：

1. **防无限增长** —— 长跑必然溢出上下文窗口，这是**崩溃风险**，不是性能问题
2. **控成本** —— 输入 token 是要付费的
3. **保质量** —— 记忆/观察表塞满陈年旧事，会稀释"最近发生的事"的权重

# 为什么不上 RAG / 向量化

| RAG 有意义的条件 | 本场景 |
|-----------------|--------|
| 库远大于上下文窗口 | ❌ 记忆 30 条、观察 20 条 |
| 相关性可判断 | ❌ **NPC 的表是"它自己"，全部相关**，检索出来还是全部 |
| 顺序无关 | ❌ **叙事强依赖时序**——"他上周答应我的"与"他今天反悔了"相似度极低，但必须同时在场 |
| — | ❌ 引入 embedding 服务、缓存、存档兼容（向量存不存？读档怎么办？） |

**真解（按性价比）**：

1. 补齐硬上限 —— gushi3 只截了 3 张表，`对玩家观察`/`好感度`/`信念`/`目标`
   **无上限无限增长**，而每轮全量喂给角色层
2. **记忆巩固（摘要压缩）** —— 超额时不丢弃，把最旧一批**压缩成一条摘要**。
   这符合本系统的"信息有损"哲学：**NPC 本来就该记不清陈年旧事的细节**。
3. 条目瘦身 —— 写入时限制记忆条目长度
4. 按层裁剪 —— L1 不需要任何 NPC 表

第 2 条放后台跑，**不占关键路径**。
"""

import json

from common.base_cnf import (
    MEMORY_CONSOLIDATION_ENABLED, MEMORY_CONSOLIDATE_BATCH, MEMORY_MIN_KEEP,
)
from common.LLMclient import LLMClient
from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads, pick_field

logger = get_logger('Memory')

# 巩固管线的目标表 → 对应的截断上限属性名
CONSOLIDATE_TARGETS = {
    '知识记忆Memory': 'MAX_MEMORY',
    '观察Observation': 'MAX_OBSERVATION',
    '对玩家观察PlayerObservation': 'MAX_PLAYER_OBSERVATION',
    '信念Belief': 'MAX_BELIEF',
    '生活目标Goals': 'MAX_GOALS',
}

# 单条写入的长度上限（条目瘦身）
MAX_ENTRY_CHARS = 120


class MemoryManager:
    """记忆巩固：超额时把最旧一批压缩成一条摘要"""

    def __init__(self):
        self.cli = LLMClient('MEM')
        self.enabled = MEMORY_CONSOLIDATION_ENABLED

    # ===================== 条目瘦身 =====================

    @staticmethod
    def trim_entry(text: str) -> str:
        """写入时限制单条长度。超长的截断并加省略号。"""
        if not isinstance(text, str):
            return text
        t = text.strip()
        return t if len(t) <= MAX_ENTRY_CHARS else t[:MAX_ENTRY_CHARS] + '…'

    # ===================== 巩固 =====================

    def needs_consolidation(self, npc) -> list:
        """返回需要巩固的 (表名, 上限) 列表"""
        from Schema.B3_1_npc_sch import B3NPCLayer
        todo = []
        for table, attr in CONSOLIDATE_TARGETS.items():
            cap = getattr(B3NPCLayer, attr, None)
            if cap is None:
                continue
            items = npc.to_dict().get(table) or []
            if isinstance(items, list) and len(items) > cap:
                todo.append((table, cap))
        return todo

    def consolidate_npc(self, npc) -> bool:
        """
        对单个 NPC 做一轮巩固。**原地修改传入的 npc 实例**。

        :return: 是否有改动
        """
        if not self.enabled:
            return False

        todo = self.needs_consolidation(npc)
        if not todo:
            return False

        changed = False
        for table, cap in todo:
            items = list(npc.to_dict().get(table) or [])
            if len(items) <= cap:
                continue

            # 压缩最旧的一批，但至少保留 MEMORY_MIN_KEEP 条原文
            batch = min(MEMORY_CONSOLIDATE_BATCH, len(items) - MEMORY_MIN_KEEP)
            if batch <= 0:
                continue
            old, keep = items[:batch], items[batch:]

            summary = self._summarize(table, old, npc.name)
            if not summary:
                # 摘要失败 → 退回"直接丢弃"（比无限增长好）
                logger.warn(f'[{npc.name}] {table} 摘要失败，退回截断丢弃 {batch} 条')
                new_items = keep
            else:
                new_items = [f'（往忆·{len(old)}条汇）{summary}'] + keep

            self._set_table(npc, table, new_items)
            logger.info(f'🧠 [{npc.name}] {table} 巩固：{len(items)} → {len(new_items)} 条')
            changed = True

        if changed:
            npc.cap_fields()
        return changed

    # ===================== 内部 =====================

    @staticmethod
    def _set_table(npc, table: str, items: list):
        """把表写回 NPC。用 to_dict→重建的方式，避免逐表写 setattr 分支。"""
        from Schema.B3_1_npc_sch import B3NPCLayer
        d = npc.to_dict()
        d[table] = items
        fresh = B3NPCLayer(d)
        npc.__dict__.update(fresh.__dict__)

    def _summarize(self, table: str, old_items: list, npc_name: str) -> str:
        """
        把一批旧条目压成一句摘要。

        **"记不清"是设计，不是妥协**——摘要天然有损，而 NPC 本来就该
        对陈年旧事只有模糊印象。压出来的句子保留"谁/在哪/大概什么事"，
        丢掉具体数字和细节。
        """
        try:
            sys_prompt = load_prompt('memory_consolidate_sys.txt')
            payload = {
                '表名': table,
                '人物': npc_name.split('、')[0],
                '待压缩条目': old_items,
            }
            context = [
                {'role': 'system', 'content': sys_prompt},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ]
            reply = self.cli.chat_by_context(context, label='MEM')
            data = safe_json_loads(reply, label='MEM') or {}
            return str(pick_field(data, '摘要', 'summary', default='') or '').strip()[:MAX_ENTRY_CHARS]
        except Exception as e:
            logger.warn(f'记忆摘要调用失败：{type(e).__name__}: {e}')
            return ''

    # ===================== 批量入口（后台调用） =====================

    def run_round(self, all_npc_list) -> int:
        """
        对全部 NPC 跑一轮巩固。**由后台线程调用，绝不能在关键路径上跑。**

        :return: 发生巩固的 NPC 数
        """
        if not self.enabled:
            return 0
        count = 0
        for npc in all_npc_list:
            try:
                if self.consolidate_npc(npc):
                    count += 1
            except Exception as e:
                logger.error(f'[{npc.name}] 记忆巩固异常：{e}')
        if count:
            logger.info(f'🧠 本轮巩固 {count} 个 NPC 的记忆')
        return count
