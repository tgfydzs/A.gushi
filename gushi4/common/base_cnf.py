"""
gushi4 全局配置 —— 所有【程序级】设置都在这里。

与【世界观级】设置的分工：
    本文件       程序级：路径、模型、超时、日志等级
    data/B1_worldState/世界.map 的 `配置` 行   世界观级：表上限、心跳间隔、
                                              机缘间隔…
    新增世界观不应该需要改本文件。

⚠️ 世界观配置的真源是 **`世界.map` 的 `配置` 行**，不是 `global.json`——
    地理与配置在阶段 0 一起搬到了 `.map`（见 `地图格式.md` §0）。
    `global.json` 现在只剩四份字符串列表（世界规则/世界状态/演变规则/知识）。
    改世界观配置**不用**重启：编辑器保存后会热重载（`GameController.reload_world`）。

改本文件后需要重启程序（模块级常量，不做热重载）。
"""

import os


# ══════════════════════════════════════════════════════════════
#  一、路径
# ══════════════════════════════════════════════════════════════

# 由本文件位置反推项目根（common/base_cnf.py → common → 项目根）。
# 曾经写死成 r'E:\A.gushi\gushi4'：换台机器或换个盘符就全落空，而且失败点散在
# log / save / load_json / prompt 各处——报错信息（如 makedirs 失败）与真正原因
# 隔着一整个调用链，排查成本远高于这一行。
BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEBUG_MODE = False              # 调试快照开关，运行中可用 /debug 切换

WORLD_DATA_FILE = r'data\B1_worldState\global.json'
#: 世界地理与配置的**真源**（地点/路/段/坐标/配置）。见 `地图格式.md`。
#: JSON 里只剩"没有几何、也不常手改"的那几份列表（规则/状态/演变/知识）。
#: ⚠️ 地理数据**不许**再留在 JSON 里——两处真源必然漂，而且漂了看不出来。
WORLD_MAP_FILE = r'data\B1_worldState\世界.map'
WORLD_DATA_SELECT = '苍云大陆·仙途诡道'      # 切换世界观改这里
NPC_DATA_ROOT_PATH = r'data\B3_npc'

SAVE_ROOT_PATH = r'save'        # 存档根目录（每个存档一个文件夹）
SAVE_VERSION = 1                # 存档格式版本号，Schema 变更时递增
# ⚠️ 2026-10-08「位置改坐标」那次**刻意没有升版本**：旧档（`当前所处位置` 是路径字符串）
# 在 `GameController.migrate_positions()` 里被**就地迁移**成坐标，读得进来、语义不变。
# 升了只会让这些本来能用的档报一句"可能 Schema 不兼容"的**假警报**——
# 而这项目最怕的就是"警告多了没人看"。真有不可迁移的变更时才升。


# ══════════════════════════════════════════════════════════════
#  二、日志
# ══════════════════════════════════════════════════════════════

LOG_PATH = r'log\gushi4.log'
LOG_FILE_LEVEL = 20             # 文件记录等级（10=DEBUG 20=INFO 30=WARN 40=ERROR）
LOG_CONSOLE_LEVEL = 30          # 控制台打印等级


# ══════════════════════════════════════════════════════════════
#  三、主模型
# ══════════════════════════════════════════════════════════════

# 可选：'deepseek' / '魔搭' / '自建'
# 决定下面三个默认值，各层未覆写时都用这套
PROVIDER = 'deepseek'

if PROVIDER == 'deepseek':
    DEFAULT_API_KEY = os.environ['DEEPSEEK_API_KEY']
    DEFAULT_BASE_URL = 'https://api.deepseek.com/chat/completions'
    DEFAULT_MODEL = 'deepseek-flash'
elif PROVIDER == '魔搭':
    DEFAULT_API_KEY = os.environ['MODELSCOPE_API_KEY']
    DEFAULT_BASE_URL = 'https://api-inference.modelscope.cn/v1/chat/completions'
    DEFAULT_MODEL = 'Qwen/Qwen3.5-27B'
elif PROVIDER == '自建':
    DEFAULT_API_KEY = os.environ.get('LOCAL_API_KEY', 'none')
    DEFAULT_BASE_URL = 'http://localhost:8000/v1/chat/completions'
    DEFAULT_MODEL = 'local-model'
else:
    raise ValueError(f'未知的 PROVIDER：{PROVIDER}')


# ══════════════════════════════════════════════════════════════
#  四、分层模型路由
# ══════════════════════════════════════════════════════════════

# 用法：LLMClient('S1') 会查这张表，命中则覆写，未命中用主模型。
#
# 每个键都可选，不填的项回落到主模型默认值：
#     thinking     False = 关闭推理（见下方说明）★ 当前唯一在用的开关
#     model        模型名（暂不用——见下）
#     base_url     接口地址
#     api_key_env  从哪个环境变量取 key
#     timeout      单次请求超时秒数
#     max_retries  最大重试次数
#     temperature  默认温度
#
# ★ 关于模型：**目前所有层都用同一个 DeepSeek 模型，区别只在开不开推理。**
#   "极简模型"就是"同一个模型关掉思考"——不需要另找一个模型。
#   下面保留 model/base_url/api_key_env 三个字段，是为了将来真接入不同模型时
#   不用改代码，现在留空即可。
LAYER_MODEL_OVERRIDE = {

    # ---- S1 轻判断通道：同模型，关推理 ----
    # 它只做结构化提取（移动方式 → 倍数），输出标量，不需要想。
    # 关推理同时省下延迟和成本（推理 token 按输出计费，这里能省 ~20 倍）。
    'S1': {'thinking': False, 'timeout': 20, 'max_retries': 1},

    # ---- 关键路径三层 ----
    #
    # L1 **开推理**（2026-10-06 改）。理由不是"scope 判得准"——对照实验
    # （`src/test/diag_l1_thinking.py`）证明推理买不来 scope 准确性，开推理后
    # 悄悄话的 private 反而从 1/5 掉到 0/5。
    #
    # 它买来的是**感知分发的稳定性**：
    #     关推理 → 感知人数 [(0人,2次), (1人,2次), (3人,1次)]   空转 2 次，还有 1 次给了 3 个人
    #     开推理 → 感知人数 [(1人,5次)]                          稳定
    # 而 R3 的距离过滤已删（判断权交回 L1），所以"给了 3 个人"就是**真的泄漏**。
    # 判断力必须落在 L1 身上，就得给它预算。代价：L1 单次 1.5s → 5.4s，每轮 +3.9 秒。
    'L1': {'thinking': True},
    'L2': {'thinking': False},
    'L3': {'thinking': False},

    # ---- L1 的兜底通道：**强制开推理** ----
    # L1 关掉推理后会偶尔把玩家的真实实力**逐字抄进感知**（实测 1/12）。
    # 那等于没有失真——本设计核心机制的硬失败。
    # 检测到就单独用这个通道把 L1 重跑一次。
    'L1_retry': {'thinking': True},

    # ---- 需要固定温度的层 ----
    # 温度是"质量需求"，和开不开推理无关，所以即便同一个模型也要显式设。
    'B1a': {'temperature': 0.3},    # 规则触发：严谨因果链
    'B1b': {'temperature': 0.9},    # 环境叙事：创意写作
    'B2':  {'temperature': 0.6},    # 幕后调度：中温
    'MEM': {'temperature': 0.4},    # 记忆摘要：要忠实，少发挥
}


# ══════════════════════════════════════════════════════════════
#  四·补 实验钩子：阶段 1 推理消融
# ══════════════════════════════════════════════════════════════
#
#   G4_THINKING_OFF=L1,L3   → 关掉 L1 和 L3 的推理
#   G4_THINKING_OFF=ALL     → 关掉全部（L1/L2/L3）
#
# 这是**运行时覆写**，不是第二份配置——上面那张表仍是唯一事实来源。
# 存在的理由只有一个：做消融实验要在多个配置间来回跑，
# 改文件容易忘改回来，而且容易把"这次跑的是哪一档"记混。
# 实验脚本给每次运行设好环境变量，配置表原封不动。
_THINKING_OFF_ENV = 'G4_THINKING_OFF'
_ALL_LAYERS = ('L1', 'L2', 'L3')


# ══════════════════════════════════════════════════════════════
#  六·补 L1 触发式重试
# ══════════════════════════════════════════════════════════════

# ⚠️ **默认关闭。实测触发条件不可靠。**
#
# 原本的设想：L1 关掉推理后会偶尔把玩家真实实力逐字抄进感知（近修为场景下 1/12），
# 检测到就用 `L1_retry` 重跑一次。
#
# **但真跑起来发现触发条件是错的：**
#
#     "感知里出现了玩家的真实等级" ≠ 失真失败
#
# 金丹后期的白鹤看穿炼气中期的玩家，写"你一眼看出他不过炼气中期"是**正确**的
# 失真结果——设计文档 §2.3 的表格里，"高阶看低阶 → 炼气期蝼蚁"本来就是期望行为。
#
# 实测：白鹤在场时 **2 轮触发 1 轮（50%）**，触发时 L1 从 2.1s 变 9.6s——
# **把优化整个吐回去了**。而近修为场景下才 8%。触发率跟在场者修为强相关。
#
# 要修得先能判断"这个观察者本来该不该看穿"，那需要比较双方等级——
# 而**"不给刻度"正是本设计的硬规矩**（§2.3），比较等级等于把刻度引回来。
#
# 所以先关掉。那 8% 的近修为复述**暂时接受**：它的危害是"同级 NPC 不该知道
# 却知道了"，属于质量问题，不是崩溃。
#
# 代码保留（`L1_perception._has_truth_echo` + `_call_llm(retry=True)`），
# 等有更好的触发信号再打开。
L1_THINKING_RETRY_ENABLED = False

_off = os.environ.get(_THINKING_OFF_ENV, '').strip()
if _off:
    _layers = _ALL_LAYERS if _off.upper() == 'ALL' else \
        tuple(x.strip() for x in _off.split(',') if x.strip())
    for _l in _layers:
        LAYER_MODEL_OVERRIDE.setdefault(_l, {})['thinking'] = False


# ══════════════════════════════════════════════════════════════
#  五、调用策略
# ══════════════════════════════════════════════════════════════

# 主模型：质量优先，值得等、值得重试
DEFAULT_TIMEOUT = 90
DEFAULT_MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [1, 3, 8]
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# S1 轻判断（同模型 + 关推理）：判错代价可控，**失败就走规则兜底，不值得等**
# 注意这是与大模型层相反的策略——见 S1_quick_judge 的降级契约
QUICK_TIMEOUT = 20
QUICK_MAX_RETRIES = 1

# 连接池复用（省 TCP+TLS 握手）
# 一轮流水线有十几次调用，且 L2 的并行线程共享同一实例的 Session
USE_SESSION_POOL = True


# ══════════════════════════════════════════════════════════════
#  六、S1 轻判断通道
# ══════════════════════════════════════════════════════════════

# S1 只做"只看玩家输入 + 输出标量 + 判错代价可控"的判断。
# 入选条件见 设计方案.md §3.5，不要往里塞别的。

# —— 关推理怎么发 ——
# LLMClient 在 thinking=False 时，往请求体里塞一行（裸 requests，不需要 SDK）：
#     payload['thinking'] = {'type': 'disabled'}
# 开了推理时 response 里会多出 message.reasoning_content，
# **它按输出 token 计费**——实测占输出的 67~96%。这是延迟和成本的双料大头。

# —— 数值夹取（不推理时模型给的数字更容易乱来）——
QUICK_MULTIPLIER_MIN = 0.1      # 时间倍数下限（比走路还慢）
QUICK_MULTIPLIER_MAX = 100.0    # 时间倍数上限（传送级）
QUICK_DECLARED_MINUTES_MAX = 52_560_000   # 玩家声明时长上限（100年）

# —— 兜底值（S1 失败时回落）——
QUICK_FALLBACK_MULTIPLIER = 1.0             # 未识别移动方式 → 按步行
QUICK_FALLBACK_DECLARED_MINUTES = 0         # 未声明 → 走规则折算

# —— 开关 ——
# 关掉后 S1 完全不发起调用，移动方式恒为兜底值（纯规则模式）。
# 用于：调试主链时排除旁路干扰，或排查旁路引入的问题。
QUICK_JUDGE_ENABLED = True


# ══════════════════════════════════════════════════════════════
#  七、并行度
# ══════════════════════════════════════════════════════════════

# L2 角色层：每 NPC 一次调用，并行执行
LAYER2_MAX_WORKERS = 6

# 后台心跳：限制并发，避免和主线程抢 API 额度
BACKGROUND_MAX_WORKERS = 3


# ══════════════════════════════════════════════════════════════
#  八、记忆管理
# ══════════════════════════════════════════════════════════════

# 记忆巩固：超额时把最旧一批压缩成一条摘要（而不是直接丢弃）
# 放后台跑，不占关键路径。详见 设计方案.md §七
MEMORY_CONSOLIDATION_ENABLED = True
MEMORY_CONSOLIDATE_BATCH = 10       # 每次压缩多少条最旧记忆
MEMORY_MIN_KEEP = 5                 # 压缩时至少保留多少条原文
