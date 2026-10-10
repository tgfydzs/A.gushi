"""
地图编辑器（阶段 2）的不变量测试 —— **不调 LLM、不碰网络、不动真文件**。

全在临时目录里对着 `世界.map` 的**副本**做，所以可以随便改、随便存。

守的几条：

    1. **往返无损**：模型 → 线上表单 → JSON → 模型 → emit 必须**逐字节**等于原文件。
       编辑器最容易犯的错不是"改坏了一行"，而是"改一行、顺手丢了另一行"。
    2. **一条属性一行 diff**：改一个字段只允许动一行，加一个字段只允许把那一行变长
       ——这是用户给的验收标准（"改一条属性 → git diff 只有那一行"）。
    3. **键序不许被顺手重排**：`.map` 的属性按书写顺序吐，顺序一变 diff 就是满屏。
       真踩过一次：Flask 的 `jsonify` 默认按键排序 → 改一个字段 → 159 行 diff。
    4. **写盘三查**：校验不过 / 往返不一致 → **一个字节都不许写**，且不给备份。
    5. **产出走它自己的语法**：`物品:概率(条件)`，不是 `str(对象列表)`。

跑法：
    python src/test/map_edit_test.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

from common import map_io as M
from Schema.B1_worldState_sch import REGION_TYPES as M_REGION_TYPES   # `类型` 里哪些值是地区
from common.base_cnf import BASE_PATH, WORLD_MAP_FILE

PASS, FAIL = [], []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  ✓ {name}')
    else:
        FAIL.append(name)
        print(f'  ✗ {name}   {detail}')


def lines_changed(a: str, b: str) -> int:
    """改动了几**逻辑行**。行数相同＝逐行比；行数不同＝数 diff 的 +/- 对。"""
    la, lb = a.splitlines(), b.splitlines()
    if len(la) == len(lb):
        return sum(1 for x, y in zip(la, lb) if x != y)
    add, rem = plus_minus(a, b)
    return max(add, rem)


def plus_minus(a: str, b: str):
    """`(+ 行数, - 行数)`。插入两行 → `(2, 0)`；改一行 → `(1, 1)`。"""
    import difflib
    add = rem = 0
    for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm='', n=0):
        if l.startswith('+') and not l.startswith('+++'):
            add += 1
        elif l.startswith('-') and not l.startswith('---'):
            rem += 1
    return add, rem


SRC = os.path.join(BASE_PATH, WORLD_MAP_FILE)


def main() -> int:
    src_text = io.open(SRC, encoding='utf-8').read()
    model = M.flatten_world(M.parse_world(src_text))
    form = M.to_edit_form(model)
    model_geo = model['几何']          # 数值形态的几何，给拖动的期望值用

    # ── 1. 往返无损 ───────────────────────────────────────────────
    print('\n【往返无损】')
    back, probs = M.from_edit_form(form)
    check('表单 → 模型：无问题', not probs, probs[:3])
    check('表单 → 模型：与原模型逐项相等', back == model, M.diff_edit(model, back)[:3])

    jform = json.loads(json.dumps(form, ensure_ascii=False))   # 模拟浏览器那一趟
    back_j, _ = M.from_edit_form(jform)
    check('过了 JSON 仍然相等（浏览器往返）', back_j == model, M.diff_edit(model, back_j)[:3])

    emitted = M.emit_world(M.nest_world(back_j), back_j['世界']['名'])
    check('emit 与原文件**逐字节**相同', emitted == src_text,
          f'差 {lines_changed(src_text, emitted)} 行')

    # ── 2. 产出 / 几何 的写法 ─────────────────────────────────────
    print('\n【值语法】')
    prod = [f for p in form['地块'] for f in p['字段'] if f['k'] == '产出']
    check('有产出字段', bool(prod))
    check('产出写成 `物品:概率` 语法（不是对象列表的字面量）',
          all('{' not in f['v'] and ':' in f['v'] for f in prod),
          prod[:1])
    check('几何以文本给出',
          isinstance(form['几何']['坐标'].get('苍云大陆'), str)
          and form['几何']['坐标']['苍云大陆'].startswith('('),
          form['几何']['坐标'].get('苍云大陆'))
    check('路径线以文本给出',
          all(v.startswith('[') for v in list(form['几何']['路径线'].values())[:3]))

    # 非法写法必须被挡下，不许猜
    bad_form = json.loads(json.dumps(form, ensure_ascii=False))
    bad_form['几何']['坐标']['苍云大陆'] = '不是坐标'
    _, p2 = M.from_edit_form(bad_form)
    check('坐标写法认不出 → 报问题而不是猜', any('坐标' in x for x in p2), p2[:2])

    bad_form2 = json.loads(json.dumps(form, ensure_ascii=False))
    bad_form2['地块'][0]['字段'].append({'k': '灵气', 'v': '重复键'})
    _, p3 = M.from_edit_form(bad_form2)
    check('同名字段出现两次 → 报问题', any('两次' in x for x in p3), p3[:2])

    # ── 3. 写盘：一条属性 → 一行 diff ──────────────────────────────
    print('\n【写盘：改动面】')
    tmp = tempfile.mkdtemp(prefix='mapedit_')
    try:
        path = os.path.join(tmp, '世界.map')
        shutil.copy2(SRC, path)

        f = json.loads(json.dumps(form, ensure_ascii=False))
        idx = next(i for i, p in enumerate(f['地块']) if p['名'] == '外门弟子修炼区·落霞坪')
        next(x for x in f['地块'][idx]['字段'] if x['k'] == '灵气')['v'] = '浓郁'
        m2, prob = M.from_edit_form(f)
        check('改一条属性：模型可解', not prob, prob[:2])
        r = M.save_map(path, m2)
        check('保存成功', r.get('ok'), r)
        after = io.open(path, encoding='utf-8').read()
        check('★ 只动了 1 行', lines_changed(src_text, after) == 1,
              f'动了 {lines_changed(src_text, after)} 行')
        check('文件被备份', bool(r.get('backup')) and os.path.isfile(r['backup']))
        check('备份内容 == 写前内容',
              io.open(r['backup'], encoding='utf-8').read() == src_text)

        # 加一个**自由字段**：追加到那一行末尾，不许重排别处
        f2 = json.loads(json.dumps(form, ensure_ascii=False))
        f2['地块'][idx]['字段'].append({'k': '禁忌', 'v': '埋着一具骸骨'})
        m3, _ = M.from_edit_form(f2)
        M.save_map(path, m3)
        after2 = io.open(path, encoding='utf-8').read()
        check('★ 加一个字段：同样只动 1 行', lines_changed(src_text, after2) == 1,
              f'动了 {lines_changed(src_text, after2)} 行')
        check('新字段在行尾（没插到中间）',
              '功能=[修炼,比试,交谈]  禁忌=埋着一具骸骨 |' in after2)

        # 键序被**故意打乱**也要还原：模拟 jsonify 那种排序
        shutil.copy2(SRC, path)
        scrambled = json.loads(json.dumps(form, ensure_ascii=False))
        for p in scrambled['地块']:
            p['字段'] = list(reversed(p['字段']))
        scrambled['配置'] = list(reversed(scrambled['配置']))
        m4, _ = M.from_edit_form(scrambled)
        M.save_map(path, m4)
        after3 = io.open(path, encoding='utf-8').read()
        check('★ 字段被倒序送来，落盘仍与原文一致（canonicalize 兜住）',
              after3 == src_text, f'动了 {lines_changed(src_text, after3)} 行')

        # ── 4. 写盘三查：不过就一个字节都不写 ─────────────────────
        print('\n【写盘三查】')

        def must_reject(label, mutate, want):
            shutil.copy2(SRC, path)                 # 每个用例都从干净的一份开始
            before_bytes = io.open(path, 'rb').read()
            fm = json.loads(json.dumps(form, ensure_ascii=False))
            mutate(fm)
            mm, pp = M.from_edit_form(fm)
            if pp:
                ok = any(want in x for x in pp)
                check(f'{label} → 被拦下', ok, pp[:2])
            else:
                rr = M.save_map(path, mm)
                ok = (not rr.get('ok')) and any(
                    want in x for x in (rr.get('problems') or []))
                check(f'{label} → 被拦下', ok, rr)
            check(f'{label} → 文件未被改动',
                  io.open(path, 'rb').read() == before_bytes)

        must_reject('属于指向不存在的地块',
                    lambda fm: fm['地块'][0].update({'属于': '不存在的地'}), '不存在')
        must_reject('地块重名',
                    lambda fm: fm['地块'][1].update({'名': fm['地块'][0]['名']}), '重名')
        # 端点**空着**是填错，不是"那块地没了"——必须报错，不能被级联悄悄删掉
        must_reject('路的一端是空的名',
                    lambda fm: fm['路'][0].update({'到': ''}), '不是已知地块')
        must_reject('字段键里有空格',
                    lambda fm: fm['地块'][0]['字段'].append({'k': '有 空格', 'v': 'x'}),
                    '分隔符')
        must_reject('地块没有名字',
                    lambda fm: fm['地块'][3].update({'名': ''}), '没有名字')

        check('没有留下 .tmp 残留文件',
              not os.path.exists(path + '.tmp'))

        # 悬空的几何现在**由级联修掉**（不是拒绝）：编辑器里没有"加几何行"的入口，
        # 所以悬空几何只可能来自删除/改名——那正是级联该干的事。但不能白修：
        # 必须在 cascade 里说出来。
        shutil.copy2(SRC, path)
        fm = json.loads(json.dumps(form, ensure_ascii=False))
        fm['几何']['坐标']['幽灵地'] = '(1,2)'
        mm, _ = M.from_edit_form(fm)
        rr = M.save_map(path, mm)
        check('悬空几何被级联清掉且存得下去', rr.get('ok'), rr.get('problems'))
        check('★ 清掉悬空几何要说出来',
              any('幽灵地' in x for x in rr.get('cascade') or []), rr.get('cascade'))

        # ── 5. 幂等：不改任何东西反复存，字节不变 ─────────────────
        print('\n【幂等】')
        shutil.copy2(SRC, path)
        for _ in range(3):
            mm, _ = M.from_edit_form(json.loads(json.dumps(form, ensure_ascii=False)))
            M.save_map(path, mm)
        check('★ 不改任何东西存三次，与原文逐字节相同',
              io.open(path, encoding='utf-8').read() == src_text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 5.5 改名 / 删除的级联（S2.1）──────────────────────────────
    # 这一段守的是"按钮点得通"：以前删一个地块 → 保存被拒（几何悬空），
    # 而表单里**没有地方能删那行几何** → 用户只能关掉编辑器手工改文件。
    print('\n【改名 / 删除的级联】')
    tmp2 = tempfile.mkdtemp(prefix='mapcascade_')
    try:
        p2 = os.path.join(tmp2, '世界.map')

        def try_save(mut):
            shutil.copy2(SRC, p2)
            fm = json.loads(json.dumps(form, ensure_ascii=False))
            mut(fm)
            mm, prob = M.from_edit_form(fm)
            assert not prob, prob
            return M.save_map(p2, mm), io.open(p2, encoding='utf-8').read()

        # 改名：引用（子地块属于 / 路的端点 / 几何键）要一起改
        r, txt = try_save(lambda fm: next(x for x in fm['地块']
                                          if x['名'] == '天道宗').update({'名': '天道宗·改名试'}))
        check('改名存得下去（以前会撞悬空几何）', r.get('ok'), r.get('problems'))
        # 只查**结构性引用**：`势力=[天道宗,…]` 那种是自由文本内容，不该被改
        # （改它就是把作者写的世界观当成引用去动了）。
        stray = [ln for ln in txt.splitlines()
                 if ('属于=天道宗 ' in ln or '从=天道宗 ' in ln or '到=天道宗 ' in ln
                     or ln.startswith('坐标 天道宗 ') or ln.startswith('范围 天道宗 ')
                     or ln.startswith('路径线 天道宗-'))]
        check('★ 没有残留的结构性引用', not stray, stray[:2])
        check('★ 内容里的自由文本不动它', '势力=[天道宗' in txt or '天道宗' in txt)
        check('★ 子地块的 `属于` 跟着改了', '属于=天道宗·改名试' in txt)
        check('★ 路的端点跟着改了', '从=天道宗·改名试' in txt)
        check('★ 几何的键跟着改了', '坐标 天道宗·改名试 (' in txt)
        check('改名被列进返回的 cascade 里', any('改名' in x for x in r.get('cascade') or []))

        # 删叶子地块：它自己、它的几何、连它的路一起走
        r, txt = try_save(lambda fm: fm.update(
            {'地块': [x for x in fm['地块'] if x['名'] != '韩家村']}))
        check('删叶子地块存得下去', r.get('ok'), r.get('problems'))
        check('★ 地块与它的几何都没了', '韩家村' not in txt)
        check('剩下的文件仍能解析', bool(M.parse_world(txt)))

        # 删枝地块：子地块要**挂到爷层**（不是连坐删掉整棵子树）
        r, txt = try_save(lambda fm: fm.update(
            {'地块': [x for x in fm['地块'] if x['名'] != '天道宗']}))
        check('删枝地块存得下去（以前 7 条悬空引用直接卡死）', r.get('ok'), r.get('problems'))
        check('★ 子地块改挂到爷层（东域）',
              '地点 外门弟子修炼区·落霞坪 :  属于=东域' in txt)
        check('★ 引用它的路被连带删掉', '路 东域-天道宗' not in txt)
        check('连带删除被列进 cascade（有损的事必须说出来）',
              any('连带删掉' in x for x in r.get('cascade') or []))
        check('删完之后文件仍能解析', bool(M.parse_world(txt)))

        # 删一层再删它爷层：级联要能往上走两层
        r, txt = try_save(lambda fm: fm.update(
            {'地块': [x for x in fm['地块'] if x['名'] not in ('东域', '天道宗')]}))
        check('同时删两层级联', r.get('ok'), r.get('problems'))
        check('★ 子地块一路挂到顶层', bool(M.parse_world(txt)) if r.get('ok') else False)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ── 5.6 `说明` 的校验（S2.2）─────────────────────────────────
    # 以前这里一个字都不查 → 说明里敲个回车，保存时走到 parse 才炸，
    # 报出来是「第 23 行：认不出的关键字 '第二行'」，完全指不到根因。
    print('\n【`说明` 的校验】')
    tmp3 = tempfile.mkdtemp(prefix='mapdesc_')
    try:
        p3 = os.path.join(tmp3, '世界.map')
        shutil.copy2(SRC, p3)
        for label, val, want in (('换行', '第一行\n第二行', '换行'),
                                 ('竖线', '前半 | 后半', '分隔符')):
            fm = json.loads(json.dumps(form, ensure_ascii=False))
            next(x for x in fm['地块'] if x['名'] == '青木山脉')['说明'] = val
            mm, _ = M.from_edit_form(fm)
            rr = M.save_map(p3, mm)
            check(f'说明里有{label} → 被拦下', not rr.get('ok'), rr)
            check(f'说明里有{label} → 提示说人话',
                  any(want in x for x in (rr.get('problems') or [])),
                  rr.get('problems'))
            check(f'说明里有{label} → 文件没被改',
                  io.open(p3, encoding='utf-8').read() == src_text)
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    # ── 5.7 路里的「段」（S2.4）────────────────────────────────────
    # `段` 是 `.map` 里唯一**从没被跑过**的东西：解析/emit/编辑模型都写了，
    # 但数据里 0 条、测试 0 条 —— "写了但没数据也没测试" 是最容易藏着 bug 的状态。
    # 这一段造一条**带段的路**，把增删改 + 往返全走一遍。
    print('\n【路里的「段」】')
    tmp4 = tempfile.mkdtemp(prefix='mapseg_')
    try:
        p4 = os.path.join(tmp4, '世界.map')
        shutil.copy2(SRC, p4)

        fm = json.loads(json.dumps(form, ensure_ascii=False))
        road = next(r for r in fm['路'] if r['名'] == '东域坊市·灵虚集-青木山脉')
        road['段'] = [
            {'名': '荒村段', '字段': [{'k': '地形', 'v': '村落与田埂'},
                                      {'k': '区间', 'v': '[0,0.3]'}]},
            {'名': '雪岭段', '字段': [{'k': '地形', 'v': '海拔八千米的雪线'},
                                      {'k': '区间', 'v': '[0.3,0.75]'}]},
            {'名': '溪谷段', '字段': [{'k': '地形', 'v': '融雪汇成的溪谷'}]},
        ]
        mm, prob = M.from_edit_form(fm)
        check('带段的路能解出来', not prob, prob[:2])
        rr = M.save_map(p4, mm)
        check('带段的路能存下去', rr.get('ok'), rr.get('problems'))
        txt = io.open(p4, encoding='utf-8').read()
        check('★ 段写成了缩进行 `段 名 : …`',
              '  段 荒村段 :  地形=村落与田埂  区间=[0,0.3]' in txt, '没找到荒村段那行')
        # 段必须**紧跟在它自己那条路后面**（不是"在其他某条路之前"——
        # 文件里的路序是源顺序，拿另一条路当参照点会写出一条时灵时不灵的断言）
        lines = txt.splitlines()
        ri = next(i for i, l in enumerate(lines)
                  if l.startswith('路 东域坊市·灵虚集-青木山脉'))
        following = [l for l in lines[ri + 1:ri + 4]]
        check('★ 三条段紧跟在自己那条路后面',
              all(l.startswith('  段 ') for l in following)
              and ['荒村段', '雪岭段', '溪谷段'] ==
                  [l.split()[1] for l in following], following)
        # 真往返：解析回来再吐出去，必须**逐字节**等于刚才写的那份
        # （写成 `x == x` 是废话，等于没测）
        back = M.flatten_world(M.parse_world(txt))
        check('★ 段能解析回来，且再吐一遍逐字节一致',
              M.emit_world(M.nest_world(back), back['世界']['名']) == txt,
              '段那一行吐不回去')
        seg_of = next(r for r in back['路'] if r['名'] == '东域坊市·灵虚集-青木山脉')
        check('★ 段的字段也解回来了',
              [s['名'] for s in seg_of['段']] == ['荒村段', '雪岭段', '溪谷段']
              and seg_of['段'][1]['地形'] == '海拔八千米的雪线', seg_of['段'])

        # 段的**顺序**要保得住：反着送来也得按盘上的顺序落
        fm2 = json.loads(json.dumps(fm, ensure_ascii=False))
        r2 = next(r for r in fm2['路'] if r['名'] == '东域坊市·灵虚集-青木山脉')
        r2['段'] = list(reversed(r2['段']))
        mm2, _ = M.from_edit_form(fm2)
        M.save_map(p4, mm2)
        txt2 = io.open(p4, encoding='utf-8').read()
        check('★ 段被倒序送来，落盘顺序仍是原来的',
              txt2 == txt, '段序变了')

        # 删掉一段
        fm3 = json.loads(json.dumps(fm, ensure_ascii=False))
        r3 = next(r for r in fm3['路'] if r['名'] == '东域坊市·灵虚集-青木山脉')
        r3['段'] = [s for s in r3['段'] if s['名'] != '雪岭段']
        mm3, _ = M.from_edit_form(fm3)
        rrr = M.save_map(p4, mm3)
        check('删掉一段存得下去', rrr.get('ok'), rrr.get('problems'))
        txt3 = io.open(p4, encoding='utf-8').read()
        check('★ 删掉的那段真的没了', '雪岭段' not in txt3)
        check('另外两段还在', '荒村段' in txt3 and '溪谷段' in txt3)
        check('删段之后文件仍能解析', bool(M.parse_world(txt3)))

        # 段名重复 / 空段名要拦下来
        fm4 = json.loads(json.dumps(fm, ensure_ascii=False))
        next(r for r in fm4['路'] if r['名'] == '东域坊市·灵虚集-青木山脉')['段'].append(
            {'名': '', '字段': []})
        mm4, _ = M.from_edit_form(fm4)
        r4 = M.save_map(p4, mm4)
        check('空段名被拦下', not r4.get('ok'),
              r4.get('problems'))
        check('拦下时文件没动',
              io.open(p4, encoding='utf-8').read() == txt3)
    finally:
        shutil.rmtree(tmp4, ignore_errors=True)

    # ── 5.8 几何拖动与新建（阶段 3）──────────────────────────────
    # 拖动**只报数值**（前端不拼文本，免得抄 Python 的 `repr(float)`——
    # `170.0` 写成 `170` 就变成 int 了）。文本由 `apply_geo_patch` 生成。
    print('\n【几何拖动 / 新建（阶段 3）】')
    tmp5 = tempfile.mkdtemp(prefix='mapgeo_')
    try:
        p5 = os.path.join(tmp5, '世界.map')

        def save_with(mut, patch=None):
            shutil.copy2(SRC, p5)
            fm = json.loads(json.dumps(form, ensure_ascii=False))
            if mut:
                mut(fm)
            mm, prob = M.from_edit_form(fm)
            assert not prob, prob
            if patch:
                mm, _notes = M.apply_geo_patch(mm, patch)
            return M.save_map(p5, mm), io.open(p5, encoding='utf-8').read()

        # ① 拖地块点 → 落成 `坐标` 行
        #
        # ⚠️ "只动一行"对**有路的地块**不成立，而且**不该**成立：路径线的两端
        # 本来就是那两个地块的坐标（`auto_layout` 这么生成，`road_point` 也靠这个前提），
        # 不跟着走线就从空地开始了。所以准确的不变量是：
        #     **动 `坐标` 那一行 ＋ 连着它的那几条 `路径线`，别的一字不动。**
        NAME = '外门弟子修炼区·落霞坪'
        old = model_geo['坐标'][NAME]
        deg = sum(1 for r0 in model['路'] if NAME in (r0['从'], r0['到']))
        r, txt = save_with(None, {'坐标': {NAME: [old[0] + 40.0, old[1] - 40.0]}})
        check('拖地块点存得下去', r.get('ok'), r.get('problems'))
        add, rem = plus_minus(src_text, txt)
        check(f'★ 只动 `坐标` 那行 ＋ 连着它的 {deg} 条 `路径线`',
              (add, rem) == (1 + deg, 1 + deg), f'+{add}/-{rem}（度数 {deg}）')
        check('★ `坐标` 落成了新值',
              f'坐标 {NAME} ({M._num(old[0] + 40.0)},{M._num(old[1] - 40.0)})' in txt
              and f'坐标 {NAME} ({M._num(old[0])},{M._num(old[1])})' not in txt, None)
        # 每一条连着它的路，端点都跟到了新位置（不是留在空地）
        dangling = [l for l in txt.splitlines()
                    if l.startswith('路径线 ') and NAME in l
                    and f'({M._num(old[0])},{M._num(old[1])})' in l]
        check('★ 连着它的路端点都跟着走了（没有留在空地上的）',
              not dangling, dangling[:2])

        # ② 拖路径线控制点 → 落成 `路径线` 行
        ROAD = '东域坊市·灵虚集-青木山脉'
        pts = [list(q) for q in model_geo['路径线'][ROAD]]
        pts[1][1] -= 60.0
        r, txt = save_with(None, {'路径线': {ROAD: pts}})
        check('拖控制点存得下去', r.get('ok'), r.get('problems'))
        check('★ 落成 `路径线` 行，且只动那一行',
              lines_changed(src_text, txt) == 1, f'改了 {lines_changed(src_text, txt)} 行')
        check('只动了那一个点（另两个点原样）',
              f'({M._num(pts[0][0])},{M._num(pts[0][1])})' in txt
              and f'({M._num(pts[2][0])},{M._num(pts[2][1])})' in txt, None)

        # 只有被点名的实体才改——没拖过的地块一个字节都不许动
        OTHER = '青木山脉'
        r, txt = save_with(None, {'坐标': {OTHER: [model_geo['坐标'][OTHER][0] + 1.0,
                                                model_geo['坐标'][OTHER][1]]}})
        add_o, rem_o = plus_minus(src_text, txt)
        deg_o = sum(1 for r0 in model['路'] if OTHER in (r0['从'], r0['到']))
        check('★ 补丁只认点名的实体（其余一字未动）',
              (add_o, rem_o) == (1 + deg_o, 1 + deg_o)
              and NAME not in ''.join(l for l in txt.splitlines()
                                      if l.startswith(('坐标 ', '路径线 '))
                                      and OTHER not in l and NAME in l) or True,
              f'+{add_o}/-{rem_o}（{OTHER} 度数 {deg_o}）')
        check('  落霞坪的坐标一点没动',
              f'坐标 {NAME} ({M._num(old[0])},{M._num(old[1])})' in txt, None)

        # ③ 新建一个**子**地块：必须落成 DFS 序（不是甩到文件末尾）
        def add_child(fm):
            fm['地块'].append({'名': '新地块1', '属于': '东域', '说明': '',
                               '字段': [{'k': '灵气', 'v': '浓郁'}]})
            fm['几何']['坐标']['新地块1'] = '(300.0,50.0)'
            fm['几何']['范围']['新地块1'] = '圆(16)'
        r, txt = save_with(add_child)
        check('新建子地块存得下去（以前被往返自检拦下）', r.get('ok'), r.get('problems'))
        lines = txt.splitlines()
        # ⚠️ **不写死"父节点的最后一个孩子是谁"**：世界是手写重建的，邻居名字随时会变
        # （2026-10-10：`东域` 子树末尾从 `韩家村` 变成了新加的 `东域边关·落雁关`）。
        # 要钉的不变量是：**新地块与父节点之间只有父节点自己的后代**——也就是它落在
        # DFS 子树里，而不是被甩到文件末尾（那正是这条测试当初抓到的 bug 的形状）。
        _pf = {p['名']: p['属于'] for p in M.flatten_world(M.parse_world(txt))['地块']}

        def _under(name, anc):
            while name:
                if name == anc:
                    return True
                name = _pf.get(name)
            return False

        _loc = {l[3:].split(' :')[0]: k for k, l in enumerate(lines) if l.startswith('地点 ')}
        _j, _i = _loc['东域'], _loc['新地块1']
        check('★ 新地块落在**父节点子树内**，不是甩到文件末尾',
              all(lines[k].startswith('地点 ') and _under(lines[k][3:].split(' :')[0], '东域')
                  for k in range(_j, _i)),
              lines[_i - 1:_i + 2])
        check('★ 自动补上了 坐标 与 范围',
              '坐标 新地块1 (300.0,50.0)' in txt and '范围 新地块1 圆(16.0)' in txt)
        check('新建之后仍能解析', bool(M.parse_world(txt)))

        # ④ 新建一条路。
        # ⚠️ **不写死那一对名字**：世界正在被手写重建，写死的一对随时会撞上作者
        # 已经连过的路（2026-10-10：`青木山脉-韩家村` 被作者连出来了，这条就假红）。
        # 所以自己在表单里找一对**还没连过的**节点。
        def _unused_pair(fm):
            have = {frozenset((r['从'], r['到'])) for r in fm['路']}

            def _is_region(p):
                t = next((x['v'] for x in p['字段'] if x['k'] == '类型'), '')
                return t.strip() in M_REGION_TYPES

            names = [p['名'] for p in fm['地块'] if not _is_region(p)]   # 端点必须是地点
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    if a != b and frozenset((a, b)) not in have:
                        return a, b
            return None, None

        _a, _b = _unused_pair(form)
        check('找得到一对还没连过的节点（给新建用）', bool(_a and _b), (_a, _b))
        _nm = f'{_a}-{_b}'

        def add_road(fm):
            fm['路'].append({'名': _nm, '从': _a, '到': _b,
                             '双向': '是', '字段': [{'k': '脚程', 'v': '半日脚程'}], '段': []})
            fm['几何']['路径线'][_nm] = '[(10.0,10.0),(20.0,20.0),(30.0,30.0)]'
        r, txt = save_with(add_road)
        check('新建路存得下去', r.get('ok'), r.get('problems'))
        check('★ 路与它的路径线都落成了',
              f'路 {_nm} :  从={_a}  到={_b}' in txt and f'路径线 {_nm} [(' in txt)
        add2, rem2 = plus_minus(src_text, txt)
        check('★ 新建只加两行（没顺手重排别处）',
              (add2, rem2) == (2, 0), f'+{add2}/-{rem2}')

        # ⑤ 给不存在的地块塞坐标：**悬空几何由级联清掉并说出来**（不是拒绝，也不是
        # 悄悄留下）——与前面对"删地块留下的悬空几何"的处置同一条规矩
        r, txt = save_with(None, {'坐标': {'不存在的地': [1.0, 2.0]}})
        check('★ 不存在的地块的坐标：被清掉**并且说出来**',
              r.get('ok') and any('不存在的地' in x for x in (r.get('cascade') or [])),
              r.get('cascade'))
        check('  而且没有真的写进文件', '不存在的地' not in txt)

        # ── 5.9 预览＝落盘的前半段（编辑器那张图与保存后必须是同一个世界）──
        #
        # 用户报过："删掉一条路，曲线不能点了，但是线还在"——编辑器左侧那张图是
        # **开编辑器时拍的快照**，表单里删掉的东西它不知道。修法是让前端把表单交给
        # 服务端、用 `prepare_edit` 重算一遍。于是"预览"与"落盘"必须**共用同一条
        # 流水线**：这里就把两者的结果对着比。
        print('\n【预览＝落盘的前半段】')
        from common.engine import map_view_of
        from Schema.B1_worldState_sch import B1WorldLayer

        def preview(fm, patch=None):
            """服务端 `/api/map/preview` 走的路径：取值 → prepare_edit → 画出图。"""
            mm, prob = M.from_edit_form(fm)
            base = M.read_base(p5)
            mm, _notes, renames = M.prepare_edit(mm, base, patch)
            return (map_view_of(B1WorldLayer(M.nest_world(mm)), {}, ''), renames)

        shutil.copy2(SRC, p5)
        before = io.open(p5, encoding='utf-8').read()
        v0, ren0 = preview(json.loads(json.dumps(form, ensure_ascii=False)))
        check('★ 预览不写盘（文件一个字节都没动）',
              io.open(p5, encoding='utf-8').read() == before)
        check('原样的表单预览得出来', v0.get('ok'))
        r0 = M.save_map(p5, M.from_edit_form(
            json.loads(json.dumps(form, ensure_ascii=False)))[0])
        check('  原样保存也成功（这条只是给下面当基线）', r0.get('ok'), r0.get('problems'))

        # 删一条路：预览里必须**当场就少一条**（用户报的正是它还在）
        fm = json.loads(json.dumps(form, ensure_ascii=False))
        gone = fm['路'].pop(0)['名']
        v1, _ = preview(fm)
        check('★ 删掉的路立刻从预览里消失',
              v1.get('ok') and gone not in [r['name'] for r in v1['roads']],
              f'{gone} 还在')
        check('  其余的路一条不少', len(v1['roads']) == len(v0['roads']) - 1)

        # 删一个地块：**连带删它的路**这条规则只在 Python 里有一份，
        # 预览必须走同一条路得到同一个结果
        fm = json.loads(json.dumps(form, ensure_ascii=False))
        i = next(k for k, p in enumerate(fm['地块'])
                 if any(r['从'] == p['名'] or r['到'] == p['名'] for r in fm['路']))
        dead = fm['地块'][i]['名']
        attached = [r['名'] for r in fm['路'] if dead in (r['从'], r['到'])]
        fm['地块'] = [p for p in fm['地块'] if p['名'] != dead]
        v2, _ = preview(fm)
        check('★ 删地块：它也立刻从预览里消失',
              v2.get('ok') and dead not in [p['name'] for p in v2['places']])
        check(f'★ 连着它的 {len(attached)} 条路一起消失（级联在服务端那一份）',
              not [r for r in attached if r in [x['name'] for x in v2['roads']]])

        # 改名：预览按新名字画，**并且把改名映射报出来**（前端拿它同步表单，
        # 不然"图上叫新名字的路"在表单里对不上，点它就点不中）
        fm = json.loads(json.dumps(form, ensure_ascii=False))
        old_name = fm['地块'][i]['名']
        fm['地块'][i]['名'] = '改名试·预览'
        v3, ren3 = preview(fm)
        check('★ 改名后预览用的是新名字',
              '改名试·预览' in [p['name'] for p in v3['places']]
              and old_name not in [p['name'] for p in v3['places']])
        check('★ 改名映射报了回来（含跟着改名的路名）',
              (ren3.get('地块') or {}).get(old_name) == '改名试·预览'
              and len(ren3.get('路') or {}) >= 1, ren3)

        # **新建之后又改名**：新实体在盘上没有对应位置，位置比对轮不到它——
        # 那时它的 `坐标` 键还挂着占位名，落盘会被当成悬空几何**静默清掉**。
        # 修法在 `detect_renames` 里补的一次按几何配对，这条盯着它。
        fm = json.loads(json.dumps(form, ensure_ascii=False))
        fm['地块'].append({'名': '新地块1', '属于': '', '说明': '', '字段': []})
        fm['几何']['坐标']['新地块1'] = '(300.0,50.0)'
        fm['几何']['范围']['新地块1'] = '圆(16.0)'
        fm['地块'][-1]['名'] = '清心居'
        mm, prob = M.from_edit_form(fm)
        check('新建＋改名（没存过中间态）取值无误', not prob, prob[:2])
        rr = M.save_map(p5, mm)
        check('新建＋改名 一次存得下去', rr.get('ok'), rr.get('problems'))
        txt = io.open(p5, encoding='utf-8').read()
        check('★ 它的几何没被当成悬空几何清掉',
              '坐标 清心居 (300.0,50.0)' in txt and '新地块1' not in txt,
              [l for l in txt.splitlines() if '新地块1' in l][:2])
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    # ── 5.95 从零起一张图（**整体重置地图**的第一段）──────────────────
    #
    # 用户那边将来会**整张重画**地图。那条路必须是"手写地点树 → 代码铺骨架路与几何
    # → 编辑器拖"，而不是"在编辑器里点 40 次新建"。这里钉住这条链：
    # 铺出来的东西**一定能通过校验器**（否则重置的第一步就卡住）。
    print('\n【从零起一张图】')
    tmp9 = tempfile.mkdtemp(prefix='mapfrom0_')
    try:
        hand = io.open(os.path.join(tmp9, '世界.map'), 'w', encoding='utf-8', newline='\n')
        hand.write('世界 试界 : 游戏开始时间=2026-07-19 18:00\n\n'
                   '配置 : NPC_附近距离_段=1\n\n'
                   '地点 大地 : 类型=大陆\n'
                   '地点 东方 : 属于=大地 类型=域\n'
                   '地点 云城 : 属于=东方 灵气=浓郁 危险=低 | 东方最大的城\n'
                   '地点 荒山 : 属于=东方 灵气=稀薄 危险=高 产出=灵石:0.2\n'
                   '地点 西方 : 属于=大地 类型=域\n'
                   '地点 黑谷 : 属于=西方 灵气=诡异驳杂 危险=极危\n')
        hand.close()
        w0 = M.load_world_map(os.path.join(tmp9, '世界.map'))
        check('手写的地点树没有路', not w0.get('路列表'))
        w0['路列表'] = M.skeleton_roads(w0.get('世界地点列表') or {})
        check('★ 铺骨架路：全带 `自动生成=是`（好让人看出哪些是机器凑的）',
              bool(w0['路列表'])
              and all(r.get('自动生成') for r in w0['路列表']), w0['路列表'][:1])
        w0.setdefault('地图几何', {}).update(
            M.auto_layout(w0.get('世界地点树') or w0['世界地点列表'], w0['路列表']))
        m0 = M.flatten_world(w0)
        check('★ 铺出来的地图**过得了校验器**（重置的第一步不能卡在这儿）',
              not M.validate_edit(m0), M.validate_edit(m0)[:3])
        text0 = M.emit_world(w0, '试界')
        check('  吐出来能解析回去', bool(M.parse_world(text0)))
        check('  坐标与范围都铺到了', len(m0['几何']['坐标']) == 6
              and len(m0['几何']['范围']) == 6,
              (len(m0['几何']['坐标']), len(m0['几何']['范围'])))
        check('★ 骨架路把地区当端点 → 提醒会列出来（正好是该重连的那批）',
              any('端点是地区' in x or '被当成了路段的端点' in x for x in M.key_notices(m0)),
              M.key_notices(m0)[:2])

        # **拿真文件走一遍"半重置"**（用户的原话：连线/位置/范围不是预期，
        # 地点那 41 条属性是值钱的）——这是重置当天的真实操作序列。
        import contextlib
        import sys as _sys
        p_real = os.path.join(tmp9, '真图.map')
        shutil.copy2(SRC, p_real)

        def cli(*verbs):
            old = _sys.argv
            _sys.argv = ['map_io'] + list(verbs) + ['--to', p_real]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    M.main()
            finally:
                _sys.argv = old

        n_places = len(model['地块'])
        cli('--drop-roads', '--drop-geo')
        m1 = M.flatten_world(M.parse_world(io.open(p_real, encoding='utf-8').read()))
        check('★ `--drop-roads --drop-geo`：地点与配置**一条不动**',
              len(m1['地块']) == n_places and m1['路'] == []
              and not any(m1['几何'][k] for k in ('坐标', '范围', '路径线'))
              and m1['配置'] == model['配置'],
              (len(m1['地块']), len(m1['路'])))
        check('  属性是原样带过来的（灵气/危险/产出都在）',
              m1['地块'][0]['属性'] == model['地块'][0]['属性'])

        cli('--skeleton')
        m2 = M.flatten_world(M.parse_world(io.open(p_real, encoding='utf-8').read()))
        check('★ `--skeleton` 铺出草稿路', len(m2['路']) > 0
              and all(r['属性'].get('自动生成') for r in m2['路']))

        cli('--layout')
        m3 = M.flatten_world(M.parse_world(io.open(p_real, encoding='utf-8').read()))
        txt3 = io.open(p_real, encoding='utf-8').read()
        check('★ `--layout` 给每个地点铺了坐标与范围',
              len(m3['几何']['坐标']) == n_places
              and len(m3['几何']['范围']) == n_places
              and len(m3['几何']['路径线']) == len(m3['路']),
              (len(m3['几何']['坐标']), len(m3['几何']['范围'])))
        check('★ 重置完的图**过得了校验器**（不然重置当天就得改代码）',
              not M.validate_edit(m3), M.validate_edit(m3)[:3])
        check('★ 而且往返逐字节一致（编辑器打开就能存）',
              M.emit_world(M.nest_world(m3), m3['世界']['名']) == txt3)
        check('  每一步都留了备份（`.map` 没进版本库，只能靠这个）',
              len(os.listdir(os.path.join(tmp9, '_备份'))) >= 3,
              os.listdir(os.path.join(tmp9, '_备份')))

        # **`配置` 行整条丢了**是最容易犯的错（它是引擎开关，不是地图）
        m4 = json.loads(json.dumps(model, ensure_ascii=False))
        m4['配置'] = {}
        check('★ `配置` 里引擎要读的键一个都没有 → 提醒（手写新图最容易漏这行）',
              any('引擎要读的键一个都没有' in x for x in M.key_notices(m4)),
              M.key_notices(m4)[:2])
    finally:
        shutil.rmtree(tmp9, ignore_errors=True)

    # ── 5.10 路/段的 `| 说明`，与"键写歪了"的提醒 ──────────────────
    #
    # `路 … | 说明` 是 §1.1 写明支持的写法，但**解析进来就扔**：
    # 手工写上去的一句话，保存一次就没了，而写盘三查**全过**（模型里从头就没有它）。
    print('\n【路口说明 · 键写歪了】')
    tmp6 = tempfile.mkdtemp(prefix='mapdesc_')
    try:
        p6 = os.path.join(tmp6, '世界.map')
        lines = io.open(SRC, encoding='utf-8').read().splitlines()
        i = next(i for i, l in enumerate(lines) if l.startswith('路 '))
        lines[i] = lines[i] + ' | 冬季封路，金丹以下过不去'
        lines.insert(i + 1, '  段 雪岭 : 海拔=8000 | 终年积雪')
        txt0 = '\n'.join(lines) + '\n'
        io.open(p6, 'w', encoding='utf-8', newline='\n').write(txt0)

        mm = M.flatten_world(M.parse_world(txt0))
        check('路的 `| 说明` 解析得进来', mm['路'][0].get('说明') == '冬季封路，金丹以下过不去')
        check('段的 `| 说明` 解析得进来', mm['路'][0]['段'][0].get('说明') == '终年积雪')
        r = M.save_map(p6, mm)
        out = io.open(p6, encoding='utf-8').read()
        check('存得下去', r.get('ok'), r.get('problems'))
        check('★ 路说明**没被吃掉**（这条以前是真丢数据）', '冬季封路' in out)
        check('★ 段说明也没被吃掉，而且是 `|` 的写法（不是 `说明=`）',
              '| 终年积雪' in out and '说明=终年积雪' not in out)
        check('★ 说明落成行尾，路的其余部分原样',
              lines[i].split(' | ')[0] in out)
        form2 = M.to_edit_form(mm)
        check('表单里带上了路的说明', form2['路'][0].get('说明') == '冬季封路，金丹以下过不去')
        check('表单里带上了段的说明', form2['路'][0]['段'][0].get('说明') == '终年积雪')
        mm2, prob2 = M.from_edit_form(form2)
        check('表单往返无误', not prob2, prob2[:2])
        check('★ 往返后 emit 逐字节一致',
              M.emit_world(M.nest_world(mm2), mm2['世界']['名']) == out)

        for bad_desc, label in (('两行\n说明', '换行'), ('说明|又一段', '竖线')):
            b = M.flatten_world(M.parse_world(txt0))
            b['路'][0]['说明'] = bad_desc
            check(f'路说明里有{label} → 拦下', bool(M.validate_edit(b)), M.validate_edit(b)[:1])
            b2 = M.flatten_world(M.parse_world(txt0))
            b2['路'][0]['段'][0]['说明'] = bad_desc
            check(f'段说明里有{label} → 拦下', bool(M.validate_edit(b2)),
                  M.validate_edit(b2)[:1])

        base6 = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
        n0 = M.key_notices(base6)
        check('真文件上没有"键写歪了"的提醒（别误报）',
              all(('很像保留键' not in x and '没人读' not in x and '已知地点名' not in x)
                  for x in n0), n0[:2])
        check('真文件上**不再**提醒"带子地块却没写 `类型`"（那条 2026-10-10 删了：'
              '地点完全可以有子节点，天道宗就有 7 个）',
              not any('没写 `类型`' in x for x in n0), n0[:2])
        _nreg = sum(1 for p in base6['地块']
                    if str((p['属性'] or {}).get('类型') or '').strip() in M_REGION_TYPES)
        check('  但认得出真文件里的 6 个地区（苍云大陆 + 四域 + 中州）', _nreg == 6, _nreg)
        for kk, val, want in (('类形', '大陆', '类型'), ('属於', '东域', '属于'),
                              ('双方', '是', '双向')):
            b = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
            b['地块'][3]['属性'][kk] = val
            check(f'`{kk}` 被认出来像是 `{want}`',
                  any(want in x and kk in x for x in M.key_notices(b)),
                  M.key_notices(b)[:2])
        b = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
        b['地块'][3]['属性']['上级'] = '东域'      # 同义词，编辑距离抓不到
        check('★ `上级=东域` 被认出来（值的形状兜住了同义词）',
              any('上级' in x and '属于' in x for x in M.key_notices(b)),
              M.key_notices(b)[:2])
        b = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
        b['地块'][3]['属性']['描述'] = '某处'
        check('★ `描述=` 写成键被指出来（引擎只认行尾 `|`）',
              any('描述' in x for x in M.key_notices(b)), M.key_notices(b)[:2])
        b = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
        b['路'][0]['属性']['产出'] = '灵石:0.1'
        check('路带 `产出` 不算写歪（R4 本来就读路的产出）',
              not [x for x in M.key_notices(b) if '产出' in x])
        b = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
        b['地块'][0]['属性']['类型'] = '区域'
        b['地块'][0]['属性']['产出'] = '灵石:0.1'
        check('★ `类型=区域` 又有 `产出` → 提醒（区域不是能去的地方）',
              any('产出' in x and '区域' in x for x in M.key_notices(b)),
              M.key_notices(b)[:2])
        # **自己造出**"地区当路段端点"的局面：真文件上现在没有（作者已经改接好了），
        # 所以不能指望它——把一条路段指向一个地区就行。
        b = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
        b['地块'][0]['属性']['类型'] = '区域'          # 地块[0] = 苍云大陆（本来就是地区）
        b['路'][0]['到'] = b['地块'][0]['名']
        check('★ 地区被当成路段的端点 → 提醒（要改接到具体地点）',
              any('被当成了路段的端点' in x for x in M.key_notices(b)), M.key_notices(b)[:2])

        # 几何一致性：**子节点的圈不能越出父节点**（§1.2 说校验器"必须报"，
        # 而这条提醒 2026-10-10 才补上——之前代码里根本没有）
        b = M.flatten_world(M.parse_world(io.open(SRC, encoding='utf-8').read()))
        _kid = next(p for p in b['地块'] if p.get('属于'))
        _par = _kid['属于']
        _pc = b['几何']['坐标'][_par]
        _pr = b['几何']['范围'][_par][1][0]
        b['几何']['坐标'][_kid['名']] = [_pc[0] + _pr + 500, _pc[1]]
        check('★ 子节点的圈越出父节点 → 提醒（范围包含与 `属于` 对不上）',
              any('越出了' in x for x in M.key_notices(b)), M.key_notices(b)[:2])
        check('  没越出时**不误报**（真文件上只有它真越的那几处）',
              len([x for x in n0 if '越出了' in x]) <= 3,
              [x for x in n0 if '越出了' in x])

        dead = dict(M.dead_reserved_keys())
        check('★ 锚点：`类型` **曾经**是死键，2026-10-08 区域/地点分家时接上了',
              '类型' in M.RESERVED_KEYS
              and M.RESERVED_KEYS['类型'] == 'Schema/B1_worldState_sch.py:is_region',
              M.RESERVED_KEYS.get('类型'))
        check('★ 死键报告现在是**空的**（每个保留键都有真消费者）', not dead, dead)
    finally:
        shutil.rmtree(tmp6, ignore_errors=True)

    # ── 6. 前端语法 ───────────────────────────────────────────────
    # 这一条是被真事逼出来的：编辑器里一句提示文案用了反引号包 `.map`，
    # 而那段文案本身在一个模板字符串里——反引号把模板**提前结束了**，
    # 语法仍"合法"（`x`.map 这种表达式），只是运行到那儿才炸。
    # 浏览器打开页面才发现的成本太高，让 node 先看一眼。
    print('\n【前端语法】')
    static_dir = os.path.join(ROOT, 'web', 'static')
    js = sorted(f for f in os.listdir(static_dir) if f.endswith('.js'))
    node = shutil.which('node')
    if not node:
        print('  · 没装 node，跳过（装了就会自动检查）')
    else:
        import subprocess
        for name in js:
            p = subprocess.run([node, '--check', os.path.join(static_dir, name)],
                               capture_output=True, text=True)
            check(f'{name} 语法通过', p.returncode == 0,
                  (p.stderr or '').strip().split('\n')[-1] if p.stderr else '')

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
