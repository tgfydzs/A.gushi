import os

# 由本文件位置反推项目根（common/base_cnf.py → common → 项目根）。
# 原来写死成 r'E:\A.gushi\gushi3'：换机器或换盘符就全落空，而且失败点散在
# log / save / load_json / prompt 各处，报错信息与真正原因隔着一整个调用链。
BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBUG_MODE = False  # 调试快照开关，运行中可用 /debug 切换




# ----------------- 日志 ---------------------
LOG_PATH = r'log\gushi5.log'   # 原来叫 log\N3.log —— 名不副实（日志不是 N3 一层的）
LOG_FILE_LEVEL = 20 #日志文件记录等级
LOG_CONSOLE_LEVEL = 30 #控制台打印等级（40=ERROR，30=WARNING）


# ---------------- 数据驱动 ----------------------
WORLD_DATA_FILE = r'data\B1_worldState\global.json'
#: 世界**地理与配置**的真源（地点/路/段/坐标/`配置` 行）。见 gushi4 的 `地图格式.md`。
#: JSON 里只剩"没有几何、也不常手改"的那几份列表（规则/状态/演变/知识）。
#: ⚠️ 地理数据**不许**再留在 JSON 里——两处真源必然漂，而且漂了看不出来。
WORLD_MAP_FILE = r'data\B1_worldState\世界.map'
WORLD_DATA_SELECT = '苍云大陆·仙途诡道'

NPC_DATA_ROOT_PATH = r'data\B3_npc'

SAVE_ROOT_PATH = r'save'    # 存档根目录
SAVE_VERSION = 1            # 存档格式版本号，Schema 变更时递增



# ---------------- ai配置 --------------------------
name = 'deepseek'

if name == 'deepseek':
    API_KEY = os.environ['DEEPSEEK_API_KEY']
    BASE_URL = 'https://api.deepseek.com/chat/completions'
    MODEL_NAME = 'deepseek-flash'
elif name == '魔搭':
    API_KEY = os.environ['MODELSCOPE_API_KEY']
    BASE_URL = 'https://api-inference.modelscope.cn/v1/chat/completions'
    MODEL_NAME = 'Qwen/Qwen3.5-27B'


# ---------------- 调用策略（分层模型路由要用）----------------
#
# ⚠️ 这几项是**从 gushi4 的 LLMclient 搬来的**。上面三个名字（`API_KEY`/`BASE_URL`/
# `MODEL_NAME`）是 gushi3 的老名字，别处还在用，所以这里**做别名**而不是改名——
# 两份名字指同一批值，避免"改了这边忘了那边"。
DEFAULT_API_KEY = API_KEY
DEFAULT_BASE_URL = BASE_URL
DEFAULT_MODEL = MODEL_NAME

DEFAULT_TIMEOUT = 90              # 单次请求超时秒数
DEFAULT_MAX_RETRIES = 3           # 最大重试次数
RETRY_BACKOFF_SECONDS = [1, 3, 8] # 每次重试前的退避
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# 连接池复用（省 TCP+TLS 握手）。一轮流水线有十几次调用，
# 且 N45/N6 的并行线程共享同一实例的 Session。
USE_SESSION_POOL = True


# ---------------- 分层模型路由 ================
# 按【层名】覆写这一层的调用参数。未列出的字段回落到上面的全局默认值。
#
# 可填的键（都可选）：
#     thinking     False = 关推理（**本表最常用的一个**）
#     model        模型名（暂时全用同一个模型，留空即可）
#     temperature  本层默认温度（不填 = 用 API 默认）
#     timeout / max_retries
#
# —— 为什么"推理开关"值得单独一个键 ——
# 推理 token 实测占输出的 67~96%，**按输出计费**，而且是延迟的主因。
# 关掉它 = 同时省延迟和账单。gushi4 的消融实测（关键路径 = N2 + N3 + 最慢的 N45 + N6）：
#     全开 41.6s / 只关 N3 之外……见下面的表
#
# 定档依据是 gushi4 阶段 1 的消融（75 轮）：
#     · 关掉"感知分发"的推理 → "看不透"式表达 92% → 58%，**这是承重件，别关**
#     · 关掉回应层/结算层的推理 → 未测出质量代价，约 1.2~1.3× 提速
# ⚠️ 那是 gushi4 把"事件+感知"合成一层（L1）跑出来的数据；gushi5 的 N2/N3 是分开的
#    两层，所以照搬是**外推**——要再扫档位就用下面的 `G5_THINKING_OFF`，**别改表**。
#
# —— 当前生效档：**只留 N3 开推理**，其余全关（表在下面，改档就改表）——
# 实测依据（2026-10-17 09:07~09:10 那 4 轮真实日志，见 log/gushi5.log）：
#     单次 N2 3.5~6.3s · N3 13.1~17.0s · N45 7.3~12.1s · N6 3.8~16.5s，一轮墙钟 38~47s
#     推理 token 占输出 63~90%（如 N45 `out=1263 reason=953`、N3 `out=2345 reason=2109`）
# 复验判据：启动日志 `LLMClient 初始化 | N45 | ... | think=off`，
#     且每笔调用日志里的 `reasoning=` 变成 `0字`。
#
# 合法键**只有 6 个**（= 有 `LLMClient('层名')` 的调用点）：N2 / N3 / N45 / N6 / N9 / N9c。
# ⚠️ **没有 N4 / N5**：那两层已合并成 N45（`common/N45_reason_response.py`，
#    `common/N4_reasoning.py` / `N5_response.py` 是弃用残留，无人 import）。
#    写 'N4' / 'N5' **不报错也不生效**——查表落空 → 静默回落默认（开）。
#
# 表就下面那一张，改档**只改表**（这里不再另列一份示例，免得两处口径漂）。
LAYER_MODEL_OVERRIDE = {
    'N2':  {'thinking': False},   # 事件判断：输出全是名单/枚举，不需要想
    'N45': {'thinking': False},   # 理解+回应（**原 N4+N5 合并，键名只有 N45**）
    'N6':  {'thinking': False},   # 结算/收尾
    'N9':  {'thinking': False},   # 导演（N9a 规则触发 / N9b 环境叙事）
    'N9c': {'thinking': False},   # 幕后行为 + 剧情级传播
    # 'N3' 故意不填 = 保持推理开：感知分发是**承重件**
    # （关掉后"看不透"式表达 92% → 58%，见上面的消融）
}


# —— 实验钩子：运行时覆写推理开关（**临时扫档位用**）——
#
#     G5_THINKING_OFF=N2,N6      → 关掉 N2 和 N6 的推理
#     G5_THINKING_OFF=ALL        → 关掉全部
#
# 这是**运行时覆写，不是第二份配置**——上面那张表仍是唯一事实来源。
# 存在的理由：扫档位要在多个配置间来回跑，改文件容易忘改回来，
# 也容易把"这次跑的是哪一档"记混。实验脚本给每次运行设好环境变量，配置表原封不动。
_THINKING_OFF_ENV = 'G5_THINKING_OFF'
_ALL_LAYERS = ('N2', 'N3', 'N45', 'N6', 'N9', 'N9c')

_off = os.environ.get(_THINKING_OFF_ENV, '').strip()
if _off:
    _layers = _ALL_LAYERS if _off.upper() == 'ALL' else \
        tuple(x.strip() for x in _off.split(',') if x.strip())
    for _l in _layers:
        LAYER_MODEL_OVERRIDE.setdefault(_l, {})['thinking'] = False













