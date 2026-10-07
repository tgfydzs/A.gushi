"""
gushi4 Web 服务 —— 挂在 `GameEngine` 上的 HTTP + SSE 外壳。

    python web/server.py                 # 默认 127.0.0.1:8765
    python web/server.py --port 9000

# 只监听 127.0.0.1

没有认证、没有多用户。这是**本机单人工具**，绑到 0.0.0.0 等于把整个游戏状态
（含所有 NPC 记忆和存档）开放给局域网。要远程访问得先加认证。

# 为什么 SSE 用一条常驻通道

页面上开**一条** `/api/stream` 并保持，`POST /api/input` 只负责触发回合，
所有事件从那条已有通道推下来，带 `round` 字段供前端归属。

每轮开一条新连接的话，`POST` 和 `GET` 之间的到达顺序就成了竞态：
回合可能在 SSE 连接建立**之前**就开始跑，前几个事件直接丢掉。

# 并发

- **回合**：`_state['running']` 保证同时只有一个回合线程；引擎内部还有一把锁兜底。
- **读接口**：实时读，不加锁（理由见 `common/engine.py` 的并发说明）。
"""

import argparse
import atexit
import json
import os
import queue
import sys
import threading

# 让 `python web/server.py` 也能 import 到 common/ 与 Schema/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flask import Flask, Response, jsonify, request, send_from_directory

from common.engine import GameEngine
from common.log import get_logger

logger = get_logger('WebServer')
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

app = Flask(__name__, static_folder=None)      # 静态文件自己管，见 index()
engine = GameEngine(session_id='gushi4-web')

# 回合调度：同一时刻只允许一个回合在跑
_state = {'running': False, 'thread': None}
_state_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════
# 回合执行
# ═══════════════════════════════════════════════════════════════

def _round_worker(text: str, move_info: dict):
    """
    一个回合的完整过程（对应终端主循环里的一次迭代）：

        N0 消费后台产出 + 显示场景  →  run_round

    跑在后台线程里，`POST /api/input` 立刻返回，进度从 SSE 推。
    """
    try:
        engine.consume_background()
        engine.show_scene()
        engine.run_round(text, move_info)
    except Exception as e:
        logger.error(f'回合异常：{type(e).__name__}: {e}', exc_info=True)
        engine.emit({'type': 'error', 'text': f'本轮异常：{type(e).__name__}: {e}'})
    finally:
        with _state_lock:
            _state['running'] = False
        engine.emit({'type': 'round_done'})


def _spawn_round(text: str, move_info: dict = None) -> bool:
    """起一个回合线程。已有回合在跑则返回 False。"""
    with _state_lock:
        if _state['running']:
            return False
        _state['running'] = True

    engine.emit({'type': 'round_queued', 'input': text})
    t = threading.Thread(target=_round_worker, args=(text, move_info), daemon=True)
    _state['thread'] = t
    t.start()
    return True


# ═══════════════════════════════════════════════════════════════
# 静态页
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route('/favicon.ico')
def favicon():
    # 空响应即可：不处理的话浏览器每次都会在控制台留一条 404，调试时碍眼
    return '', 204


# ═══════════════════════════════════════════════════════════════
# 只读接口
# ═══════════════════════════════════════════════════════════════

@app.route('/api/state')
def api_state():
    view = engine.scene_view()
    view['busy'] = _state['running']
    return jsonify(view)


@app.route('/api/locations')
def api_locations():
    return jsonify(engine.location_tree())


@app.route('/api/npcs')
def api_npcs():
    return jsonify({'npcs': engine.list_npcs(),
                    'total': len(engine.gc.all_npc_list)})


@app.route('/api/npc/<path:name>')
def api_npc(name):
    return jsonify(engine.npc_detail(name))


@app.route('/api/context/<which>')
def api_context(which):
    return jsonify(engine.contexts(which))


@app.route('/api/saves')
def api_saves():
    return jsonify({'saves': engine.list_saves()})


@app.route('/api/history')
def api_history():
    """
    叙事流历史。**页面 F5 之后靠它恢复**——否则一刷新就只剩一片空白。

    不含 `stage`（进度条是瞬时的，恢复它没意义）。
    """
    return jsonify({'events': engine.history(), 'round': engine.round_no})


# ═══════════════════════════════════════════════════════════════
# 输入接口
# ═══════════════════════════════════════════════════════════════

@app.route('/api/input', methods=['POST'])
def api_input():
    """
    `{'text': '...'}`

    返回 `action` 告诉前端接下来干什么：

    | action       | 前端该做的                                     |
    |--------------|------------------------------------------------|
    | `started`    | 回合已在后台跑，等 SSE 的 `round_done`         |
    | `need_move`  | 输入里有移动标记，弹地点选择器，再 POST `/api/move` |
    | `skip`/`empty` | 命令已处理，什么都不用做                     |
    | `break`      | 玩家要退出（网页上一般不理）                   |
    | `busy`       | 上一轮还没跑完                                 |
    """
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'action': 'empty'})

    act = engine.handle_input(text)
    action = act['action']

    if action == 'run':
        if not _spawn_round(act['text'], act.get('move_info')):
            return jsonify({'action': 'busy'})
        return jsonify({'action': 'started'})

    if action == 'need_move':
        # 移动目标交给前端选（网页是地点树，终端是 input() 菜单）
        return jsonify({'action': 'need_move', 'raw': act['raw'],
                        'marker': act['marker']})

    return jsonify({'action': action})


@app.route('/api/move', methods=['POST'])
def api_move():
    """
    `{'path': '苍云大陆·东域·青木山脉', 'raw': '(可选) 带 {移动} 的原文'}`

    不带 `raw` 就是纯粹的地点树点击移动；带 `raw` 则把路径填回原文再跑，
    这样「我去{移动}看看」里的前后文不会丢。
    """
    data = request.get_json(silent=True) or {}
    path = (data.get('path') or '').strip()
    raw = (data.get('raw') or '').strip()
    if not path:
        return jsonify({'ok': False, 'error': '缺少 path'}), 400

    text, move_info = engine.resolve_move(raw or '/move', path)
    if text is None:
        return jsonify({'ok': False, 'error': '路径为空'}), 400

    if not _spawn_round(text, move_info):
        return jsonify({'ok': False, 'error': 'busy'}), 409
    return jsonify({'ok': True, 'text': text, 'move': move_info})


@app.route('/api/command', methods=['POST'])
def api_command():
    """`{'cmd': '/save 名字'}` —— 命令输出通过事件流回来，这里只报执行结果。"""
    data = request.get_json(silent=True) or {}
    cmd = (data.get('cmd') or '').strip()
    if not cmd:
        return jsonify({'action': 'empty'})
    act = engine.handle_input(cmd)
    return jsonify({'action': act['action']})


@app.route('/api/undo', methods=['POST'])
def api_undo():
    return jsonify({'ok': engine.undo()})


# ═══════════════════════════════════════════════════════════════
# SSE
# ═══════════════════════════════════════════════════════════════

def _sse(event: dict) -> str:
    return 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'


@app.route('/api/stream')
def api_stream():
    """
    一条常驻事件流。页面加载时开一次，一直用到页面关闭。

    15 秒没事件就发一个 SSE 注释当心跳——代理和浏览器会把长时间静默的连接掐掉。
    """
    q = engine.subscribe(maxsize=500)

    def generate():
        try:
            yield _sse({'type': 'hello'})
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ': ping\n\n'
                    continue
                yield _sse(ev)
        except GeneratorExit:
            pass                      # 客户端断开，正常路径
        finally:
            engine.unsubscribe(q)

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',    # 有反向代理时禁用缓冲
    })


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

def _shutdown():
    # 存档写在后台线程里，daemon 线程随进程消失——不等它落盘就会丢存档
    try:
        engine.close()
    except Exception:
        pass


atexit.register(_shutdown)


def main():
    ap = argparse.ArgumentParser(description='gushi4 web 服务')
    ap.add_argument('--host', default='127.0.0.1',
                    help='默认只监听本机；改成 0.0.0.0 会把存档暴露到局域网')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--debug', action='store_true', help='Flask 调试模式（会重启进程）')
    args = ap.parse_args()

    logger.info(f'gushi4 web 启动 → http://{args.host}:{args.port}')
    print(f'\n  gushi4 · http://{args.host}:{args.port}\n')
    # threaded=True：引擎是同步阻塞的，一个回合要跑好几秒，
    # 单线程的话回合期间连状态都刷不出来
    app.run(host=args.host, port=args.port, threaded=True,
            debug=args.debug, use_reloader=False)


if __name__ == '__main__':
    main()
