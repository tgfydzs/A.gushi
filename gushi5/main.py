"""
gushi5 主流程（**终端前端**）

引擎在 `common/engine.py`，这里只剩两件事：**读键盘、把事件画到终端**。
网页前端（`web/server.py`）挂的是同一个引擎，所以两边不会漂移。

    python main.py            # 终端版
    python start.py           # 网页版（默认 127.0.0.1:8765）
    python start.py term      # 网页版的壳，跑终端

# 每轮的调用链

    N0 消费后台产出 + 显示场景
    N1 读输入 + 命令
    ── 快照（供 !! 回滚）──
    N2 事件判断                  ← 关键路径第 1 跳
    N3 感知分发                  ← 第 2 跳
    N45→N6 并行 per NPC          ← 第 3/4 跳
    N7 地段传播（纯规则）
    玩家自主行动（仅无 NPC 时）
    N8 耗时聚合（纯规则）→ 推进时钟 → 移动落盘
    心跳触发

**关键路径 LLM 跳数：N2 → N3 → N45 → N6，共 4 跳。**
（N45 与 N6 同一池并行，所以串行长度是 `N2 + N3 + max(N45) + max(N6)`。）

移动有两种打法：
  · 打字说去哪（自由文本，N2 判 `move_style`、N8 折算耗时）
  · `{移动}` 或 `/move` —— **交互式导航**（`[数字]` 下钻 / `[c]` 确认 / `[b]` 返回 /
    `[x]` 取消）。它要一问一答地选节点，**所以只有终端能做**；网页端请点地图。
"""

from common.engine import GameEngine
from common.progress import terminal_renderer

from common import log as log_cfg


def main():
    # 正式入口要显式 setup：不调的话日志引擎会"懒初始化"并警告——
    # 而懒初始化意味着**日志可能和别的进程混到同一个文件**。
    log_cfg.setup(session_id='gushi5-term')

    engine = GameEngine(session_id='gushi5')
    engine.attach(terminal_renderer)

    while True:

        # ── N0 ──
        engine.consume_background()
        engine.show_scene()

        # ── N1 ──
        try:
            raw = input('>')
        except EOFError:
            break
        except KeyboardInterrupt:
            print('\n（中断）')
            break

        # 交互式导航要在**读输入这一步**处理掉：它自己会一问一答地要键盘。
        # 交给 `handle_input` 就晚了——那边是"这条输入该怎么走"的判断，不该阻塞。
        if engine.is_move_input(raw):
            text, move_info = engine.resolve_move(raw)
            if text is None:
                continue                       # 玩家取消了导航
            engine.run_round(text, move_info)
            continue

        act = engine.handle_input(raw)
        action = act['action']

        if action == 'break':
            break
        if action in ('empty', 'skip'):
            continue

        engine.run_round(act['text'])

    # 存档在后台线程写盘，而线程是 daemon 的——**进程一退，没写完的存档就没了**。
    # `/save` 之后紧接着 `exit` 会踩到这条。close() 里兜底等它落盘。
    engine.close()


if __name__ == '__main__':
    main()
