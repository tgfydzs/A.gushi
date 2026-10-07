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
  tree: null,          // /api/locations 的结果
  npcs: [],
  selNpc: null,        // 选中查看内部表的 NPC
  npcDetail: null,
  busy: false,
  timing: [],          // 本轮的层耗时 [{name, seconds}]
  ctxCache: {},        // which -> {count, lines}
  expanded: new Set(), // 地点树展开的节点 path
  collapsedPanels: new Set(),
  pendingMoveRaw: null,// need_move 时暂存的原文
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

function panelMap() {
  if (!S.tree) return '<div class="v empty">加载中…</div>';
  const onPath = onPathSet();
  const cur = S.tree.current_path || '';
  let h = '';
  if (!S.tree.in_tree && cur) {
    h += `<div class="kv"><span class="k">当前</span><span class="v jade">${esc(cur)}</span></div>
          <div class="v empty" style="margin-bottom:6px">（自由地点，不在预设树中）</div>`;
  }
  h += `<div class="tree">${renderTree(S.tree.tree, onPath, null, 0)}</div>`;
  h += `<div class="v empty" style="margin-top:8px">点任一节点即前往</div>`;
  return h;
}

// ══════════════════════════════════════════════════════════════
// 布局刷新
// ══════════════════════════════════════════════════════════════

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
  $('railRight').innerHTML = right.map(panelHTML).join('');
}

function renderPanelsOnly() {
  for (const p of PANELS) {
    if (!S.visible.has(p.id)) continue;
    const el = document.querySelector(`[data-panel="${p.id}"] .panel-body`);
    if (el) el.innerHTML = p.render();
  }
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
    if (!S.streamCount) clearStreamOnFirstRound();
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
    if (r.action === 'need_move') {
      // 输入里有 {移动}：先让他选目的地，再把路径填回原文
      S.pendingMoveRaw = r.raw;
      openMove(r.raw);
    } else if (r.action === 'busy') {
      pushStream('上一轮还在跑，稍等。', 'e-dim');
    }
  } catch (e) {
    pushStream('发送失败：' + e.message, 'e-err');
  }
}

async function goto(path, raw) {
  try {
    await post('/api/move', { path, raw: raw || '' });
    closeMove();
  } catch (e) {
    pushStream('移动失败：' + e.message, 'e-err');
  }
}

// ══════════════════════════════════════════════════════════════
// 移动浮层
// ══════════════════════════════════════════════════════════════

function openMove(raw) {
  S.pendingMoveRaw = raw || S.pendingMoveRaw;
  $('moveModal').classList.remove('hidden');
  $('moveFilter').value = '';
  drawMoveTree('');
  $('moveFilter').focus();
}

function closeMove() {
  $('moveModal').classList.add('hidden');
  S.pendingMoveRaw = null;
}

function drawMoveTree(filter) {
  if (!S.tree) return;
  const onPath = onPathSet();
  $('moveTree').innerHTML =
    `<div class="tree">${renderTree(S.tree.tree, onPath, filter || null, 0)}</div>`;
}

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

  // 地点树点击移动（侧栏里的）
  const row = t.closest('.trow');
  if (row && !row.closest('#moveTree')) {
    goto(row.dataset.path, '');
    return;
  }
});

// 浮层里的树走另一套（选完要填回原文）
$('moveTree').addEventListener('click', e => {
  const tw = e.target.closest('[data-toggle]');
  if (tw) {
    const p = tw.dataset.toggle;
    S.expanded.has(p) ? S.expanded.delete(p) : S.expanded.add(p);
    drawMoveTree($('moveFilter').value.trim());
    return;
  }
  const row = e.target.closest('.trow');
  if (row) goto(row.dataset.path, S.pendingMoveRaw);
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
$('hLoc').onclick = () => openMove(null);
$('moveCancel').onclick = closeMove;

$('moveFilter').addEventListener('input', e => drawMoveTree(e.target.value.trim()));

$('btnUndo').onclick = async () => {
  try { await post('/api/undo'); refreshSoon(0); }
  catch (e) { pushStream('撤回失败：' + e.message, 'e-err'); }
};

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeMove(); $('panelDrawer').classList.add('hidden'); }
});

// ══════════════════════════════════════════════════════════════
// 启动
// ══════════════════════════════════════════════════════════════

(async function init() {
  await refreshState();
  // 先把历史拉回来**再**接 SSE —— 反过来的话，接流期间到达的事件会排在历史前面
  await Promise.all([loadTree(), loadNpcs(), loadHistory()]);
  for (const w of ['event', 'director', 'global']) loadCtx(w);
  connectSSE();

  // 状态兜底轮询：SSE 断了也不至于一直显示旧数据
  setInterval(() => { if (!S.busy) refreshState(); }, 15000);
})();
