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

  render();
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

function render() {
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
// Settings modal
// ---------------------------------------------------------------------------
async function openSettings() {
  const cfg = await window.predator.getConfig();
  $('#cfgHosts').value = (cfg.hosts || []).join('\n');
  $('#cfgMqttPort').value = cfg.mqttPort || 1883;
  $('#cfgInfluxPort').value = cfg.influxPort || 8086;
  $('#modal').classList.remove('hidden');
}

function closeSettings() {
  $('#modal').classList.add('hidden');
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
  state.units.clear();
  render();
  closeSettings();
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
// Boot
// ---------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
  $('#settingsBtn').addEventListener('click', openSettings);
  $('#cancelBtn').addEventListener('click', closeSettings);
  $('#saveBtn').addEventListener('click', saveSettings);
  $('#demoBtn').addEventListener('click', toggleDemo);
  $('#reconnectBtn').addEventListener('click', () => window.predator.reconnect());
  $('#modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') closeSettings();
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
  render();

  // Refresh "last seen" timers and stale styling each second.
  setInterval(render, 1000);
});
