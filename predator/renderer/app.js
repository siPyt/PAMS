'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const MAX_HISTORY = 120; // ~10 min at 5s cadence
const STALE_MS = 20000; // grey out a unit after 20s of silence

const state = {
  units: new Map(), // unit_id -> { id, last, history: [{ t, temp, health }] }
  status: { state: 'searching', hosts: ['alpha-p.local'], activeHost: null }
};

// Client-side demo generator: populates the UI with simulated freezer units
// when there is no live PAMS data. Fully offline, touches nothing on the Pi.
const demo = {
  on: false,
  timer: null,
  seeds: [
    { id: 'FRZ-01', temp: -20.5 },
    { id: 'FRZ-02', temp: -19.2 },
    { id: 'FRZ-03', temp: -21.8 },
    { id: 'FRZ-04', temp: -17.4 }
  ]
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);

function num(v) {
  return typeof v === 'number' && !Number.isNaN(v) ? v : null;
}

function fmt(v, d = 1) {
  return v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d);
}

function ago(ms) {
  if (!ms) return '—';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`;
}

function healthClass(h) {
  if (h == null) return 'muted';
  if (h >= 80) return 'ok';
  if (h >= 50) return 'warn';
  return 'crit';
}

function healthColor(h) {
  if (h == null) return 'var(--text-mut)';
  if (h >= 80) return 'var(--ok)';
  if (h >= 50) return 'var(--warn)';
  return 'var(--crit)';
}

function unitKey(topic, data) {
  return data.unit_id || (topic ? topic.split('/').pop() : null);
}

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------
function upsert(msg) {
  const { topic, data, at } = msg;
  const id = unitKey(topic, data);
  if (!id) return;

  let u = state.units.get(id);
  if (!u) {
    u = { id, last: {}, history: [] };
    state.units.set(id, u);
  }

  // Scored stream is richer; raw stream supplies temperature/door. Merge both.
  u.last = { ...u.last, ...data, _topic: topic, _at: at };

  const temp = num(data.temperature) ?? num(u.last.temperature);
  const health = num(data.health_score) ?? num(u.last.health_score);
  const ts = data.ts ? data.ts * 1000 : at;

  if (temp != null) {
    const tail = u.history[u.history.length - 1];
    if (!tail || ts - tail.t > 500) {
      u.history.push({ t: ts, temp, health });
      if (u.history.length > MAX_HISTORY) u.history.shift();
    }
  }

  refreshData();
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderStatus() {
  const el = $('#stat');
  if (demo.on) {
    el.className = 'status demo';
    el.querySelector('.status-text').textContent = 'demo mode · simulated';
    el.title = 'Simulated data — not live';
    return;
  }
  const s = state.status || {};
  // 'searching' shares the amber pulsing look with 'connecting'.
  const cls = s.state === 'searching' ? 'connecting' : s.state || '';
  el.className = `status ${cls}`;

  const active = s.activeHost || '';
  const list = Array.isArray(s.hosts) ? s.hosts.join(', ') : '';
  const label =
    {
      connected: `connected · ${active}`,
      connecting: `connecting · ${active}…`,
      searching: `searching USB / Ethernet…`,
      disconnected: `disconnected`,
      error: `error`
    }[s.state] || 'searching…';
  el.querySelector('.status-text').textContent = label;
  el.title = list ? `Endpoints: ${list}` : '';

  const emptyMsg = $('#emptyMsg');
  if (s.state === 'connected') {
    emptyMsg.innerHTML = `Connected to <b>${active}</b> — waiting for units to report.`;
  } else {
    emptyMsg.innerHTML = `Searching for the PAMS host over USB / Ethernet…<br /><span class="empty-hint">${list}</span>`;
  }
}

function renderSummary(units) {
  const summary = $('#summary');
  if (units.length === 0) {
    summary.classList.add('hidden');
    return;
  }
  summary.classList.remove('hidden');

  let ok = 0;
  let warn = 0;
  let crit = 0;
  let anom = 0;
  for (const u of units) {
    const h = num(u.last.health_score);
    if (h != null) {
      if (h >= 80) ok += 1;
      else if (h >= 50) warn += 1;
      else crit += 1;
    }
    if (u.last.anomaly) anom += 1;
  }
  $('#sumUnits').textContent = String(units.length);
  $('#sumOk').textContent = String(ok);
  $('#sumWarn').textContent = String(warn);
  $('#sumCrit').textContent = String(crit);
  $('#sumAnom').textContent = String(anom);
}

function cardTemplate(u) {
  const L = u.last;
  const health = num(L.health_score);
  const temp = num(L.temperature);
  const stale = Date.now() - (L._at || 0) > STALE_MS;
  const doorOpen = Number(L.door_status) === 1;
  const anomaly = !!L.anomaly;
  const training = Number(L.training) === 1;
  const rul = num(L.rul_days);
  const tv = num(L.thermal_velocity);
  const stateName = L.inferred_state || '—';
  const hp = health == null ? 0 : Math.max(0, Math.min(100, health));

  const card = document.createElement('div');
  card.className = `card${anomaly || (health != null && health < 50) ? ' alarm' : ''}${
    stale ? ' stale' : ''
  }`;
  card.dataset.id = u.id;

  card.innerHTML = `
    <div class="card-head">
      <div class="unit-id">${u.id}</div>
      <div class="badges">
        ${training ? '<span class="badge train">training</span>' : ''}
        ${anomaly ? '<span class="badge anom">anomaly</span>' : ''}
      </div>
    </div>
    <div class="card-main">
      <div class="temp">${fmt(temp, 1)}<span class="u">°C</span></div>
      <div class="health-ring" style="--p:${hp};--c:${healthColor(health)}">
        <span class="health-val ${healthClass(health)}">${health == null ? '—' : Math.round(health)}</span>
      </div>
    </div>
    <canvas class="spark" width="600" height="92"></canvas>
    <div class="metrics">
      <div class="metric">
        <div class="m-lbl">RUL</div>
        <div class="m-val">${rul == null ? '—' : `${fmt(rul, 0)}d`}</div>
      </div>
      <div class="metric">
        <div class="m-lbl">Therm vel</div>
        <div class="m-val">${fmt(tv, 2)}</div>
      </div>
      <div class="metric">
        <div class="m-lbl">State</div>
        <div class="m-val" style="font-size:13px">${stateName}</div>
      </div>
    </div>
    <div class="card-foot">
      <span class="pill ${doorOpen ? 'door-open' : 'door-closed'}">
        Door ${doorOpen ? 'OPEN' : 'closed'}
      </span>
      <span>${ago(L._at)}</span>
    </div>
  `;
  return card;
}

function drawSpark(canvas, history) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!history || history.length < 2) return;

  const temps = history.map((p) => p.temp);
  let min = Math.min(...temps);
  let max = Math.max(...temps);
  if (max - min < 0.5) {
    min -= 0.5;
    max += 0.5;
  }
  const pad = 6;
  const xStep = (w - pad * 2) / (history.length - 1);
  const yScale = (v) => h - pad - ((v - min) / (max - min)) * (h - pad * 2);

  // area fill
  ctx.beginPath();
  history.forEach((p, i) => {
    const x = pad + i * xStep;
    const y = yScale(p.temp);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  const lastX = pad + (history.length - 1) * xStep;
  ctx.lineTo(lastX, h - pad);
  ctx.lineTo(pad, h - pad);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, 'rgba(51, 201, 214, 0.28)');
  grad.addColorStop(1, 'rgba(51, 201, 214, 0.0)');
  ctx.fillStyle = grad;
  ctx.fill();

  // line
  ctx.beginPath();
  history.forEach((p, i) => {
    const x = pad + i * xStep;
    const y = yScale(p.temp);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#33c9d6';
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // last point
  const ly = yScale(history[history.length - 1].temp);
  ctx.beginPath();
  ctx.arc(lastX, ly, 3, 0, Math.PI * 2);
  ctx.fillStyle = '#eafcff';
  ctx.fill();
}

function renderDashboard() {
  const units = [...state.units.values()].sort((a, b) => a.id.localeCompare(b.id));
  const grid = $('#grid');
  const empty = $('#empty');

  renderSummary(units);

  if (units.length === 0) {
    grid.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  grid.innerHTML = '';
  for (const u of units) {
    const card = cardTemplate(u);
    grid.appendChild(card);
    drawSpark(card.querySelector('.spark'), u.history);
  }
}

// ---------------------------------------------------------------------------
// Navigation (offline-browsable views, like a project tree)
// ---------------------------------------------------------------------------
let activeView = 'dashboard';

function navigate(view) {
  activeView = view;
  document.querySelectorAll('.nav-item').forEach((b) =>
    b.classList.toggle('active', b.dataset.view === view)
  );
  document.querySelectorAll('.view').forEach((v) =>
    v.classList.toggle('hidden', v.dataset.view !== view)
  );
  if (view === 'settings') fillSettings();
  renderActive();
  if (view === 'connections') doProbe();
}

function renderActive() {
  switch (activeView) {
    case 'dashboard':
      return renderDashboard();
    case 'trends':
      return renderTrends();
    case 'health':
      return renderHealth();
    case 'devices':
      return renderDevices();
    case 'points':
      return renderPoints();
    case 'services':
      return renderServices();
    case 'connections':
      return renderConnections();
    case 'help':
      return renderHelp();
    default:
      return;
  }
}

// Full re-render of whatever view is active.
function render() {
  renderActive();
}

// Lighter refresh for live ticks — only re-render data-driven views so we never
// clobber static views (docs, tables) on every message or second.
function refreshData() {
  if (activeView === 'dashboard' || activeView === 'trends' || activeView === 'health') {
    renderActive();
  }
}

function firstUnit() {
  const it = state.units.values().next();
  return it.done ? null : it.value;
}

// ---------------------------------------------------------------------------
// Health / ML view
// ---------------------------------------------------------------------------
function mlRow(label, val, cls = '') {
  return `<div class="ml-row"><span>${label}</span><b class="${cls}">${val}</b></div>`;
}

function renderHealth() {
  const units = [...state.units.values()].sort((a, b) => a.id.localeCompare(b.id));
  const grid = $('#healthGrid');
  const empty = $('#healthEmpty');
  if (!units.length) {
    grid.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  grid.innerHTML = '';
  for (const u of units) {
    const L = u.last;
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="card-head">
        <div class="unit-id">${u.id}</div>
        <div class="badges">
          ${Number(L.training) === 1 ? '<span class="badge train">training</span>' : ''}
          ${L.anomaly ? '<span class="badge anom">anomaly</span>' : ''}
        </div>
      </div>
      <div class="ml-rows">
        ${mlRow('Combined health', fmt(L.health_score, 0), healthClass(num(L.health_score)))}
        ${mlRow('Ensemble', fmt(L.ensemble_health, 0))}
        ${mlRow('IsolationForest', fmt(L.if_health, 0))}
        ${mlRow('HMM', fmt(L.hmm_health, 0))}
        ${mlRow('LSTM', fmt(L.lstm_health, 0))}
        ${mlRow('RUL', L.rul_days == null ? '—' : fmt(L.rul_days, 0) + ' d')}
        ${mlRow('Thermal velocity', fmt(L.thermal_velocity, 2))}
        ${mlRow('Inferred state', L.inferred_state || '—')}
        ${mlRow('Samples', L.n_points == null ? '—' : String(L.n_points))}
      </div>`;
    grid.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// Trends view
// ---------------------------------------------------------------------------
let trendUnit = null;

function renderTrends() {
  const panel = $('#trendsPanel');
  const units = [...state.units.values()].sort((a, b) => a.id.localeCompare(b.id));
  if (!units.length) {
    panel.innerHTML =
      '<div class="preview-note big">No data yet. Turn on <b>Demo</b> (top-right) to preview live trends.</div>';
    return;
  }
  if (!trendUnit || !state.units.has(trendUnit)) trendUnit = units[0].id;
  panel.innerHTML = `
    <div class="trend-bar">
      <label>Unit
        <select id="trendSel">
          ${units.map((u) => `<option ${u.id === trendUnit ? 'selected' : ''}>${u.id}</option>`).join('')}
        </select>
      </label>
      <span class="legend"><i class="lg temp"></i>Temperature (°C)</span>
      <span class="legend"><i class="lg health"></i>Health</span>
    </div>
    <canvas id="trendCanvas" width="1000" height="320"></canvas>`;
  $('#trendSel').addEventListener('change', (e) => {
    trendUnit = e.target.value;
    renderTrends();
  });
  drawTrend($('#trendCanvas'), state.units.get(trendUnit));
}

function drawSeries(ctx, hist, yFn, xStep, pad, color, on) {
  if (!on) return;
  ctx.beginPath();
  hist.forEach((p, i) => {
    const x = pad + i * xStep;
    const y = yFn(p);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.stroke();
}

function drawTrend(canvas, u) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const hist = u.history || [];
  if (hist.length < 2) return;
  const pad = 40;
  const temps = hist.map((p) => p.temp);
  let tmin = Math.min(...temps);
  let tmax = Math.max(...temps);
  if (tmax - tmin < 1) {
    tmin -= 1;
    tmax += 1;
  }
  const xStep = (w - pad * 2) / (hist.length - 1);
  const yT = (v) => h - pad - ((v - tmin) / (tmax - tmin)) * (h - pad * 2);
  const yH = (v) => h - pad - (v / 100) * (h - pad * 2);

  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad + i * ((h - pad * 2) / 4);
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(w - pad, y);
    ctx.stroke();
  }
  drawSeries(ctx, hist, (p) => yH(p.health == null ? 0 : p.health), xStep, pad, '#35d07f',
    hist.some((p) => p.health != null));
  drawSeries(ctx, hist, (p) => yT(p.temp), xStep, pad, '#33c9d6', true);

  ctx.fillStyle = '#7d8ea0';
  ctx.font = '11px Segoe UI';
  ctx.fillText(`${tmax.toFixed(1)}°C`, 4, pad + 4);
  ctx.fillText(`${tmin.toFixed(1)}°C`, 4, h - pad + 4);
  ctx.fillText('100', w - pad + 6, pad + 4);
  ctx.fillText('0', w - pad + 6, h - pad + 4);
}

// ---------------------------------------------------------------------------
// Devices + Points views (BACnet explorer / configurator preview)
// ---------------------------------------------------------------------------
const demoDevices = [
  {
    id: 1,
    name: 'Walk-in Freezer BMS',
    vendor: 'Siemens',
    mac: 45,
    objects: [
      { type: 'analog-input', inst: 1, name: 'Freezer Temp', unit: '°C', src: 'temperature' },
      { type: 'binary-input', inst: 1, name: 'Door Contact', unit: '', src: 'door_status' },
      { type: 'analog-value', inst: 50, name: 'PAMS Health Score', unit: '%', src: 'health_score', writable: true }
    ]
  },
  {
    id: 45001,
    name: 'RaspberryPi_PAMS_BMS_Node',
    vendor: 'PAMS',
    mac: 45,
    objects: [{ type: 'device', inst: 45001, name: 'Predator Edge Node', unit: '' }]
  }
];
let selDevice = 1;

function fmtPoint(o, val) {
  if (val == null) return '—';
  if (o.type === 'binary-input') return Number(val) === 1 ? 'OPEN' : 'closed';
  if (o.src === 'temperature') return Number(val).toFixed(2);
  if (o.src === 'health_score') return Number(val).toFixed(0);
  return String(val);
}

function renderDevices() {
  const tree = $('#deviceTree');
  tree.innerHTML =
    '<div class="tree-title">Discovered devices <span class="preview-tag">demo</span></div>' +
    demoDevices
      .map(
        (d) =>
          `<button class="tree-item ${d.id === selDevice ? 'active' : ''}" data-dev="${d.id}"><span class="ti-ico">◆</span>${d.id} · ${d.name}</button>`
      )
      .join('');
  tree.querySelectorAll('.tree-item').forEach((b) =>
    b.addEventListener('click', () => {
      selDevice = Number(b.dataset.dev);
      renderDevices();
    })
  );
  const dev = demoDevices.find((d) => d.id === selDevice);
  const sample = firstUnit();
  const rows = dev.objects
    .map((o) => {
      const val = o.src && sample ? fmtPoint(o, sample.last[o.src]) : '—';
      return `<tr><td class="mono">${o.type}:${o.inst}</td><td>${o.name}</td><td class="mono">${val}</td><td>${o.unit || ''}</td><td>${o.writable ? '<span class="pill door-open">W</span>' : '<span class="pill">R</span>'}</td></tr>`;
    })
    .join('');
  $('#deviceObjects').innerHTML = `
    <div class="panel-title">${dev.name} <span class="muted">· device ${dev.id} · ${dev.vendor} · MAC ${dev.mac}</span></div>
    <table class="tbl">
      <thead><tr><th>Object</th><th>Name</th><th>Present value</th><th>Units</th><th>Access</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="preview-note">Live discovery &amp; read/write arrive with the Pi-side gateway (phase 2). Values are ${sample ? 'from demo mode' : 'placeholders — enable Demo'}.</p>`;
}

function renderPoints() {
  const sample = firstUnit();
  const pts = [];
  for (const d of demoDevices) for (const o of d.objects) if (o.src) pts.push({ dev: d.id, ...o });
  const rows = pts
    .map((o) => {
      const val = sample ? fmtPoint(o, sample.last[o.src]) : '—';
      return `<tr><td class="mono">${o.dev}</td><td class="mono">${o.type}:${o.inst}</td><td>${o.name}</td><td class="mono">${val}</td><td>${o.unit || ''}</td><td>${o.writable ? '<button class="btn ghost sm" disabled title="Enabled when connected to the gateway">Write…</button>' : '<span class="muted">read-only</span>'}</td></tr>`;
    })
    .join('');
  $('#pointsPanel').innerHTML = `
    <table class="tbl">
      <thead><tr><th>Device</th><th>Object</th><th>Name</th><th>Value</th><th>Units</th><th>Action</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="preview-note">Writing is disabled in preview; it activates with the Pi gateway and will require confirmation.</p>`;
}

// ---------------------------------------------------------------------------
// Services view
// ---------------------------------------------------------------------------
const demoServices = [
  { name: 'pams-ml', kind: 'systemd', desc: 'ML scoring service', state: 'active' },
  { name: 'pams-bms', kind: 'systemd', desc: 'BACnet BMS node', state: 'inactive' },
  { name: 'field-mqtt', kind: 'container', desc: 'Mosquitto broker :1883', state: 'active' },
  { name: 'field-nodered', kind: 'container', desc: 'Node-RED :1880', state: 'active' },
  { name: 'field-influxdb', kind: 'container', desc: 'InfluxDB :8086', state: 'active' },
  { name: 'field-grafana', kind: 'container', desc: 'Grafana :3000', state: 'active' }
];

function renderServices() {
  const rows = demoServices
    .map((s) => {
      const cls = s.state === 'active' ? 'ok' : s.state === 'inactive' ? 'crit' : 'warn';
      return `<tr>
        <td><b>${s.name}</b><div class="muted">${s.desc}</div></td>
        <td>${s.kind}</td>
        <td><span class="svc-pill ${cls}">${s.state}</span></td>
        <td class="svc-actions">
          <button class="btn ghost sm" disabled>Start</button>
          <button class="btn ghost sm" disabled>Stop</button>
          <button class="btn ghost sm" disabled>Restart</button>
        </td></tr>`;
    })
    .join('');
  $('#servicesPanel').innerHTML = `
    <table class="tbl">
      <thead><tr><th>Service</th><th>Type</th><th>State</th><th>Controls</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="preview-note">States are a preview. Live status &amp; controls activate with the Pi gateway (phase 2).</p>`;
}

// ---------------------------------------------------------------------------
// Settings view
// ---------------------------------------------------------------------------
async function fillSettings() {
  const cfg = await window.predator.getConfig();
  $('#cfgHosts').value = (cfg.hosts || []).join('\n');
  $('#cfgMqttPort').value = cfg.mqttPort || 1883;
  $('#cfgInfluxPort').value = cfg.influxPort || 8086;
}

async function saveSettings() {
  const hosts = $('#cfgHosts')
    .value.split('\n')
    .map((h) => h.trim())
    .filter(Boolean);
  const patch = {
    hosts: hosts.length ? hosts : ['alpha-p.local', 'alpha-p'],
    mqttPort: Number($('#cfgMqttPort').value) || 1883,
    influxPort: Number($('#cfgInfluxPort').value) || 8086
  };
  await window.predator.setConfig(patch);
  if (!demo.on) state.units.clear();
  navigate('dashboard');
}

// ---------------------------------------------------------------------------
// Connections (RSWho-style network browser)
// ---------------------------------------------------------------------------
const connState = {
  probing: false,
  scanning: false,
  endpoints: [],
  discovered: [],
  subnets: [],
  active: null
};

async function doProbe() {
  connState.probing = true;
  renderConnections();
  try {
    const r = await window.predator.probeEndpoints();
    connState.endpoints = r.results || [];
    connState.active = r.activeHost || null;
  } catch {
    /* ignore */
  }
  connState.probing = false;
  renderConnections();
}

async function doScan() {
  connState.scanning = true;
  renderConnections();
  try {
    const r = await window.predator.scanSubnet();
    connState.discovered = r.found || [];
    connState.subnets = r.subnets || [];
  } catch {
    /* ignore */
  }
  connState.scanning = false;
  renderConnections();
}

async function addConnHost() {
  const inp = $('#connAdd');
  const host = inp.value.trim();
  if (!host) return;
  const cfg = await window.predator.getConfig();
  const hosts = cfg.hosts.filter((h) => h !== host);
  hosts.push(host);
  await window.predator.setConfig({ hosts });
  inp.value = '';
  doProbe();
}

function portBadges(ports) {
  const map = { mqtt: 'MQTT', influx: 'InfluxDB', nodered: 'Node-RED', grafana: 'Grafana', ssh: 'SSH' };
  const on = Object.entries(map).filter(([k]) => ports && ports[k]);
  return on.length
    ? on.map(([, label]) => `<span class="port-badge">${label}</span>`).join('')
    : '<span class="muted">no known services</span>';
}

function connItem(host, ports, online, active) {
  return `<div class="conn-item ${active ? 'active' : ''}">
    <span class="conn-dot ${online ? 'on' : 'off'}"></span>
    <span class="conn-host mono">${host}</span>
    <span class="conn-ports">${online ? portBadges(ports) : '<span class="muted">offline</span>'}</span>
    <span class="spacer"></span>
    ${active ? '<span class="conn-badge">Connected</span>' : `<button class="btn ghost sm" data-connect="${host}" ${online ? '' : 'disabled'}>Connect</button>`}
  </div>`;
}

function renderConnections() {
  const el = $('#connTree');
  if (!el) return;
  const active = connState.active;
  $('#connActive').innerHTML = active
    ? `<span class="conn-dot on"></span> Connected to <b>${active}</b>`
    : '<span class="conn-dot off"></span> Not connected';

  const eps = connState.endpoints.length
    ? connState.endpoints.map((e) => connItem(e.host, e.ports, e.online, e.host === active)).join('')
    : `<div class="conn-empty">${connState.probing ? 'Probing endpoints…' : 'No endpoints configured.'}</div>`;

  const disc = connState.discovered.length
    ? connState.discovered.map((h) => connItem(h, { mqtt: true }, true, h === active)).join('')
    : `<div class="conn-empty">${connState.scanning ? 'Scanning subnet…' : 'Press “Scan network” to browse the local subnet for PAMS hosts.'}</div>`;

  el.innerHTML = `
    <div class="tree-root"><span class="ti-ico">▣</span> PAMS Network</div>
    <div class="tree-group">Configured endpoints ${connState.probing ? '<span class="muted">· probing…</span>' : ''}</div>
    ${eps}
    <div class="tree-group">Discovered on subnet ${connState.subnets.length ? `<span class="muted">· ${connState.subnets.map((s) => s + '.0/24').join(', ')}</span>` : ''}</div>
    ${disc}`;

  el.querySelectorAll('[data-connect]').forEach((b) =>
    b.addEventListener('click', async () => {
      await window.predator.connectTo(b.dataset.connect);
      setTimeout(doProbe, 700);
    })
  );
}

// ---------------------------------------------------------------------------
// Demo mode (simulated data)
// ---------------------------------------------------------------------------
function demoTick() {
  const now = Date.now();
  for (const s of demo.seeds) {
    const prev = s.temp;
    s.temp += (Math.random() - 0.5) * 0.6; // random walk
    if (Math.random() < 0.03) s.temp += (Math.random() - 0.5) * 5; // excursion
    s.temp = Math.max(-30, Math.min(-8, s.temp));

    const door = Math.random() < 0.06 ? 1 : 0;
    const tv = Number((s.temp - prev).toFixed(2));
    let health = 100;
    if (s.temp > -18) health -= (s.temp + 18) * 15;
    if (door) health -= 20;
    health -= Math.random() * 4;
    health = Math.max(0, Math.min(100, health));

    const anomaly = s.temp > -14 || Math.abs(tv) > 2.5 ? 1 : 0;
    const stateName = door
      ? 'door-open'
      : s.temp > -16
        ? 'warming'
        : Math.abs(tv) > 1.5
          ? 'defrost'
          : 'nominal';
    const rul = Math.round(Math.max(1, (health / 100) * 180));
    const jitter = (h, d) => Number((h + (Math.random() - 0.5) * d).toFixed(1));

    const data = {
      unit_id: s.id,
      temperature: Number(s.temp.toFixed(2)),
      door_status: door,
      health_score: Number(health.toFixed(1)),
      ml_health_score: Number(health.toFixed(1)),
      ensemble_health: Number(health.toFixed(1)),
      if_health: jitter(health, 4),
      hmm_health: jitter(health, 6),
      lstm_health: jitter(health, 6),
      rul_days: rul,
      thermal_velocity: tv,
      inferred_state: stateName,
      anomaly,
      training: 0,
      n_points: 500,
      ts: now / 1000
    };
    upsert({ topic: `pams/scored/${s.id}`, data, at: now });
  }
}

function toggleDemo() {
  demo.on = !demo.on;
  const btn = $('#demoBtn');
  state.units.clear();
  if (demo.on) {
    btn.classList.add('active');
    demoTick();
    demo.timer = setInterval(demoTick, 2000);
  } else {
    btn.classList.remove('active');
    clearInterval(demo.timer);
    demo.timer = null;
    render();
  }
  renderStatus();
}

// ---------------------------------------------------------------------------
// Help / Docs (searchable, offline)
// ---------------------------------------------------------------------------
const DOCS = [
  {
    title: 'Overview — what Predator is',
    tags: 'about intro overview cockpit pams freezer offline what is',
    body: `<p><b>Predator</b> is an offline desktop cockpit for the PAMS freezer-monitoring
      system. It runs on any Windows PC and talks only to the PAMS host
      (<code>alpha-p</code>) over the local network — no internet, no cloud.</p>
      <p>Use the left sidebar to move between views. Everything works offline; the
      <b>Demo</b> button fills the UI with simulated units so you can explore
      without a live connection.</p>`
  },
  {
    title: 'Connecting to the Pi (USB / Ethernet / WiFi)',
    tags: 'connect connection network ethernet usb wifi mdns alpha-p endpoints link',
    body: `<p>Predator reaches <code>alpha-p</code> over whichever link is plugged in and
      switches automatically if you swap cables. It tries these in order and uses
      the first that answers:</p>
      <ul>
        <li><code>alpha-p.local</code> — works over any link (recommended)</li>
        <li><code>alpha-p</code> — plain hostname</li>
        <li><code>192.168.1.112</code> — WiFi/LAN address</li>
        <li><code>169.254.7.1</code> — direct-cable fallback</li>
      </ul>
      <p><b>Ethernet / direct cable:</b> plug a cable between the PC and the Pi; it
      connects with no router needed. <b>USB:</b> use a USB-to-Ethernet adapter
      (the Pi 5 cannot network over a bare USB cable). Change endpoints in
      <b>Settings</b>.</p>`
  },
  {
    title: 'Demo mode (simulated data)',
    tags: 'demo simulate placeholder test preview sample data offline',
    body: `<p>Click <b>Demo</b> (top-right) to generate four simulated freezers that
      update every 2 seconds. The status pill turns teal and reads
      <b>demo mode</b>. Click it again to stop and return to live/searching.</p>
      <p>Demo data is generated inside the app — it never touches the Pi or PAMS.</p>`
  },
  {
    title: 'Dashboard',
    tags: 'dashboard fleet units cards summary health temperature door anomaly sparkline',
    body: `<p>The fleet at a glance. Each card shows temperature, a color-coded health
      ring, a temperature sparkline, door state, RUL, thermal velocity, inferred
      state, and anomaly/training badges. The top bar totals healthy / watch /
      critical / anomalies.</p>
      <p>Colors: <b>green</b> healthy (≥80), <b>amber</b> watch (50–79),
      <b>red</b> critical (&lt;50).</p>`
  },
  {
    title: 'Devices (BACnet explorer)',
    tags: 'devices bacnet yabe explorer tree objects present value discover',
    body: `<p>A YABE-style explorer. Pick a device on the left to see its objects
      (analog-input, binary-input, analog-value) with present values and R/W
      access. In the current build this is a <b>preview</b> with representative
      data; live discovery/read activates with the Pi-side gateway.</p>`
  },
  {
    title: 'Points (read / write)',
    tags: 'points icc configurator read write present value priority table',
    body: `<p>A flat table of every point across devices — like an ICC configurator.
      Shows value, units, and read/write access. Writing is disabled in preview
      and will require confirmation once the gateway is connected.</p>`
  },
  {
    title: 'Trends',
    tags: 'trends chart history graph temperature health time series',
    body: `<p>Pick a unit to chart its temperature (teal) and health (green) over time.
      History builds while the app is open (live or demo). Turn on <b>Demo</b> if
      no units are present yet.</p>`
  },
  {
    title: 'Health / ML — what the numbers mean',
    tags: 'health ml machine learning isolation forest hmm lstm rul anomaly ensemble thermal velocity',
    body: `<p>Per-unit model breakdown:</p>
      <ul>
        <li><b>Combined / Ensemble health</b> — fused score across models (0–100)</li>
        <li><b>IsolationForest / HMM / LSTM</b> — each model's own health estimate</li>
        <li><b>RUL</b> — remaining useful life, in days</li>
        <li><b>Thermal velocity</b> — rate of temperature change</li>
        <li><b>Inferred state</b> — nominal, defrost, warming, door-open</li>
      </ul>`
  },
  {
    title: 'Services',
    tags: 'services systemd containers docker status start stop restart pams-ml pams-bms',
    body: `<p>Lists the PAMS services and containers (broker, Node-RED, InfluxDB,
      Grafana, ML/BMS) with status. Controls are a <b>preview</b>; live status and
      start/stop/restart activate with the Pi-side gateway.</p>`
  },
  {
    title: 'Settings (endpoints & ports)',
    tags: 'settings endpoints host ip port mqtt influxdb configure save reconnect',
    body: `<p>Edit the endpoint list (one per line) and ports, then <b>Save &amp;
      reconnect</b>. Predator tries endpoints top-to-bottom. Settings are stored
      per-PC at <code>%APPDATA%\\Predator\\predator-config.json</code>.</p>`
  },
  {
    title: 'Install on another PC',
    tags: 'install build dist installer exe distribute deploy setup offline',
    body: `<p>On a machine with internet, run <code>npm run dist</code> in the
      <code>predator</code> folder. This produces an installer and a portable app
      in <code>dist\\</code>:</p>
      <ul>
        <li><code>Predator Setup x.y.z.exe</code> — double-click installer</li>
        <li><code>Predator x.y.z.exe</code> — portable, no install</li>
      </ul>
      <p>Copy either to any desktop. No internet or dependencies are needed to run.</p>`
  },
  {
    title: 'Troubleshooting — no data / can\u2019t connect',
    tags: 'troubleshoot no data blank empty not connecting problem fix spinner waiting broker',
    body: `<p>If the Dashboard says <b>No live data</b>:</p>
      <ul>
        <li>Confirm the PC is on the same network as <code>alpha-p</code> (or
          cabled to it).</li>
        <li>Check the status pill (top-right) — <b>connected</b> vs
          <b>searching</b>.</li>
        <li>Even when connected, the dashboard stays empty until a freezer (or a
          simulator) publishes data. Use <b>Demo</b> to verify the UI works.</li>
        <li>Verify endpoints/ports in <b>Settings</b>, then Save &amp; reconnect.</li>
      </ul>`
  },
  {
    title: 'Safety & privacy',
    tags: 'safety privacy offline secure telemetry pams-safe read-only',
    body: `<p>Predator makes no internet calls and has no telemetry. It reads the data
      PAMS already publishes and does not change anything on the Pi. Write actions
      (points, services) are disabled until a gateway is added, and will require
      explicit confirmation.</p>`
  },
  {
    title: 'Connections manager (RSWho-style)',
    tags: 'connections rswho rslinx browse scan network discover connect endpoint manager select driver online offline',
    body: `<p>The <b>Connections</b> view is a network browser, similar to RSLinx
      RSWho. It shows the PAMS network as a tree and lets you find and pick the
      host.</p>
      <ul>
        <li><b>Refresh</b> — probes every configured endpoint and shows which are
          online and which services answer (MQTT, InfluxDB, Node-RED, Grafana, SSH).</li>
        <li><b>Scan network</b> — sweeps your PC's local subnet(s) for any host with
          the MQTT port open, so you can find <code>alpha-p</code> even without a
          hostname.</li>
        <li><b>Connect</b> — click Connect on a host to make it the active
          connection; Predator remembers it for next time.</li>
        <li><b>Add host or IP</b> — type an address and press Add to include it in
          the endpoint list.</li>
      </ul>
      <p>Green dot = reachable, grey = offline. The banner shows the currently
      connected host.</p>`
  },
  {
    title: 'First run & prerequisites',
    tags: 'first run install prerequisites requirements setup start windows launch',
    body: `<p>To run the app you need nothing but the Predator installer or exe — the
      runtime is bundled and no internet is required.</p>
      <p>On first launch Predator opens on the Dashboard and begins searching for
      <code>alpha-p</code>. With no live host, press <b>Demo</b>, or open
      <b>Connections</b> to browse the network and connect.</p>`
  },
  {
    title: 'Network ports reference',
    tags: 'ports reference 1883 8086 1880 3000 22 9443 mqtt influxdb nodered grafana ssh portainer firewall',
    body: `<ul>
        <li><code>1883</code> — MQTT broker (Predator's live data source)</li>
        <li><code>8086</code> — InfluxDB (history, future use)</li>
        <li><code>1880</code> — Node-RED editor</li>
        <li><code>3000</code> — Grafana dashboards</li>
        <li><code>22</code> — SSH (management)</li>
        <li><code>9443</code> — Portainer (container management)</li>
      </ul>
      <p>Predator only needs <code>1883</code> for live data. Allow outbound TCP to
      these on the LAN in your firewall.</p>`
  },
  {
    title: 'Data model & MQTT topics',
    tags: 'data mqtt topics pams freezers scored payload json fields schema unit_id',
    body: `<p>Predator subscribes to two topic trees on the broker:</p>
      <ul>
        <li><code>pams/freezers/&lt;unit&gt;</code> — raw readings (temperature, door)</li>
        <li><code>pams/scored/&lt;unit&gt;</code> — ML-enriched readings (health, RUL,
          anomaly, per-model scores)</li>
      </ul>
      <p>Each message is JSON keyed by <code>unit_id</code>. Predator merges both
      streams per unit and keeps a rolling in-memory history for sparklines and
      trends.</p>`
  },
  {
    title: 'Navigation tips',
    tags: 'navigation sidebar views move switch layout',
    body: `<p>Use the left sidebar to switch views. Data-driven views (Dashboard,
      Trends, Health) update live; reference views (Connections, Devices, Points,
      Services) and the docs stay put until you act. The status pill, Demo, and
      Reconnect are always in the top bar.</p>`
  },
  {
    title: 'Updating Predator',
    tags: 'update upgrade version new build reinstall',
    body: `<p>Predator has no auto-update (by design — it is offline). To update,
      build a new installer from the latest source (<code>npm run dist</code>) and
      run it on each PC; it upgrades in place and keeps your settings.</p>`
  },
  {
    title: 'Uninstalling & resetting settings',
    tags: 'uninstall remove reset delete settings config appdata clean',
    body: `<p>Uninstall via Windows <b>Apps &amp; features</b> (installer build) or
      delete the portable exe. To reset connection settings, delete
      <code>%APPDATA%\\Predator\\predator-config.json</code>; Predator recreates
      defaults on next launch.</p>`
  },
  {
    title: 'Building from source',
    tags: 'build source npm electron dist installer developer compile node',
    body: `<p>Requirements: Node.js 18+ and internet once, to install dependencies.</p>
      <ul>
        <li><code>npm install</code> — install dependencies</li>
        <li><code>npm start</code> — run in development</li>
        <li><code>npm run dist</code> — build a Windows installer + portable exe in
          <code>dist\\</code></li>
      </ul>
      <p>The produced installer is fully offline and self-contained.</p>`
  },
  {
    title: 'FAQ',
    tags: 'faq questions common why how empty many pcs internet',
    body: `<p><b>Does Predator change anything on the Pi?</b> No — it reads existing
      data only. Writes are disabled until a gateway is added.</p>
      <p><b>Do I need internet?</b> No, only the local network to the Pi.</p>
      <p><b>Why is the dashboard empty when connected?</b> Because no freezer or
      simulator is publishing yet — use Demo to verify the UI.</p>
      <p><b>Can I run it on many PCs?</b> Yes — install the same build anywhere;
      settings are per-PC.</p>`
  },
  {
    title: 'Glossary',
    tags: 'glossary terms definitions bacnet mqtt influxdb rul mdns link-local anomaly hmm lstm isolation forest ensemble',
    body: `<ul>
        <li><b>BACnet</b> — building-automation protocol for freezer/BMS points.</li>
        <li><b>MQTT</b> — lightweight publish/subscribe messaging; the live feed.</li>
        <li><b>InfluxDB</b> — time-series database storing history.</li>
        <li><b>RUL</b> — Remaining Useful Life, an estimate in days.</li>
        <li><b>mDNS / .local</b> — zero-config naming (<code>alpha-p.local</code>).</li>
        <li><b>Link-local (169.254.x.x)</b> — auto address used on a direct cable.</li>
        <li><b>IsolationForest / HMM / LSTM</b> — ML models fused into the health score.</li>
        <li><b>Anomaly</b> — a reading the models flag as abnormal.</li>
      </ul>`
  }
];
let docsQuery = '';

function escapeHtml(s) {
  return String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
}

function highlightTitle(text, q) {
  if (!q) return escapeHtml(text);
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return escapeHtml(text);
  return (
    escapeHtml(text.slice(0, i)) +
    '<mark>' +
    escapeHtml(text.slice(i, i + q.length)) +
    '</mark>' +
    escapeHtml(text.slice(i + q.length))
  );
}

function renderHelp() {
  const box = $('#docs');
  const q = docsQuery.trim().toLowerCase();
  const matches = DOCS.filter(
    (d) => !q || (d.title + ' ' + d.tags + ' ' + d.body).toLowerCase().includes(q)
  );
  $('#docsCount').textContent = q
    ? `${matches.length} of ${DOCS.length} topics`
    : `${DOCS.length} topics`;
  if (!matches.length) {
    box.innerHTML = `<div class="preview-note big">No topics match “${escapeHtml(docsQuery)}”.</div>`;
    return;
  }
  box.innerHTML = matches
    .map(
      (d) =>
        `<article class="doc"><h3>${highlightTitle(d.title, q)}</h3><div class="doc-body">${d.body}</div></article>`
    )
    .join('');
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
  document.querySelectorAll('.nav-item').forEach((b) =>
    b.addEventListener('click', () => navigate(b.dataset.view))
  );
  $('#demoBtn').addEventListener('click', toggleDemo);
  $('#reconnectBtn').addEventListener('click', () => window.predator.reconnect());
  $('#saveBtn').addEventListener('click', saveSettings);
  $('#docsSearch').addEventListener('input', (e) => {
    docsQuery = e.target.value;
    renderHelp();
  });
  const emptyDemo = $('#emptyDemoBtn');
  if (emptyDemo) emptyDemo.addEventListener('click', () => {
    if (!demo.on) toggleDemo();
  });
  $('#connRefresh').addEventListener('click', doProbe);
  $('#connScan').addEventListener('click', doScan);
  $('#connAddBtn').addEventListener('click', addConnHost);
  $('#connAdd').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addConnHost();
  });

  window.predator.onStatus((s) => {
    state.status = s;
    renderStatus();
  });
  // Ignore live readings while demo mode is driving the UI.
  window.predator.onReading((msg) => {
    if (!demo.on) upsert(msg);
  });

  const cfg = await window.predator.getConfig();
  state.status.hosts = cfg.hosts;
  renderStatus();
  navigate('dashboard');

  // Refresh data-driven views (timers, sparklines) each second.
  setInterval(refreshData, 1000);
});
