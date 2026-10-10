/* gushi4 前端 —— 纯原生，无构建步骤。
 *
 * 三条主线：
 *   1. 面板注册表：每个面板是独立组件，可自由开关，存 localStorage
 *   2. SSE：一条常驻事件流驱动叙事流 + 阶段进度 + 状态刷新
 *   3. 地点树：可折叠、可点击移动，替掉终端那个 input() 菜单
 */

'use strict';

// ══════════════════════════════════════════════════════════════
// 状态
// ══════════════════════════════════════════════════════════════

const S = {
  view: null,          // /api/state 的结果
  tree: null,          // /api/locations 的结果（**地图不可用时的降级**）
  map: null,           // /api/map 的结果（几何 + 当前所在）
  mapStatic: '',       // 静态层 SVG 片段（地块/路/范围）——一局只算一次
  npcs: [],
  selNpc: null,        // 选中查看内部表的 NPC
  npcDetail: null,
  busy: false,
  timing: [],          // 本轮的层耗时 [{name, seconds}]
  ctxCache: {},        // which -> {count, lines}
  expanded: new Set(), // 地点树展开的节点 path
  collapsedPanels: new Set(),
  streamCount: 0,
};

const MAX_STREAM = 400;   // 叙事流 DOM 上限，超了从头砍

// ══════════════════════════════════════════════════════════════
// 小工具
// ══════════════════════════════════════════════════════════════

const $ = id => document.getElementById(id);

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * 字段名容错 —— 与 Python 侧 `small_tool.pick_field` 同一套规则。
 *
 * 数据里键是「中文+英文」连写（`当前状态CurrentState`），但 LLM 产出的增量
 * 经常丢掉英文后缀（只写 `当前状态`）。这里按 精确 → 归一化 两级找。
 */
function pick(o, ...names) {
  if (!o || typeof o !== 'object') return undefined;
  for (const n of names) if (o[n] !== undefined) return o[n];
  const norm = s => String(s).replace(/[A-Za-z0-9_\s]/g, '');
  const byNorm = new Map(Object.keys(o).map(k => [norm(k), k]));
  for (const n of names) {
    const k = byNorm.get(norm(n));
    if (k !== undefined) return o[k];
  }
  return undefined;
}

const isEmpty = v => v == null || v === '' || (Array.isArray(v) && !v.length)
  || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length);

/**
 * 地段属性值 → 一行可读文字。
 *
 * 属性的值形状是自由的（`"浓郁"` 字符串、`["修炼","比试"]` 数组、
 * 将来还可能嵌套），所以不能直接丢进模板——数组会渲染成 `a,b`，
 * 对象会变成 `[object Object]`。
 */
function attrText(v) {
  if (Array.isArray(v)) return v.length ? v.join('、') : '无';
  if (v && typeof v === 'object') {
    return Object.entries(v).map(([k, x]) => `${k}${attrText(x)}`).join('　');
  }
  return String(v == null ? '' : v);
}

/** 地段属性里**不该给玩家看**的键。 */
const ATTR_HIDDEN = new Set([
  '产出',   // 投放概率表 —— 露出来等于把骰子给玩家看，那是引擎内部
]);

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => '')}`.slice(0, 200));
  return r.json();
}

const post = (path, body) => api(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

// ══════════════════════════════════════════════════════════════
// 面板注册表
// ══════════════════════════════════════════════════════════════
//
// zone：放在左栏还是右栏
// presets：哪些预设默认打开

const PANELS = [
  { id: 'status', title: '状态', zone: 'left', presets: ['play', 'dev'], render: panelStatus },
  { id: 'vitals', title: '身体', zone: 'left', presets: ['play', 'dev'], render: panelVitals },
  { id: 'inventory', title: '持有物', zone: 'left', presets: ['play', 'dev'], render: panelInventory },
  { id: 'present', title: '在场', zone: 'left', presets: ['play', 'dev'], render: panelPresent },

  { id: 'map', title: '地点', zone: 'right', presets: ['play', 'dev'], render: panelMap },
  { id: 'npcs', title: '全部 NPC', zone: 'right', presets: ['dev'], render: panelAllNpcs },
  { id: 'npcdetail', title: 'NPC 内部表', zone: 'right', presets: ['dev'], render: panelNpcDetail },
  { id: 'eventctx', title: '事件上下文', zone: 'right', presets: ['dev'], render: () => panelCtx('event') },
  { id: 'directorctx', title: '导演上下文', zone: 'right', presets: ['dev'], render: () => panelCtx('director') },
  { id: 'globalctx', title: '全局上下文', zone: 'right', presets: ['dev'], render: () => panelCtx('global') },
  { id: 'timing', title: '层耗时', zone: 'right', presets: ['dev'], render: panelTiming },
];

const PANEL_BY_ID = Object.fromEntries(PANELS.map(p => [p.id, p]));

function defaultVisible(preset) {
  return new Set(PANELS.filter(p => p.presets.includes(preset)).map(p => p.id));
}

function loadVisible() {
  try {
    const raw = localStorage.getItem('g4.panels');
    if (raw) return new Set(JSON.parse(raw));
  } catch (e) { /* 存储坏了就用默认 */ }
  return defaultVisible('play');
}

function saveVisible() {
  localStorage.setItem('g4.panels', JSON.stringify([...S.visible]));
}

S.visible = loadVisible();

// ══════════════════════════════════════════════════════════════
// 面板渲染
// ══════════════════════════════════════════════════════════════

const st = () => (S.view && S.view.player && S.view.player.status) || {};
const inv = () => (S.view && S.view.player && S.view.player.inventory) || {};

function kv(k, v, cls) {
  const empty = isEmpty(v);
  return `<div class="kv"><span class="k">${esc(k)}</span>` +
    `<span class="v ${empty ? 'empty' : (cls || '')}">${empty ? '—' : esc(v)}</span></div>`;
}

function panelStatus() {
  const s = st(), p = S.view.player;
  return kv('修为', pick(s, '实力等级', '修为'), 'gold')
       + kv('身份', pick(s, '当前身份', '身份'))
       + kv('位置', p.location);
}

/**
 * 身体 —— 数值条**按字段存在与否自适应**。
 *
 * 「生命值体系」在世界配置里可以是 `数值` 或 `文本`：
 * 有数值就画条（更像游戏），没有就退回文字行（更像小说）。
 * UI 不猜、不造数，只渲染拿到的字段。
 */
function panelVitals() {
  const s = st();
  const hp = pick(s, '生命', '生命值', 'HP');
  const mp = pick(s, '灵力', '灵力值', 'MP');
  let h = '';

  if (typeof hp === 'number') h += bar('生命', hp, 100, 'hp');
  if (typeof mp === 'number') h += bar('灵力', mp, 100, 'mp');

  const phys = pick(s, '体力状态', '伤势');
  const mood = pick(s, '当前心情', '心情');
  if (!isEmpty(phys)) h += kv('体力', phys);
  if (!isEmpty(mood)) h += kv('心情', mood);

  if (!h) h = '<div class="v empty">（尚无身体状态记录）</div>';
  return h;
}

function bar(name, val, max, cls) {
  const pct = Math.max(0, Math.min(100, (val / max) * 100));
  return `<div class="bar">
    <div class="bar-top"><span class="bn">${esc(name)}</span><span class="bv">${val} / ${max}</span></div>
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
  </div>`;
}

function panelInventory() {
  const items = Object.entries(inv());
  if (!items.length) return '<div class="v empty">（空）</div>';
  return items.map(([k, v]) =>
    `<div class="inv-item"><div class="inv-name">${esc(k)}</div>` +
    (isEmpty(v) ? '' : `<div class="inv-desc">${esc(v)}</div>`) + '</div>'
  ).join('');
}

function panelPresent() {
  const list = (S.view && S.view.present_npcs) || [];
  if (!list.length) return '<div class="v empty">（周围无人）</div>';
  return list.map(n => `
    <div class="npc here" data-npc="${esc(n.name)}">
      <div class="npc-top">
        <span class="npc-name">${esc(n.short)}</span>
        ${n.involved ? '<span class="npc-star">★</span>' : ''}
      </div>
      <div class="npc-sub">${esc(n.level || '')}${n.level && n.identity ? ' · ' : ''}${esc(n.identity || '')}</div>
    </div>`).join('');
}

function panelAllNpcs() {
  if (!S.npcs.length) return '<div class="v empty">（未加载）</div>';
  const here = (S.view && S.view.player.location) || '';
  return S.npcs.map(n => {
    const at = n.location === here;
    return `<div class="npc ${at ? 'here' : ''} ${S.selNpc === n.name ? 'sel' : ''}"
                 data-npc="${esc(n.name)}" title="${esc(n.location)}">
      <div class="npc-top">
        <span class="npc-name">${esc(n.short)}</span>
        ${n.involved ? '<span class="npc-star">★</span>' : ''}
      </div>
      <div class="npc-sub">${esc(n.level || '')} · ${esc((n.location || '').split('·').slice(-1)[0] || '?')}</div>
    </div>`;
  }).join('');
}

function panelNpcDetail() {
  if (!S.selNpc) return '<div class="v empty">点上面任一 NPC 查看其内部表</div>';
  const d = S.npcDetail;
  if (!d || !d.found) return `<div class="v empty">找不到：${esc(S.selNpc)}</div>`;

  const t = d.tables || {};
  // 客观表（dict）在前，主观表（list）在后 —— 与信息域的分层一致
  const order = [
    '当前状态CurrentState', '持有物Inventory',
    '待解释事件PendingReasoning', '对玩家观察PlayerObservation',
    '观察Observation', '知识记忆Memory', '信念Belief',
    '性格Personality', '生活目标Goals', '好感度Relationship',
    '表更新成本UpdateCost',
  ];
  const keys = [...order.filter(k => k in t), ...Object.keys(t).filter(k => !order.includes(k))];

  let h = `<div class="panel-head" style="padding:0 0 6px">
      <span class="tag">玩家不可见</span>
      <span class="muted" style="font-size:11px">${esc(d.name)}</span></div>`;

  for (const k of keys) {
    const v = t[k];
    h += `<div style="margin:8px 0 2px"><span class="tag">${esc(k)}</span></div>`;
    if (Array.isArray(v)) {
      h += v.length
        ? v.map(x => `<div class="ctx-line">· ${esc(x)}</div>`).join('')
        : '<div class="v empty">（空）</div>';
    } else if (v && typeof v === 'object') {
      const es = Object.entries(v);
      h += es.length
        ? es.map(([a, b]) => `<div class="kv"><span class="k">${esc(a)}</span><span class="v">${esc(b)}</span></div>`).join('')
        : '<div class="v empty">（空）</div>';
    } else {
      h += `<div class="ctx-line">${esc(v)}</div>`;
    }
  }
  return h;
}

function panelCtx(which) {
  const c = S.ctxCache[which];
  if (!c) return '<div class="v empty">加载中…</div>';
  if (!c.lines.length) return '<div class="v empty">（空）</div>';
  return c.lines.slice().reverse().map(l => `<div class="ctx-line">${esc(l)}</div>`).join('');
}

function panelTiming() {
  if (!S.timing.length) return '<div class="v empty">（本轮还没跑）</div>';
  const total = S.timing.reduce((a, b) => a + b.seconds, 0);
  const worst = Math.max(...S.timing.map(x => x.seconds), 0.001);
  return S.timing.map(t => `
    <div class="bar">
      <div class="bar-top"><span class="bn">${esc(t.name)}</span>
        <span class="bv">${t.seconds.toFixed(1)}s</span></div>
      <div class="bar-track"><div class="bar-fill mp"
        style="width:${(t.seconds / worst) * 100}%"></div></div>
    </div>`).join('')
    + `<div class="kv" style="margin-top:6px"><span class="k">合计</span>
        <span class="v">${total.toFixed(1)}s</span></div>`;
}

// ══════════════════════════════════════════════════════════════
// 地点树
// ══════════════════════════════════════════════════════════════

function onPathSet() {
  const parts = (S.tree && S.tree.current_parts) || [];
  const set = new Set();
  let acc = '';
  for (const p of parts) { acc = acc ? acc + '·' + p : p; set.add(acc); }
  return set;
}

function renderTree(nodes, onPath, filter, depth) {
  let h = '';
  for (const n of nodes) {
    if (filter && !matchFilter(n, filter)) continue;

    const cur = n.path === (S.tree.current_path || '');
    const hasKids = n.children && n.children.length;
    const open = S.expanded.has(n.path) || (onPath.has(n.path) && hasKids);

    h += `<div class="tnode">`;
    // 描述放 title 里当悬停提示——全铺开会把树撑得没法看
    h += `<div class="trow ${cur ? 'cur' : ''}" data-path="${esc(n.path)}"
               data-leaf="${n.leaf}" title="${esc(n.desc || n.path)}">`;
    h += `<span class="tw" data-toggle="${esc(n.path)}">${hasKids ? (open ? '▾' : '▸') : (n.leaf ? '·' : '')}</span>`;
    h += `<span class="tname">${esc(n.name)}</span>`;
    if (n.leaf) h += `<span class="tleaf">地</span>`;
    h += `</div>`;

    // 只在**当前所在**的那一行把描述摊开——那一个才是你真要看的
    if (cur && n.desc) h += `<div class="tdesc">${esc(n.desc)}</div>`;

    // 同样只在当前那一行摊开地段属性。跟描述一个道理：
    // 全树都铺开的话，39 个节点每个挂四五个标签，树就没法看了。
    if (cur && n.attrs) {
      const chips = Object.entries(n.attrs)
        .filter(([k]) => !ATTR_HIDDEN.has(k) && !k.startsWith('_'))
        .map(([k, v]) => `<span class="tattr"><i>${esc(k)}</i>${esc(attrText(v))}</span>`)
        .join('');
      if (chips) h += `<div class="tattrs">${chips}</div>`;
    }

    if (open && hasKids) {
      h += `<div class="tkids">${renderTree(n.children, onPath, filter, depth + 1)}</div>`;
    }
    h += `</div>`;
  }
  return h;
}

function matchFilter(node, f) {
  if (node.name.includes(f) || (node.desc || '').includes(f)) return true;
  // 属性也要能被搜到——不然"哪里有坊市"这种问法就得靠肉眼翻整棵树
  if (node.attrs && Object.entries(node.attrs)
    .some(([k, v]) => !ATTR_HIDDEN.has(k) && (k.includes(f) || attrText(v).includes(f)))) {
    return true;
  }
  return (node.children || []).some(c => matchFilter(c, f));
}


// ── 小地图（阶段 1B）──────────────────────────────────────────────
//
// 旧的地点树面板在这里**退成降级路径**：地图有几何就画地图，
// 没有（或接口挂了）就退回折叠树——不然一个渲染组件就能把整个前端拖死。

/**
 * 一条路段的 SVG `d`：**3 个点走二次曲线**（前端把点列渲染成平滑线），
 * 其余按折线连。静态层、选中高亮、邻居高亮三处**同一份**——以前这三处各抄了一遍
 * 那个三元表达式，抄三份迟早漂开（改平滑算法时改一处忘两处）。
 */
function roadPathD(r) {
  const L = (r && r.line) || [];
  if (L.length < 2) return '';
  return L.length === 3
    ? `M${L[0][0]} ${L[0][1]} Q${L[1][0]} ${L[1][1]} ${L[2][0]} ${L[2][1]}`
    : L.map((q, i) => `${i ? 'L' : 'M'}${q[0]} ${q[1]}`).join(' ');
}

/**
 * 选中一件事物之后的**一跳邻居**——让图上的局部结构一眼看得见。
 *
 *   选中**节点** → 连它的所有**路段** ＋ 那些路段**另一头**的节点（**节点-线-节点**）
 *   选中**路段** → 它的两个**端点**节点（**线-节点**）
 *
 * 只认服务端 `map_view_of` 给的 `roads[].from/.to`：前端**不解析路名**——
 * `A-B` 那种拼法只是历史惯例（改名/新建之后不一定还拼得上），端点是数据、不是字符串。
 *
 * @return `{roads:Set<路名>, places:Set<地名>}`（都**不含**被选中的那一个本身）。
 */
function mapNeighbors(m, kind, name) {
  const roads = new Set(), places = new Set();
  if (!m || !m.ok || !name) return { roads, places };
  const R = m.roads || [];
  if (kind === 'road') {
    const r = R.find(x => x.name === name);
    if (r) {
      if (r.from) places.add(r.from);
      if (r.to) places.add(r.to);
    }
    return { roads, places };
  }
  for (const r of R) {
    if (r.from !== name && r.to !== name) continue;
    roads.add(r.name);
    const other = (r.from === name) ? r.to : r.from;
    if (other && other !== name) places.add(other);
  }
  return { roads, places };
}

/**
 * 这一张图上**当前选中的是什么**。没有选中（或选的东西在图上不存在）给 `null`。
 *
 * 编辑器看 `S.edit.sel`（`{tab, i}`，`i` 为 -1 ＝ 没选中），面板与浮层看 `S.mapSel`。
 * 判据是**名字**：`sel.i` 是列表下标，而列表可能正被筛选过 / 刚删过一条，
 * 下标和图上那块地对不上——名字是两边唯一共用的东西。
 */
function mapSelOf(box) {
  if (box.dataset.mapwhich === 'edit') {
    const e = S.edit;
    if (!e) return null;
    const s = e.sel || {};
    const it = (s.i >= 0) ? ((e.form || {})[s.tab] || [])[s.i] : null;
    const n = it && it.名;
    return n ? { kind: s.tab === '路' ? 'road' : 'place', name: n } : null;
  }
  return S.mapSel ? { kind: 'place', name: S.mapSel } : null;
}

/**
 * 选中项**自己** ＋ 它的一跳邻居。`mapNeighbors` 给的集合里不含被选中那个，
 * 所以这里补进去——"你选的这一个"当然要留在亮的那一边。
 */
function mapFocusOf(m, sel) {
  if (!sel) return null;
  const f = mapNeighbors(m, sel.kind, sel.name);
  (sel.kind === 'road' ? f.roads : f.places).add(sel.name);
  return f;
}

/**
 * 选中之后给静态层挂状态类，一步到位（**只改 class、不重建 DOM**——重建会丢
 * 拖动状态与命中）。三类：
 *
 *   `on`  —— 在焦点里（选中项 ＋ 它的一跳邻居）→ **不压暗**
 *   `sel` —— **就是选中的那一个**              → 它自己那条**边/线**染金
 *   `nb`  —— 一跳邻居                          → 它自己那条**边/线**染青
 *
 * ⚠️ 颜色一律**染节点/路自己**，**不另画一个圈、也不叠一条线**：
 *   · 节点：它的"盘"就是它的 `范围`，再套一个圈，`范围` 一大（星辰阁 102、天道宗 101）
 *     就跟自己的边重合，看着是同一圈描了两遍。地区的虚线圈同理——直接染那个圈。
 *   · 路：原来是在 `.mdyn` 里**另叠一条**（`.mconn-road` / `.ed-sel-road`），而
 *     `.mdyn` 整层在 `.mstatic` **之上**——于是高亮的路会盖住**文字**，
 *     与"线段在底、文字在最上"的涂层顺序自相矛盾。改成染线之后它留在线段层里，
 *     谁压谁就自动跟着涂层顺序走了。
 * 没选中时全部复原（`has-sel` 摘掉，三个类都清）。
 */
function applyMapFocus(svg, m, sel) {
  const f = mapFocusOf(m, sel);
  svg.classList.toggle('has-sel', !!f);
  const selName = f ? sel.name : '', selKind = f ? sel.kind : '';
  for (const el of svg.querySelectorAll('.mplace, .mreg, .mroad')) {
    const pn = el.dataset.place, rn = el.dataset.road;
    const inFocus = !!f && (pn ? f.places.has(pn) : f.roads.has(rn));
    el.classList.toggle('on', inFocus);
    // `places` 与 `roads` 是两套名字空间——同一个字符串理论上可能既当地名又当路名
    //（路名是 `A-B` 拼的），所以"这是不是选中项"要**连类型一起比**，不能只比名字。
    const isSel = inFocus && (pn ? selKind === 'place' : selKind === 'road')
                  && (pn || rn) === selName;
    el.classList.toggle('sel', isSel);
    el.classList.toggle('nb', inFocus && !isSel);
  }
}

/**
 * 一个节点对应的**名字元素**。名字在单独一层（`.lyr-label` > `.mlabel`），
 * 只能按 `data-place` 找——**不要**用属性选择器拼名字：地名里有 `·`、`-`、
 * 引号，拼进去就得转义（`CSS.escape` 是给标识符用的，在引号里反而不对）。
 */
function labelOf(nodeEl) {
  const svg = nodeEl.closest && nodeEl.closest('svg.minimap');
  const n = nodeEl.dataset && nodeEl.dataset.place;
  if (!svg || !n) return null;
  return [...svg.querySelectorAll('.mlabel')].find(t => t.dataset.place === n) || null;
}

/**
 * 静态层。分**四个子层**，拼装顺序就是**涂层顺序**（SVG 后画的在上面）：
 *
 *     线段（路 + 命中带）  →  节点（盘 + 热区）  →  文字（名字）
 *     地区（虚线圈）压在最底下（`pointer-events: none`，只画不接手势）
 *
 * ⚠️ 为什么要拆成四层、而不是像原来那样"一个地点一个 `<g>`（点+热区+名字）"：
 * 那样**谁在上面取决于地点在列表里的先后**——一个大地块的名字会被排在它后面的
 * 小地块的点压住，而"名字被别人的点盖住"正是"点 A 的名字却选不中"的来源。
 * 拆开之后：**所有文字 > 所有节点 > 所有线段**，与点击顺序完全一致。
 *
 * 代价：名字不再是节点的子元素，`applyMapLabels` 认标签要换选择器，
 * `.mplace:hover text` 够不着了（那条从 CSS 搬到 JS，见 hover 监听）。
 */
function buildMapStatic(m, o) {
  o = o || {};
  const lyrRegion = [], lyrRoad = [], lyrNode = [], lyrLabel = [];

  // ── 第 0 层：地区（描边虚线圆，由大到小）──
  // ⚠️ 画圈的判据是 `p.region`（作者写的 `类型`），**不是"有没有子节点"**：
  // 地点完全可以有子节点（天道宗有 7 个子地块，可它自己能停留）。
  for (const p of m.places.filter(p => p.region).sort((a, b) => b.r - a.r)) {
    // `data-place` 是给**置灰/染边**用的（`applyMapFocus` 要认出"这个圈属于谁"）：
    // 地区的圈没有别的身份标记，只有名字。
    // `pointer-events: none` 是原样保留的——加了属性不等于把它做成热区。
    lyrRegion.push(`<circle class="mreg" data-place="${esc(p.name)}" cx="${p.x}" cy="${p.y}" r="${p.r}"/>`);
  }

  // ── 第 1 层：线段 ──
  for (const r of m.roads) {
    const d = roadPathD(r);
    if (!d) continue;
    // `data-road` 同上：静态那条细线也得带自己的名字，置灰才分得清该留哪条。
    lyrRoad.push(`<path class="mroad" data-road="${esc(r.name)}" d="${d}"/>`);
    // 编辑器里再叠一条**透明粗线**当命中区：线本身只有 4 个画布单位宽，
    // 直接用鼠标点是点不中的——"点了线没反应"就是这么来的。
    // 只在编辑器里加（面板不需要，65 条线白加没意义）。
    if (o.roadHit) lyrRoad.push(`<path class="mroad-hit" data-road="${esc(r.name)}" d="${d}"/>`);
  }

  // ── 第 2 层：节点（**不含名字**）──
  // **地点**＝实心盘，盘的大小就是它的 `范围`；**地区**＝虚线圈 + **不画点**。
  // 于是"点了那个盘"和"那个地方有多大"是同一件事，不用在两个东西之间对照。
  for (const p of m.places) {
    const range = +p.r || 0;                    // `范围`（`map_view` 给的）
    const rr = p.region ? 0 : (range || 6);     // 地点没有 `范围` 时退回一个小点
    const dot = p.region ? ''
      : `<circle class="mdot" cx="${p.x}" cy="${p.y}" r="${rr}"><title>${esc(p.name)}</title></circle>`;
    // **地区没有"盘"**：它的热区只留**名字那一小块**（圈里不算它的）。
    // 为什么非这样：放大之后一个地区的圈能盖住整个视口——圈要是热区，在编辑器里
    // **拖哪儿都在拖它**，画板就再也拖不动了（用户实测报的）。而且"点圈里任意处"
    // 本来就该落到圈里最近的那个地点，不该是地区自己。所以**地区点字选中**。
    const pick = p.region ? 6 : (range || 6);
    lyrNode.push(`<g class="mplace${p.leaf ? ' leaf' : ''}${p.region ? ' region' : ''}"
      data-place="${esc(p.name)}" data-range="${range}" data-pick="${pick}">
      ${dot}
      <circle class="mhit" cx="${p.x}" cy="${p.y}" r="${pick}"/></g>`);
  }

  // ── 第 3 层：文字（最上面一层，**可以点**——点名字＝点那个地点）──
  for (const p of m.places) {
    // 名字的位置**不跟着盘走**：盘可以很大（天道宗 r=104），名字飘到一百多个单位外
    // 就看不出是谁的了。所以标签仍按"一个小点的高度"往上让，跟从前一样。
    const ly = p.y - (p.leaf ? 6 : 9) - 6;
    lyrLabel.push(`<text class="mlabel" data-place="${esc(p.name)}"
      x="${p.x}" y="${ly}">${esc(p.name)}</text>`);
  }

  return `<g class="lyr-region">${lyrRegion.join('')}</g>`
       + `<g class="lyr-road">${lyrRoad.join('')}</g>`
       + `<g class="lyr-node">${lyrNode.join('')}</g>`
       + `<g class="lyr-label">${lyrLabel.join('')}</g>`;
}

/**
 * 点地图上的地块：取**离指针最近的那一块**，不信"画在最上面的那一个"。
 *
 * 为什么不能用 `e.target.closest('[data-place]')`（**实测**，41 块里 7 块会错）：
 *
 *   1. `.mhit`（给手指用的透明命中圈）是**屏幕恒定**的，密集处两块地的圈
 *      会互相盖住，而 SVG 里**谁后画谁在上面**——点 A 的圆心，选中的却是旁边的 B。
 *      实测：`上古遗迹·破云墟` → 选中 `破云墟入口`；`冰封古城·霜落城` → `冰川下的洞府`；
 *      `中州` → `上古试炼秘境·万象洞天`。
 *   2. **📍 标记压在玩家所在地**上，它的 `<text>`/脉冲圈不在 `[data-place]` 里——
 *      `closest()` 直接返回 `null`，于是**点了没反应**。实测 4 块（落霞坪、登仙梯、
 *      膳食房、万象楼），拿到的元素都是 `<text>`。
 *   3. 邻居的**名字标签**也会盖住别人的点，同 1。
 *
 * 所以按"距离"判，不按"层叠"判。宽容半径见 `mapReachPx`：**跟着那个点自己的
 * 屏幕大小走**（+4px、下限 6px），不再恒定——恒定像素的字可以，恒定像素的热区
 * 会在缩小时抢邻居（天道宗院里 7 个点彼此只差 15px）。
 *
 * @return: `{name, d}`（最近的地块名 + **屏幕距离**）；不靠近任何地块给 `null`。
 *   带距离是为了与"离线的距离"比大小——**谁近选谁**（见调用处的注释）。
 */
function mapPickPlace(box, ev) {
  // `svg.minimap` 不能省：`.mapctl` 里那几个按钮（＋/－/⟲/⤢）也是 `<svg>`，
  // 用 `querySelector('svg')` 会抓到图标，于是坐标系整个错位（实测：一块都点不中）
  const svg = box && box.querySelector('svg.minimap');
  const m = box ? mapThreadOf(box) : null;
  if (!svg || !m || !m.ok || !m.places) return null;
  // ⚠️ 两级：`mapThreadOf(box)` 取"这一张图绑的是哪份数据"，
  // `mapViewOf(m)` 才是**归一化过的视图框**（`{x,y,w,h}`，来自 `m.bounds`）。
  // 少一级就会把 `box` 当成数据（它没有 `.bounds`）→ 退到默认 100×100 的框 →
  // 算出来的世界坐标全错（实测：42 块一块都点不中）。
  const v = mapViewOf(m);
  const { s, offX, offY, rect } = mapScale(svg, v);
  if (!s) return null;
  // ⚠️ 别忘了加**视图原点** `v.x/v.y`：`viewBox` 是 `x y w h`，地图被拖过之后
  // 它的原点是负的（实测当前这一张是 `-744,-1295`）。漏掉它算出来就是
  // "世界坐标永远在右上角"，一块都点不中。
  const wx = v.x + (ev.clientX - rect.left - offX) / s;
  const wy = v.y + (ev.clientY - rect.top - offY) / s;
  let best = null, bestD = 0, bestDot = 0;
  for (const p of m.places) {
    // 那个点在屏幕上有多大：地点＝它的**盘**（＝`范围`），地区＝**名字那一小块**
    // （与 `buildMapStatic` 的 `data-pick`、`applyMapView` 的热区是同一个规则）
    const dot = (p.region ? 6 : (+p.r || 6)) * s;
    const d = Math.hypot(p.x - wx, p.y - wy) * s;          // 换算成屏幕像素再比
    const reach = mapReachPx(dot);
    if (d <= reach && (best === null || d < bestD)) { best = p.name; bestD = d; bestDot = dot; }
  }
  return best === null ? null : { name: best, d: bestD, dot: bestDot };
}

/**
 * 指针**到每条线的最短屏幕距离** → 最近的那条路（超过抓取半径给 `null`）。
 *
 * 为什么要它：`.mroad-hit` 是透明的粗命中带，但它**画在地块点下面**——
 * 一块地的热区盖住线的一截时，`elementFromPoint` 拿到的是**地块**，
 * 于是"点线上的那一截"选中了旁边那块地（实测：点 总舵-万象洞天 曲线的中点 → 选中 `中州`）。
 * 所以线也要按**距离**算：路从地块旁边经过时，指针离线 0px、离地块中心 12px，**线该赢**。
 *
 * ⚠️ **端点不算在"线"里**：一条路的两个端点**就是那两块地**（`从` / `到`），
 * 指针压在那儿时该点的是**地**、不是线（"线段只能点线，不能点端点"）。
 * 让开量 = `min(那一端那块地的热区, MAP_ENDPOINT_PX)`——跟着地的实际大小走，但封顶。
 *
 * ⚠️ **不做 bbox 粗筛**：`.mroad-hit` 在 svg 里、svg 又被 viewBox 平移缩放，
 * 路径的 `getBoundingClientRect()` 在这种情况下不可靠（实测：筛完一条都进不来，
 * 点线上任何位置都选不中——变成了"点了没反应"）。
 *
 * @return: `{name, d}`
 */
function mapPickRoad(box, ev) {
  const svg = box && box.querySelector('svg.minimap');
  if (!svg) return null;
  const m = mapThreadOf(box);
  if (!m || !m.ok) return null;
  const placeByName = new Map((m.places || []).map(p => [p.name, p]));
  const roadByName = new Map((m.roads || []).map(r => [r.name, r]));
  const px = ev.clientX, py = ev.clientY;
  let best = null, bd = MAP_ROADHIT_PX;
  // 采样密度：17 个点（原来）在长曲线上误差能到 6px 上下，而 `MAP_ONLINE_PX` 就是 6——
  // 于是"明明点在线上，却判成不在线上"，线的选中/不选中全看运气。33 个点把这个误差
  // 压到 1~2px（一次点击几毫秒，不值得为省这点而让判定发抖）。
  const N = 32;
  for (const path of svg.querySelectorAll('.mroad-hit')) {
    const ctm = path.getScreenCTM();
    const len = path.getTotalLength();
    if (!len) continue;
    // `k` = 这条路径"画布单位 → 屏幕像素"的比例，两个用途：把采样点换算到屏幕坐标、
    // 以及把下面按像素定的让开量折回路径自己的长度单位。
    const k = Math.hypot(ctm.a, ctm.b) || 1;
    // **两头各让开一截**——端点就是那两块地，指针压在那儿时该点的是**地**、不是线
    // （"线段只能点线，不能点端点"）。一端让多少 = **那一端那块地自己的热区半径**
    //   · 不封顶地按"整块热区"让：盘大的地（星辰阁 r=102）会把落在它盘里的**整条**路
    //     都吞掉，那条路就再也点不动了（实测）
    //   · 一点不让：缩得小时点旁边的小点既够不着地（热区才 6px）又不算线，
    //     变成"点了没反应"的空档
    // 所以取 `min(热区, MAP_ENDPOINT_PX)`：跟着地的实际大小走，但封顶。
    const trim = (nm) => {
      const p = placeByName.get(nm);
      if (!p) return 0;
      const dot = (p.region ? 6 : (+p.r || 6)) * k;
      return Math.min(mapReachPx(dot), MAP_ENDPOINT_PX) / k;
    };
    const road = roadByName.get(path.dataset.road) || {};
    const cap = Math.floor(N / 2);
    const i0 = Math.min(cap, Math.ceil(trim(road.from) / len * N));
    const i1 = N - Math.min(cap, Math.ceil(trim(road.to) / len * N));
    for (let i = i0; i <= i1; i++) {
      const q = path.getPointAtLength(len * i / N);
      const d = Math.hypot(ctm.a * q.x + ctm.c * q.y + ctm.e - px,
                           ctm.b * q.x + ctm.d * q.y + ctm.f - py);
      if (d < bd) { bd = d; best = path.dataset.road; }
    }
  }
  return best ? { name: best, d: bd } : null;
}

function mapMarkerSvg(m) {
  const c = m.current || {};
  if (!c.xy) return '';
  const [x, y] = c.xy;
  // 在路上就写**语义描述**（后端算好的，前端不解释坐标）。
  // 别再拼路名——`desc` 里已经写了"A—B 之间的路上（半路）"，拼上去是重复。
  const label = c.on_road ? `📍${c.desc || (c.road || '在路上')}`
                          : `📍${c.name}`;
  return `<g class="mme">
    <circle class="mpulse" cx="${x}" cy="${y}" r="16"/>
    <circle cx="${x}" cy="${y}" r="6"/>
    <text x="${x}" y="${y + 22}">${esc(label)}</text>
  </g>`;
}

// ── 地图组件（三处共用：右栏面板 / 它的全屏浮层 / 编辑器的左图）─────────
//
// 共用的是：`buildMapStatic` 的静态层、`mapMarkerSvg` 的动态层、
// 以及**同一个** `S.mapView`——缩放平移是"同一张图"的操作，三处联动。
//
// 为什么 viewBox 单独拎出来改：缩放每帧都要动它，而**重建 DOM 会丢拖动状态
// 和命中**。所以这里只改属性 + 换 class，不碰 innerHTML。

//: 标签字号与光晕宽度，单位是**屏幕像素**——不是画布单位。
//:
//: ⚠️ 这一点搞错过一次：`font-size` 写成画布用户单位（34）时，缩放会把**文字和几何
//: 一起**放大，于是标签间距与字宽同比变化 —— **重叠跟缩放无关，放大再多次也不分开**。
//: 而且同一个 34 在面板里只有 8.8px（太小）、在编辑器里 29.5px（太大），两头都不对。
//: 改成"屏幕恒定像素"之后：放大 = 几何拉开、字不变 → 标签自然分开。
const MAP_FONT_PX = 11.5;
const MAP_HALO_PX = 3.0;
//: 「点地块」的热区半径＝**那个点自己的屏幕半径 + 这么多像素**（见 `mapReachPx`）。
//:
//: 以前这里是**屏幕恒定 15/16px**。恒定像素的只有**字**——字必须恒定，不然"放大才
//: 看得清"这件事不成立。热区恒定是错的：未缩放时天道宗院里那 7 个点彼此只差
//: **15 个屏幕像素**，而热区也是 15px——范围比间距还大，点 A 抢 B，谁都分不开
//: （"未缩放时点的范围过大"就是这么来的）。跟着点走之后：点小、热区就小，
//: 放大到点有 20px 时热区 24px，边缘照样点得中。
const MAP_DOT_PAD_PX = 4.0;
//: 热区下限（屏幕像素）。再小就真的点不中了——这是"能点"与"别抢邻居"的折中。
const MAP_PICK_MIN_PX = 6.0;
//: 路径线控制点的半径，也是**屏幕像素**。
//: 写成画布单位（曾经的 `r="8"`）时，缩到 0.53 就只剩 4 屏幕像素——**根本点不中**，
//: 表现就是"线拖不动"。
const MAP_CTRL_PX = 7.0;
//: 路的命中带宽度（屏幕像素）——线本身细得点不中，靠这条透明带抓
const MAP_ROADHIT_PX = 13.0;
/**
 * **前端构建号**（调试期专用）。改了前端以后，"我重开了服务/重开了页面"
 * 都**不一定**等于"页面上跑的是新代码"——服务重启只让 SSE 重连，页面重开可能吃缓存。
 * 于是"改了没生效"会被误当成"没修好"。这个号显示在顶栏，一眼就能对齐事实：
 * 顶栏那个号 ≠ 盘上这个号 → 页面上是旧代码，硬刷新（Ctrl+Shift+R）。
 */
const BUILD = '1010-1628';
function showBuild() {
  const el = document.getElementById('hBuild');
  if (el) el.textContent = 'build ' + BUILD;
}
try {
  console.log('gushi4 前端 build', BUILD);
  showBuild();                                   // 这一行脚本在 DOM 之后加载
  document.addEventListener('DOMContentLoaded', showBuild);
} catch (e) { /* 构建号只是调试用，坏了不能影响游戏 */ }

/** 「点地块」的宽容半径（屏幕像素）：跟着点的大小走，外加一点点余量。
 *  `dotPx` 是那个点在屏幕上的半径。DOM 那层（`.mhit`，见 `applyMapView`）与
 *  按距离判的这层（`mapPickPlace`）**必须用同一个公式**，不然"看着能点、
 *  实际点到的是另一块"。 */
function mapReachPx(dotPx) {
  return Math.max(MAP_PICK_MIN_PX, dotPx + MAP_DOT_PAD_PX);
}
const MAP_ONLINE_PX = 6.0;    // 指针离一条线多近才算"点在线上"（屏幕像素）
const MAP_DOT_STICKY_PX = 3.0; // 指针离某个地块的**点**这么近时，一律算点它（线抢不走）
//: 路的**两头各让开这么长一截**不算"线"（屏幕像素）：端点就是那两块地，
//: 指针压在那儿时该点的是地（"线段只能点线，不能点端点"）。
//: 取 14 ≈ 命中带的宽度（`MAP_ROADHIT_PX`），正好把带子的两个端头削掉。
const MAP_ENDPOINT_PX = 14.0;

S.mapView = null;             // {x,y,w,h}；null = 用 m.bounds
S.mapSel = null;              // 被选中的地块名（点＝选中，再点「前往」才出发）
S.mapDragged = false;         // 刚刚拖过图 → 抑制这一次的 click
S.railR = null;               // 右栏宽度（可拖、存 localStorage）

function mapViewOf(m) {
  const b = (m && m.bounds) || [0, 0, 100, 100];
  const v = S.mapView;
  return v ? v : { x: b[0], y: b[1], w: b[2], h: b[3] };
}

/**
 * 逐个决定**哪些名字显示**——地图标签的常规做法：一个标签只在
 * 「不跟已经放下的标签相交、且落在视野里」时才显示。
 *
 * 为什么不是"全显 / 只显区域名"两档：那太粗——实测要缩到第 14 档才跳档，
 * 中间十几档全是"区域名 + 一片空白"。逐个摆放之后，**放大 = 名字一个个冒出来**，
 * 每一步都有进展。
 *
 * 优先级（决定谁抢到位置）：当前所在地 · 选中项 → 区域名（大区）→ 其余地块。
 * 于是"缩着看格局、放大看细节"，而且玩家自己站的地方永远看得见。
 */
function applyMapLabels(svg, m, v) {
  const { w, h, s, offX, offY } = mapScale(svg, v);
  if (!w || !h || !s) return;
  const places = (m && m.places) || [];
  const cur = (m && m.current && m.current.name) || '';

  // 名字现在是**单独一层**（`.lyr-label` > `.mlabel`），不再是节点的子元素——
  // 所以直接按 `.mlabel` 找；键仍是 `data-place`（与节点、地区共用同一个名字空间）。
  const els = new Map();
  for (const t of svg.querySelectorAll('.mlabel')) {
    if (t.dataset.place) els.set(t.dataset.place, t);
  }

  // 文字框按**屏幕**算（字号恒定），宽按 CJK ≈ 1em/字 估。
  // ⚠️ 位置必须走 `mapScale` 出的**同一个 s**：`preserveAspectRatio="meet"` 是等比缩放
  // 加信箱留白，x/y 各算各的 scale 会让标签在一个**被拉扁的坐标系**里互相避让
  // （全屏浮层高度写死时就会这样，实测：1145px 宽反而只放下 10 个名字）。
  const boxOf = (p) => {
    const halfW = (String(p.name || '').length * MAP_FONT_PX) / 2;
    const px = offX + (p.x - v.x) * s;
    const py = offY + (p.y - v.y) * s - (p.leaf ? 6 : 9) * s - MAP_FONT_PX;
    return [px - halfW, py - MAP_FONT_PX, px + halfW, py + MAP_FONT_PX * 0.4];
  };
  const hits = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

  const placed = [];
  // 📍 标记的字最要紧：先占位，别的名字绕开它
  const mk = (m && m.current) || {};
  if (mk.xy) {
    const halfW = (String(mk.name || '').length * MAP_FONT_PX) / 2;
    const px = offX + (mk.xy[0] - v.x) * s;
    const py = offY + (mk.xy[1] - v.y) * s + 22 * s;
    placed.push([px - halfW, py, px + halfW, py + MAP_FONT_PX * 1.4]);
  }

  // 优先级不是固定的：**缩着看格局，放大看细节**。
  // 缩着时区域名（东域/天道宗）该赢，放大时叶子名（落霞坪/膳食房）该赢——
  // 不然缩进天道宗那一片时，"天道宗"三个字会把它的 7 个子地块全挤掉。
  const zoomedIn = v.w < ((m.bounds ? m.bounds[2] : v.w) * 0.35);
  const rank = (p) => {
    if (p.name === cur || p.name === S.mapSel) return 0;   // 当前/选中永远看得见
    return (!!p.leaf === zoomedIn) ? 1 : 2;
  };
  const sorted = [...places].sort((a, b) =>
    rank(a) - rank(b) || (b.r || 0) - (a.r || 0));

  for (const p of sorted) {
    const t = els.get(p.name);
    if (!t) continue;
    const b = boxOf(p);
    const off = b[0] > w || b[2] < 0 || b[1] > h || b[3] < 0;
    if (!off && !placed.some(q => hits(b, q))) {
      t.style.display = '';
      placed.push(b);
    } else {
      t.style.display = 'none';      // 悬停时 CSS 用 !important 放它出来
    }
  }
}

/** 这一张图绑的是哪份数据。面板/浮层用 `S.map`，编辑器用 `S.edit.view`。 */
function mapThreadOf(box) {
  return box.dataset.mapwhich === 'edit' ? (S.edit && S.edit.view) : S.map;
}

/**
 * **唯一**一处把画布坐标换成屏幕像素的地方。
 *
 * `preserveAspectRatio="xMidYMid meet"` = 等比缩放 + 居中留白，所以只能有一个 `s`，
 * 而且必须把留白算进去。分开算 x/y 会在浮层（宽高比与 viewBox 不同）里得到一个
 * 被拉扁的坐标系——标签会"照着错的间距避让"（实测踩过）。
 */
function mapScale(svg, v) {
  const w = svg.clientWidth || 0, h = svg.clientHeight || 0;
  const s = Math.min(w / v.w, h / v.h) || 0;
  return { w, h, s, offX: (w - v.w * s) / 2, offY: (h - v.h * s) / 2,
           rect: svg.getBoundingClientRect() };
}

/**
 * 把 `S.mapView` 应用到页面上所有地图实例。只改属性，**不重建 DOM**。
 *
 * 同时把"屏幕像素 → 画布单位"的值挂成 CSS 变量（字号、光晕、热区），
 * 于是标签在任何缩放下都是**恒定像素大小**——这是"放大能把标签拉开"的前提。
 */
function applyMapView() {
  for (const box of document.querySelectorAll('[data-mapbox]')) {
    const svg = box.querySelector('svg.minimap');
    if (!svg) continue;
    const m = mapThreadOf(box);
    const v = mapViewOf(m);
    svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`);
    const { s } = mapScale(svg, v);
    if (!s) continue;
    svg.style.setProperty('--mlabel', (MAP_FONT_PX / s) + 'px');
    svg.style.setProperty('--mhalo', (MAP_HALO_PX / s) + 'px');
    // 热区**逐个节点**算（不能一刀切成一个屏幕定值）：半径 = 那个节点自己的屏幕半径
    // + 4px、下限 6px，与 `mapPickPlace` 用同一个 `mapReachPx`。
    // 那个半径是 `data-pick`：地点＝它的**盘**（＝`范围`），地区＝**名字那一小块**
    // （圈不算——放大后圈能盖住整个视口，算热区就再也拖不动画板了）。
    for (const g of svg.querySelectorAll('.mplace')) {
      const c = g.querySelector('.mhit');
      if (!c) continue;
      const rr = +g.dataset.pick || 6;
      c.setAttribute('r', (mapReachPx(rr * s) / s).toFixed(1));
    }
    // 控制点也按屏幕像素给大小——不然缩远了它只有几个像素，点不中
    const ctrl = MAP_CTRL_PX / s;
    for (const c of svg.querySelectorAll('.ectrl')) c.setAttribute('r', ctrl.toFixed(1));
    // 路的命中区也按屏幕像素给粗细（它只是个透明的抓取带）
    for (const c of svg.querySelectorAll('.mroad-hit')) {
      c.style.strokeWidth = (MAP_ROADHIT_PX / s).toFixed(2);
    }
    applyMapLabels(svg, m, v);
    // 焦点状态（压暗 + 染边）排在标签之后：两件事互不影响，但这一处是
    // **每张图都会经过**的地方（重建静态层、缩放、平移、换选中都要走 `applyMapView`），
    // 放在这里就不用让每个调用点自己记得刷一次。
    applyMapFocus(svg, m, mapSelOf(box));
  }
}

/** 以某个屏幕点为锚点缩放。锚点下的世界坐标保持不动。 */
function mapZoomAt(svg, m, clientX, clientY, factor) {
  const v = mapViewOf(m);
  const { rect, s, offX, offY } = mapScale(svg, v);
  const wx = v.x + (clientX - rect.left - offX) / s;
  const wy = v.y + (clientY - rect.top - offY) / s;
  const b = m.bounds;
  // 缩放下限：原来卡在 `bounds.w / 14`，而天道宗那一片 7 个地块彼此只差几十个
  // 画布单位——**根本缩不到能把它们分开的程度**。放到 /80 并给一个绝对的 18 单位下限。
  const nw = Math.max(Math.min(b[2] / 80, 18), Math.min(b[2], v.w * factor));
  const k = nw / v.w;
  const nh = v.h * k;
  S.mapView = { x: wx - (wx - v.x) * k, y: wy - (wy - v.y) * k, w: nw, h: nh };
  applyMapView();
}

function mapResetView() { S.mapView = null; applyMapView(); }

/** 拖出太远就没方向感了：限制视图中心不许离开 bounds 的 1.5 倍范围。 */
function mapClamp(v, b) {
  const cx = Math.max(b[0] - v.w / 2, Math.min(b[0] + b[2] + v.w / 2, v.x + v.w / 2));
  const cy = Math.max(b[1] - v.h / 2, Math.min(b[1] + b[3] + v.h / 2, v.y + v.h / 2));
  return { x: cx - v.w / 2, y: cy - v.h / 2, w: v.w, h: v.h };
}

/**
 * 地图块的 HTML。
 *
 * `thread` 是"这一处地图绑定的数据"（面板/浮层用 `S.map`，编辑器用 `S.edit.view`），
 * 挂在 DOM 上供 `applyMapView` 取用——因为同一时刻页面上可能有 2~3 张图。
 */
function mapBoxHTML(m, o) {
  o = o || {};
  const v = mapViewOf(m);
  const dfltLegend = `${(m.places || []).length} 地块 / ${(m.roads || []).length} 路`;
  return `<div class="mapbox" data-mapbox data-mapwhich="${o.which || 'map'}">
    <div class="maptools">
      <button class="mtool" data-mapzoom="in" title="放大">＋</button>
      <button class="mtool" data-mapzoom="out" title="缩小">－</button>
      <button class="mtool" data-mapzoom="reset" title="复位">⟲</button>
      ${o.fullscreen === false ? '' : '<button class="mtool" data-mapfull title="全屏看图">⤢</button>'}
      ${o.editor === false ? '' : '<button class="mtool" data-ed="open" title="打开世界编辑器">✎</button>'}
    </div>
    <svg class="minimap" viewBox="${v.x} ${v.y} ${v.w} ${v.h}"
         preserveAspectRatio="xMidYMid meet">
      <g class="mstatic">${o.static != null ? o.static : S.mapStatic}</g>
      <g class="mdyn">${o.dyn != null ? o.dyn : mapMarkerSvg(m)}</g>
    </svg>
    <div class="maplegend">${o.legend || dfltLegend}</div>
  </div>`;
}

function panelMap() {
  const m = S.map;
  if (!m || !m.ok) return panelTreeFallback(m);
  let h = mapBoxHTML(m, {
    dyn: mapMarkerSvg(m),
    legend: '点地块＝选中　·　滚轮缩放 / 拖动平移',
  });
  h += `<div data-mapcard>${placeCardHTML(m, S.mapSel)}</div>`;
  return h;
}

/**
 * 地块属性卡。`name` 给了就显示那一块（选中的），否则显示当前所在。
 * 选中的那块多一个「前往」——**这是唯一的出发动作**（点地图只是选中）。
 */
function placeCardHTML(m, name) {
  const isSel = !!name;
  const p = (m.places || []).find(x => x.name === (name || (m.current || {}).name));
  if (!p) return '';
  let h = `<div class="kv"><span class="k">${isSel ? '选中' : '当前'}</span>` +
          `<span class="v jade">${esc(p.name)}</span></div>`;
  if (p.desc) h += `<div class="tdesc">${esc(p.desc)}</div>`;
  // **地点还是地区**永远显示（地区是"面"：不画点、不当路段端点）。
  // 一个地点的属性里没有 `类型`，不是"丢了"，是它本来就缺省＝地点——
  // 所以这一格由 `region` + 属性的 `类型` 一起合成，而不是等属性里那条。
  const ty = (p.attrs || {})['类型'];
  const chips = [`<span class="tattr"><i>类型</i>${
      p.region ? '地区' + (ty ? '·' + esc(attrText(ty)) : '') : '地点'}</span>`]
    .concat(Object.entries(p.attrs || {}).filter(([k]) => k !== '类型')
      .map(([k, v]) => `<span class="tattr"><i>${esc(k)}</i>${esc(attrText(v))}</span>`))
    .join('');
  if (chips) h += `<div class="tattrs">${chips}</div>`;
  if (isSel && p.name !== (m.current || {}).name) {
    h += `<button class="btn btn-sm go" data-goplace="${esc(p.name)}">前往 ${esc(p.name)}</button>`;
  }
  return h;
}

// ── 全屏看图：复用早就死在那儿的 .modal 样式（style.css §modal 家族）──

function openMapModal() {
  closeMapModal();
  if (!S.map || !S.map.ok) return;
  const el = document.createElement('div');
  el.id = 'mapModal';
  el.className = 'modal';
  el.innerHTML = `<div class="modal-box mapmodal-box">
      <div class="modal-head"><span>世界地图</span>
        <span class="spacer"></span>
        <button class="btn btn-sm" data-mapclose>关闭</button></div>
      <div class="modal-body mapmodal-body">
        ${mapBoxHTML(S.map, { fullscreen: false, dyn: mapMarkerSvg(S.map),
                              legend: '点地块＝选中　·　滚轮缩放 / 拖动平移' })}
      </div>
    </div>`;
  document.body.appendChild(el);
  applyMapView();
}

function closeMapModal() {
  const el = $('mapModal');
  if (el) el.remove();
}

// ── 存档弹窗（存档 / 读档 / 快速存取 / 删除）──────────────────────
//
// 为什么不是一个动作一个按钮：存档要**起名字**、读档要**挑哪一个**，两件事都得
// 先看见一张列表。一个弹窗把"存 / 读 / 删 / 快速存读 + 列表"装在一起，比顶栏上
// 排四个图标按钮少一半点击，也不会把顶栏挤爆。
//
// 动作**一律走 `/api/command`**（引擎自己那套 `/save` `/load` …），前端**不重写
// 一遍存取逻辑**：存档格式、版本迁移、"后台还在写盘就先等它"都在 Python 那边。
// 前端只负责"把名字/那条存档拼成命令"和"把结果刷新出来"。

S.saves = null;

/** 默认存档名：带时间戳，省得每次现想。同一天存多次不会互相盖掉。 */
function defaultSaveName() {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return `存档${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

function closeSavesDialog() {
  const el = $('savesModal');
  if (el) el.remove();
}

async function openSavesDialog() {
  closeSavesDialog();
  const el = document.createElement('div');
  el.id = 'savesModal';
  el.className = 'modal';
  el.innerHTML = `<div class="modal-box saves-box">
      <div class="modal-head"><span>存档</span>
        <span class="spacer"></span>
        <button class="btn btn-sm" data-savesclose>关闭</button></div>
      <div class="modal-body saves-body" data-savesbody>载入中…</div>
    </div>`;
  document.body.appendChild(el);
  await refreshSaves();
}

/** 重拉列表并重画。**每次动作后都要重画**——新建/删除会改变列表本身。 */
async function refreshSaves() {
  const body = document.querySelector('[data-savesbody]');
  if (!body) return;
  // 重画会把输入框整个换掉，先把用户已经敲进去的名字捞出来
  const keep = (document.querySelector('[data-savenameinput]') || {}).value;
  try {
    S.saves = (await api('/api/saves')).saves || [];
  } catch (e) {
    body.innerHTML = `<div class="v empty">读不到存档列表：${esc(e.message)}</div>`;
    return;
  }
  const rows = S.saves.map(s => {
    const m = s.meta || {};
    const meta = [s.mtime, m.player_name, m.location,
                  m.round_count != null ? `第 ${m.round_count} 轮` : null]
      .filter(Boolean).map(esc).join(' · ');
    return `<div class="srow">
      <div class="sinfo"><b>${esc(s.name)}</b><span class="smeta">${meta}</span></div>
      <button class="btn btn-sm" data-savedo="load" data-savename="${esc(s.name)}">读取</button>
      <button class="btn btn-sm" data-savedo="del" data-savename="${esc(s.name)}">删除</button>
    </div>`;
  }).join('') || '<div class="v empty">还没有存档。起个名字点「保存」。</div>';
  body.innerHTML = `
    <div class="snew">
      <input class="sname" data-savenameinput placeholder="存档名…"
             value="${esc(keep || defaultSaveName())}">
      <button class="btn btn-sm btn-send" data-savedo="save">保存</button>
      <button class="btn btn-sm" data-savedo="qsave">快速存</button>
      <button class="btn btn-sm" data-savedo="qload">快速读</button>
    </div>
    <div class="slist">${rows}</div>`;
}

/**
 * 弹窗里的一个动作 → 一条引擎命令。
 *
 * 读档 / 开新局会把状态**整个换掉**，所以完事要 `refreshState()` +
 * `loadHistory()`：叙事流得从 `global_context` 重建，两侧面板也得重拉。
 * 不等那 15 秒的兜底轮询——那时候人已经以为"点了没反应"了。
 */
async function saveAction(how, name) {
  let cmd = '';
  if (how === 'save') {
    const nm = String(name || '').trim();
    if (!nm) { pushStream('存档要一个名字。', 'e-dim'); return; }
    cmd = `/save ${nm}`;
  } else if (how === 'load') { cmd = `/load ${name}`; }
  else if (how === 'del') {
    if (!confirm(`删掉存档「${name}」？删了就找不回来了。`)) return;
    cmd = `/save del ${name}`;
  } else if (how === 'qsave') { cmd = '/quick_save'; }
  else if (how === 'qload') { cmd = '/quick_load'; }
  if (!cmd) return;
  try {
    await post('/api/command', { cmd });
  } catch (e) {
    pushStream('命令失败：' + e.message, 'e-err');
  }
  // `/save` 是**后台线程写盘**的（立刻返回），刚存完就列一遍会**看不到刚存的那个**
  // ——界面上就是"点了保存没反应"。所以存完先等它落盘再拉列表（`/load` 也在做同一件事）。
  if (how === 'save' || how === 'qsave') {
    try { await post('/api/saves/wait'); } catch (e) { /* 等不到就不等，列表照拉 */ }
    await refreshSaves();
    return;
  }
  await Promise.all([refreshState(), loadHistory()]);
  await refreshSaves();
}

document.addEventListener('click', e => {
  const t = e.target;
  // 点背景或「关闭」＝关掉
  if (t.closest('[data-savesclose]') || t.id === 'savesModal') { closeSavesDialog(); return; }
  const b = t.closest('[data-savedo]');
  if (!b) return;
  // 存/读/删那条存档的名字在按钮上；「保存」用的是输入框里的名字
  const inp = document.querySelector('[data-savenameinput]');
  saveAction(b.dataset.savedo, b.dataset.savename || (inp && inp.value));
});

// 降级：地图不可用时退回折叠树（旧面板原样保留，但**不再是主路径**）
function panelTreeFallback(m) {
  const why = (m && m.reason) ? m.reason : '地图数据加载中…';
  if (!S.tree) return `<div class="v empty">${esc(why)}</div>`;
  const onPath = onPathSet();
  const cur = S.tree.current_path || '';
  let h = `<div class="v empty">地图不可用：${esc(why)}（下面是旧的地点树）</div>`;
  if (!S.tree.in_tree && cur) {
    h += `<div class="kv"><span class="k">当前</span><span class="v jade">${esc(cur)}</span></div>
          <div class="v empty" style="margin-bottom:6px">（自由地点，不在预设树中）</div>`;
  }
  h += `<div class="tree">${renderTree(S.tree.tree, onPath, null, 0)}</div>`;
  return h;
}

function panelHTML(p) {
  const collapsed = S.collapsedPanels.has(p.id);
  return `<div class="panel ${collapsed ? 'collapsed' : ''}" data-panel="${p.id}">
    <div class="panel-head">
      <span class="exp" data-collapse="${p.id}">${collapsed ? '▸' : '▾'}</span>
      <span>${esc(p.title)}</span>
    </div>
    <div class="panel-body">${p.render()}</div>
  </div>`;
}

function renderRails() {
  const left = PANELS.filter(p => p.zone === 'left' && S.visible.has(p.id));
  const right = PANELS.filter(p => p.zone === 'right' && S.visible.has(p.id));

  $('railLeft').innerHTML = left.map(panelHTML).join('');
  // 拖拽条会被 innerHTML 冲掉，所以每次重画都带上它
  $('railRight').innerHTML =
    '<div id="railGrip" title="拖动调整右栏宽度（双击复位）"></div>'
    + right.map(panelHTML).join('');
  applyMapView();
}

function renderPanelsOnly() {
  for (const p of PANELS) {
    if (!S.visible.has(p.id)) continue;
    const el = document.querySelector(`[data-panel="${p.id}"] .panel-body`);
    if (el) el.innerHTML = p.render();
  }
  // 面板是**重建**的（innerHTML），所以缩放档位得重新贴上去。
  // 放在这里而不是每个调用点：`loadMap` / `loadTree` / `loadNpcs` 都在
  // 同一个 `Promise.all` 里，谁最后跑完谁的重画就生效——漏一个就丢档位。
  applyMapView();
}

function renderTopbar() {
  if (!S.view) return;
  const v = S.view;
  $('hName').textContent = v.player.name;
  $('hLoc').textContent = v.player.location || '未知';
  $('hClock').textContent = v.clock.text;
  $('hRound').textContent = `第 ${v.round} 轮`;
  $('btnUndo').disabled = !v.can_undo;
}

function renderAll() {
  renderTopbar();
  renderRails();
}

// ══════════════════════════════════════════════════════════════
// 叙事流
// ══════════════════════════════════════════════════════════════

function pushStream(html, cls) {
  const box = $('stream');
  const empty = box.querySelector('.stream-empty');
  if (empty) empty.remove();

  const d = document.createElement('div');
  d.className = 'entry ' + cls;
  d.innerHTML = html;
  box.appendChild(d);

  while (box.children.length > MAX_STREAM) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function evToStream(ev) {
  switch (ev.type) {
    case 'round_start':
      // `who` 只在从历史重建时才有（SSE 事件里靠当前玩家名）
      pushStream(`<span class="who">${esc(ev.who || (S.view ? S.view.player.name : '你'))}：</span>${esc(ev.input)}`,
        'e-player');
      break;
    case 'scene':
      pushStream(
        `<div class="loc">📍 ${esc(ev.location)}</div>` +
        `<div class="muted">👥 ${ev.present && ev.present.length
          ? '在场：' + esc(ev.present.join('、')) : '（周围无人）'}　⏰ ${esc(ev.clock)}</div>`,
        'e-scene');
      break;
    case 'dialogue':
      pushStream(`<span class="who">${esc(ev.speaker)}：</span>${esc(ev.text)}`, 'e-dialog');
      break;
    case 'narration':
      pushStream(esc(ev.text), 'e-narr');
      break;
    case 'dim':
      pushStream(esc(ev.text), 'e-dim');
      break;
    case 'time':
      pushStream(`游戏时间 ${esc(ev.text)}　+${Math.round(ev.minutes)} 分`, 'e-time');
      break;
    case 'info':
      pushStream(esc(ev.text), 'e-info');
      break;
    case 'error':
      pushStream(esc(ev.text), 'e-err');
      break;
    // stage / round_start / round_done / round_queued 不进叙事流，走进度条
  }
}

function showEmptyStream() {
  $('stream').innerHTML =
    '<div class="stream-empty">输入你想做的事，故事从这里开始。<br>' +
    '<span style="font-size:12px">「{移动}」可以让你在句中插入一个目的地。</span></div>';
}

// ══════════════════════════════════════════════════════════════
// 阶段进度
// ══════════════════════════════════════════════════════════════

function showProgress(on) {
  $('progress').classList.toggle('hidden', !on);
  if (!on) { $('progSteps').innerHTML = ''; S.timing = []; }
}

function onStage(ev) {
  if (ev.phase === 'start') {
    showProgress(true);
    $('progName').textContent = ev.name;
    $('progSecs').textContent = '';
    $('progDetail').textContent = '';
  } else if (ev.phase === 'tick') {
    $('progSecs').textContent = `${ev.seconds}s`;
    $('progDetail').textContent = ev.detail || '';
  } else if (ev.phase === 'end') {
    S.timing.push({ name: ev.name, seconds: ev.seconds });
    $('progSteps').innerHTML = S.timing
      .map(t => `<span class="step done">✓ ${esc(t.name)} <span class="s-sec">${t.seconds.toFixed(1)}s</span></span>`)
      .join('');
    if (S.visible.has('timing')) renderPanelsOnly();
  }
}

// ══════════════════════════════════════════════════════════════
// SSE
// ══════════════════════════════════════════════════════════════

let refreshTimer = null;
function refreshSoon(delay) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshState, delay || 120);
}

function connectSSE() {
  const es = new EventSource('/api/stream');

  es.onmessage = e => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (err) { return; }

    if (ev.type === 'hello') return;

    if (ev.type === 'round_start') {
      S.busy = true;
      S.timing = [];
      setBusy(true);
      showProgress(true);
      evToStream(ev);
      S.streamCount++;

    } else if (ev.type === 'round_done') {
      S.busy = false;
      setBusy(false);
      showProgress(false);
      refreshSoon(0);
      // 📍 得跟着走。以前只在开页和保存后拉地图，于是玩家走了标记还杵在原地
      // ——1B 自己写的验收标准「小地图跟 SSE 动」当时并没做到。
      // 静态层不重建：几何一局不变，白算 65 条路径没意义。
      loadMap({ static: false });

    } else if (ev.type === 'stage') {
      onStage(ev);

    } else {
      evToStream(ev);
      S.streamCount++;
      // 旁白紧跟 L3 落盘，这一刻刷新状态最及时
      if (ev.type === 'narration') refreshSoon(80);
      if (ev.type === 'scene') refreshSoon(0);
    }
  };

  es.onerror = () => {
    // EventSource 自带重连，这里只提示一下
    $('hRound').textContent = '⚠ 连接断开，重连中…';
    setTimeout(() => { if (S.view) renderTopbar(); }, 3000);
  };
}

function setBusy(b) {
  $('btnSend').disabled = b;
  $('input').disabled = b;
  $('input').placeholder = b ? '这一轮还在跑…' : '说点什么…（Enter 发送，Shift+Enter 换行）';
}

// ══════════════════════════════════════════════════════════════
// 数据加载
// ══════════════════════════════════════════════════════════════

async function refreshState() {
  try {
    S.view = await api('/api/state');
    S.busy = !!S.view.busy;
    setBusy(S.busy);
    renderAll();
  } catch (e) { console.error('state', e); }
}

/**
 * 拉地图。`static:false` 时不重建静态层——几何一局不变，
 * 每轮白算 65 条路径是纯浪费（见 `round_done`）。
 */
async function loadMap(o) {
  try {
    S.map = await api('/api/map');
    if (!o || o.static !== false) {
      S.mapStatic = (S.map && S.map.ok) ? buildMapStatic(S.map) : '';
    }
    renderPanelsOnly();
    applyMapView();          // 面板是重建的，视图框得重新贴上去
  } catch (e) { console.error('map', e); }
}

async function loadTree() {
  try {
    S.tree = await api('/api/locations');

    // 默认展开当前位置的祖先链
    const parts = S.tree.current_parts || [];
    let acc = '';
    for (const p of parts) { acc = acc ? acc + '·' + p : p; S.expanded.add(acc); }

    renderPanelsOnly();
  } catch (e) { console.error('tree', e); }
}

async function loadNpcs() {
  try {
    const r = await api('/api/npcs');
    S.npcs = r.npcs || [];
    S.npcs.sort((a, b) => (b.involved - a.involved) || a.short.localeCompare(b.short, 'zh'));
    renderPanelsOnly();
  } catch (e) { console.error('npcs', e); }
}

async function loadCtx(which) {
  try {
    S.ctxCache[which] = await api('/api/context/' + which);
    renderPanelsOnly();
  } catch (e) { console.error('ctx', e); }
}

/**
 * 用服务端保存的历史重建叙事流。
 *
 * **不做这个的话，F5 刷新就是一片空白**——叙事流是纯客户端从 SSE 事件拼的，
 * 页面一重载就全丢了，而服务端其实一直存着。
 */
async function loadHistory() {
  try {
    const h = await api('/api/history');
    const box = $('stream');
    box.innerHTML = '';
    S.streamCount = 0;
    for (const ev of (h.events || [])) { evToStream(ev); S.streamCount++; }
    // 历史为空 = 全新一局，叙事流该显示那句"你在哪"，而不是一片空白。
    // （这里原来调的是一个**从未定义**的 `clearStreamOnFirstRound()`：
    //   后端 history 至少给一条 scene，所以从没炸过——但它就挂在 `Promise.all`
    //   里，一旦 history 真空一次，`connectSSE()` 就永远不执行。）
  } catch (e) { console.error('history', e); }
}

async function selectNpc(name) {
  S.selNpc = name;
  S.npcDetail = null;
  if (!S.visible.has('npcdetail')) { S.visible.add('npcdetail'); saveVisible(); renderRails(); }
  renderPanelsOnly();
  try {
    S.npcDetail = await api('/api/npc/' + encodeURIComponent(name));
  } catch (e) { console.error('npc', e); }
  renderPanelsOnly();
}

// ══════════════════════════════════════════════════════════════
// 动作
// ══════════════════════════════════════════════════════════════

async function send() {
  const ta = $('input');
  const text = ta.value.trim();
  if (!text || S.busy) return;
  ta.value = '';
  try {
    const r = await post('/api/input', { text });
    if (r.action === 'busy') {
      pushStream('上一轮还在跑，稍等。', 'e-dim');
    }
  } catch (e) {
    pushStream('发送失败：' + e.message, 'e-err');
  }
}

// 移动：`/api/move` 与这个浮层**已退役**（2026-10-08）。
// 地图是主输入——1B 会给出一张可点击的小地图；在那之前，玩家用打字移动
// （"我往青木山脉去"本来就是自由文本，由 L3 判去向、代码落坐标）。

// ══════════════════════════════════════════════════════════════
// 面板抽屉
// ══════════════════════════════════════════════════════════════

function drawDrawer() {
  $('panelList').innerHTML = PANELS.map(p => `
    <label class="pitem">
      <input type="checkbox" data-pid="${p.id}" ${S.visible.has(p.id) ? 'checked' : ''}>
      <span class="pt">${esc(p.title)}</span>
      <span class="pz">${p.zone === 'left' ? '左' : '右'}</span>
    </label>`).join('');
}

function applyPreset(name) {
  S.visible = defaultVisible(name);
  saveVisible();
  drawDrawer();
  renderRails();
}

// ══════════════════════════════════════════════════════════════
// 事件绑定
// ══════════════════════════════════════════════════════════════

document.addEventListener('click', e => {
  const t = e.target;

  // 编辑器是全屏浮层，它自己处理点击（下面的「点地块＝前往」在编辑模式下
  // 会顺手发一轮"我往X去"——那是**移动**，不是**选中**）。见下面「世界编辑器」。
  if (t.closest('#editor')) return;

  // 打开编辑器
  if (t.closest('[data-ed="open"]')) { openEditor(); return; }

  // 面板折叠
  const col = t.closest('[data-collapse]');
  if (col) {
    const id = col.dataset.collapse;
    S.collapsedPanels.has(id) ? S.collapsedPanels.delete(id) : S.collapsedPanels.add(id);
    renderRails();
    return;
  }

  // NPC 选中
  const npc = t.closest('[data-npc]');
  if (npc) { selectNpc(npc.dataset.npc); return; }

  // 地点树展开/收起
  const tw = t.closest('[data-toggle]');
  if (tw && S.tree && !S.tree._modal) {
    const p = tw.dataset.toggle;
    S.expanded.has(p) ? S.expanded.delete(p) : S.expanded.add(p);
    renderPanelsOnly();
    return;
  }

  // 全屏看图的开/关
  if (t.closest('[data-mapfull]')) { openMapModal(); return; }
  if (t.closest('[data-mapclose]') || (t.id === 'mapModal')) { closeMapModal(); return; }
  const zoomBtn = t.closest('[data-mapzoom]');
  if (zoomBtn) {
    const box = zoomBtn.closest('[data-mapbox]');
    const svg = box.querySelector('svg.minimap');
    const m = mapThreadOf(box);
    const how = zoomBtn.dataset.mapzoom;
    if (how === 'reset') mapResetView();
    else {
      const r = svg.getBoundingClientRect();
      mapZoomAt(svg, m, r.left + r.width / 2, r.top + r.height / 2,
                how === 'in' ? 0.72 : 1 / 0.72);
    }
    return;
  }

  // 地图上的地块：**点＝选中**（不是前往）。
  // 「前往」是独立的一步——41 个地块挤在一张图上，误点一下就是一次真实回合
  // （花额度、推进世界），得让人看得见自己要做什么。
  const mbox = t.closest('[data-mapbox]');
  // 点在**文字**上＝点那个节点（`onLabel`）。为什么必须有这一步：**地区的名字**
  // 是选中它唯一的把手（它的圈不再算热区，见 `buildMapStatic`），而字是画在圈心的，
  // 鼠标落在字的边缘时按距离判可能差几个像素。
  const lbl = t.closest('[data-place]');
  const onLbl = !!(lbl && t.closest('text'));
  const pk = onLbl ? { name: lbl.dataset.place, d: 0, dot: 0 }
    : (mbox && t.closest('svg')) ? mapPickPlace(mbox, e) : null;
  if (pk) {
    if (!S.mapDragged) {
      S.mapSel = (S.mapSel === pk.name) ? null : pk.name;
      renderMapEverywhere();
    }
    S.mapDragged = false;
    return;
  }
  // 点地图上的**空白**＝取消选中（和"再点一次同一块"同一个出口：都回到没选中）。
  // ⚠️ 先在**清 `S.mapDragged` 之前**把它记下来：下面那行是原有动作（每次点击都要清），
  // 清完再判的话永远是假——于是**每一帧平移完松手都会顺手清掉选中**，
  // 拖动地图就等于"点空白"。拖动与点击靠位移阈值分开，这里必须尊重同一个阈值。
  const wasDragged = S.mapDragged;
  S.mapDragged = false;
  if (mbox && !wasDragged && t.closest('svg') && S.mapSel) {
    S.mapSel = null;
    renderMapEverywhere();
  }

  // 「前往」按钮：**唯一的出发动作**。合成一句"我往X去"走 `/api/input` 这个
  // 同一个入口——去哪要由 L3 判、落坐标由代码做，所以不另开移动接口。
  const go = t.closest('[data-goplace]');
  if (go) {
    const name = go.dataset.goplace;
    if (S.busy) { pushStream('上一轮还在跑，稍等。', 'e-dim'); return; }
    post('/api/input', { text: `我往${name}去。` }).then(r => {
      if (r.action === 'busy') pushStream('上一轮还在跑，稍等。', 'e-dim');
    }).catch(e => pushStream('发送失败：' + e.message, 'e-err'));
    return;
  }
});

/** 重画面板上所有地图实例（含全屏浮层）。选中变化时用。 */
function renderMapEverywhere() {
  const m = S.map;
  if (!m || !m.ok) return;
  for (const box of document.querySelectorAll('[data-mapbox]')) {
    if (box.dataset.mapwhich === 'edit') continue;      // 编辑器另有自己的选中
    const dyn = box.querySelector('.mdyn');
    if (dyn) dyn.innerHTML = mapMarkerSvg(m);
  }
  const card = document.querySelector('[data-mapcard]');
  if (card) card.innerHTML = placeCardHTML(m, S.mapSel);
  // 选中变了 = 该亮的那一片变了。静态层没重建，所以这里只有"重算置灰"这一件事
  // （`applyMapView` 顺带把 viewBox 也照旧设一遍，是幂等的）。
  applyMapView();
}

// ── 悬停一个节点 → 把它的名字放出来 ────────────────────────────
//
// 名字在**单独一层**（`.lyr-label`，排在最上面），已经**不是**节点的子元素了，
// 所以原来那条 `.mplace:hover text { display: block !important }` 够不着它——
// 那条规则靠的是"父子"。它只有一个用处：`applyMapLabels` 因为重叠把某个名字藏了，
// 指到那块地时得能看见。这里把同一件事用 JS 做掉。
//
// 用会冒泡的 `mouseover` / `mouseout` 而不是 `mouseenter` / `leave`：页面上
// 有 2~3 张图、上百个节点，逐个挂监听不值得。
document.addEventListener('mouseover', e => {
  const g = e.target.closest && e.target.closest('.mplace');
  if (!g) return;
  const t = labelOf(g);
  if (t) t.style.display = '';              // 压过 `applyMapLabels` 写的行内 display:none
});
document.addEventListener('mouseout', e => {
  const g = e.target.closest && e.target.closest('.mplace');
  if (!g || g.contains(e.relatedTarget)) return;   // 还在同一个节点里挪 → 别重排一遍
  const box = g.closest('[data-mapbox]');
  const svg = box && box.querySelector('svg.minimap');
  if (svg) applyMapLabels(svg, mapThreadOf(box), mapViewOf(mapThreadOf(box)));
});

// ── 滚轮缩放 / 拖动平移 ──────────────────────────────────────
//
// 拖动与点击靠**位移阈值**分开：不分开的话，每次平移都会顺手选中一个地块。
// `S.mapDragged` 在 pointerup 后留一个宏任务再清，好让随后的 click 看得见它。

document.addEventListener('wheel', e => {
  const box = e.target.closest && e.target.closest('[data-mapbox]');
  if (!box) return;
  e.preventDefault();                       // 否则整页跟着滚
  const svg = box.querySelector('svg.minimap');
  mapZoomAt(svg, mapThreadOf(box), e.clientX, e.clientY, e.deltaY > 0 ? 1.16 : 1 / 1.16);
}, { passive: false });

let _drag = null;

// ── 右栏宽度可拖（默认 322px 对地图太窄）──
// 默认比原来宽：322px 让 41 个地块挤在 298px 里，那是「占位偏小」的直接来源。
// 再宽交给拖拽条（宽度存 localStorage）。
const RAIL_W0 = 400;

function applyRailWidth() {
  document.documentElement.style.setProperty('--rail-r', (S.railR || RAIL_W0) + 'px');
}

function loadRailWidth() {
  try {
    const v = +localStorage.getItem('g4.railR');
    if (v >= 240 && v <= 1100) S.railR = v;
  } catch (e) { /* 存储坏了就用默认 */ }
}

/**
 * 一次手势开始前必须做的两件事：**清掉那点蓝底选中**，并且**不让浏览器再有机会选**。
 *
 * 不做的后果（实测）：在地图上按下拖动，浏览器先把标签里的字选成蓝底（`.mlabel`
 * 是可选文本），于是第二次拖就变成"拖动那段选中的字"——Chrome 会把它当搜索/拖放，
 * 指针事件当场丢掉，人看到的就是"拖着拖着自己跑去搜索了"。
 *
 * `preventDefault()` 只在这一刻、只在真的开始一次拖动时调；点在 `.mtools`（＋/－/⟲/⤢）
 * 上时不调——那几个是按钮，别动它们的默认行为。
 */
function dragStartKeepGesture(e) {
  const sel = document.getSelection && document.getSelection();
  if (sel && sel.rangeCount) sel.removeAllRanges();
  const t = e.target;
  if (!(t && t.closest && t.closest('.maptools'))) {
    try { e.preventDefault(); } catch (err) { /* 合成事件里没有可取消的动作 */ }
  }
}

document.addEventListener('dragstart', e => {
  // 兜底：HTML5 的拖放（"拖着选中的文字去搜索"）在有些情况下绕过 `pointerdown`
  if (e.target && e.target.closest && e.target.closest('[data-mapbox]')) {
    e.preventDefault();
  }
});

document.addEventListener('pointerdown', e => {
  if (e.button !== 0) return;
  if (e.target.closest && e.target.closest('#railGrip')) {
    dragStartKeepGesture(e);
    _drag = { kind: 'rail', x: e.clientX, w: S.railR || RAIL_W0 };
    document.querySelector('#railGrip').classList.add('on');
    return;
  }
  const box = e.target.closest && e.target.closest('[data-mapbox]');
  if (!box) return;
  S.mapDragged = false;             // 新手势开始，上次的抑制标记作废

  // ── 编辑器里：拖几何优先于拖画布（阶段 3）──
  // 只在 `#editor` 的地图里认这些把手：面板是"看"的，不该能拖。
  //
  // ⚠️ 认的是**整个地块组 `.mplace`**，不是那个可见的小圆点 `.mdot`。
  // 手在图上按时，最上面那个元素是 `.mhit`（一圈透明的热区，专门给点击用的，
  // 它**盖住了** `.mdot`——只认 `.mdot` 的话，`closest()` 一律返回 null，
  // **地块永远拖不动**（实测：圆心正中也拖不动）。
  // 这个坑测试里看不出来：直接 `dot.dispatchEvent()` 会跳过命中测试，
  // 而真人的手**必须**经过命中测试。
  const handle = e.target.closest && e.target.closest(
    '#editor .ed-map [data-ctr], #editor .ed-map .mplace');
  if (handle && S.edit) {
    const m = S.edit.view || {};
    const isCtrl = handle.hasAttribute('data-ctr');
    const road = (m.roads || []).find(x =>
      x.name === ((S.edit.form.路[S.edit.sel.i] || {}).名));
    const place = handle.closest('.mplace');
    let start = null;
    if (isCtrl) {
      start = (road && road.line && road.line[+handle.dataset.ctr]) || null;
    } else if (place) {
      const p = (m.places || []).find(x => x.name === place.dataset.place);
      start = p ? [p.x, p.y] : null;
    }
    if (start) {
      dragStartKeepGesture(e);       // 见上：不清干净，第二拖就变成"拖选中的字"
      _drag = { kind: 'geo', box, svg: box.querySelector('svg.minimap'),
                x: e.clientX, y: e.clientY, moved: false,
                ctrl: isCtrl ? +handle.dataset.ctr : null,
                roadName: road ? road.name : null,
                placeName: place ? place.dataset.place : null,
                start, m };
      return;
    }
  }

  dragStartKeepGesture(e);
  _drag = { kind: 'map', box, svg: box.querySelector('svg.minimap'),
            x: e.clientX, y: e.clientY, v: mapViewOf(mapThreadOf(box)), moved: false };
});

document.addEventListener('pointermove', e => {
  if (!_drag) return;
  if (_drag.kind === 'rail') {
    // 往左拖 = 右栏变宽
    S.railR = Math.max(240, Math.min(1100, _drag.w - (e.clientX - _drag.x)));
    applyRailWidth();
    return;
  }
  const dx = e.clientX - _drag.x, dy = e.clientY - _drag.y;
  if (!_drag.moved) {
    if (Math.hypot(dx, dy) < 5) return;          // 还没到"拖"的门槛
    _drag.moved = true;
    // 拖的过程中再清一次：`pointerdown` 那一下有时轮不到我们（比如手先落在
    // 别的层上），而只要蓝底一出现，这一拖就会被浏览器抢成"拖选中文字"。
    { const sel = document.getSelection && document.getSelection();
      if (sel && sel.rangeCount) sel.removeAllRanges(); }
    _drag.box.classList.add('grabbing');
    // `setPointerCapture` 对合成事件（pointerId 不对应真实指针）会抛 NotFoundError，
    // 而它在**这一句之后的所有逻辑之前**——一抛，整个拖动静默失效，而且因为
    // 监听器里的异常不会冒到 `dispatchEvent` 调用方，**外面完全看不出来**（踩过）。
    // 捕获只是"指针移出元素后仍然收得到事件"的优化；这里监听的是 `document`，
    // 所以丢了也没关系——但**绝不能让它把后面那段带下去**。
    try { _drag.box.setPointerCapture(e.pointerId); } catch (err) { /* 见上 */ }
  }

  if (_drag.kind === 'geo') { edGeoMove(e); return; }

  const { s } = mapScale(_drag.svg, _drag.v);
  S.mapView = mapClamp({
    x: _drag.v.x - dx / s, y: _drag.v.y - dy / s, w: _drag.v.w, h: _drag.v.h,
  }, mapThreadOf(_drag.box).bounds);
  applyMapView();
});

/**
 * 拖几何时**只改数字**（`S.edit.view`），渲染跟着它走。
 *
 * 不碰 `S.edit.form` 里的文本——`.map` 的坐标写法是 Python 的 `repr(float)`，
 * 前端自己拼字符串就等于抄一份格式化规则。松手时把数字记进 `S.edit.geo`，
 * 保存时交给服务端格式化（见 `map_io.apply_geo_patch`）。
 */
function edGeoMove(e) {
  const { s } = mapScale(_drag.svg, mapViewOf(S.edit && S.edit.view));
  if (!s) return;
  const wx = _drag.start[0] + (e.clientX - _drag.x) / s;
  const wy = _drag.start[1] + (e.clientY - _drag.y) / s;
  const m = S.edit.view;
  if (!m) return;
  if (_drag.ctrl !== null && _drag.roadName) {
    const road = (m.roads || []).find(r => r.name === _drag.roadName);
    if (road && road.line && road.line[_drag.ctrl]) {
      road.line[_drag.ctrl] = [wx, wy];
    }
  } else if (_drag.placeName) {
    edMovePlaceInView(_drag.placeName, wx, wy);
  }
  redrawEdMap();
}

/**
 * 把一个地块在**视图里**挪到新位置，**并且把它连着的路的端点一起挪**。
 *
 * 为什么必须一起挪：路径线的两端**就是**那两个地块（`auto_layout` 这么生成，
 * `road_point` 也靠这个前提）。只挪点不挪线，拖的时候线就掉在原地——看着像断了。
 * 落数据那边（`map_io.apply_geo_patch`）做的是同一件事；这里做的是**实时预览**，
 * 两边规则必须一样，不然松手那一刻画面会跳一下。
 *
 * :return: 被牵动的路名，好让 `edGeoCommit` 把它们也记进待存数据
 */
function edMovePlaceInView(name, x, y) {
  const m = S.edit.view;
  const p = (m.places || []).find(q => q.name === name);
  if (!p) return [];
  p.x = x; p.y = y;
  const touched = [];
  for (const road of (m.roads || [])) {
    const line = road.line;
    if (!line || line.length < 2) continue;
    let hit = false;
    if (road.from === name) { line[0] = [x, y]; hit = true; }
    if (road.to === name) { line[line.length - 1] = [x, y]; hit = true; }
    if (hit) touched.push(road.name);
  }
  return touched;
}

/** 只重画编辑器地图的两层（拖动每帧都走这里，别整块重建 `#editor`）。 */
function redrawEdMap() {
  const box = document.querySelector('#editor [data-mapbox]');
  if (!box) return;
  const m = S.edit.view || {};
  const st = box.querySelector('.mstatic');
  if (st) st.innerHTML = m.ok ? buildMapStatic(m, { roadHit: true }) : '';
  const dy = box.querySelector('.mdyn');
  if (dy) dy.innerHTML = edHighlight(m);
  applyMapView();
}

/**
 * 让**服务端**照保存那条流水线重算一次左侧那张图（只算不写）。
 *
 * 为什么必须有它：`S.edit.view` 是**开编辑器时拍的一张快照**，而表单是活的。
 * 删掉一条路、删掉一个地块、改一个名字——快照都不知道，于是地图上留下一条
 * **看得见却点不动、也删不掉**的幽灵线（用户报的就是这个）。
 *
 * 为什么不在 JS 里照着删：删地块要**连带删它的路**、改名要**连带改引用与几何的键**
 * （改名还会连带改 `A-B` 拼出来的路名）——那些规则在 `map_io.cascade` 里。
 * 在 JS 里抄一份，抄的那份迟早和 Python 漂开（S2 那次"改一个字段重排 159 行"
 * 就是这么来的）。所以：**一条流水线，两个出口**（写盘 / 只看）。
 *
 * 失败时**留着上一张图**并说话：半成品的表单（键名重复、几何文案写到一半）
 * 服务端会算不出来，这时宁可图旧一点，也不能给一张错的。
 */
async function edPreviewMap(quiet) {
  if (!S.edit) return;
  try {
    const r = await post('/api/map/preview',
                         { form: S.edit.form, geo: S.edit.geo });
    if (!r.ok) {
      // 打字停顿触发的那一次**不出声**：几何框边打边算，中间态认不出是常事，
      // 每次都报一句会刷屏。手点了删除/改名那种才说话——那时"没变"是要紧的。
      if (!quiet) {
        edMark([{ t: '地图没跟着变：' + (r.reason || '服务端算不出来'), cls: 'bad' }]);
      }
      return;
    }
    S.edit.view = r.view;
    // 改名的那一串引用，服务端已经认出来了，照着改表单（**规则没抄**：
    // 配对是 `map_io.detect_renames` 算的，这里只是把它报出来的映射应用一遍）
    const synced = edApplyRenames(r.renames);
    if (synced) drawEditor(); else redrawEdMap();
  } catch (e) {
    if (!quiet) edMark([{ t: '地图没跟着变：' + e.message, cls: 'bad' }]);
  }
}

/**
 * 把服务端报出来的**改名映射**应用到手上的表单（含几何的键）。
 *
 * 为什么非要同步：改名不只是换个标签——子地块的 `属于`、路的 `从/到`、路的 `名`
 * （若它是 `A-B` 拼的）、`几何` 的键都得跟着走。图那边服务端已经改过了，
 * 表单不改就会出现"图上叫 `东域·改名试-西域`、表单里还叫 `东域-西域`"，
 * 于是**点得中、选不中**。规则只有 Python 那一份，这里只是把映射照抄一遍。
 *
 * :return: 是否真的改动了什么（决定要不要整块重画侧栏）
 */
function edApplyRenames(ren) {
  const pr = (ren && ren.地块) || {}, rr = (ren && ren.路) || {};
  if (!Object.keys(pr).length && !Object.keys(rr).length) return false;
  const f = S.edit.form;
  for (const p of f.地块 || []) {
    if (pr[p.属于]) p.属于 = pr[p.属于];
  }
  for (const r of f.路 || []) {
    if (pr[r.从]) r.从 = pr[r.从];
    if (pr[r.到]) r.到 = pr[r.到];
    if (rr[r.名]) r.名 = rr[r.名];
  }
  // 几何的键就是实体名，也得改名——否则下一次预览会把它们当成悬空几何清掉。
  // 重建对象而不是删了再加：**键序就是文件里的行序**，动了序要多出几十行 diff。
  for (const [kind, map] of [['坐标', pr], ['范围', pr], ['路径线', rr]]) {
    if (!f.几何) break;
    const g = f.几何[kind] || {};
    if (!Object.keys(map).some(k => k in g)) continue;
    const next = {};
    for (const [k, v] of Object.entries(g)) next[map[k] || k] = v;
    f.几何[kind] = next;
  }
  // 拖出来还没保存的那份（`S.edit.geo`）同理，否则松手存下去的是旧名字
  for (const [kind, map] of [['坐标', pr], ['路径线', rr]]) {
    const g = S.edit.geo[kind] || {};
    const next = {};
    for (const [k, v] of Object.entries(g)) next[map[k] || k] = v;
    S.edit.geo[kind] = next;
  }
  const touched = S.edit.geoTouched;
  if (touched && touched.size) {
    S.edit.geoTouched = new Set([...touched].map(k => {
      for (const [old, nw] of Object.entries({ ...pr, ...rr })) {
        if (k.endsWith(':' + old)) return k.slice(0, -old.length) + nw;
      }
      return k;
    }));
  }
  return true;
}

function endDrag() {
  if (!_drag) return;
  const d = _drag;
  _drag = null;
  if (d.kind === 'rail') {
    const g = document.querySelector('#railGrip');
    if (g) g.classList.remove('on');
    localStorage.setItem('g4.railR', String(S.railR));
    applyMapView();                                  // 宽度变了，标签密度也要重算
    return;
  }
  d.box.classList.remove('grabbing');
  if (d.kind === 'geo') {
    // **松手才落数据**：把拖出来的数字记进 `S.edit.geo`，保存时随 form 一起交给服务端。
    // 拖动过程中只改 `S.edit.view`（渲染用），不动模型——这样一帧的渲染开销
    // 和"改了多少数据"是分开的两件事。
    if (d.moved) edGeoCommit(d);
    S.mapDragged = true;      // 拖完的这一次 click 不该再触发"选中/切换"
    return;
  }
  // 抑制**紧接着那一次** click——不然每次平移都会顺手选中一个地块。
  // 不用定时器：`click` 一定跟在 `pointerup` 之后的同一轮里到达，
  // 用定时器既依赖调度顺序，又会被别的宏任务抢跑（试过，不可靠）。
  // 这个标记由 click 处理器消费、由下一次 pointerdown 兜底清掉。
  if (d.moved) S.mapDragged = true;
}

/** 把这一拖的结果记进 `S.edit.geo`（**数值**，不是文本）。 */
function edGeoCommit(d) {
  if (!S.edit) return;
  S.edit.geo = S.edit.geo || { 坐标: {}, 路径线: {} };
  const m = S.edit.view || {};
  if (d.ctrl !== null && d.roadName) {
    const road = (m.roads || []).find(r => r.name === d.roadName);
    if (road && road.line) {
      S.edit.geo.路径线[d.roadName] = road.line.map(q => [+q[0].toFixed(4), +q[1].toFixed(4)]);
      edGeoTouched(`路径线:${d.roadName}`);
    }
  } else if (d.placeName) {
    const p = (m.places || []).find(x => x.name === d.placeName);
    if (p) {
      S.edit.geo.坐标[d.placeName] = [+p.x.toFixed(4), +p.y.toFixed(4)];
      edGeoTouched(`坐标:${d.placeName}`);
      // 被牵动的路端点也一并记下来：预览与保存必须是同一组数字，
      // 否则松手保存后线会跳一下（服务端本来也会跟，但把它显式带上更稳）
      for (const rn of edMovePlaceInView(d.placeName, p.x, p.y)) {
        const road = (m.roads || []).find(x => x.name === rn);
        if (road && road.line) {
          S.edit.geo.路径线[rn] = road.line.map(q => [+q[0].toFixed(4), +q[1].toFixed(4)]);
        }
      }
    }
  }
  markDirty();
}

/**
 * 手改了某一格几何文本 → **作废那一格"拖过还没保存"的数字**。
 *
 * 为什么必须有它（用户报的"改了下曲线就不行了"）：拖动把新数值记进 `S.edit.geo`，
 * 而它是**优先于文本**的——预览（`/api/map/preview`）与落盘（`apply_geo_patch`）
 * 都先应用它、再读文本。于是"先拖、后手改文本"就成了**改了没反应**：
 * 你敲进去的那一串被拖出来的旧数字盖掉了。最后动手的那个该说了算。
 *
 * 牵连的也一起作废：改了**地块的坐标**，就把连着它的那几条路的 `路径线` 覆盖也丢掉
 * ——端点该由服务端"跟着地块走"那条规矩去接（见 `apply_geo_patch`），
 * 而不是继续用拖动时的旧线。
 */
function edDropGeoOverride(name, kind) {
  if (!S.edit || !S.edit.geo) return;
  const g = S.edit.geo, touched = S.edit.geoTouched;
  const drop = k => { if (touched) touched.delete(k); };
  if (kind === '路径线') {
    delete (g.路径线 || {})[name];
    drop(`路径线:${name}`);
  } else {
    delete (g[kind] || {})[name];
    drop(`${kind}:${name}`);
    if (kind === '坐标') {
      for (const r of (S.edit.form.路 || [])) {
        if (r.从 === name || r.到 === name) {
          delete (g.路径线 || {})[r.名];
          drop(`路径线:${r.名}`);
        }
      }
    }
  }
  // 那一格不再"过期"了（它现在就是最新的），把"拖过"的标记一并撤掉
  const side = document.querySelector('.ed-side');
  if (!side) return;
  for (const el of side.querySelectorAll('[data-geo]')) {
    if (`${el.dataset.geo}:${el.dataset.geoname}` !== `${kind}:${name}`) continue;
    el.classList.remove('stale');
    const m = el.closest('.kv') && el.closest('.kv').querySelector('.stale-mark');
    if (m) m.remove();
  }
}

/** 记下"这个几何项被拖过、还没保存"，好让检视器把话说明白。 */
function edGeoTouched(key) {
  if (!S.edit) return;
  S.edit.geoTouched = S.edit.geoTouched || new Set();
  S.edit.geoTouched.add(key);
  // 检视器里的坐标/路径线输入框现在**过期了**（它显示的是拖之前的文本）。
  // 不去 JS 里重新格式化（那是抄 Python 的 repr），而是明说一句。
  const side = document.querySelector('.ed-side');
  if (side) {
    for (const el of side.querySelectorAll('[data-geo]')) {
      const k = `${el.dataset.geo}:${el.dataset.geoname}`;
      if (S.edit.geoTouched.has(k)) {
        el.classList.add('stale');
        const t = el.closest('.kv') && el.closest('.kv').querySelector('.k');
        if (t && !t.querySelector('.stale-mark')) {
          t.insertAdjacentHTML('beforeend', '<i class="stale-mark">拖过</i>');
        }
      }
    }
  }
}
document.addEventListener('pointerup', endDrag);
document.addEventListener('pointercancel', endDrag);

document.addEventListener('dblclick', e => {
  if (e.target.closest && e.target.closest('#railGrip')) {
    S.railR = RAIL_W0;
    applyRailWidth();
    localStorage.removeItem('g4.railR');
    applyMapView();
  }
});

$('btnSend').onclick = send;

$('input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

$('btnPanels').onclick = () => {
  const d = $('panelDrawer');
  d.classList.toggle('hidden');
  if (!d.classList.contains('hidden')) drawDrawer();
};
$('drawerClose').onclick = () => $('panelDrawer').classList.add('hidden');

$('panelList').addEventListener('change', e => {
  const pid = e.target.dataset.pid;
  if (!pid) return;
  e.target.checked ? S.visible.add(pid) : S.visible.delete(pid);
  saveVisible();
  renderRails();
});

$('btnPresetPlay').onclick = () => applyPreset('play');
$('btnPresetDev').onclick = () => applyPreset('dev');

$('btnUndo').onclick = async () => {
  try { await post('/api/undo'); refreshSoon(0); }
  catch (e) { pushStream('撤回失败：' + e.message, 'e-err'); }
};

$('btnSaves').onclick = () => openSavesDialog();

/**
 * 顶栏那几个"一条命令"按钮的公共收尾：发命令 → **立刻**重拉状态。
 *
 * 不等那 15 秒的兜底轮询——不然点完按钮这段时间里，顶栏和面板显示的还是旧数据，
 * 人只会以为"点了没反应"。
 */
async function sendCmd(cmd, rebuildHistory) {
  try {
    const r = await post('/api/command', { cmd });
    if (r.action === 'busy') pushStream('上一轮还在跑，等它跑完。', 'e-dim');
  } catch (e) {
    pushStream('命令失败：' + e.message, 'e-err');
  }
  await (rebuildHistory ? Promise.all([refreshState(), loadHistory()])
                        : refreshState());
}

// 「新游戏」不可逆（这一局的进度没了）——**先问一句**。措辞要说清"存档不动"：
// 它其实是"重开"，不是"删除"。
$('btnNewGame').onclick = async () => {
  if (!confirm('回到开局？这一局的进度会被清掉。\n'
               + '（save/ 里的存档不会动，随时能读回来）')) return;
  await sendCmd('/new', true);      // 轮次归零、叙事流重建
};

// 「重载世界」＝编辑器保存时**自动做的那件事**的手动版：在编辑器外面改了 `.map`
// （手改 / `git` 切分支 / 跑 `map_io` 命令行）不用重启服务。
// 轮次与叙事流都不受影响，所以不重建历史。
$('btnReload').onclick = () => sendCmd('/reload', false);

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('panelDrawer').classList.add('hidden'); }
});

// ══════════════════════════════════════════════════════════════
// 世界编辑器（阶段 2：改字段；阶段 3 再加拖拽几何）
// ══════════════════════════════════════════════════════════════
//
// 两条设计决定，都是为了**只有一处真相**：
//
// 1. 字段值**就用 `.map` 的写法**（`[a,b]`=列表、`是/否`=布尔、纯数字=数字）。
//    类型规则在 `map_io._parse_value` / `_fmt_value` 里已经存在且被往返测试钉死，
//    在这里用 JS 再判一遍就是抄一份规则——抄的那份迟早和 Python 的漂开。
//    副作用还挺好：字段框里显示的就是"这一条写进 `.map` 会是什么样"。
// 2. 保存不由前端拼 `.map` 文本，只把表单 POST 回去，**写盘三查全在服务端**
//    （`validate_edit` → 往返自检 → 备份，见 `map_io.save_map`）。

S.edit = null;   // {form, view, sel:{tab,i}, filter, msg:[], dirty}

async function openEditor() {
  try {
    const r = await api('/api/map/edit');
    if (!r.ok) { pushStream('打不开编辑器：' + esc(r.reason || '未知'), 'e-err'); return; }
    // `notices`（键写歪了这类提醒）与 `problems`（存不了的硬错）分开显示：
    // 都塞进"问题"里的话，一句提醒看起来就像"存不了"
    S.edit = { form: r.form, view: r.view, path: r.path,
               // **哪些节点是地区**：由服务端一处算好（`类型` 那张词表在后端，
               // 前端不抄第二份）。列表上标 `◇`、检视器里显示"地区/地点"。
               // ⚠️ `regionsKnown` 必须单独记：`new Set()` 本身是**真值**，
               // 只判 `S.edit.regions` 的话，老服务端（没这个字段）会被当成
               // "服务端说了：全都是地点"——那是**假的**（实测：六个地区全被说成地点）。
               regions: new Set(r.regions || []),
               regionsKnown: Array.isArray(r.regions),
               // 能填哪些 `类型` 值（地区那几种词）——下拉框的选项也由服务端给，
               // 空数组＝老服务端，这时那一格**只读**（见 `edPlaceHTML`）
               regionTypes: Array.isArray(r.region_types) ? r.region_types : [],
               sel: { tab: '地块', i: 0 }, filter: '',
               msg: [...(r.notices || []).map(t => ({ t, cls: 'warn' })),
                     ...(r.problems || []).map(t => ({ t, cls: 'bad' }))],
               dirty: false,
               // 拖出来的几何：**数值**，保存时随 form 交给服务端格式化（阶段 3）
               geo: { 坐标: {}, 路径线: {} }, geoTouched: new Set() };
    drawEditor();
  } catch (e) {
    pushStream('打不开编辑器：' + e.message, 'e-err');
  }
}

function closeEditor(force) {
  if (S.edit && S.edit.dirty && !force
      && !confirm('有改动还没保存，关掉就丢了。要关吗？')) return;
  S.edit = null;
  const el = $('editor');
  if (el) el.remove();
}

/** `msg` 可以是字符串（统一用 `cls`），也可以是 `{t, cls}`（逐行自己带类）。 */
function edMark(msg, cls) {
  S.edit.msg = (msg || []).map(m => (m && m.t !== undefined) ? m : { t: m, cls: cls || '' });
  const box = document.querySelector('.ed-msg');
  if (box) box.innerHTML = edMsgHTML();
}

/** 标脏：改一个字符就该在标题上看见——不然"改了没存"要靠记性。 */
function markDirty() {
  if (!S.edit) return;
  S.edit.dirty = true;
  const d = document.querySelector('.ed-dirty');
  if (d) d.textContent = ED_DIRTY_TEXT;
}

const ED_DIRTY_TEXT = '● 未保存';

/** 头部的"未保存"位。**必须由状态渲染**：`drawEditor` 整块重建头部，
    写死成空串的话，任何一次重画（加字段、删连线……）都会把提示抹掉，
    而 `S.edit.dirty` 还是 true——**看起来像已经存过了**。 */
const edDirtyHTML = () => (S.edit && S.edit.dirty ? ED_DIRTY_TEXT : '');

const edMsgHTML = () => (S.edit.msg || [])
  .map(m => `<div class="ed-prob ${m.cls}">${esc(m.t)}</div>`).join('');

/**
 * 只重画表单区（打字时不重画——重画会丢焦点）。
 *
 * `refocus` 是 `data-obj` 路径：**结构变了**（加/删一行）时必须给，否则
 * `innerHTML` 一换，焦点掉回 body——玩家接着敲键盘就会滚动页面、或者触发
 * 主界面的快捷键。以前这个参数从没传过，等于死代码（S2.4 补上）。
 */
function edRedraw(refocus) {
  const side = document.querySelector('.ed-side');
  if (!side) return;
  side.innerHTML = edSideHTML();
  if (refocus) {
    // 路径里有 `:`，属性选择器里用引号包着就行；别再走 `CSS.escape`
    // （那是给标识符用的，转义出来的反斜杠在引号里反而不匹配）
    const el = side.querySelector(`[data-obj="${String(refocus).replace(/"/g, '\\"')}"]`);
    if (el) { el.focus(); el.selectionStart = el.selectionEnd = el.value.length; }
  }
}

function drawEditor() {
  let el = $('editor');
  if (!el) { el = document.createElement('div'); el.id = 'editor'; document.body.appendChild(el); }
  const m = S.edit.view || {};
  const b = m.bounds || [0, 0, 100, 100];
  el.innerHTML = `
    <div class="ed-head">
      <b>世界编辑器</b>
      <span class="ed-dirty">${edDirtyHTML()}</span>
      <span class="ed-path">${esc(S.edit.path || '')}</span>
      <span class="ed-msg">${edMsgHTML()}</span>
      <button class="btn btn-send" data-ed="save">保存</button>
      <button class="btn" data-ed="close">关闭</button>
    </div>
    <div class="ed-body">
      <div class="ed-map">
        ${mapBoxHTML(m, {
          which: 'edit', fullscreen: false, editor: false,
          static: m.ok ? buildMapStatic(m, { roadHit: true }) : '',
          dyn: edHighlight(m),
          legend: '点地块＝选中它　·　滚轮缩放 / 拖动平移',
        })}
      </div>
      <div class="ed-side">${edSideHTML()}</div>
    </div>`;
  applyMapView();
}

/**
 * 编辑器里**额外画在 `.mdyn` 上的东西**：现在**只剩路的编辑手柄**。
 *
 * 选中态的颜色一概不在这里画——那是 `applyMapFocus` 给静态层挂
 * `.on` / `.sel` / `.nb` 干的（见 style.css）。全搬走的原因是：`.mdyn` 整层在
 * `.mstatic` **之上**，画在这儿的任何东西都会盖住**文字**，跟
 * "线段在底、节点在中、文字在最上"的涂层顺序打架。
 * （原来这里有选中圈、邻居圈、青绿叠线三样，都删了。）
 *
 * 手柄（`.ectrl`）是**例外**，必须留在最上面：那是要用鼠标拖的编辑件，
 * 压到文字底下就抓不住了。
 */
function edHighlight(m) {
  const sel = S.edit.sel;
  if (sel.tab !== '路') return '';      // 选地块时地图上不需要再画什么
  const r = (m.roads || []).find(x => x.name === (S.edit.form.路[sel.i] || {}).名);
  const L = (r && r.line) || [];
  if (L.length < 2) return '';
  // 控制点：**只给中间那些**，两端不给。
  // 两端**就是**那两个地块本身——拖地块的时候它们会跟着走（`edMovePlaceInView`），
  // 所以给它们手柄既多余，又会让人误会"这两头是可以单独挪的"。
  // 而且手柄画在 `.mdyn` 里，`.mdyn` 在 `.mstatic` **之上**——
  // 给端点手柄等于给地块点**套一个金圈**，看着像点被框住了。
  //
  // 二次贝塞尔只有**一个**自由控制点（`L[1]`），所以典型情况就是出 1 个手柄。
  // 两点的那种直线路没有任何自由点——要弯就用检视器里的「＋ 加一个弯」。
  const handles = L.slice(1, -1).map((q, k) =>
    `<circle class="ectrl" data-ctr="${k + 1}" cx="${q[0]}" cy="${q[1]}" r="8"/>`).join('');
  const tip = L.length < 3
    ? `<text class="ed-seltip" x="${L[0][0]}" y="${L[0][1] + 40}">直线路：用右边「＋ 加一个弯」</text>`
    : '';
  return handles + tip;
}

/** 选中第 i 个实体：只重画侧栏 + 高亮，不重建整张地图（重建会清掉列表滚动位置）。 */
function edSelect(i) {
  S.edit.sel.i = i;
  edRedraw();
  // 走 `redrawEdMap`（它末尾会 `applyMapView`）而不是自己塞 `.mdyn`：
  // 控制点的半径是 `applyMapView` 按屏幕像素给的，自己塞的话新出的手柄
  // 会停在 emit 时的 `r="8"`（画布单位）——缩远了只剩 4 个屏幕像素，点不中。
  redrawEdMap();
  // 按 `data-edsel` 比，不能按下标——列表可能正被筛选过，下标和 `i` 对不上
  document.querySelectorAll('.ed-item').forEach(el =>
    el.classList.toggle('on', +el.dataset.edsel === i));
}

function edSideHTML() {
  const f = S.edit.form, sel = S.edit.sel;
  const tabs = ['地块', '路', '全局'].map(t =>
    `<span class="ed-tab${sel.tab === t ? ' on' : ''}" data-edtab="${t}">${t}${
      t === '地块' ? ' ' + (f.地块 || []).length
      : t === '路' ? ' ' + (f.路 || []).length : ''}</span>`).join('');

  let list = '', insp = '';
  if (sel.tab === '全局') {
    insp = edGlobalHTML(f);
  } else {
    const items = f[sel.tab] || [];
    const flt = (S.edit.filter || '').toLowerCase();
    const shown = items.map((it, i) => ({ it, i }))
      .filter(({ it }) => !flt || JSON.stringify(it).toLowerCase().includes(flt));
    // 地区在列表上标 `◇`（地点是 `·`）——不然"哪些是地区"只能靠记住哪个字段写了什么。
    // 表没到（老服务端）就不标，**不猜**。
    const mark = n => (sel.tab !== '地块' || !S.edit.regionsKnown) ? ''
      : (S.edit.regions.has(n) ? '◇ ' : '· ');
    list = shown.map(({ it, i }) => `<div class="ed-item${sel.i === i ? ' on' : ''}"
        data-edsel="${i}">${mark(it.名)}${esc(it.名 || '(无名)')}
        <i>${esc(it.属于 || it.从 || '')}</i></div>`).join('')
      || '<div class="v empty">没有匹配的</div>';
    insp = sel.tab === '地块' ? edPlaceHTML(f, items[sel.i], sel.i)
                              : edRoadHTML(f, items[sel.i], sel.i);
  }
  return `<div class="ed-tabs">${tabs}</div>
    ${sel.tab === '全局' ? '' : `
      <input class="ed-filter" data-edfilter placeholder="筛选…" value="${esc(S.edit.filter)}">
      <div class="ed-list">${list}</div>
      <button class="ed-add ed-new" data-ednew="${sel.tab}">＋ 新建${sel.tab === '地块' ? '地块' : '路'}</button>`}
    <div class="ed-insp">${insp}</div>`;
}

/**
 * 字段行的通用渲染。`obj` 是 `data-obj` 路径前缀（见 `setByPath`）。
 *
 * 值长过一行就换成 textarea（`产出` 一条就有六七个物品）——单行输入里
 * 后半截是**看不见**的，而"看不见的值"改起来只能靠猜。
 */
const ED_WIDE = 26;

function edFields(rows, obj, addLabel) {
  return (rows || []).map((r, i) => {
    const v = `data-obj="${obj}:${i}:v"`;
    const box = (r.v || '').length > ED_WIDE
      ? `<textarea class="ed-v" rows="2" ${v}>${esc(r.v)}</textarea>`
      : `<input class="ed-v" ${v} value="${esc(r.v)}" placeholder="值">`;
    return `<div class="ed-frow">
      <input class="ed-k" data-obj="${obj}:${i}:k" value="${esc(r.k)}" placeholder="键">
      ${box}
      <button class="ed-x" data-eddel="${obj}:${i}" title="删掉这个字段">✕</button>
    </div>`;
  }).join('')
    + `<button class="ed-add" data-edaddrow="${obj}">＋ ${addLabel}</button>`;
}

/**
 * 一行「标签 + 输入框」。`geoKind` 给了就把它标成几何字段——
 * 拖动改的是**数值**、不改这里的文本，所以检视器得知道"这一格过期了"，
 * 好在它旁边写一句"拖过"（而不是悄悄显示一个旧值）。
 */
const edKV = (label, obj, val, geoKind) =>
  `<div class="kv"><span class="k">${esc(label)}</span>
     <input class="ed-in" data-obj="${obj}"
       ${geoKind ? `data-geo="${esc(geoKind)}" data-geoname="${esc(String(obj).split(':').pop())}"` : ''}
       value="${esc(val)}"></div>`;

function edPlaceHTML(f, p, i) {
  if (!p) return '<div class="v empty">左边选一个地块</div>';
  const geo = f.几何 || {};
  // **这一块是地点还是地区**：判据是 `类型` 里那几个词（`REGION_TYPES`，只在 Python
  // 一处），由 `/api/map/edit` 的 `regions` 告诉前端。没有这一行的话，一个**地点**
  // 的字段里根本没有 `类型`，看着就像"这属性丢了吧"。
  const known = !!(S.edit && S.edit.regionsKnown);  // 服务端到底给没给那张表
  const isReg = known && S.edit.regions.has(p.名);
  const tyVal = (p.字段 || []).find(x => x.k === '类型');
  const cur = tyVal ? String(tyVal.v).trim() : '';
  const types = S.edit.regionTypes || [];
  // **这一格是可编辑的**（地点 ↔ 地区直接在下拉里换）：它就是那份 `类型` 字段，
  // 选"地点"＝把字段删掉（缺省即地点，见 地图格式.md §9.1）。
  // 服务端没给词表（老服务端）时**只读**：这时下拉里只剩"地点"一项，
  // 让人选一下就把 `类型=域` 删掉了——那是**悄悄毁数据**，宁可不给编辑。
  let kindCtl;
  if (!types.length) {
    kindCtl = `<span class="v ${isReg ? 'jade' : ''}">${known ? (isReg ? '地区' : '地点')
      : (cur || '未标 `类型`')}</span>`;
  } else {
    // 括号里写的是**这份数据会变成什么**，不是让人猜的黑话："地点"＝**不写 `类型` 这一项**
    // （缺省即地点，见 地图格式.md §9.1）——不是写一个 `类型=地点` 出来。
    const opts = [`<option value=""${cur ? '' : ' selected'}>地点（不写类型）</option>`]
      .concat(types.map(v => `<option value="${esc(v)}"${cur === v ? ' selected' : ''}>地区 · ${esc(v)}</option>`));
    if (cur && !types.includes(cur)) {
      opts.unshift(`<option value="${esc(cur)}" selected>${esc(cur)}（不在词表里）</option>`);
    }
    kindCtl = `<select class="ed-in" data-edkind="${i}">${opts.join('')}</select>`;
  }
  const kindHint = !known
    ? '服务端还没重启——重启后这一格能直接改'
    : (isReg ? `写了「类型=${cur || '…'}」＝地区：只画虚线圈、不画点，也不当路段端点。`
             + '改成「地点（不写类型）」＝把那一项删掉，盘的大小回到它的 `范围`'
             : '不写「类型」这一项就是地点——画一个实心盘，盘的大小＝`范围`。'
             + '改成「地区 · 域/州/大陆…」＝写上一项 `类型`，它立刻变成只画圈、不画点');
  return `<div class="ed-title">地块 ${esc(p.名 || '(无名)')}</div>
    <div class="kv"><span class="k">类型</span>${kindCtl}
      <span class="ed-hint">${kindHint}</span></div>
    ${edKV('名', `地块:${i}:名`, p.名)}
    ${edKV('属于', `地块:${i}:属于`, p.属于)}
    <datalist id="edPlaces">${(f.地块 || []).map(x => `<option>${esc(x.名)}</option>`).join('')}</datalist>
    <div class="kv"><span class="k">说明</span>
      <textarea class="ed-in" rows="2" data-obj="地块:${i}:说明">${esc(p.说明)}</textarea></div>
    <div class="ed-sub">字段<span class="ed-hint">值的写法＝<code>.map</code> 语法：<code>[a,b]</code> 列表 · <code>是/否</code> 布尔 · 纯数字 · 其余文本</span></div>
    ${edFields(p.字段, `地块:${i}:字段`, '加字段')}
    <div class="ed-sub">几何<span class="ed-hint">只给前端画图；引擎侧一概不读（见 地图格式.md §0）</span></div>
    ${edKV('坐标', `几何:坐标:${p.名}`, (geo.坐标 || {})[p.名] || '', '坐标')}
    ${edKV('范围', `几何:范围:${p.名}`, (geo.范围 || {})[p.名] || '', '范围')}
    <div class="ed-sub">连着它的路<span class="ed-hint">点 ✕ 删掉那条连线；按钮或 Shift+点另一块地 连一条新的</span></div>
    ${edLinkedRoadsHTML(p.名)}
    <button class="ed-add" data-edlink="${esc(p.名)}">＋ 连一条路到…</button>
    <button class="ed-del" data-eddelent="地块:${i}"
      title="会一并清掉它的坐标/范围；连着它的路也会被删掉（保存时会列出来）"
      >删除这个地块</button>`;
}

/**
 * 「连着它的路」清单——**从地块这一侧**看它的所有连线，每条都能删。
 *
 * 为什么要有它：删除路本来只在「路」那个页签里够得着，可是你的念头总是
 * "这块地不该连到那儿去"，而不是"我要删掉编号 37 那条路"。从地块出发才顺手。
 */
function edLinkedRoadsHTML(name) {
  const rows = (S.edit.form.路 || []).map((r, i) => ({ r, i }))
    .filter(({ r }) => r.从 === name || r.到 === name);
  if (!rows.length) {
    return '<div class="v empty">一条都没有 —— 它是个孤岛（`去向` 解析不出，走不进去）</div>';
  }
  return rows.map(({ r, i }) => `<div class="ed-lrow">
      <span class="ed-lname">${esc(r.从)} — ${esc(r.到)}</span>
      <button class="ed-x" data-eddelent="路:${i}" title="删掉这条连线">✕</button>
    </div>`).join('');
}

function edRoadHTML(f, r, i) {
  if (!r) return '<div class="v empty">左边选一条路</div>';
  const geo = f.几何 || {};
  const segs = (r.段 || []).map((s, j) => `<div class="ed-seg">
      ${edKV('段名', `路:${i}:段:${j}:名`, s.名)}
      ${edKV('说明', `路:${i}:段:${j}:说明`, s.说明)}
      ${edFields(s.字段, `路:${i}:段:${j}:字段`, '加字段')}
      <button class="ed-x" data-eddel="路:${i}:段:${j}">✕ 删掉这一段</button>
    </div>`).join('');
  return `<div class="ed-title">路 ${esc(r.名 || '(无名)')}</div>
    ${edKV('名', `路:${i}:名`, r.名)}
    ${edKV('从', `路:${i}:从`, r.从)}
    ${edKV('到', `路:${i}:到`, r.到)}
    <div class="kv"><span class="k">双向</span>
      <select class="ed-in" data-obj="路:${i}:双向">
        <option${r.双向 !== '否' ? ' selected' : ''}>是</option>
        <option${r.双向 === '否' ? ' selected' : ''}>否</option>
      </select></div>
    <div class="kv"><span class="k">说明</span>
      <textarea class="ed-in" rows="2" data-obj="路:${i}:说明">${esc(r.说明 || '')}</textarea></div>
    <div class="ed-sub">字段</div>
    ${edFields(r.字段, `路:${i}:字段`, '加字段')}
    <div class="ed-sub">段<span class="ed-hint">路内部的地形分段，只穿过、不能停。落到第几成可以写 <code>区间=[0.3,0.8]</code>（不写就按段序等分）</span></div>
    ${segs}<button class="ed-add" data-edaddseg="路:${i}">＋ 加一段</button>
    <div class="ed-sub">几何<span class="ed-hint">两端是地块本身（拖地块会带着走），这里只管中间的弯</span></div>
    ${edKV('路径线', `几何:路径线:${r.名}`, (geo.路径线 || {})[r.名] || '', '路径线')}
    <button class="ed-add" data-edbend="路:${i}">＋ 加一个弯</button>
    <button class="ed-del" data-eddelent="路:${i}">删除这条路</button>`;
}

function edGlobalHTML(f) {
  const w = f.世界 || {};
  return `<div class="ed-title">世界</div>
    ${edKV('世界名', '世界:名', w.名)}
    ${edKV('开始时间', '世界:游戏开始时间', w.游戏开始时间)}
    ${edKV('时间格式', '世界:时间格式', w.时间格式)}
    <div class="ed-sub">配置<span class="ed-hint">世界级开关。键名由代码读，改名前先在代码里搜一遍</span></div>
    ${edFields(f.配置, '配置', '加配置项')}`;
}

/** `a:0:字段:2:v` → 按路径写回。数组用下标（JS 里 `arr['0']` 也是合法的）。 */
function setByPath(root, path, val) {
  const segs = path.split(':');
  let o = root;
  for (let i = 0; i < segs.length - 1; i++) o = o[segs[i]];
  o[segs[segs.length - 1]] = val;
}

document.addEventListener('input', e => {
  const t = e.target;
  if (!S.edit || !t.closest('#editor')) return;
  if (t.hasAttribute('data-edfilter')) { S.edit.filter = t.value; edRedraw(); return; }
  if (!t.dataset.obj) return;
  setByPath(S.edit.form, t.dataset.obj, t.value);
  markDirty();
  // **手改几何**也要让图跟上（不然"改完坐标图不动"，与幽灵线是同一个毛病）。
  // 落 `change` 不行：这几行常常是"改到一半先看看"。所以**停手 500ms 再算**，
  // 而且服务端在有一行还没写完时会拒答（图宁可旧一点，也不能少一块地）。
  // 名字框例外——半个名字会连带改一串引用，只能等失焦（见下面 `change`）。
  if (t.dataset.geo) {
    // ⚠️ **手改文本 = 作废这一格"拖过还没保存"的数字**——最后动手的那个说了算。
    // 不作废的话就是"改了没反应"：拖动把数值记进了 `S.edit.geo`，而它是**优先于文本**的
    // （预览与落盘都先应用它），于是你刚敲进去的那一串被拖出来的旧数字盖掉，
    // 图上一点都不动。用户报的"改了下曲线就不行了"就是这一条。
    edDropGeoOverride(t.dataset.geoname, t.dataset.geo);
    edPreviewSoon();
  }
});

/** 打字停顿后再重算图。定时器只留一个：连打不打折，停手才算一次。 */
let _edPreviewTimer = null;
function edPreviewSoon() {
  if (_edPreviewTimer) clearTimeout(_edPreviewTimer);
  _edPreviewTimer = setTimeout(() => { _edPreviewTimer = null; edPreviewMap(true); }, 500);
}

document.addEventListener('change', e => {
  const t = e.target;
  if (!S.edit || !t.closest('#editor')) return;

  // 「类型」那一格：**地点 ↔ 地区**直接换。它落到实处就是**字段里的 `类型`**
  // （选"地点"＝把那个字段删掉，缺省即地点），所以数据只有一处真源。
  // 换完要重算一次图（点 ↔ 圈）并重画检视器/列表——不然改了看不到。
  if (t.dataset.edkind !== undefined) {
    const p = (S.edit.form.地块 || [])[+t.dataset.edkind];
    if (!p) return;
    const rows = p.字段 || (p.字段 = []);
    const j = rows.findIndex(x => String(x.k || '').trim() === '类型');
    if (t.value) { if (j >= 0) rows[j].v = t.value; else rows.push({ k: '类型', v: t.value }); }
    else if (j >= 0) rows.splice(j, 1);
    if (S.edit.regionsKnown) {
      t.value ? S.edit.regions.add(p.名) : S.edit.regions.delete(p.名);
    }
    markDirty();
    edPreviewMap();
    drawEditor();
    return;
  }
  if (!t.dataset.obj) return;
  setByPath(S.edit.form, t.dataset.obj, t.value);
  markDirty();
  // **改名**要重算一次图：名字不只画在图上，它还是 `属于`、路的 `从/到`、
  // 几何的键——改一处得连着改一串引用，而那串只有服务端的 `cascade` 算得出来。
  // 用 `change`（失焦/回车）而不是 `input`：打字打到一半的名字没有意义，
  // 每敲一个字就请求一次也是白费。
  if (/^(地块|路):\d+:名$/.test(t.dataset.obj)) edPreviewMap();
});

// 编辑器是全屏浮层：自己处理点击并**止住冒泡**，否则主界面的
// 「点地块＝前往」会在选中实体时顺手发一轮"我往X去"。
document.addEventListener('click', e => {
  if (!S.edit || !e.target.closest('#editor')) return;
  e.stopPropagation();

  if (e.target.closest('[data-ed="close"]')) { closeEditor(); return; }
  if (e.target.closest('[data-ed="save"]')) { saveEditor(); return; }

  const tab = e.target.closest('[data-edtab]');
  if (tab) { S.edit.sel = { tab: tab.dataset.edtab, i: 0 }; S.edit.filter = ''; drawEditor(); return; }

  const sel = e.target.closest('[data-edsel]');
  if (sel) { edSelect(+sel.dataset.edsel); return; }

  // 地图上的地块：连线中 → 收尾；Shift+点 → 从当前选中连过去；否则＝选中。
  // 用 `mapPickPlace`（离指针最近的那块）而不是 `closest('[data-place]')`——
  // 命中圈互相盖住时"点 A 选中 B"，📍标记还压着一片点的都点不动（实测 7/41）。
  const pl = e.target.closest('[data-place]');
  const mbox = e.target.closest('#editor [data-mapbox]');
  const inSvg = !!(mbox && e.target.closest('svg'));
  const pk = inSvg ? mapPickPlace(mbox, e) : null;
  const rk = inSvg ? mapPickRoad(mbox, e) : null;
  // **谁近选谁，但"线"要基本压在指针上才赢得过地块**：
  // 地块的点与名字是眼睛盯着的那两个目标，而线是细的、又铺在地块下面。
  // 纯按距离判会出怪事——实测：苍云大陆的**名字**离它的点 10px，而一条从旁边
  // 穿过的曲线只离 7.7px，于是"点名字选中的是那条路"。所以：
  //   ① 指针就在某个**名字**上 → 那块地（名字属于它自己，没有歧义）；
  //   ② 指针**压在线上**（离线 ≤ `MAP_ONLINE_PX`）、**没压在那个点上**（离点 > `MAP_DOT_STICKY_PX`）
  //      且线确实更近 → 线；
  //   ③ 其余 → 最近的那块地。
  // 三个条件缺一不可，都是实测逼出来的：
  //   · 只要"在线上"：线的**端点就是地块的坐标**——点地块自己的点时线离指针 0px，
  //     于是 42 个点里有 11 个被"连着它的某条路"抢走；
  //   · 只要"线更近"：苍云大陆的**名字**离它的点 10px、而一条穿过的曲线只有 7.7px，
  //     点名字就选中了那条路；
  //   · 少了"没压在那个点上"：一条路从某块地旁边 5px 处经过时（指针在线上 3px），
  //     会因为地块的**热区**（当时恒定 16px）把它圈进去而选中那块地——你想点的是线。
  const onLabel = !!e.target.closest('text');
  // ⚠️ `!onLabel`：**点在名字上就是点那块地**，线抢不走。
  // 名字在节点**上方**十几像素处（`ly = y - 6/9 - 6`），常常正压在某条路上——
  // 而"路上"的判据只看指针离线的距离，不看这个字属于谁。不拦的话就会出现
  // "点 A 的名字，选中的却是穿过它的一条路"。
  const roadWins = !onLabel && !!(rk && rk.d <= MAP_ONLINE_PX
                      && (!pk || (pk.d > MAP_DOT_STICKY_PX && rk.d < pk.d)));
  const name = (onLabel && pl && pl.dataset.place) ? pl.dataset.place
             : (pk && !roadWins) ? pk.name : '';
  if (name) {
    if (S.edit.linkFrom) { edFinishLink(name); return; }
    if (e.shiftKey && S.edit.sel.tab === '地块') {
      const from = (S.edit.form.地块[S.edit.sel.i] || {}).名;
      if (from && from !== name) {
        S.edit.linkFrom = from;
        edFinishLink(name);
        return;
      }
    }
    const i = (S.edit.form.地块 || []).findIndex(p => p.名 === name);
    if (i >= 0) {
      // **地图上点地块＝把页签切回「地块」**。
      // `edSelect(i)` 只改 `i`、**不切页签**——在「路」页签下点地图上的地块，
      // 它会把 `路[i]` 当成你选中的东西：检视器显示**另一条路**、地图高亮的
      // 也是那条路的**曲线**。用户报的"我点的跟标记的不是一个"就是这个
      //（2026-10-10 复现：选中一条路拖完曲线，再点任意地块，全变成别的路）。
      S.edit.sel = { tab: '地块', i };
      drawEditor();
    }
    return;
  }

  // 点地图上的**线**＝选中那条路（然后就能拖它的控制点）
  const rl = e.target.closest('[data-road]');
  const roadName = (rl && rl.dataset.road) || (rk && rk.name) || '';
  if (roadName) {
    const i = (S.edit.form.路 || []).findIndex(r0 => r0.名 === roadName);
    if (i >= 0) {
      S.edit.sel = { tab: '路', i };
      drawEditor();
    }
    return;
  }

  const arow = e.target.closest('[data-edaddrow]');
  if (arow) {
    const path = arow.dataset.edaddrow;
    const rows = byPath(S.edit.form, path);
    rows.push({ k: '', v: '' });
    markDirty();
    edRedraw(`${path}:${rows.length - 1}:k`);      // 焦点落到新行的「键」上
    return;
  }
  const aseg = e.target.closest('[data-edaddseg]');
  if (aseg) {
    const path = aseg.dataset.edaddseg;
    const segs = byPath(S.edit.form, path).段;
    segs.push({ 名: '', 说明: '', 字段: [] });
    markDirty();
    edRedraw(`${path}:段:${segs.length - 1}:名`);
    return;
  }
  const del = e.target.closest('[data-eddel]');
  if (del) {
    const segs = del.dataset.eddel.split(':');
    const key = +segs.pop();
    const path = segs.join(':');
    const arr = byPath(S.edit.form, path);
    arr.splice(key, 1);
    markDirty();
    // 删的是**几何**（`坐标 X` / `路径线 Y`）时，地图跟着变——它就是把那两样画出来的。
    if (path.startsWith('几何:')) { drawEditor(); edPreviewMap(); return; }
    // 删完把焦点放到相邻那一行（没有行就交回给页面）——
    // 否则焦点掉到 body，接着敲键盘会滚页
    const at = Math.min(key, arr.length - 1);
    const isSeg = path.split(':').pop() === '段';    // `…:段:1` 删的是一整段
    edRedraw(arr.length && at >= 0 ? `${path}:${at}:${isSeg ? '名' : 'k'}` : null);
    return;
  }
  const delent = e.target.closest('[data-eddelent]');
  if (delent) {
    const [tab, i] = delent.dataset.eddelent.split(':');
    const arr = S.edit.form[tab];
    arr.splice(+i, 1);
    // **只在自己那个页签里才动选中**。不然"在地块页签里删掉一条连线"
    // 会把你从 韩家村 甩到 苍云大陆（它确实把 sel.i 归零了），观感像丢了东西。
    if (S.edit.sel.tab === tab) {
      S.edit.sel.i = Math.min(S.edit.sel.i, Math.max(0, arr.length - 1));
    }
    // 删掉的是**画在地图上的东西**（地块或路）→ 图必须跟着变。
    // 尤其"删地块会连带删掉连着它的路"，那条规则在服务端（`cascade`）。
    markDirty(); drawEditor(); edPreviewMap(); return;
  }

  const nw = e.target.closest('[data-ednew]');
  if (nw) { edCreate(nw.dataset.ednew); return; }

  const lk = e.target.closest('[data-edlink]');
  if (lk) { edStartLink(lk.dataset.edlink); return; }

  const bd = e.target.closest('[data-edbend]');
  if (bd) { edAddBend(+bd.dataset.edbend.split(':')[1]); return; }

  // 落到这里 ＝ 点在编辑器里、但没点中任何东西。**只有真的点在图上**才算"点空白"——
  // 侧栏的空白（列表背景、检视器里的空隙）不该清选中，那是手滑不是意图。
  // 没有这一步：选中一个就再也退不出来（点哪儿都保持选中）。
  if (inSvg && S.edit.sel.i >= 0) {
    // 只清下标、**留着页签**：列表照常显示，只是没哪一行是 `on`。
    // `edHighlight` / `edPlaceHTML` / `edRoadHTML` 都已经吃得住 `i = -1`。
    S.edit.sel = { tab: S.edit.sel.tab, i: -1 };
    drawEditor();
  }
});

/**
 * 给一条路**加一个弯**（在最长的那一段中间插一个点，往垂直于该段的方向推一点）。
 *
 * 为什么需要它：两点的那种直线路**没有任何自由控制点**（两端是地块），
 * 所以它无从"拉"——只能先插一个点出来。插完那个点就能拖了。
 * 结果写进 `S.edit.geo.路径线`（数值），文本仍由服务端生成。
 */
function edAddBend(i) {
  const f = S.edit.form;
  const road = (f.路 || [])[i];
  if (!road || !S.edit) return;
  const m = S.edit.view || {};
  const vr = (m.roads || []).find(r => r.name === road.名);
  const line = (vr && vr.line ? vr.line : []).map(q => [q[0], q[1]]);
  if (line.length < 2) {
    edMark([{ t: '这条路还没有端点，先选两个地块连一下', cls: 'bad' }]);
    return;
  }
  // 插在**最长的那一段**中间（两点时就是那唯一一段）
  let seg = 0, best = -1;
  for (let k = 0; k < line.length - 1; k++) {
    const len = Math.hypot(line[k + 1][0] - line[k][0], line[k + 1][1] - line[k][1]);
    if (len > best) { best = len; seg = k; }
  }
  const a = line[seg], b = line[seg + 1];
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
  // 往垂直于这一段的方向推 18%，好让新点**看得出**偏离了原线
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const nx = -dy, ny = dx;
  const nl = Math.hypot(nx, ny) || 1;
  const push = Math.hypot(dx, dy) * 0.18;
  line.splice(seg + 1, 0, [+(mx + nx / nl * push).toFixed(4),
                           +(my + ny / nl * push).toFixed(4)]);
  S.edit.geo.路径线[road.名] = line;
  if (vr) vr.line = line;
  edGeoTouched(`路径线:${road.名}`);
  markDirty();
  drawEditor();
  edMark([{ t: `给「${road.名}」加了一个弯 —— 拖动地图上那个金色控制点来调`,
            cls: 'good' }]);
}

/**
 * 连线：把"从哪儿"记下来，等下一次点地图上的**另一块地**。
 *
 * 为什么不做成"选一条路再填两个端点"：作者脑子里的动作是**在图上把两点连起来**，
 * 不是填一张表单。按钮和 Shift+点 是同一个机制的两个入口。
 */
function edStartLink(from) {
  if (!from) {
    edMark([{ t: '先在左边选一个地块，再点「连一条路到…」', cls: '' }]);
    return;
  }
  S.edit.linkFrom = from;
  edMark([{ t: `连线中：从「${from}」出发 —— 现在点地图上**另一块地**（Esc 取消）`,
            cls: 'good' }]);
}

/** 收尾连线。返回是否真的建了一条。 */
function edFinishLink(to) {
  const from = S.edit.linkFrom;
  S.edit.linkFrom = null;
  if (!from || !to) return false;
  if (from === to) {
    edMark([{ t: '两头是同一块地，连不起来', cls: 'bad' }]);
    return false;
  }
  const f = S.edit.form;
  const dup = (f.路 || []).some(r =>
    (r.从 === from && r.到 === to)
    || (String(r.双向) !== '否' && r.从 === to && r.到 === from));
  if (dup) {
    edMark([{ t: `「${from}」和「${to}」之间已经有一条路了`, cls: 'bad' }]);
    return false;
  }
  // 名字按惯例取「从-到」：这样地块改名时，`cascade` 能把它一起改掉
  // （`detect_renames` 只认这种拼出来的名字）
  const name = `${from}-${to}`;
  const v = mapViewOf(S.edit.view);
  const ca = S.edit.view.places.find(x => x.name === from) || { x: v.x + v.w / 2, y: v.y + v.h / 2 };
  const cb = S.edit.view.places.find(x => x.name === to) || ca;
  (f.路 || (f.路 = [])).push({ 名: name, 从: from, 到: to, 双向: '是', 字段: [], 段: [] });
  const pts = [[ca.x, ca.y], [cb.x, cb.y]];
  S.edit.geo.路径线[name] = pts;
  f.几何.路径线[name] = `[(${ca.x.toFixed(1)},${ca.y.toFixed(1)}),(${cb.x.toFixed(1)},${cb.y.toFixed(1)})]`;
  S.edit.view.roads.push({ name, from, to, line: pts });
  S.edit.sel = { tab: '路', i: f.路.length - 1 };
  markDirty();
  drawEditor();
  edMark([{ t: `连了一条路：${from} — ${to}（名字按惯例取「从-到」）`, cls: 'good' }]);
  return true;
}

/**
 * 新建实体（阶段 3）。
 *
 * 关键是**顺手把几何补上**，否则新建的东西是个"看不见也站不住"的幽灵：
 *   - 地块没有 `坐标` → `map_view` 只画有坐标的地块，它根本不出现；
 *   - 地块没有 `范围` → `locate()` 永远判不到它，站上去也只会是"在路上"；
 *   - 路没有 `路径线` → 画不出来（而且校验器会拦"少于两个点"）。
 *
 * 名字给一个**明显是占位**的（`新地块1`），免得手滑就存进世界了。
 */
function edCreate(tab) {
  const f = S.edit.form;
  const v = mapViewOf(S.edit.view);
  const cx = v.x + v.w / 2, cy = v.y + v.h / 2;
  if (tab === '地块') {
    const used = new Set((f.地块 || []).map(p => p.名));
    let n = 1; while (used.has(`新地块${n}`)) n++;
    const name = `新地块${n}`;
    (f.地块 || (f.地块 = [])).push({
      名: name, 属于: '', 说明: '', 字段: [],
    });
    // 落在视野中心，并给一个 16 的圆——`locate()` 靠它认人
    S.edit.geo.坐标[name] = [+cx.toFixed(4), +cy.toFixed(4)];
    f.几何.坐标[name] = `(${cx.toFixed(1)},${cy.toFixed(1)})`;
    f.几何.范围[name] = '圆(16)';
    S.edit.view.places.push({ name, x: cx, y: cy, r: 16, leaf: true,
                              path: name, desc: '', attrs: {} });
    S.edit.sel = { tab: '地块', i: f.地块.length - 1 };
  } else {
    const used = new Set((f.路 || []).map(r => r.名));
    let n = 1; while (used.has(`新路${n}`)) n++;
    const name = `新路${n}`;
    const a = (f.地块[0] || {}).名 || '', b = (f.地块[1] || {}).名 || '';
    (f.路 || (f.路 = [])).push({
      名: name, 从: a, 到: b, 双向: '是', 字段: [], 段: [],
    });
    // 先给一条**看得见**的直线（两端落在两个地块上）；要弯自己拖控制点
    const ca = (S.edit.view.places.find(p => p.name === a) || { x: cx, y: cy });
    const cb = (S.edit.view.places.find(p => p.name === b) || { x: cx, y: cy });
    const pts = [[ca.x, ca.y], [cb.x, cb.y]];
    S.edit.geo.路径线[name] = pts;
    f.几何.路径线[name] = `[(${ca.x.toFixed(1)},${ca.y.toFixed(1)}),(${cb.x.toFixed(1)},${cb.y.toFixed(1)})]`;
    S.edit.view.roads.push({ name, from: a, to: b, line: pts });
    S.edit.sel = { tab: '路', i: f.路.length - 1 };
  }
  markDirty();
  drawEditor();
  edMark([{ t: `新建了「${tab === '地块' ? '新地块' : '新路'}」，名字是占位的——先改掉再保存`,
            cls: '' }]);
}

function byPath(root, path) {
  return path.split(':').reduce((o, k) => o[k], root);
}

async function saveEditor() {
  const btn = document.querySelector('[data-ed="save"]');
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  try {
    const r = await post('/api/map/save', { form: S.edit.form, geo: S.edit.geo });
    if (!r.ok) {
      edMark([...(r.cascade || []).map(t => '顺带：' + t),
              '保存被拒（' + (r.stage || '未知') + '）：',
              ...(r.problems || [])], 'bad');
    } else {
      const b = r.backup ? `备份 ${r.backup}` : '原来没有文件，是新建的';
      // **不再说"已保存并热重载"**——那是一句把三种情况说成一种的话。
      // 有些键立刻生效，有些要重启，有些对已经生成的数据根本无效。
      const rl = r.reloaded || {};
      const msgs = [{ t: `已写入 ${r.lines} 行 / ${r.bytes} 字节　·　${b}`, cls: 'good' }];
      for (const t of (r.cascade || [])) msgs.push({ t: '顺带改的：' + t, cls: '' });
      if ((rl['立即生效'] || []).length) {
        msgs.push({ t: '立即生效：' + rl['立即生效'].join('、'), cls: 'good' });
      }
      // 这条只在真改了延后键时才出现——平时不拿它刷屏
      if ((rl['需重启'] || []).length) {
        msgs.push({ t: '⚠ 要重启才生效：' + rl['需重启'].join('、'), cls: 'bad' });
      }
      // 这条是三分类里最容易被忽略的一类，所以**每次都写**：
      // "保存成功"不等于"世界已经按新数据重新长过一遍"。
      msgs.push({ t: '注：改 `产出` / 改地段属性，不会重算场上 NPC 已有的记忆与观察',
                  cls: '' });
      edMark(msgs);
      S.edit.dirty = false;
      // 重新拉一次表单：服务端会按盘上顺序对齐（`canonicalize`），
      // 不回读的话编辑器显示的顺序会和文件里的越差越远
      const fresh = await api('/api/map/edit');
      if (fresh.ok) {
        S.edit.form = fresh.form; S.edit.view = fresh.view;
        S.edit.regions = new Set(fresh.regions || []);
        S.edit.regionsKnown = Array.isArray(fresh.regions);
        S.edit.regionTypes = Array.isArray(fresh.region_types) ? fresh.region_types : [];
        // 拖出来的数字已经变成文件里的文本了，清掉待办与"过期"标记
        S.edit.geo = { 坐标: {}, 路径线: {} };
        S.edit.geoTouched = new Set();
      }
      drawEditor();
      await loadMap();
      renderPanelsOnly();
    }
  } catch (e) {
    edMark(['保存失败：' + e.message], 'bad');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '保存'; }
  }
}

document.addEventListener('keydown', e => {
  if (!S.edit) return;
  if (e.key === 'Escape') {
    // 连线中途按 Esc = 取消连线（先别把整个编辑器关了）
    if (S.edit.linkFrom) {
      S.edit.linkFrom = null;
      edMark([{ t: '已取消连线', cls: '' }]);
      e.stopPropagation();
      return;
    }
    closeEditor();
    e.stopPropagation();
  }
});

// ══════════════════════════════════════════════════════════════
// 启动
// ══════════════════════════════════════════════════════════════

(async function init() {
  loadRailWidth();
  applyRailWidth();

  await refreshState();
  // 先把历史拉回来**再**接 SSE —— 反过来的话，接流期间到达的事件会排在历史前面
  await Promise.all([loadMap(), loadTree(), loadNpcs(), loadHistory()]);
  for (const w of ['event', 'director', 'global']) loadCtx(w);
  connectSSE();

  // 状态兜底轮询：SSE 断了也不至于一直显示旧数据
  setInterval(() => { if (!S.busy) refreshState(); }, 15000);

  // 窗口尺寸变了，"一个标签占多少像素"就变了 → 标签密度得重算
  addEventListener('resize', applyMapView);
  // Escape 关掉全屏看图与存档弹窗（编辑器那个 Escape 在上面自己处理了）
  addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeMapModal(); closeSavesDialog(); }
  });
})();
