const { app, BrowserWindow, WebContentsView, ipcMain, shell, session } = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const { createCampusController } = require('./campus/campus-controller')

const defaults = {
  backendBaseUrl: 'http://127.0.0.1:8080',
  window: { width: 1440, height: 900 },
  campus: { allowedHosts: [], services: {} },
}
function loadConfig() {
  const raw = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'))
  // 分发包用：同目录的 server.txt 覆盖后端地址。
  // ★ 为什么不让人改 config.json：那份文件里还有 campus.services 的 hash/URL 等一串
  //   精细配置，手改很容易改坏（少个逗号就整个应用起不来）。单独一个小文件，
  //   内容就一行 http://x.x.x.x:8080，改错了也只影响地址、不会波及别的配置。
  try {
    const override = path.join(__dirname, 'server.txt')
    if (fs.existsSync(override)) {
      const url = fs.readFileSync(override, 'utf8').trim().split(/\r?\n/)[0].trim()
      if (/^https?:\/\/\S+$/i.test(url)) raw.backendBaseUrl = url
    }
  } catch { /* 读不到或格式不对就沿用 config.json */ }
  return { ...defaults, ...raw, window: { ...defaults.window, ...raw.window },
    campus: { ...defaults.campus, ...raw.campus } }
}
const config = loadConfig()
let mainWindow = null
function createWindow() {
  mainWindow = new BrowserWindow({
    ...config.window, minWidth: 1024, minHeight: 680,
    title: 'SAI', backgroundColor: '#f6f6f9', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false, contextIsolation: true, sandbox: true },
  })
  mainWindow.setMenu(null)
  const origin = new URL(config.backendBaseUrl).origin
  ipcMain.handle('school:import-session', async (event, payload) => {
    if (event.sender !== mainWindow?.webContents || event.senderFrame !== mainWindow.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== origin) throw new Error('调用来源不可信')
    const token = String(payload?.token || '')
    if (!token) throw new Error('缺少登录状态')
    const headers = { Authorization: `Bearer ${token}` }
    const userResponse = await fetch(new URL('/api/auth/me', origin), { headers })
    if (!userResponse.ok) throw new Error('登录状态已失效')
    const user = await userResponse.json()
    if (!Number.isInteger(user.id)) throw new Error('用户身份无效')
    const response = await fetch(new URL('/api/auth/school/session', origin), { method: 'POST', headers })
    if (!response.ok) throw new Error('无法恢复学校登录状态')
    const { cookies } = await response.json()
    if (!Array.isArray(cookies) || !cookies.length) throw new Error('学校登录状态为空')
    const campusSession = session.fromPartition(`persist:campus-user-${user.id}`)
    const existing = await campusSession.cookies.get({})
    for (const c of existing) {
      const host = (c.domain || '').replace(/^\./, '')
      if (host === 'guet.edu.cn' || host.endsWith('.guet.edu.cn'))
        await campusSession.cookies.remove(`https://${host}${c.path || '/'}`, c.name)
    }
    for (const c of cookies) {
      const host = String(c.domain || '').replace(/^\./, '')
      if (!(host === 'guet.edu.cn' || host.endsWith('.guet.edu.cn'))) throw new Error('学校会话域名无效')
      const data = { url: `https://${host}${c.path || '/'}`, name: c.name, value: c.value,
        path: c.path || '/', secure: !!c.secure, httpOnly: !!c.httpOnly }
      if (c.domainSpecified) data.domain = c.domain
      if (Number.isFinite(c.expires) && c.expires > Date.now() / 1000) data.expirationDate = c.expires
      await campusSession.cookies.set(data)
    }
    return { ok: true }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (new URL(url).origin !== origin) event.preventDefault()
  })
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try { if (['http:', 'https:'].includes(new URL(url).protocol)) void shell.openExternal(url) }
    catch { /* Invalid links cannot be opened. */ }
    return { action: 'deny' }
  })
  const campus = createCampusController({ ipcMain, WebContentsView, window: mainWindow, config })
  mainWindow.on('closed', () => { campus.dispose(); ipcMain.removeHandler('school:import-session'); mainWindow = null })
  void mainWindow.loadURL(config.backendBaseUrl)
}
app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow() })
})
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
