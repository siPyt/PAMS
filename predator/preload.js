'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('predator', {
  onReading: (cb) => ipcRenderer.on('reading', (_event, msg) => cb(msg)),
  onStatus: (cb) => ipcRenderer.on('status', (_event, msg) => cb(msg)),
  getConfig: () => ipcRenderer.invoke('config:get'),
  setConfig: (patch) => ipcRenderer.invoke('config:set', patch),
  reconnect: () => ipcRenderer.invoke('app:reconnect'),
  probeEndpoints: () => ipcRenderer.invoke('net:probe'),
  scanSubnet: () => ipcRenderer.invoke('net:scan'),
  connectTo: (host) => ipcRenderer.invoke('app:connectTo', host),
  gatewayGet: (pathname) => ipcRenderer.invoke('gateway:get', pathname),
  term: {
    start: () => ipcRenderer.invoke('term:start'),
    write: (data) => ipcRenderer.send('term:write', data),
    resize: (cols, rows) => ipcRenderer.send('term:resize', { cols, rows }),
    reset: () => ipcRenderer.invoke('term:reset'),
    onData: (cb) => ipcRenderer.on('term:data', (_event, m) => cb(m)),
    onExit: (cb) => ipcRenderer.on('term:exit', (_event, m) => cb(m))
  }
});
