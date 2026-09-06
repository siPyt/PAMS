'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('predator', {
  onReading: (cb) => ipcRenderer.on('reading', (_event, msg) => cb(msg)),
  onStatus: (cb) => ipcRenderer.on('status', (_event, msg) => cb(msg)),
  getConfig: () => ipcRenderer.invoke('config:get'),
  setConfig: (patch) => ipcRenderer.invoke('config:set', patch),
  reconnect: () => ipcRenderer.invoke('app:reconnect')
});
