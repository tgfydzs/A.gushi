"""
S1 轻判断通道 —— ⚠️ **已退役，不再被调用**（2026-10-06）

保留在仓库里只为备查，和 gushi3 保留 N4/N5 同理。

# 为什么退役

S1 的全部职责是「移动方式 → 时间倍数 · 玩家声明的时长 → 分钟」，
产出喂给 `R2_Time` 做 `段数 × 每段分钟 ÷ 倍数`。而那条路已经删掉了：
**移动耗时改由 L3 判断**（`本轮耗时分钟` 的语义改成"原地活动 + 赶路"），
因为移动方式和距离一样是没法用表描述的——见 `common/R2_time.py` 的模块说明。

S1 当初入选的三条理由里，"输出是标量、关推理也能做对"这条本身就是那个窄词汇的产物。
词汇一旦放宽到"你自己判断这一轮过了多久"，标量提取就没事可做了。

**别删这个文件**：它记录了"用旁路做轻判断"这个模式，将来如果有别的
真正适合旁路的任务（只依赖玩家输入、标量输出、判错代价可控），可以照这个结构再建。

---

S1 轻判断通道（旁路，不占跳数）

**不是层，是旁路。** 它与 L1 同时发起，在 L3 收尾前返回——因为 L1→L2 的耗时
远大于它，所以它**不增加关键路径长度**。

    主链：L1 ──────→ L2 ──────→ L3 ──────→ R2
    旁路：S1 ──────────────────────────────↗
          ↑ 与 L1 同时发起

# 入选条件（三条必须同时满足，见设计方案 §3.5）

| 条件 | 为什么 |
|------|--------|
| 只依赖玩家输入 | 能并行发起，不占跳数 |
| 输出是标量/枚举 | **关推理后模型对结构化提取仍有把握，对自由文本没有** |
| 判错代价可控 | 错了最多时间算不准，不会毁剧情 |

**当前入选**：移动方式 → 时间倍数 · 玩家声明的时长 → 分钟。一次调用产出两者。

**明确落选**（别往里塞）：感知失真 · NPC 回应 · 规则触发 · 环境叙事（都要叙事质量）；
事件边界 `is_new_event`（判错清空上下文，代价太大）；意图标签（多一个不可靠信号源）。

# 降级契约（本模块最重要的部分）

**旁路绝不能成为新的故障点。** 超时/失败 → 静默回落规则兜底：

| 失败项 | 兜底 |
|--------|------|
| 移动倍数 | 1.0（按步行算） |
| 声明时长 | 0（当没声明，走纯距离折算） |

因此这里的策略与大模型层**相反**：短超时（`QUICK_TIMEOUT`）+ 不重试
（`QUICK_MAX_RETRIES`）。等它不如不等的值——反正兜底结果只是"时间估算粗糙一点"。
"""

from common.base_cnf import (
    QUICK_TIMEOUT, QUICK_MAX_RETRIES, QUICK_FALLBACK_MULTIPLIER,
    QUICK_FALLBACK_DECLARED_MINUTES, QUICK_JUDGE_ENABLED, QUICK_MULTIPLIER_MIN,
    QUICK_MULTIPLIER_MAX, QUICK_DECLARED_MINUTES_MAX,
)
from common.LLMclient import LLMClient
from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads

logger = get_logger('S1_QuickJudge')


class S1_QuickJudge:
    """轻判断旁路：玩家输入 → (移动倍数, 声明时长分钟)"""

    def __init__(self):
        self.cli = LLMClient('S1')
        self.enabled = QUICK_JUDGE_ENABLED

    # ===================== 兜底 =====================

    @staticmethod
    def _fallback(reason: str) -> dict:
        """统一的兜底产出。**任何异常路径都必须走到这里，绝不向上抛。**"""
        logger.warn(f'S1 回落兜底（{reason}）：倍数={QUICK_FALLBACK_MULTIPLIER} '
                    f'声明={QUICK_FALLBACK_DECLARED_MINUTES}')
        return {
            '移动倍数': QUICK_FALLBACK_MULTIPLIER,
            '声明时长分钟': QUICK_FALLBACK_DECLARED_MINUTES,
            '方式描述': '',
            '兜底': True,
        }

    # ===================== 数值夹取 =====================

    @staticmethod
    def _clamp(raw, lo, hi, default):
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return default
        if v != v:              # NaN
            return default
        return max(lo, min(hi, v))

    # ===================== 主入口 =====================

    def judge(self, player_input: str) -> dict:
        """
        判断移动方式与声明时长。

        **这个方法永远不会抛异常**——失败即兜底。调用方可以放心地把它
        丢进线程池并行发起，不需要 try。

        :param player_input: 玩家本轮原话
        :return: {'移动倍数': float, '声明时长分钟': int, '方式描述': str, '兜底': bool}
        """
        if not self.enabled:
            return self._fallback('开关关闭')
        if not player_input or not player_input.strip():
            return self._fallback('空输入')

        try:
            sys_prompt = load_prompt('S1_quick_judge_sys.txt')
            context = [
                {'role': 'system', 'content': sys_prompt},
                {'role': 'user', 'content': player_input},
            ]
            reply = self.cli.chat_by_context(context, label='S1')
            result = safe_json_loads(reply, label='S1') or {}
        except Exception as e:
            return self._fallback(f'{type(e).__name__}: {e}')

        # ---- 夹取（关推理的模型给数字更容易乱来）----
        mult = self._clamp(result.get('移动倍数', QUICK_FALLBACK_MULTIPLIER),
                           QUICK_MULTIPLIER_MIN, QUICK_MULTIPLIER_MAX,
                           QUICK_FALLBACK_MULTIPLIER)
        try:
            declared = int(float(result.get('声明时长分钟', 0) or 0))
        except (TypeError, ValueError):
            declared = 0
        declared = max(0, min(declared, QUICK_DECLARED_MINUTES_MAX))

        out = {
            '移动倍数': mult,
            '声明时长分钟': declared,
            '方式描述': str(result.get('方式描述', '') or '')[:20],
            '兜底': False,
        }
        logger.info(f'S1：方式「{out["方式描述"] or "未提"}」×{mult}'
                    f' | 声明 {declared} 分')
        return out
