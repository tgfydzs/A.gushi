"""
gushi4 主流程（**终端前端**）

引擎在 `common/engine.py`，这里只剩两件事：读键盘、把事件画到终端。
网页前端（`web/server.py`）挂的是同一个引擎，所以两边不会漂移。

每轮的调用链：

    N0 消费后台产出 + 显示场景
    N1 读输入 + 命令
    ── 快照（供 !! 回滚）──
    S1 旁路 ‖ L1 感知过滤         ← 同时发起；S1 只依赖玩家输入，不占跳数
    L2 角色层（每 NPC 一次，并行） ← 关键路径第 2 跳
    R1 地段传播（纯规则）
    L3 结算层                     ← 关键路径第 3 跳
    R2 耗时折算（纯规则）→ 推进时钟
    心跳触发

**关键路径 LLM 跳数：L1 → L2 → L3，共 3 跳**（gushi3 是 4）。
每 NPC 串行链 1 跳（gushi3 是 2）。

⚠️ 跳数是**假设**不是结论——按设计方案 §八，阶段 2 才用实验定 L1 要不要拆。

`{移动}` 走**终端专用**的交互式地点菜单（`resolve_move_markers` → `navigate_interactive`，
会阻塞在 `input()` 上）；网页走地点树的点击。引擎自己从不读输入。
"""

from common.engine import GameEngine, terminal_renderer
from common.small_tool import resolve_move_markers


def main():
    engine = GameEngine(session_id='gushi4')
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

        act = engine.handle_input(raw)
        action = act['action']

        if action == 'break':
            break
        if action in ('empty', 'skip'):
            continue

        if action == 'need_move':
            # 终端走交互式菜单；玩家取消（返回 None）则跳过本轮
            text, move_info = resolve_move_markers(
                raw, engine.gc.b1WorldLayer, engine.gc.player)
            if text is None:
                continue
        else:
            text, move_info = act['text'], act.get('move_info')

        engine.run_round(text, move_info)

    # 存档在后台线程写盘，而线程是 daemon 的——**进程一退，没写完的存档就没了**。
    # `/save` 之后紧接着 `exit` 会踩到这条。close() 里兜底等它落盘。
    engine.close()


if __name__ == '__main__':
    main()
