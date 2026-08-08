/**
 * 预加载脚本（S8：contextIsolation=true + sandbox=true 下的最小桥接）。
 * 仅暴露"外部后端地址"读写（userData 持久化）——不暴露 Node/文件系统能力。
 */
'use strict'

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('desktop', {
  setExternalBase: (url) => ipcRenderer.send('set-external-base', url),
  getExternalBase: () => ipcRenderer.invoke('get-external-base'),
})
