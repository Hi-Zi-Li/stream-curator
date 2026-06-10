const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("streamCuratorDesktop", {
  isDesktopClient: true,
  getPush: (options = {}) => ipcRenderer.invoke("push:get", options),
  refreshPush: () => ipcRenderer.invoke("push:refresh"),
  openExternal: (url) => ipcRenderer.invoke("desktop:open-external", url),
  copyText: (text) => ipcRenderer.invoke("desktop:copy-text", text),
});
