"""
gushi5 Web 服务 —— 挂在 `GameEngine` 上的 HTTP + SSE 外壳。

    python web/server.py                 # 默认 127.0.0.1:8765
    python web/server.py --port 9000
    python start.py                      # 启动器（会挑端口、装依赖、开浏览器）

前端 `static/` 是从 gushi4 搬来的：**同一份 HTTP + SSE 契约**（路由名、事件类型、
状态字段形状都没变），所以那份 `app.js` 能原样用。契约的落点在 `common/engine.py`
的只读视图与事件里——改任何一边都要照另一边改。

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

from common import map_io
from common.engine import GameEngine
from common import log as log_cfg
from common.log import get_logger

# 正式入口要显式 setup：不调的话日志引擎会"懒初始化"——那意味着日志可能
# 和别的进程混到同一个文件。放在建引擎**之前**，引擎初始化的日志才进得来。
log_cfg.setup(session_id='gushi5-web')

logger = get_logger('WebServer')
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

app = Flask(__name__, static_folder=None)      # 静态文件自己管，见 index()
# ⚠️ **不许按键排序**。Flask 默认 `sort_keys=True`，而 `.map` 的属性是**按书写顺序**
# 吐的——一排序，编辑器原样存回就会把整个文件重排（试过一次：改一个字段 → 159 行 diff）。
# `map_io.canonicalize` 是第二道保险，但这里不排序才是"编辑器看到的就是文件里的顺序"。
app.json.sort_keys = False
engine = GameEngine(session_id='gushi5-web')

# 回合调度：同一时刻只允许一个回合在跑
_state = {'running': False, 'thread': None}
_state_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════
# 回合执行
# ═══════════════════════════════════════════════════════════════

def _round_worker(text: str):
    """
    一个回合的完整过程（对应终端主循环里的一次迭代）：

        N0 消费后台产出 + 显示场景  →  run_round

    跑在后台线程里，`POST /api/input` 立刻返回，进度从 SSE 推。
    """
    try:
        engine.consume_background()
        engine.show_scene()
        engine.run_round(text)
    except Exception as e:
        logger.error(f'回合异常：{type(e).__name__}: {e}', exc_info=True)
        engine.emit({'type': 'error', 'text': f'本轮异常：{type(e).__name__}: {e}'})
    finally:
        with _state_lock:
            _state['running'] = False
        engine.emit({'type': 'round_done'})


def _spawn_round(text: str) -> bool:
    """起一个回合线程。已有回合在跑则返回 False。"""
    with _state_lock:
        if _state['running']:
            return False
        _state['running'] = True

    engine.emit({'type': 'round_queued', 'input': text})
    t = threading.Thread(target=_round_worker, args=(text,), daemon=True)
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


@app.route('/api/map')
def api_map():
    """
    小地图的数据：几何（地块/路/范围）+ 当前所在。

    **只读、不加锁**（同 `/api/locations`）。移动不走这里——点地图 = 下达**去向**，
    走的是 `POST /api/input`（合成一句"我往X去"），因为去哪要由 N2 判（`move_to`）、
    落坐标由代码做（`GameController.move_player_to`）。
    """
    return jsonify(engine.map_view())


@app.route('/api/locations')
def api_locations():
    """旧的地点树视图。地图上线后它只作为**没几何时的降级**保留。"""
    return jsonify(engine.location_tree())


# ═══════════════════════════════════════════════════════════════
# 世界编辑（阶段 2：改字段；阶段 3 再加几何）
# ═══════════════════════════════════════════════════════════════

@app.route('/api/map/edit')
def api_map_edit():
    """
    编辑器的数据：`世界.map` 的**扁平实体模型**（地块 / 路 / 几何 / 全局）。

    刻意与 `/api/map` 分开：那个是**画给玩家看的**（剥掉了 `产出` 这类概率字段，
    见 `FRONTEND_HIDDEN_ATTRS`），这个是**给编辑器用的**，要原样。
    合成的编辑器不能拿玩家视角的数据去回写——那会把剥掉的东西写没。
    """
    import io as _io
    from common import map_io
    from Schema.B1_worldState_sch import REGION_TYPES          # `类型` 里哪些值是地区
    from common.base_cnf import BASE_PATH, WORLD_MAP_FILE
    path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
    try:
        with _io.open(path, encoding='utf-8') as f:
            world = map_io.parse_world(f.read())
    except FileNotFoundError:
        return jsonify({'ok': False,
                        'reason': f'找不到 {WORLD_MAP_FILE}——先跑 '
                                  f'`python -m common.map_io --export --from-json`'})
    except Exception as e:
        return jsonify({'ok': False, 'reason': f'{type(e).__name__}: {e}'})
    model = map_io.flatten_world(world)
    # `view` 就是 `/api/map` 那个形状——编辑器左侧那张图直接复用 `buildMapStatic`，
    # 于是"小地图"与"编辑器地图"真的是同一个组件，而不是长得像的两个。
    #
    # `regions`：**哪些节点是地区**，由 Python 一处算好告诉前端。
    # 为什么要它：判据是 `类型` 里那几个词（`REGION_TYPES`），前端要标"这块是地区"
    # 就得知道那张词表——**抄一份进 JS 就是两处真相**（改一处另一处悄悄漂），
    # 所以只传结论、不传规则。
    return jsonify({'ok': True, 'form': map_io.to_edit_form(model),
                    'view': engine.map_view(),
                    # `regions`：哪些节点**是**地区；`region_types`：**能填哪些值**。
                    # 两个都由 Python 一处算好告诉前端——前端要标"这块是地区"、
                    # 要给出一个可选值的下拉框，就得知道那张词表；**抄一份进 JS
                    # 就是两处真相**（改一处另一处悄悄漂），所以只传结论、不传规则。
                    'regions': [p['名'] for p in model['地块']
                                if str((p.get('属性') or {}).get('类型') or '').strip()
                                in REGION_TYPES],
                    'region_types': sorted(REGION_TYPES),
                    'path': WORLD_MAP_FILE,
                    # `problems` 是**拦住保存**的东西；`notices` 只是提醒
                    # （键写歪了、保留键写在没人读的地方）——两者分开回，
                    # 不然界面会把"提醒"说得像"存不了"
                    'problems': map_io.validate_edit(model),
                    'notices': map_io.key_notices(model)})


@app.route('/api/map/preview', methods=['POST'])
def api_map_preview():
    """
    **只看不写**：把"手上这份表单要是保存，地图会变成什么样"算出来。

    为什么非要有它：编辑器左侧那张图是**开编辑器时拍的一张快照**（`/api/map/edit`
    只给那一次），而表单是活的。删掉一条路、删掉一个地块、改一个名字，快照都不知道
    ——于是地图上留下一条**看得见却点不动、也删不掉**的幽灵线（用户报的正是这个）。

    这里是照**保存那条流水线**（`map_io.prepare_edit`）重算一遍再画：
    不校验（半成品也要能看）、不备份、不落盘、不碰运行中的世界。
    于是"删一个地块连带删它的路"这类规则**只有 Python 那一份**，
    前端不必在 JS 里抄一遍——抄的那份迟早漂开。

    玩家标记仍取自**运行中的世界**：预览改的是地图长什么样，不是他现在在哪。
    """
    from common import map_io
    from common.base_cnf import BASE_PATH, WORLD_MAP_FILE
    from Schema.B1_worldState_sch import B1WorldLayer

    body = request.get_json(silent=True) or {}
    form = body.get('form')
    if not isinstance(form, dict) or '地块' not in form:
        return jsonify({'ok': False, 'reason': '请求里没有编辑表单'})

    path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
    try:
        edit, _problems = map_io.from_edit_form(form)
        # 几何那几行**写坏了就不画**：`from_edit_form` 会把解析不出的那一条丢掉，
        # 照丢掉的画出来就是"某块地凭空不见了"——比图旧一点难查得多。
        # （字段行没写完之类不影响画图，那些不拦。）
        _gi = form.get('几何') or {}
        if any(len(_gi.get(k) or {}) != len(edit['几何'][k])
               for k in ('坐标', '范围', '路径线')):
            return jsonify({'ok': False,
                            'reason': '坐标/范围/路径线有几行还没写完（写法认不出），'
                                      '先把它们补齐——这一版画不出来'})
        edit, _notes, renames = map_io.prepare_edit(edit, map_io.read_base(path),
                                                    body.get('geo'))
        w = B1WorldLayer(map_io.nest_world(edit))
        st = engine.gc.player.player_dict.get('当前状态CurrentState') or {}
        # `map_view_of` **归位到地图簇**了（原来住在 gushi4 的 engine.py 里，
        # 而它自己的 docstring 就写着"只认一个世界层 + 玩家在哪，不碰引擎其它部分"）。
        view = map_io.map_view_of(w, st, engine.gc.player_location)
        # 画不出来（比如坐标全被写坏了）就别假装成功——前端宁可留着上一张图
        if not view.get('ok'):
            return jsonify({'ok': False, 'reason': view.get('reason') or '这一版画不出来'})
        # `renames` 一起给出去：图按改名后的样子画了，表单里那串引用也得跟着改，
        # 不然"图上叫新名字的路"在表单里对不上，点它就点不中。
        return jsonify({'ok': True, 'view': view, 'renames': renames})
    except Exception as e:
        # 半成品的表单（键名重复、几何文案写到一半）会走到这里。
        # **不猜**：如实告诉前端"这一版画不出来"，让它留着上一张图。
        logger.error(f'预览失败：{type(e).__name__}: {e}', exc_info=True)
        return jsonify({'ok': False, 'reason': f'{type(e).__name__}: {e}'})


@app.route('/api/map/save', methods=['POST'])
def api_map_save():
    """
    编辑器保存：**校验 → 备份 → 写盘 → 热重载**。

    满轮在跑时**拒绝**（不是排队）：保存会把世界字典换掉，跑到一半的回合
    读到一半新一半旧，比报错更难查。宁可让用户等一下再点。
    """
    import io as _io
    from common import map_io
    from common.base_cnf import BASE_PATH, WORLD_MAP_FILE

    if _state['running']:
        return jsonify({'ok': False, 'busy': True,
                        'problems': ['上一轮还在跑，等它结束再保存']})

    body = request.get_json(silent=True) or {}
    form = body.get('form')
    if not isinstance(form, dict) or '地块' not in form:
        return jsonify({'ok': False, 'problems': ['请求里没有编辑表单']})

    # 先把"文本值"解回模型（这一步就会挑出不合法写法），再走写盘三查
    edit, problems = map_io.from_edit_form(form)
    if problems:
        return jsonify({'ok': False, 'stage': '取值', 'problems': problems})

    # 拖出来的几何（阶段 3）：前端只报**数值**，文本由 `map_io` 生成——
    # 免得在 JS 里抄一份 Python `repr(float)` 的格式化规则（两处真相，迟早漂）
    path = os.path.join(BASE_PATH, WORLD_MAP_FILE)
    r = map_io.save_map(path, edit, geo=body.get('geo'))
    if not r.get('ok'):
        return jsonify(r)

    try:
        r['reloaded'] = engine.gc.reload_world()
    except Exception as e:
        logger.error(f'保存后热重载失败：{type(e).__name__}: {e}', exc_info=True)
        r['reloaded'] = None
        r['problems'] = [f'文件已写入并备份，但**热重载失败**：{type(e).__name__}: {e}'
                         f'——重启一次服务即可生效']
    r['backup'] = os.path.basename(r.get('backup') or '') or None
    # `cascade` 是"顺带改了什么"（含拖出来的几何跟端点走的那几条），
    # 要原样给用户看，不能悄悄做。它由 `map_io.save_map` 一路带出来。
    r['cascade'] = r.get('cascade') or []
    return jsonify(r)


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


@app.route('/api/saves/wait', methods=['POST'])
def api_saves_wait():
    """
    等**后台的存档线程**落盘。

    `/save` 是后台写的（`GameController._do_save` 起线程、立刻返回），所以
    "存完立刻列一遍"会**看不到刚存的那个**——界面上就是"点了保存没反应"。
    `_do_save` 的注释里写着同样的理由；`/load` 也在用同一个等待（同名存档还在写
    就先等它）。这里只是把它开到网页上。
    """
    return jsonify({'ok': engine.gc.wait_for_saves(timeout=15)})


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
        if not _spawn_round(act['text']):
            return jsonify({'action': 'busy'})
        return jsonify({'action': 'started'})

    return jsonify({'action': action})


# `/api/move` 已退役（2026-10-08）：地图是主输入，移动走"打字说去哪 → 下达去向"，
# 位置由 N2 判的 `move_to`（去向）+ 代码落坐标（`GameController.move_player_to`）。


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
    ap = argparse.ArgumentParser(description='gushi5 web 服务')
    ap.add_argument('--host', default='127.0.0.1',
                    help='默认只监听本机；改成 0.0.0.0 会把存档暴露到局域网')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--debug', action='store_true', help='Flask 调试模式（会重启进程）')
    args = ap.parse_args()

    logger.info(f'gushi5 web 启动 → http://{args.host}:{args.port}')
    print(f'\n  gushi5 · http://{args.host}:{args.port}\n')
    # threaded=True：引擎是同步阻塞的，一个回合要跑好几秒，
    # 单线程的话回合期间连状态都刷不出来
    app.run(host=args.host, port=args.port, threaded=True,
            debug=args.debug, use_reloader=False)


if __name__ == '__main__':
    main()
