import os

BASE_PATH = r'E:\A.gushi\gushi3'
LOG_PATH = r'log\N2.log'
LOG_FILE_LEVEL = 10 #日志文件记录调试+
LOG_CONSOLE_LEVEL = 30 #控制台打印警告+


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













