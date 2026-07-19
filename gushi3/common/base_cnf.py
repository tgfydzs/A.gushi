import os

BASE_PATH = r'E:\A.gushi\gushi3'

# ----------------- 日志 ---------------------
LOG_PATH = r'log\N3.log'
LOG_FILE_LEVEL = 20 #日志文件记录等级
LOG_CONSOLE_LEVEL = 30 #控制台打印等级


# ---------------- 数据驱动 ----------------------
WORLD_DATA_FILE = r'data\B1_worldState\global.json'
WORLD_DATA_SELECT = '苍云大陆·仙途诡道'

NPC_DATA_ROOT_PATH = r'data\B3_npc'

PLAYER_DATA_FILE = r'data\player\叶尘.json'

SAVE_ROOT_PATH = r'save'    # 存档根目录
SAVE_VERSION = 1            # 存档格式版本号，Schema 变更时递增



# ---------------- ai配置 --------------------------
name = 'deepseek'

if name == 'deepseek':
    API_KEY = os.environ['DEEPSEEK_API_KEY']
    BASE_URL = 'https://api.deepseek.com/chat/completions'
    MODEL_NAME = 'deepseek-v4-flash'
elif name == '魔搭':
    API_KEY = os.environ['MODELSCOPE_API_KEY']
    BASE_URL = 'https://api-inference.modelscope.cn/v1/chat/completions'
    MODEL_NAME = 'Qwen/Qwen3.5-27B'













