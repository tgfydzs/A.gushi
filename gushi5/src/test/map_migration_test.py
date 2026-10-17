"""
地点模型迁移基线 —— "**先冻基线，再换形状**"的对照器。

背景：地点树要从**嵌套字典**改成**扁平实体表**（见 `地图格式.md`）。
换形状最容易发生的事故是"静默丢能力"——某个属性、某条包含链、某个位置引用
在新的解析器里没被搬过来，而不报任何错。所以：

    1. 迁移**前**跑 `--freeze`，用当时的代码榨出一份**语义快照**存进 `迁移基线/`
    2. 迁移**后**跑（默认模式），用新代码榨出同样的快照，与基线**逐条对照**
    3. 一模一样才算演化成功——**这才允许删掉旧断言**

快照刻意做成**格式无关**的：它只记录"这个世界的语义长什么样"
（有哪些地点、每个地点有哪些属性、包含链是什么、位置引用能不能解析），
不记录任何文件格式细节。所以换格式不影响它的可比性。

跑法：
    python src/test/map_migration_test.py --freeze   # 迁移前：生成基线（覆盖旧的）
    python src/test/map_migration_test.py            # 迁移后：与基线对照
    python src/test/map_migration_test.py --show 地点名   # 打印某个地点的快照

⚠️ 本脚本**不调 LLM、不碰网络**，纯读世界数据。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-not-used')

from common.game_controller import GameController
from common.base_cnf import BASE_PATH

BASELINE_DIR = os.path.join(BASE_PATH, '迁移基线')
BASELINE_JSON = os.path.join(BASELINE_DIR, '地点基线.json')
BASELINE_MD = os.path.join(BASELINE_DIR, '地点基线.md')

#: 这些字段名下的值是"位置引用"，迁移时必须全部仍能解析。
#: `位置` 是 1A 之后的新字段（**坐标**），另两个是旧字段（**路径字符串**）——
#: 两代表示都要收，然后**投影成同一个东西**（地块名）再比。
POSITION_KEYS = ('当前所处位置', '当前位置', '当前地点', '位置')
POSITION_LIST_KEYS = ('已知地点',)


def _project(value, world) -> tuple:
    """
    位置引用 → `(地块名, 能否解析)`。**表示无关的投影**——这是 1A 能安全落地的前提。

    旧表示是路径字符串、新表示是坐标；**字面上必然全变**。所以对照不能比字面，
    只能比"它指的是哪个地块"：
        字符串 → `name_of()`（含 `·` 的地块名的坑由它处理，别再自己 split）
        [x, y] → `locate()` 出落在哪个范围里

    更细的"在路上"信息（距离、走了多远）**不参与对比**：那是几何细节，
    迁移动的是表示，不该被细节噪声淹没。
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            info = world.locate(float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return (f'<坏坐标 {value!r}>', False)
        name = info.get('地块')
        return (name or '<无地块>', bool(name))
    s = str(value or '').strip()
    if not s:
        return ('', False)
    if s in world.place_paths().values():          # 旧表示：完整路径
        return (world.name_of(s), True)
    if s in world.place_paths():                   # 已经是地块名
        return (s, True)
    return (s, False)


# ═══════════════════ 快照（格式无关） ═══════════════════

def _collect_nodes(world_layer) -> list:
    """遍历地点树 → [{path, leaf, desc, attrs}]，按 path 排序。

    **只走公开 API**（normalize_node / get_location_tree），不碰内部字典的键名——
    这样新解析器只要把同一批语义交出来，这个函数原样能用。
    """
    raw = world_layer.get_location_tree() or {}
    out = []

    def walk(node, prefix):
        _a, _d, kids = world_layer.normalize_node(node)
        for key, child in (kids or {}).items():
            path = f'{prefix}·{key}' if prefix else key
            attrs, desc, grand = world_layer.normalize_node(child)
            out.append({'path': path, 'leaf': not grand, 'desc': desc, 'attrs': attrs})
            if grand:
                walk(child, path)

    walk(raw, '')
    return sorted(out, key=lambda n: n['path'])


def _collect_refs() -> list:
    """扫全库 JSON，把"位置引用"全找出来，并标出能不能解析。

    这是 §6 那条自检的基线：迁移时一定有漏网的位置引用，
    它们在换格式后**会静默变成"解析不出的自由地点名"**。
    """
    from common.game_controller import GameController as GC   # 复用已加载的世界
    refs = []
    # ⚠️ **只扫 data/**。`save/` 是**运行期产物**：每存一次档就多几十条位置引用，
    # 于是这个套件会"每存一次档就红一次"——那不是世界内容变了，是基线扫描混进了
    # 运行期数据（2026-10-17 实测：一天里两次存档就贡献了 60 处假差异）。
    # 存档里的位置引用本来就该由读档路径自己保证（`migrate_positions` 幂等归一）。
    roots = [os.path.join(BASE_PATH, 'data')]

    def is_pos(v):
        return (isinstance(v, str) and v.strip()) or \
               (isinstance(v, (list, tuple)) and len(v) == 2
                and all(isinstance(x, (int, float)) for x in v))

    def scan(obj, where, fname):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in POSITION_KEYS and is_pos(v):
                    refs.append({'来源': fname, '字段': f'{where}.{k}', '值': v})
                elif k in POSITION_LIST_KEYS and isinstance(v, list):
                    for i, item in enumerate(v):
                        if isinstance(item, str) and item.strip():
                            refs.append({'来源': fname, '字段': f'{where}.{k}[{i}]',
                                         '值': item.strip()})
                else:
                    scan(v, f'{where}.{k}', fname)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                scan(item, f'{where}[{i}]', fname)

    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith('.json'):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    data = json.load(io.open(p, encoding='utf-8'))
                except Exception:
                    continue
                scan(data, '', os.path.relpath(p, BASE_PATH).replace('\\', '/'))
    return sorted(refs, key=lambda r: (r['来源'], r['字段']))


def snapshot(session: str = 'migbase') -> dict:
    """把地点模型榨成一份语义快照。"""
    gc = GameController(session)
    gc.init_world()
    gc.init_player()
    w = gc.b1WorldLayer

    nodes = _collect_nodes(w)
    paths = [n['path'] for n in nodes]
    chains = {p: w.get_location_context(p) for p in paths}
    spawn = gc.player_location

    refs = _collect_refs()
    for r in refs:
        # 投影成"地块名"：这样对照与**表示**无关（路径字符串 or 坐标都一样比）。
        # 字面比较在 1A 之后必然全红，那等于把基线废掉。
        name, ok = _project(r['值'], w)
        r['值'] = name
        r['可解析'] = ok

    return {
        '节点': nodes,
        '包含链': chains,
        '出生点': spawn,
        '出生点链': w.get_location_context(spawn),
        '世界配置': w.dict.get('世界配置', {}),
        '位置引用': refs,
    }


# ═══════════════════ 人读的摘要 ═══════════════════

def _report(snap: dict) -> str:
    nodes = snap['节点']
    depths = {}
    for n in nodes:
        d = len(n['path'].split('·'))
        depths[d] = depths.get(d, 0) + 1

    key_freq = {}
    prod_nodes = []
    for n in nodes:
        for k in (n['attrs'] or {}):
            key_freq[k] = key_freq.get(k, 0) + 1
        if (n['attrs'] or {}).get('产出'):
            prod_nodes.append((n['path'], len(n['attrs']['产出'])))

    unresolved = [r for r in snap['位置引用'] if not r['可解析']]

    L = ['# 地点模型基线（迁移前快照）', '',
         '> 由 `src/test/map_migration_test.py --freeze` 生成。**换形状前记得先冻这一份。**',
         '> 它记录的是**语义**（有哪些地点、属性、包含链、位置引用），与文件格式无关，',
         '> 所以迁移后能用同一脚本原样对照。', '',
         f'## 规模', '',
         f'- 地点节点：**{len(nodes)}**（叶 {sum(1 for n in nodes if n["leaf"])}'
         f' / 枝 {sum(1 for n in nodes if not n["leaf"])}）',
         f'- 层级分布：' + '，'.join(f'{k} 层 {v}' for k, v in sorted(depths.items())),
         f'- 带「产出」的节点：**{len(prod_nodes)}** 个，共 '
         f'{sum(c for _p, c in prod_nodes)} 条产出',
         f'- 位置引用（NPC 档 / 玩家档 / 存档）：**{len(snap["位置引用"])}** 处，'
         f'其中**解析不出 {len(unresolved)} 处**', '',
         f'- 出生点：`{snap["出生点"]}`', '',
         '## 属性键频次（前 20）', '',
         '| 键 | 出现在几个节点 |', '|----|---------------|']

    for k, v in sorted(key_freq.items(), key=lambda kv: -kv[1])[:20]:
        L.append(f'| `{k}` | {v} |')

    L += ['', '## 资源型地段（带产出）', '',
          '| 地点 | 产出条数 |', '|------|---------|']
    for p, c in prod_nodes:
        L.append(f'| `{p}` | {c} |')

    if unresolved:
        L += ['', '## ⚠️ 解析不出的位置引用（迁移前就存在）', '',
              '| 来源 | 字段 | 值 |', '|------|------|----|']
        for r in unresolved:
            L.append(f'| {r["来源"]} | {r["字段"]} | `{r["值"]}` |')

    L += ['', '## 出生点包含链（根 → 叶）', '']
    for c in snap['出生点链']:
        L.append(f"- `{c['层级']}` → {json.dumps(c['属性'], ensure_ascii=False)}")
    L.append('')
    return '\n'.join(L)


# ═══════════════════ 对照 ═══════════════════

def _diff(old: dict, new: dict) -> list:
    """逐条对照，返回差异清单（人类可读）。"""
    out = []

    def cmp(label, a, b):
        if a != b:
            out.append(f'{label} 不一致')

    cmp('出生点', old['出生点'], new['出生点'])
    cmp('世界配置', old['世界配置'], new['世界配置'])

    o_paths = [n['path'] for n in old['节点']]
    n_paths = [n['path'] for n in new['节点']]
    for p in sorted(set(o_paths) - set(n_paths)):
        out.append(f'地点丢失：{p}')
    for p in sorted(set(n_paths) - set(o_paths)):
        out.append(f'地点新增：{p}')

    o_by = {n['path']: n for n in old['节点']}
    n_by = {n['path']: n for n in new['节点']}
    for p in sorted(set(o_by) & set(n_by)):
        for field in ('leaf', 'desc', 'attrs'):
            if o_by[p][field] != n_by[p][field]:
                out.append(f'地点「{p}」的 {field} 变了：'
                           f'{json.dumps(o_by[p][field], ensure_ascii=False)[:80]} → '
                           f'{json.dumps(n_by[p][field], ensure_ascii=False)[:80]}')

    for p in sorted(set(old['包含链']) | set(new['包含链'])):
        if old['包含链'].get(p) != new['包含链'].get(p):
            out.append(f'包含链变了：{p}')

    o_ref = {(r['来源'], r['字段'], r['值']): r['可解析'] for r in old['位置引用']}
    n_ref = {(r['来源'], r['字段'], r['值']): r['可解析'] for r in new['位置引用']}
    for k in sorted(set(o_ref) - set(n_ref)):
        out.append(f'位置引用丢失：{k}')
    for k in sorted(set(n_ref) - set(o_ref)):
        out.append(f'位置引用新增：{k}')
    for k in sorted(set(o_ref) & set(n_ref)):
        if o_ref[k] != n_ref[k]:
            out.append(f'位置引用「{k[2]}」可解析性变了：{o_ref[k]} → {n_ref[k]}')

    return out


# ═══════════════════ 入口 ═══════════════════

def check_single_source() -> list:
    """
    防"两处真源"：地理与配置**只许**待在 `.map` 里。

    这条不是洁癖。项目里栽过一次同款：`NPC_上限_MAX_MEMORY` 与 JSON 里的
    `NPC_记忆上限` 对不上，**七项配置从来没生效过**，只因值恰好等于默认值才没暴露。
    两处真源漂起来是无声的——所以要机器来盯。
    """
    import io as _io
    from common.base_cnf import WORLD_DATA_FILE, WORLD_DATA_SELECT, BASE_PATH
    bad = []
    try:
        d = json.load(_io.open(os.path.join(BASE_PATH, WORLD_DATA_FILE), encoding='utf-8'))
        w = d.get(WORLD_DATA_SELECT) or {}
    except Exception as e:
        return [f'读不到世界 JSON：{type(e).__name__}: {e}']
    for k in ('世界地点列表', '世界配置', '游戏开始时间', '时间格式'):
        if k in w:
            bad.append(f'global.json 里还有 `{k}`——地理/配置的真源应当是 世界.map')
    return bad


def main() -> int:
    args = sys.argv[1:]
    snap = snapshot()

    if '--show' in args:
        i = args.index('--show')
        want = args[i + 1] if len(args) > i + 1 else ''
        hit = [n for n in snap['节点'] if want in n['path']]
        for n in hit:
            print(json.dumps(n, ensure_ascii=False, indent=2))
            print('链：', json.dumps(snap['包含链'].get(n['path']), ensure_ascii=False))
        if not hit:
            print(f'没找到含「{want}」的地点')
        return 0

    if '--freeze' in args:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        io.open(BASELINE_JSON, 'w', encoding='utf-8', newline='\n').write(
            json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True))
        io.open(BASELINE_MD, 'w', encoding='utf-8', newline='\n').write(_report(snap))
        print(f'✓ 基线已生成：{os.path.relpath(BASELINE_JSON, BASE_PATH)}')
        print(f'✓ 可读摘要：  {os.path.relpath(BASELINE_MD, BASE_PATH)}')
        print(f'  地点 {len(snap["节点"])} 个 / 位置引用 {len(snap["位置引用"])} 处')
        return 0

    if not os.path.isfile(BASELINE_JSON):
        print(f'✗ 没有基线文件（{os.path.relpath(BASELINE_JSON, BASE_PATH)}）。'
              f'先跑 `--freeze` 冻一份。')
        return 1

    old = json.load(io.open(BASELINE_JSON, encoding='utf-8'))
    diffs = _diff(old, snap) + check_single_source()
    if not diffs:
        print(f'✓ 与基线完全一致：地点 {len(snap["节点"])} 个、'
              f'包含链 {len(snap["包含链"])} 条、位置引用 {len(snap["位置引用"])} 处'
              f'（且地理只有 .map 一处真源）')
        return 0
    print(f'✗ 与基线有 {len(diffs)} 处差异：')
    for d in diffs[:40]:
        print(f'  · {d}')
    if len(diffs) > 40:
        print(f'  …还有 {len(diffs) - 40} 处')
    print()
    print('如果是你**有意**改了世界内容（加地点、改属性…）→ 跑 `--freeze` 重新冻结。')
    print('如果是换表示 / 换格式带出来的 → 那是**演化丢了东西**，不许冻，先修。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
