import os

BASE_PATH = r'E:\A.gushi\gushi3'
DEBUG_MODE = False  # 调试快照开关，运行中可用 /debug 切换




# ----------------- 日志 ---------------------
LOG_PATH = r'log\N3.log'
LOG_FILE_LEVEL = 20 #日志文件记录等级
LOG_CONSOLE_LEVEL = 30 #控制台打印等级（40=ERROR，30=WARNING）


# ---------------- 数据驱动 ----------------------
WORLD_DATA_FILE = r'data\B1_worldState\global.json'
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


# ---------------- 分层模型路由 ----------------
# 按【层名】覆写模型；未列出的层回落到上面的 MODEL_NAME。
# 用途：判断/分发/结构化这类浅任务（N2/N3）可换更快的小模型，
#       创作类重任务（N5/N9b）保留主模型。
# 例：LAYER_MODEL_OVERRIDE = {'N2': 'deepseek-v4-lite', 'N3': 'deepseek-v4-lite'}
LAYER_MODEL_OVERRIDE = {}













