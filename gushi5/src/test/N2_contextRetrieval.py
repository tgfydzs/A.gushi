"""
【已搬走】这个文件原来就是**主循环**（gushi3 把调度放在 src/test/ 里，而且文件名与
`common/N2_contextRetrieval.py`（N2 层本体）同名，极易看错）。

现在主循环在 **`main.py`**：引擎收进 `common/engine.py`，终端只剩"读键盘 + 画事件"，
网页 `web/server.py` 挂的是同一个引擎。

这个桩留着只为两件事：
  1. 老习惯敲 `python src/test/N2_contextRetrieval.py` 还能跑起来；
  2. 谁翻到这里，一眼看到"它搬走了、搬到哪"。

可以直接删。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

print('⚠️ 这个文件已不是主循环——主循环搬到了 main.py，这里只是转发。\n')

from main import main   # noqa: E402

if __name__ == '__main__':
    main()
