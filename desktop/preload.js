// 只向 SAI 页面注入一个极窄的接口；学校网页没有 preload，拿不到这些能力。
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('saiDesktop', {
  importSchoolSession: (payload) => ipcRenderer.invoke('school:import-session', payload),
  openCampus: (payload) => ipcRenderer.invoke('campus:open', payload),
  hideCampus: () => ipcRenderer.invoke('campus:hide'),
  resetCampus: () => ipcRenderer.invoke('campus:reset'),
  resumeCampus: () => ipcRenderer.invoke('campus:resume'),
  pauseCampus: () => ipcRenderer.invoke('campus:pause'),
  zoomCampus: (direction) => ipcRenderer.invoke('campus:zoom', direction),
  reloadCampus: () => ipcRenderer.invoke('campus:reload'),
  backCampus: () => ipcRenderer.invoke('campus:back'),
  forwardCampus: () => ipcRenderer.invoke('campus:forward'),
  /** 用校园视图自己的会话直接查学校接口（空教室等），凭据不出本机。 */
  queryCampus: (payload) => ipcRenderer.invoke('campus:query', payload),
  /** 右侧栏布局变化时把学校视图的矩形交给主进程（CSS 像素）。 */
  setCampusBounds: (rect) => ipcRenderer.invoke('campus:bounds', rect),
  onCampusStatus: (callback) => {
    if (typeof callback !== 'function') return () => {}
    const listener = (_event, status) => callback(status)
    ipcRenderer.on('campus:status', listener)
    return () => ipcRenderer.removeListener('campus:status', listener)
  },
  onCampusVisible: (callback) => {
    if (typeof callback !== 'function') return () => {}
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('campus:visible', listener)
    return () => ipcRenderer.removeListener('campus:visible', listener)
  },
  onCampusUrl: (callback) => {
    if (typeof callback !== 'function') return () => {}
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('campus:url', listener)
    return () => ipcRenderer.removeListener('campus:url', listener)
  },
  onCampusZoom: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('campus:zoom', listener)
    return () => ipcRenderer.removeListener('campus:zoom', listener)
  },
})
