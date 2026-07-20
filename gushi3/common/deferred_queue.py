"""
DeferredQueue — 线程安全的N9c产出队列。

后台心跳线程写入（put），主循环N0消费（get）。
不共享任何 NPC 实例引用，仅传递序列化增量。
"""

import json
from queue import Queue as _StdQueue

from common.log import get_logger

logger = get_logger("DeferredQueue")


class DeferredQueue:
    """
    线程安全的 N9c 产出队列。

    每条消息格式：
    {
        "source": "heartbeat" | "idle",
        "trigger_reason": "3轮触发" | "12小时推进" | "空闲心跳",
        "NPC自主行为": {"NPC名": {"动作": str, "NPC增量": dict}},
        "剧情级传播": [{"信息": str, "到达NPC": [str], "方式": str}],
    }
    """

    def __init__(self):
        self._queue: _StdQueue = _StdQueue()

    def put(self, item: dict):
        """后台线程调用，推入一条 N9c 产出"""
        self._queue.put(item)
        count = self._queue.qsize()
        logger.debug(f'📥 N9c产出入队，当前队列深度：{count}')

    def get_all(self) -> list[dict]:
        """
        主线程调用（N0），取出队列全部消息。
        非阻塞——无消息时返回空列表。
        """
        items = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except Exception:
                break
        if items:
            logger.info(f'📤 N0消费队列：{len(items)} 条N9c产出')
        return items

    @property
    def size(self) -> int:
        return self._queue.qsize()

    def clear(self):
        """
        清空队列。读档或撤回时使用——旧心跳数据与恢复后的状态不兼容。
        非阻塞，丢弃所有未消费消息。
        """
        count = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                count += 1
            except Exception:
                break
        if count:
            logger.info(f'🗑️ 清空队列：{count} 条未消费消息已丢弃')
