"""
node1 玩家输入
"""

from enum import Enum, unique
from common import time_int, uuid, log

logger = log.SimpleLogger()


class N1PlayerInput:


    @unique
    class InputType(str, Enum):
        ...

    def __init__(self, raw_text: str):
        self._raw_text = raw_text
        self._timestamp = time_int.get_minute_ts()
        self._session_id = uuid.generate_session_id()
        logger.info(f'原始文本: {self._raw_text}')
        logger.info(f'timestamp: {self._timestamp}')
        logger.info(f'session_id: {self._session_id}')






if __name__ == '__main__':
    a = N1PlayerInput('good')