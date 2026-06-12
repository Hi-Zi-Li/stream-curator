const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("streamCuratorDesktop", {
  isDesktopClient: true,
  getPush: (options = {}) => ipcRenderer.invoke("push:get", options),
  refreshPush: () => ipcRenderer.invoke("push:refresh"),
  getHot: () => ipcRenderer.invoke("hot:get"),
  refreshHot: () => ipcRenderer.invoke("hot:refresh"),
  searchContent: (query) => ipcRenderer.invoke("search:get", query),
  getSettingsAuthStatus: () => ipcRenderer.invoke("settings:auth-status"),
  getSettingsLlmConfig: () => ipcRenderer.invoke("settings:llm-config:get"),
  saveSettingsLlmConfig: (payload = {}) => ipcRenderer.invoke("settings:llm-config:save", payload),
  getSettingsLoginSpec: (source) => ipcRenderer.invoke("settings:login-spec", source),
  commitSourceLogin: (source) => ipcRenderer.invoke("settings:commit-login", source),
  getReaderComments: (options = {}) => ipcRenderer.invoke("reader:comments", options),
  openExternal: (url) => ipcRenderer.invoke("desktop:open-external", url),
  copyText: (text) => ipcRenderer.invoke("desktop:copy-text", text),
});
