'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const net = require('net');
const mqtt = require('mqtt');

// Predator talks only to the PAMS host, but reaches it over whichever link is
// live (WiFi, Ethernet, direct cable, USB-to-Ethernet). It probes these
// endpoints in priority order and connects to the first that answers. mDNS
// (`alpha-p.local`) resolves over every transport, so it "just connects".
const DEFAULT_CONFIG = {
  hosts: ['alpha-p.local', 'alpha-p', '192.168.1.112', '169.254.7.1'],
  mqttPort: 1883,
  influxPort: 8086,
  influxToken: '',
  influxOrg: '',
  influxBucket: 'pams',
  topics: ['pams/scored/#', 'pams/freezers/#']
};

const CONFIG_PATH = path.join(app.getPath('userData'), 'predator-config.json');

let win = null;
let client = null;
let activeHost = null;
let discovering = false;
let redialTimer = null;
let config = loadConfig();

function loadConfig() {
  try {
    const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
    const merged = { ...DEFAULT_CONFIG, ...raw };
    if (!Array.isArray(merged.hosts) || merged.hosts.length === 0) {
      // Migrate an older single-host config.
      merged.hosts = raw.host ? [raw.host] : DEFAULT_CONFIG.hosts.slice();
    }
    delete merged.host;
    return merged;
  } catch {
    return { ...DEFAULT_CONFIG, hosts: DEFAULT_CONFIG.hosts.slice() };
  }
}

function saveConfig(patch) {
  config = { ...config, ...patch };
  try {
    fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
  } catch {
    /* best-effort persistence */
  }
  return config;
}

function send(channel, payload) {
  if (win && !win.isDestroyed()) {
    win.webContents.send(channel, payload);
  }
}

function endClient() {
  if (client) {
    try {
      client.removeAllListeners();
      client.end(true);
    } catch {
      /* ignore */
    }
    client = null;
  }
}

// Quick TCP reachability check for one endpoint (does the broker port answer?).
function probe(host, port, timeout = 1500) {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      sock.destroy();
      resolve(ok);
    };
    sock.setTimeout(timeout);
    sock.once('connect', () => finish(true));
    sock.once('timeout', () => finish(false));
    sock.once('error', () => finish(false));
    sock.connect(port, host);
  });
}

// Try endpoints in priority order; return the first that answers.
async function discover() {
  for (const host of config.hosts) {
    if (!host) continue;
    // eslint-disable-next-line no-await-in-loop
    const ok = await probe(host, config.mqttPort);
    if (ok) return host;
  }
  return null;
}

function scheduleRedial(delay = 3000) {
  clearTimeout(redialTimer);
  redialTimer = setTimeout(() => discoverAndConnect(), delay);
}

async function discoverAndConnect() {
  if (discovering) return;
  discovering = true;
  clearTimeout(redialTimer);
  endClient();
  activeHost = null;
  send('status', { state: 'searching', hosts: config.hosts, activeHost: null });

  let host;
  try {
    host = await discover();
  } finally {
    discovering = false;
  }

  if (!host) {
    send('status', { state: 'searching', hosts: config.hosts, activeHost: null });
    scheduleRedial(3000);
    return;
  }
  connectMqtt(host);
}

function onDrop() {
  // A live link went away (e.g. cable unplugged). Re-discover across all
  // endpoints so we fail over to whichever transport is now available.
  if (discovering) return;
  activeHost = null;
  send('status', { state: 'searching', hosts: config.hosts, activeHost: null });
  scheduleRedial(1500);
}

function connectMqtt(host) {
  endClient();
  activeHost = host;
  const url = `mqtt://${host}:${config.mqttPort}`;
  send('status', { state: 'connecting', hosts: config.hosts, activeHost: host, url });

  client = mqtt.connect(url, {
    reconnectPeriod: 0, // Predator manages reconnect/failover across endpoints.
    connectTimeout: 6000,
    clientId: `predator_${Math.random().toString(16).slice(2, 10)}`
  });

  client.on('connect', () => {
    send('status', { state: 'connected', hosts: config.hosts, activeHost: host, url });
    for (const topic of config.topics) {
      client.subscribe(topic);
    }
  });
  client.on('close', onDrop);
  client.on('offline', onDrop);
  client.on('error', onDrop);
  client.on('message', (topic, payload) => {
    let data;
    try {
      data = JSON.parse(payload.toString());
    } catch {
      return;
    }
    send('reading', { topic, data, at: Date.now() });
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1320,
    height: 840,
    minWidth: 940,
    minHeight: 620,
    backgroundColor: '#0b0f14',
    title: 'Predator',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.webContents.on('did-finish-load', discoverAndConnect);
  win.on('closed', () => {
    win = null;
  });
}

app.whenReady().then(createWindow);

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('window-all-closed', () => {
  clearTimeout(redialTimer);
  endClient();
  app.quit();
});

ipcMain.handle('config:get', () => config);
ipcMain.handle('config:set', (_event, patch) => {
  const next = saveConfig(patch || {});
  discoverAndConnect();
  return next;
});
ipcMain.handle('app:reconnect', () => {
  discoverAndConnect();
  return true;
});
