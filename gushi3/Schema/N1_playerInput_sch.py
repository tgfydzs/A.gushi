

from enum import Enum, unique
from common import time_int, uuid, log

logger = log.SimpleLogger()


class N1PlayerInput:
    """
    node1 玩家输入
    """


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

    def to_dict(self):
        return {
            '玩家输入': self._raw_text,
            '时间戳': time_int.ts_to_readable(self._timestamp),
            '会话id': self._session_id
        }






if __name__ == '__main__':
    a = N1PlayerInput('good')

    print(a.to_dict())