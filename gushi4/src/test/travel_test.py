"""
沿路推进（1A.1 / S3）的不变量测试 —— **不调 LLM、不碰网络、不动真文件**。

守的是"在路上"这件事终于是**真的**：

    1. **坐标落在路径线上**，而且前后端同一个贝塞尔公式（不一致的话，
       玩家在地图上就不在那条线上）。
    2. **方向不能反**：路的 `路径线` 从 `从` 排到 `到`，而人可能从任一端走。
    3. **`行程` 认不出要退回老口径**（"去向即到达"），**绝不让人卡在半路**。
    4. **L1/L3 看得见"在路上"**：`path_at` 那条静默塌缩（坐标→最近地块）不再是
       唯一出口——`location_desc` / `location_context_for` 必须说得出口。
    5. **走不通就不动**（不是已知地点 / 没有连通的路）——宁可原地，不要假位置。

跑法：
    python src/test/travel_test.py
"""

import io
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

from common.game_controller import GameController
from common.base_cnf import BASE_PATH, WORLD_MAP_FILE

PASS, FAIL = [], []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  ✓ {name}')
    else:
        FAIL.append(name)
        print(f'  ✗ {name}   {detail}')


def main() -> int:
    gc = GameController('travel_test')
    gc.init_world()
    gc.init_clock()
    gc.init_all_npcs()
    gc.init_player()
    w = gc.b1WorldLayer
    st = gc.player_state

    HOME = '外门弟子修炼区·落霞坪'

    # ── 就地挑路：**一律按图算，不写死地名** ──────────────────────
    # 世界正在被手写重建（2026-10-10 起）：写死"落霞坪↔天道宗 有直达路"这种，
    # 作者一改路网就假红（那次是把两者之间改成经山门两跳，直达路没了）。
    # 而且**必须是直达的一对**（`road_between` 找得到）：多跳的路线 15% 可能
    # 落在第一跳上、方向看着"歪"，那条断言本来就是按直达写的。
    def _leaf_roads(lo=0.0):
        """两端都是**地点**（不是地区）的直达路，按长度从短到长。"""
        out = []
        for r in ((w.dict or {}).get('路列表') or []):
            a, b = r.get('从'), r.get('到')
            if not a or not b or w.is_region(a) or w.is_region(b):
                continue                      # 地区的坐标只是圈心，不做方向判据
            ca, cb = w.place_coord(a), w.place_coord(b)
            if not (ca and cb):
                continue
            d = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
            if d >= lo:
                out.append((d, a, b))
        out.sort()
        return out

    # ── 1. 语义 → 分数 ────────────────────────────────────────────
    print('\n【`行程` 的解析】')
    check('刚上路 → 0.15', gc.travel_frac('刚上路') == 0.15, gc.travel_frac('刚上路'))
    check('半路 → 0.5', gc.travel_frac('半路') == 0.5)
    check('将到 → 0.85', gc.travel_frac('将到') == 0.85)
    check('到了 → 1.0', gc.travel_frac('到了') == 1.0)
    check('★ 认不出的说法返回 None（调用方按"到了"退回老口径）',
          gc.travel_frac('走了大半') is None, gc.travel_frac('走了大半'))
    check('空字符串也返回 None（没动）', gc.travel_frac('') is None)

    # ── 2. 落在路径线上 + 方向 ────────────────────────────────────
    print('\n【落点与方向】')
    # 取一条**两端都是地点、且最长**的直达路，方便判方向（见 `_leaf_roads`）
    _roads = _leaf_roads()
    _far = _roads[-1] if _roads else None
    check('找得到一条两端都是地点的直达路（取最长的那条）', _far is not None,
          f'{len(_roads)} 条可选')
    rd = w.road_between(_far[1], _far[2]) if _far else None
    DIR_A, DIR_B = (_far[1], _far[2]) if _far else (HOME, HOME)
    check(f'  `road_between` 拿得到它（{DIR_A} ↔ {DIR_B}，{_far[0]:.0f} 单位）'
          if _far else '  跳过', rd is not None)
    p_from = w.road_point(rd, 0.0)
    p_to = w.road_point(rd, 1.0)
    c_from = w.place_coord(rd.get('从'))
    check('★ t=0 就是 `从` 那一端（路径线的起点）',
          p_from and c_from and abs(p_from[0] - c_from[0]) < 0.01
          and abs(p_from[1] - c_from[1]) < 0.01, f'{p_from} vs {c_from}')

    # 从"到"那一端出发，t=0 应当落在"到"上（`set_position_on_road` 要翻转）
    gc.set_position(st, name=DIR_B)
    gc.advance_travel(st, DIR_A, 0.0)
    d0 = w.location_desc(st)
    check('★ frac≈0 从哪出发就在哪（方向没反）', DIR_B in d0, d0)

    # 走 15%：应当**离开**出发地、且朝目的地那边走，而不是一步跳到目的地
    def _xy(n):
        c = w.place_coord(n)
        return (c[0], c[1]) if c else (0.0, 0.0)

    gc.set_position(st, name=DIR_A)
    gc.advance_travel(st, DIR_B, 0.15)
    p15 = (st['位置'][0], st['位置'][1])
    dx, dy = _xy(DIR_A), _xy(DIR_B)
    d_ab = ((dx[0] - dy[0]) ** 2 + (dx[1] - dy[1]) ** 2) ** 0.5
    d_a15 = ((p15[0] - dx[0]) ** 2 + (p15[1] - dx[1]) ** 2) ** 0.5
    d_15b = ((p15[0] - dy[0]) ** 2 + (p15[1] - dy[1]) ** 2) ** 0.5
    # ⚠️ 不写"x 落在两端之间"：那条只在"路基本是直线"时成立，而**路是弯的**
    # （二次贝塞尔，还有拐角）。守的是方向与"没瞬移"这两件事：
    #   ① 确实离开了出发地；② 离出发地比离目的地近（15% 不是 85%）；③ 仍在半程内
    check('★ 15% 离开了出发地（走了，不是没动）', d_a15 > 0.5,
          f'{p15} vs {d_a15:.1f}')
    check('★ 15% 更靠近出发地（不是一步到目的地）', d_a15 < d_15b,
          f'到出发地 {d_a15:.1f} / 到目的地 {d_15b:.1f}')
    check('  仍在半程内（方向没反）', d_a15 < d_ab / 2,
          f'{d_a15:.1f} vs 半程 {d_ab / 2:.1f}')

    # 走到 1.0 就该到目的地
    gc.set_position(st, name=DIR_A)
    gc.advance_travel(st, DIR_B, 1.0)
    check(f'frac=1.0 → 到目的地（{DIR_B}）', w.name_at(st) == DIR_B, w.name_at(st))

    # ── 3. 「在路上」是真的 ──────────────────────────────────────
    print('\n【在路上】')
    # 短路（几十个单位那类）：中段判成在路上，两端判成在地块里。
    # 取**短但超过两个 `PLACE_CORE`** 的一条（中点必须离开两端的地界，
    # 否则它本来就该判成"还在出发地"）。
    _core = getattr(w, 'PLACE_CORE', 26)
    _tail = [t for t in _roads if t[0] >= _core * 2 + 18]
    _short = _tail[0] if _tail else (_roads[len(_roads) // 2] if _roads else None)
    check('找得到一条"短但不至于短到判不在路上"的直达路', _short is not None,
          f'{_short[0]:.0f} 单位' if _short else '')
    SHORT_A, SHORT_B = (_short[1], _short[2]) if _short else (HOME, HOME)
    gc.set_position(st, name=SHORT_A)
    gc.advance_travel(st, SHORT_B, 0.15)
    check(f'短路 15% 还在出发地（{SHORT_A}）',
          w.location_desc(st).endswith(SHORT_A), w.location_desc(st))
    gc.set_position(st, name=SHORT_A)
    gc.advance_travel(st, SHORT_B, 0.5)
    check('★ 短路中段判成「在路上」', '的路上' in w.location_desc(st),
          w.location_desc(st))

    # 长路（跨域）：整条路上都该是"在路上"。
    # ⚠️ **按图算，不写死地名**：世界正在被手写重建（2026-10-10 起），
    # 写死 `苍云大陆`/`东域` 这种名字，作者一改路网测试就假红——
    # 而它真正想守的是"**最长的那条路**上处处是'在路上'"，与叫什么都无关。
    # ⚠️ 只在**和出生地连通的那一块**里挑：作者正在手连路网，世界此刻可能分成
    # 好几个岛（2026-10-10 实测：落霞坪只到得了 15/42）。挑到岛外的话
    # 这条"长路"根本走不通，测的就不是"在路上"了。
    _names = [p for p in w.geometry('坐标') if w.route(HOME, p)]
    _best = (None, None, 0)
    for _a in _names:
        for _b in _names:
            if _a != _b:
                _h = len(w.route(_a, _b)) - 1
                if _h > _best[2]:
                    _best = (_a, _b, _h)
    LONG_A, LONG_B, LONG_HOPS = _best
    check(f'世界里有 ≥3 跳的长路（{LONG_A} → {LONG_B}，{LONG_HOPS} 跳）', LONG_HOPS >= 3,
          _best)
    # 取**每一跳的中点**去试：`frac = (k+0.5)/legs` 保证落在两跳之间，
    # 不会正好压在某个中途地块上（`frac=0.5` + 偶数跳 = 恰好落在地块里，
    # 那时候判成"在地块里"是**对的**，是测试写错了）。
    # 两端那几跳本来就该判成"在地块里"（同门之间几十个单位，你没走多远），
    # 所以断言是"**至少有一段**走在大路上"——那才是"在路上"有意义的地方。
    _legs = max(1, len(w.route(LONG_A, LONG_B)) - 1)
    _onroad = 0
    for k in range(_legs):
        gc.set_position(st, name=LONG_A)
        gc.advance_travel(st, LONG_B, (k + 0.5) / _legs)
        if '的路上' in w.location_desc(st):
            _onroad += 1
    check(f'★ 长路中段判成「在路上」（{_onroad}/{_legs} 跳的中点在路上）',
          _onroad >= 1, w.location_desc(st))
    # 属性链也要说得出口（挑一个真在路上的位置再验一次）
    gc.set_position(st, name=LONG_A)
    gc.advance_travel(st, LONG_B, 0.5 / _legs)
    _ctx = w.location_context_for(st)
    if '的路上' in w.location_desc(st):
        check('  在路上的属性链标着「在路上」',
              any(x.get('在路上') for x in _ctx), _ctx[:1])

    # 到了就不该再是"在路上"
    gc.set_position(st, name=LONG_A)
    gc.advance_travel(st, LONG_B, 1.0)
    check('到了之后不再是「在路上」', '的路上' not in w.location_desc(st),
          w.location_desc(st))
    check('到了之后属性链没有「在路上」标记',
          not any(x.get('在路上') for x in w.location_context_for(st)))

    # ── 4. 方向中性措辞 ─────────────────────────────────────────
    print('\n【措辞】')
    gc.set_position(st, name=LONG_A)
    gc.advance_travel(st, LONG_B, 0.5)
    d = w.location_desc(st)
    check('★ 路的描述**方向中性**（不写"A到B"，那是书写顺序不是行进方向）',
          '到' not in d.split('（')[0].replace(LONG_A, '').replace(LONG_B, ''),
          d)
    check('描述里不含任何数字（坐标/里程都不给 LLM）',
          not any(ch.isdigit() for ch in d), d)

    # ── 5. 走不通就不动 ─────────────────────────────────────────
    print('\n【安全失败】')
    gc.set_position(st, name=HOME)
    before = list(st['位置'])
    check('★ 目标不存在 → 不动', gc.advance_travel(st, '不存在的地方', 0.5) is False)
    check('  位置真的没动', st['位置'] == before, st['位置'])
    check('★ 目标就是当前所在地 → 不动', gc.advance_travel(st, HOME, 0.5) is False)
    check('  位置还是没动', st['位置'] == before, st['位置'])

    # ── 6. 多跳：按整段算，不是一跳一轮 ──────────────────────────
    print('\n【多跳】')
    # 远路：从出生地出发最长的那条（同样不写死地名，且**必须走得到**）
    FAR = max(_names, key=lambda n: len(w.route(HOME, n)))
    hops = w.route(HOME, FAR)
    check(f'找得到一条多跳的远路（{HOME} → {FAR}，{len(hops) - 1} 跳）',
          len(hops) - 1 >= 3, hops)
    seen = set()
    for frac in (0.15, 0.5, 0.85, 1.0):
        gc.set_position(st, name=HOME)
        gc.advance_travel(st, FAR, frac)
        seen.add(tuple(round(x, 1) for x in st['位置']))
    check('★ 不同 frac 落到不同位置（不是一跳一轮的原地踏步）', len(seen) >= 3, seen)
    gc.set_position(st, name=HOME)
    gc.advance_travel(st, FAR, 1.0)
    check(f'frac=1.0 → 真的到了（{FAR}）', tuple(st['位置']) == tuple(w.place_coord(FAR))
          or w.name_at(st) == FAR, f'{w.name_at(st)} vs {FAR}')

    # ── 7. 前后端同一个贝塞尔 ────────────────────────────────────
    print('\n【前后端同式】')
    # 前端 `buildMapStatic` 对三点走的是 `M p0 Q p1 p2`，即二次贝塞尔。
    # 这里用同一组控制点手算一遍常量比对——两边公式不一致的话，
    # 玩家在地图上就**不在那条线上**，而且看不出来。
    ctrl = w.geometry('路径线').get(rd.get('名'))
    if ctrl and len(ctrl) >= 3:
        (x0, y0), (x1, y1), (x2, y2) = ctrl[0], ctrl[1], ctrl[2]
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            u = 1.0 - t
            expect = [u * u * x0 + 2 * u * t * x1 + t * t * x2,
                      u * u * y0 + 2 * u * t * y1 + t * t * y2]
            got = w.road_point(rd, t)
            check(f'★ t={t} 与二次贝塞尔一致',
                  got and abs(got[0] - expect[0]) < 1e-6
                  and abs(got[1] - expect[1]) < 1e-6, f'{got} vs {expect}')
    else:
        check('这条路的路径线有三个点（不然测不了贝塞尔）', False, ctrl)

    # ── 8. 地图文件一个字节都不该动 ──────────────────────────────
    # ── 路的属性 / 段的属性 真的进了 L3 吗 ─────────────────────────
    #
    # prompt 里写着"`当前地段` 会告诉他**这条路**和两端的地段属性"，而代码读的是
    # `road['属性']`——`roads()` 给的是**扁平**形状（属性摊在同一层），于是**永远取到
    # 空字典**。数据里 65 条路只有 `自动生成=是`（正好被滤掉），所以从没暴露过。
    print('\n【在路上时，路的属性与段的属性进得了 L3 吗】')
    # 随便挑一条**两端都在**的路：不写死名字，免得数据一改测试就假红
    road = next(r for r in w.roads() if r.get('从') and r.get('到'))
    road['地形'] = '雪岭'
    road['凡人脚程'] = '三日'
    road['说明'] = '冬天封路'
    road['段'] = [{'名': '荒村段', '地形': '村落与田埂'},
                  {'名': '雪岭段', '海拔': 8000, '隔阻': '需御寒'}
                  ]
    try:
        mid = w.road_point(road, 0.5)
        ctx = w.location_context_for({'位置': [mid[0], mid[1]]})
        head = ctx[0]
        check('★ 在路上时第一条是"（在路上）"', '在路上' in head['层级'], head)
        check('★ **路的属性真的在里面**（这条一直是空的，直到 2026-10-08）',
              head['属性'].get('地形') == '雪岭'
              and head['属性'].get('凡人脚程') == '三日', head['属性'])
        check('  结构键与"给作者的说明"没混进去',
              not ({'名', '从', '到', '双向', '段', '说明', '自动生成'}
                   & set(head['属性'])), list(head['属性']))
        segs = [x for x in ctx if x['层级'].startswith('（正在穿过）')]
        check('★ 正在穿过哪一段也说得出口', bool(segs), ctx[:2])
        if segs:
            check('  本段的属性跟着来（`海拔`/`隔阻` 是判"能不能过"的依据）',
                  segs[0]['层级'].endswith('段')
                  and set(segs[0]['属性']) & {'地形', '海拔', '隔阻'}, segs[0])
    finally:
        for k in ('地形', '凡人脚程', '说明', '段'):
            road.pop(k, None)

    # ── `行程` 报**段名**时怎么折算 ───────────────────────────────
    #
    # 段名是作者写的，而 `_STAGE_FRAC` 只认那四个进度词。认不出的退路是 `frac = 1.0`
    # ＝**一步到目的地**——所以**铺段数据之前必须先接上这条**，不然数据越多越容易瞬移。
    print('\n【`行程` 报段名】')
    # 挑一条**孤立**的路：中点离其它路都远。不这么挑的话，落点会被 `on_road_at`
    # 认成旁边那条更近的路（实测：地图密起来之后，任意一条路的中点旁 0.5 单位
    # 就是另一条路）——那时测的就不是"段"了，是"最近路选得对不对"。
    def _isolation(r0):
        px, py = w.road_point(r0, 0.5)
        best = float('inf')
        for o in w.roads():
            if o is r0:
                continue
            for t in (0, .25, .5, .75, 1):
                qx, qy = w.road_point(o, t)
                best = min(best, math.hypot(qx - px, qy - py))
        return best

    road = max([r for r in w.roads() if r.get('从') and r.get('到')], key=_isolation)
    road['段'] = [{'名': '荒村段'}, {'名': '雪岭段', '区间': [0.3, 0.8]}, {'名': '溪谷段'}]
    try:
        st = gc.player_state
        gc.set_position(st, name=road['从'])
        rgs = w.segment_ranges(road)
        check('段区间：没写 `区间` 的按段序等分',
              abs(rgs[0][2] - 1 / 3) < 1e-9 and abs(rgs[2][1] - 2 / 3) < 1e-9
              and rgs[2][2] == 1.0, rgs)
        check('段区间：写了 `区间` 的照它', w.segment_range(road, '雪岭段') == (0.3, 0.8))
        check('查不存在的段给 None', w.segment_range(road, '没这段') is None)
        f1 = gc.travel_frac_by_segment(st, road['到'], '走到雪岭了')
        check('★ 「走到雪岭了」折算成整段路程的 frac（段名去掉尾缀也认）',
              f1 is not None and abs(f1 - 0.55) < 1e-9, f1)
        check('★ 「溪谷段」也认', gc.travel_frac_by_segment(st, road['到'], '溪谷段') is not None)
        check('  词表能认的走词表（不抢段名的活）', gc.travel_frac('刚上路') == 0.15)
        check('  认不出还是 None（调用方退回老口径）',
              gc.travel_frac_by_segment(st, road['到'], '走了好久') is None)
        # 端到端：报段名 → 落在**那一段的中间**，而不是直接到目的地
        gc.set_position(st, name=road['从'])
        assert gc.advance_travel(st, road['到'], f1, why='走到雪岭了')
        loc = w.locate(*w.coord_at(st))
        rd = w.on_road_at(*w.coord_at(st))
        check('★ 落点在**路上**，没被"到了"吞掉', bool(rd), loc)
        check('★ 而且落在雪岭段那一段里', rd and rd.get('段') == '雪岭段', rd)
    finally:
        road.pop('段', None)
        gc.set_position(st, name=HOME)

    # ── 区域（面）与地点（点）分家 ────────────────────────────────
    #
    # 判据只有作者写的 `类型`（**不猜**：天道宗有 7 个子地块，可它自己能停留）。
    # 没写 `类型` 时行为**一点不变**——所以这组断言先把"没写"跑一遍，再贴上 `类型` 跑一遍。
    print('\n【区域与地点分家（地图格式.md §9.1）】')
    import json as _json
    ori_attrs = {p['名']: dict(p['属性']) for p in
                 [{'名': k, '属性': v} for k, v in w._attrs_by_name().items()]}
    st = gc.player_state
    # 地名一律**从图里挑**（世界正在被手写重建，写死了迟早不存在）
    _all = list(w.geometry('坐标'))
    # 判据只有一条：作者写的 `类型`。所以"地区"就是**标了类型的那几个**——
    # 一个不多（不该凭空改行为）一个不少（标了就得认）。2026-10-10 起真文件里
    # 已经有 6 个地区（苍云大陆 + 四域 + 中州），所以这条不再断言"一个都没有"。
    from Schema.B1_worldState_sch import REGION_TYPES as _RT
    _marked = {n for n, a in w._attrs_by_name().items()
               if str((a or {}).get('类型') or '').strip() in _RT}
    check('地区 = 作者标了 `类型` 的那些，一个不多一个不少',
          {n for n in _all if w.is_region(n)} == _marked,
          [n for n in _all if w.is_region(n)])
    # 临时给一块**没标过的**节点贴上 `类型=区域`（别拿已经标了的做实验）
    _kids = {str(x.get('属于') or '') for x in
             [{'属于': k} for k in _all] if x['属于']}
    _plain = [n for n in _all if n not in _marked]
    REG = next((n for n in _plain if n in _kids), _plain[0] if _plain else _all[0])
    OTHERS = [n for n in _plain if n != REG][:4]
    try:
        w._attrs_by_name()[REG]['类型'] = '区域'
        check(f'标了 `类型=区域` → 认得出（`{REG}`）', w.is_region(REG))
        check('  没标的一律还是**地点**', not any(w.is_region(n) for n in OTHERS))
        center = w.geometry('坐标')[REG]
        loc = w.locate(*center)
        # 断言的是"**区域自己不被当成落脚点**"——这才是 §9.1 的规则。
        # 不能再要求 `野外` 为真：区域中心完全可能落在**另一个地点**的圈里
        # （那张圈本来就可能是画在它里头的），那时"在某个地点"是**对**的。
        check('★ 站在区域中心时，落脚点不是那个区域本身',
              not w.is_region(loc['地块'] or ''), loc)
        check('  但它还是能告诉你脚下是个**区域**（链里要那点灵气/危险）',
              w.is_region(loc['区域'] or ''), loc)
        # 找一块**真正的野外**：在区域圈里、但离所有路都远（区域中心往往紧挨着一条路，
        # 那种点上"在路上"是对的，测不出野外这条分支）
        import math as _m
        wild = None
        for deg in range(0, 360, 5):
            rad = _m.radians(deg)
            p = [center[0] + 600 * _m.cos(rad), center[1] + 600 * _m.sin(rad)]
            if w.region_at(*p) and w.on_road_at(*p) is None:
                wild = p
                break
        check('找得到一块野外（在区域里、离路很远）', wild is not None, center)
        if wild:
            wctx = w.location_context_for({'位置': list(wild)})
            check('★ 在野外时链的第一条明写"不在任何地点"',
                  bool(wctx) and wctx[0].get('不在任何地点') is True, wctx[:1])
            check('★ 而且说的是"哪个区域的旷野上"（语义，不是坐标）',
                  '旷野' in wctx[0]['层级'], wctx[0])
            check('  野外不借用最近地点的链（不假装他在那个村子里）',
                  all(x.get('说明') != '路的这一端' for x in wctx), wctx[:2])
            # 链上**要带着它所在的那个地区**（灵气/危险照给）。
            # ⚠️ 不写死是哪个地区：临时标的 `REG` 可能很小（天道宗 r=104），而野外点
            # 在 600 单位外、根本不在它里面——点落到哪个地区是几何算的（实测就是
            # 苍云大陆）。所以只断言"链上有一个**地区**，且灵气是从它那儿来的"。
            _regs = [x for x in wctx[1:] if w.is_region(x['层级'] or '')]
            check('  区域的链还在（灵气/危险照给）',
                  bool(_regs) and any((r.get('属性') or {}).get('灵气') for r in _regs),
                  wctx)
        check('  区域不参与"最近地点"（`nearest_place` 跳过它）',
              w.nearest_place(*center)[0] != REG, w.nearest_place(*center))
        view = None
        from common.engine import map_view_of
        view = map_view_of(w, {}, '')
        by = {p['name']: p for p in view['places']}
        check('★ 地图视图里区域带上了 `region`（前端据此不画点）',
              by[REG]['region'] is True
              and all(by[n]['region'] is False for n in OTHERS), 
              [p['name'] for p in view['places'] if p.get('region')])
    finally:
        for nm, a in ori_attrs.items():
            w._attrs_by_name()[nm].clear()
            w._attrs_by_name()[nm].update(a)
        gc.set_position(st, name=HOME)

    # ── 孤儿：真存在的地点却一条路都不通 ──────────────────────────
    #
    # 数据漏一条路太正常了（手写一张新世界时尤其）。**不该表现为"玩家动不了"**：
    # 那是个死胡同——他想去的地方在世界里明明存在。落点 + WARNING 才是对的处理。
    print('\n【孤儿地块：有坐标、没路】')
    st = gc.player_state
    _zero = [n for n in w.geometry('坐标')
             if not any(n in (r.get('从'), r.get('到')) for r in w.roads())]
    # **优先挑地点**：地区的 `坐标` 只是那个圈的圆心（§9.1），落上去不算"落脚"，
    # 所以两条断言不一样（下面分开走）。
    _zp = [n for n in _zero if not w.is_region(n)]
    orphan = _zp[0] if _zp else (_zero[0] if _zero else None)
    check('数据里有度数为 0 的节点（孤岛）', orphan is not None, _zero)
    if orphan:
        gc.set_position(st, name=HOME)
        ok = gc.advance_travel(st, orphan, 1.0)
        check(f'★ 去「{orphan}」不硬失败，直接落点（数据漏了要看得见，但不该走不动）', ok)
        if w.is_region(orphan):
            # **地区没有落脚点**：去向给地区名 → 落到它子树里**离你最近的地点**上
            nm = w.name_at(st)
            check('★ 去向给地区 → 落到它子树里的一个**地点**（不是那个圈的圆心）',
                  bool(nm) and not w.is_region(nm)
                  and nm in w._subtree_names(orphan), nm)
        else:
            # ⚠️ 断言落在**坐标**上，不是 `name_at` 上：坐标才是真源，而名字是派生的
            # ——"站在 A 的坐标上"完全可能被某个**更小的圈**认领成 B（区域套区域）。
            want = w.place_coord(orphan)
            got = [round(x, 1) for x in st['位置']]
            check('  落点就是它的坐标（坐标是真源，名字是派生）',
                  want and got == [round(want[0], 1), round(want[1], 1)],
                  f'{got} vs {want}（派生名 {w.name_at(st)}）')
        check('★ 但不存在的名字仍然**不动**（不编假位置）',
              not gc.advance_travel(st, '不存在的地方', 1.0))

    print('\n【不动世界数据】')
    src = io.open(os.path.join(BASE_PATH, WORLD_MAP_FILE),
                  encoding='utf-8').read()
    gc.set_position(st, name=HOME)
    for dest, frac in ((LONG_B, 0.3), (FAR, 0.6), ('天道宗', 1.0)):
        gc.advance_travel(st, dest, frac)
    now = io.open(os.path.join(BASE_PATH, WORLD_MAP_FILE),
                  encoding='utf-8').read()
    check('★ 走了三趟，`世界.map` 一个字节都没动（行进不属于地图）',
          now == src)

    print()
    print('=' * 70)
    print(f'结果：{len(PASS)} 通过 / {len(FAIL)} 失败')
    print('=' * 70)
    if FAIL:
        print('失败项：')
        for f in FAIL:
            print(f'  ✗ {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
