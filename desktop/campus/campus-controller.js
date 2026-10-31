const { app } = require('electron')
const path = require('node:path')
const { CampusNavigator } = require('./campus-navigation')
const { attachTrace } = require('./campus-trace')
const { attachParameterTrace } = require('./campus-parameter-trace')
const campusApi = require('./campus-api')

function createCampusController({ ipcMain, WebContentsView, window, config }) {
  let view = null
  let navigator = null
  let detachTrace = null
  let task = null
  let ownerId = null
  let visible = false
  let bounds = null
  let pageWidth = 0
  let autoFit = true
  let fitting = false
  let lastStatus = ''
  let disposed = false
  let openSequence = 0
  let pendingQuery = null   // 待执行的空教室查询（等登录完成后自动触发）
  let activeQuery = null
  let queryPaused = false
  let queryToken = ''       // 触发查询时回传结果用的 token
  // 自动进入教务的两步跳转状态：'' 未开始 → 'portal' 已到 pcportal → 'module' 已发 openModule
  let openRedirected = ''
  let openRetries = 0        // 自动跳转失败重试次数（防 SSO 持续 504 时无限循环）
  let queryRunning = false    // 查询进行中（防 did-stop-loading 并发重入）
  let queryVersion = 0
  const channels = []
  const backend = new URL(config.backendBaseUrl)

  function send(channel, payload) {
    if (!disposed && !window.isDestroyed()) window.webContents.send(channel, payload)
  }
  function report(status, detail = '', extra = {}) {
    if (!task) return
    const value = `${status}:${detail}`
    if (value === lastStatus) return
    lastStatus = value
    send('campus:status', { taskId: task.task_id, service: task.service, status, detail, ...extra })
  }
  function allowed(url) {
    try {
      const parsed = new URL(url)
      return ['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password &&
        (config.campus.allowedHosts || []).some(host => parsed.hostname === host || parsed.hostname.endsWith('.' + host))
    } catch { return false }
  }
  function trusted(event) {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
        new URL(event.senderFrame.url).origin !== backend.origin) throw new Error('调用来源不可信')
  }
  function handle(name, callback) {
    ipcMain.handle(name, (event, payload) => { trusted(event); return callback(payload) })
    channels.push(name)
  }
  function showUrl(url) {
    try { const value = new URL(url); send('campus:url', { url: value.origin + value.pathname }) }
    catch { send('campus:url', { url: '' }) }
  }
  function setZoom(factor) {
    if (!view || view.webContents.isDestroyed()) return
    view.webContents.setZoomFactor(Math.max(0.25, Math.min(1.5, factor)))
    send('campus:zoom', { factor: view.webContents.getZoomFactor(), auto: autoFit })
  }
  function applyBounds() {
    if (!view || !visible || !bounds || window.isDestroyed()) return
    const scale = window.webContents.getZoomFactor()
    const [width, height] = window.getContentSize()
    const x = Math.max(0, Math.min(Math.round(bounds.x * scale), width - 1))
    const y = Math.max(0, Math.min(Math.round(bounds.y * scale), height - 1))
    const size = { x, y, width: Math.max(1, Math.min(Math.round(bounds.width * scale), width - x)),
      height: Math.max(1, Math.min(Math.round(bounds.height * scale), height - y)) }
    view.setBounds(size)
    if (autoFit && pageWidth) setZoom(Math.min(1, size.width / pageWidth))
  }
  async function fitPage() {
    if (!view || fitting) return
    const web = view.webContents
    if (web.isDestroyed() || !allowed(web.getURL())) return
    fitting = true
    try {
      const widths = []
      for (const frame of web.mainFrame.framesInSubtree) {
        if (!allowed(frame.url)) continue
        try {
          widths.push(await frame.executeJavaScript('Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0)'))
        } catch { /* Redirected frames are measured after the next load. */ }
      }
      if (view?.webContents !== web || web.isDestroyed()) return
      pageWidth = Math.max(...widths.filter(Number.isFinite), 1)
      if (autoFit) applyBounds()
    } finally { fitting = false }
  }
  async function load(url) {
    if (!allowed(url)) { report('failed', '学校入口不在允许访问的站点中'); return }
    try { await view.webContents.loadURL(url) }
    catch (error) { if (error.code !== 'ERR_ABORTED') report('failed', '学校页面加载失败，请检查网络或重试') }
  }
  function destroyView() {
    queryVersion++
    pendingQuery = null
    activeQuery = null
    queryPaused = false
    navigator?.dispose()
    navigator = null
    // 先解挂抓包，再关视图；否则 debugger 会跟着被销毁的 webContents 报错
    try { detachTrace?.() } catch { /* ignore */ }
    detachTrace = null
    if (view) {
      if (visible && !window.isDestroyed()) window.contentView.removeChildView(view)
      if (!view.webContents.isDestroyed()) view.webContents.close()
    }
    view = null
    visible = false
    pageWidth = 0
    task = null
    lastStatus = ''
    send('campus:visible', { visible: false })
  }
  function ensureView(userId) {
    if (view && ownerId === userId) return
    destroyView()
    ownerId = userId
    view = new WebContentsView({ webPreferences: {
      nodeIntegration: false, contextIsolation: true, sandbox: true,
      webviewTag: false, partition: `persist:campus-user-${userId}`,
    } })
    const web = view.webContents
    // 调试用：把学校页面自己的 XHR/Fetch 记到本地日志，便于反推功能接口。
    // 默认关闭；在 config.json 里把 campus.trace 设为 true 才生效。
    if (config.campus.trace) {
      detachTrace = attachTrace(web, {
        file: path.join(app.getPath('userData'), 'campus-trace.log'),
      })
    } else if (config.campus.parameterTrace) {
      detachTrace = attachParameterTrace(web, {
        file: path.join(app.getPath('userData'), 'campus-parameters.jsonl'),
      })
    }
    web.session.webRequest.onBeforeRequest((details, callback) => {
      const blocked = details.webContentsId === web.id && navigator?.guard &&
        !['GET', 'HEAD', 'OPTIONS'].includes(details.method.toUpperCase())
      if (blocked) navigator.blocked()
      callback({ cancel: !!blocked })
    })
    web.on('will-navigate', (event, url) => {
      if (!allowed(url)) { event.preventDefault(); navigator?.pause(); report('needs_user', '学校页面请求跳转到未授权站点，已暂停') }
    })
    web.on('will-redirect', (event, url) => {
      if (!allowed(url)) { event.preventDefault(); report('failed', '学校登录跳转的站点未获授权') }
    })
    // Portal links often open a new tab. Keep them inside the same tracked view/session.
    web.setWindowOpenHandler(({ url }) => {
      if (allowed(url)) setImmediate(() => { void load(url) })
      else report('needs_user', '无法在当前校园视图打开该页面')
      return { action: 'deny' }
    })
    web.on('did-navigate', (_event, url) => showUrl(url))
    web.on('did-navigate-in-page', (_event, url) => { showUrl(url); void navigator?.inspect() })
    web.on('dom-ready', () => { void fitPage(); void navigator?.inspect() })
    web.on('did-stop-loading', () => { void fitPage(); void navigator?.inspect(); void maybeRunPendingQuery() })
    web.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
      if (isMainFrame && code !== -3) report('failed', `学校页面未能完成加载（${code}）`)
    })
  }

  handle('campus:open', async payload => {
    const requestId = ++openSequence
    const taskId = String(payload?.taskId || '')
    const token = String(payload?.token || '')
    if (!/^[0-9a-f]{6,32}$/.test(taskId) || !token) throw new Error('缺少有效的校园任务或登录状态')
    async function request(path) {
      const response = await fetch(new URL(path, backend), { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(10000) })
      if (!response.ok) throw new Error('校园任务不存在或当前登录已失效')
      return response.json()
    }
    const [user, result] = await Promise.all([request('/api/auth/me'), request(`/api/campus/tasks/${taskId}`)])
    if (disposed || requestId !== openSequence) return { ok: false }
    const settings = config.campus.services[result.task.service]
    if (!settings || !Number.isInteger(user.id)) throw new Error('未配置该校园服务')
    const sameTask = task?.task_id === taskId && ownerId === user.id
    ensureView(user.id)
    task = result.task
    if (!visible) window.contentView.addChildView(view)
    visible = true
    send('campus:visible', { visible: true })
    applyBounds()
    if (sameTask && navigator && !result.task.query) { navigator.resume(); return { ok: true, task } }
    navigator?.dispose()
    navigator = null
    // 后端已翻译好查询条件：走「直接调接口」模式，不再用 DOM 导航器。
    const savedQuery = result.task.query
    if (savedQuery && (savedQuery.start_date || savedQuery.date)) {
      queryVersion++
      pendingQuery = { ...savedQuery }
      activeQuery = pendingQuery
      queryPaused = false
      queryToken = token
      // 同一个任务、且当前页面已经落在教务域（登录态还在）：不重新加载登录页，
      // 直接触发一次查询。否则重新加载登录页（走自动跳转）。
      const settingsHash = settings.targetHash || ''
      const alreadyInCampus = settingsHash && view
        ? !!campusApi.vpnGateway(view.webContents, settings.intranetHost, settingsHash)
        : false
      if (sameTask && alreadyInCampus) {
        report('navigating', '正在查询空教室')
        void maybeRunPendingQuery()
        return { ok: true, task }
      }
      openRedirected = ''   // 重新加载登录页时，重新走「登录后自动跳教务」的两步跳转
      openRetries = 0
      report('awaiting_login', '请在学校页面完成登录，登录后会自动查询空教室')
      void load(settings.startUrl)
      return { ok: true, task }
    }
    navigator = new CampusNavigator({ web: view.webContents, allowed, report,
      decide: async (snapshot, history, signal) => {
        const response = await fetch(new URL(`/api/campus/tasks/${taskId}/decision`, backend), {
          method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ snapshot, history }), signal: AbortSignal.any([signal, AbortSignal.timeout(90000)]),
        })
        if (!response.ok) throw new Error('模型决策请求未完成')
        return (await response.json()).decision
      } })
    report('navigating', '正在打开学校系统')
    void load(settings.startUrl)
    return { ok: true, task }
  })
  handle('campus:hide', () => {
    openSequence++
    if (view && visible) window.contentView.removeChildView(view)
    visible = false
    navigator?.pause()
    send('campus:visible', { visible: false })
    return { ok: true }
  })
  handle('campus:reset', () => { openSequence++; destroyView(); ownerId = null; return { ok: true } })
  handle('campus:resume', () => {
    if (navigator) navigator.resume()
    else if (task && activeQuery && queryPaused) {
      queryVersion++
      queryPaused = false
      pendingQuery = activeQuery
      report('navigating', '正在继续查询空教室')
      void maybeRunPendingQuery()
    }
    return { ok: true }
  })
  handle('campus:pause', () => {
    if (navigator) navigator.pause()
    else if (task && activeQuery) {
      queryVersion++
      queryPaused = true
      pendingQuery = null
      report('paused', '已暂停查询；已发出的学校请求可能仍会完成')
    }
    return { ok: true }
  })
  handle('campus:bounds', rect => {
    if (!['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect?.[key]))) return { ok: false }
    bounds = rect
    applyBounds()
    return { ok: true }
  })
  handle('campus:zoom', direction => {
    if (!view) return { ok: false }
    if (direction === 'fit') { autoFit = true; void fitPage() }
    else if (direction === 'in' || direction === 'out') { autoFit = false; setZoom(view.webContents.getZoomFactor() + (direction === 'in' ? 0.1 : -0.1)) }
    return { ok: true }
  })
  handle('campus:reload', () => { navigator?.resume(); view?.webContents.reload(); return { ok: true } })
  handle('campus:back', () => { if (view?.webContents.navigationHistory.canGoBack()) view.webContents.navigationHistory.goBack(); return { ok: true } })
  handle('campus:forward', () => { if (view?.webContents.navigationHistory.canGoForward()) view.webContents.navigationHistory.goForward(); return { ok: true } })

  // 有待查条件时试一次：登录态已就绪则出结果，未就绪则保留继续等下次页面加载。
  // 用 did-stop-loading 触发（覆盖登录跳转的所有路径），比只盯 did-navigate 稳。
  async function maybeRunPendingQuery() {
    if (queryPaused || !pendingQuery || !task || !view || view.webContents.isDestroyed()) return
    if (queryRunning) return  // 上一次查询还没结束，避免 did-stop-loading 并发重入
    const q = pendingQuery, t = queryToken, id = task.task_id, version = queryVersion
    const web = view.webContents
    const url = web.getURL()
    const settings = config.campus.services[task?.service] || {}
    const intranetHost = settings.intranetHost || ''
    const targetHash = settings.targetHash || ''
    const portalHash = settings.portalHash || ''
    const portalUrl = settings.portalUrl || ''
    const openModuleUrl = settings.openModuleUrl || ''
    // 只有真正落到教务域才查，两种形态都算到达：
    //   a) WebVPN 转发形态：v.guet.edu.cn/https/<教务hash>/...（vpn-12-o2- 指向内网 host）；
    //   b) 直连形态：CAS 出票后页面落到 bkjwtest.guet.edu.cn/student/...（真实 CAS 域
    //      与教务域均公网可达）。注意排除 /student/sso/login（CAS 出票回调中间页，
    //      此时教务会话尚未建立，抢跑查询只会拿到登录页）。
    const atTarget = (() => {
      if (intranetHost) {
        if (campusApi.vpnGateway(web, intranetHost, targetHash)) return true
        try {
          const current = new URL(web.getURL())
          if (current.hostname === intranetHost && /\/student\//.test(current.pathname)
              && !/\/student\/sso\//.test(current.pathname)) return true
        } catch { /* URL 解析失败按未到达处理 */ }
        return false
      }
      return !/authserver\/login/.test(url) && /\/student\//.test(url)
    })()
    if (!atTarget) {
      // 已离开 CAS 登录页但还没进教务域。两步自动进入（抓包证实的机制）：
      //   1) loadURL 到「智慧校园」的真实入口 login.jsp（门户 portal_groups 里给的
      //      redirect 地址），由它正规建立 pcportal 会话——直接 loadURL pcportal 首页
      //      会触发一条脆弱的 SSO 重定向链（sopcb 504）；
      //   2) pcportal 会话就绪后，在页面上下文里 fetch openModule.do（打开学生端），
      //      解析返回 JSON 里的跳转地址并导航——openModule 是页面内 XHR，顶层导航会 504。
      if (!/authserver\/login/.test(url) && !/cas_login/.test(url)) {
        const atPortal = portalHash ? !!campusApi.vpnGateway(web, '', portalHash) : false
        if (openRetries >= 3) {
          // SSO 链路持续失败（如学校网关 504）：停止重试，交回用户手动操作。
          if (openRedirected !== 'giveup') {
            openRedirected = 'giveup'
            report('needs_user', '自动进入教务系统多次超时（学校网关响应 504），请在学校页面手动点击进入「本科教学信息平台学生端」')
          }
          return
        }
        if (portalUrl && !openRedirected) {
          openRetries++
          openRedirected = 'portal'
          report('navigating', '登录成功，正在进入教务系统')
          try { await web.loadURL(portalUrl) } catch { /* did-fail-load 已上报 */ }
        } else if (openRedirected === 'portal' && openModuleUrl && atPortal) {
          openRedirected = 'module'
          report('navigating', '正在打开本科教学信息平台学生端')
          const full = openModuleUrl + '&_t=' + Date.now()
          try {
            const result = await web.executeJavaScript(`
              (async () => {
                try {
                  const resp = await fetch(${JSON.stringify(full)}, { method: 'GET', credentials: 'include' })
                  const text = await resp.text()
                  if (!resp.ok) return { ok: false, status: resp.status, text: text.slice(0, 200) }
                  // openModule 返回 JSON，页面自己的 JS 从中取跳转地址再导航；这里做同样的事。
                  let target = ''
                  try {
                    const data = JSON.parse(text)
                    const findUrl = (value) => {
                      if (typeof value === 'string') {
                        return /^https?:\\/\\//.test(value) || /^\\/[^/]/.test(value) ? value : ''
                      }
                      if (value && typeof value === 'object') {
                        for (const key of ['url', 'redirect', 'targetUrl', 'appUrl', 'location', 'href']) {
                          if (typeof value[key] === 'string' && value[key]) return findUrl(value[key]) || ''
                        }
                        for (const item of Object.values(value)) {
                          const hit = findUrl(item)
                          if (hit) return hit
                        }
                      }
                      return ''
                    }
                    target = findUrl(data)
                  } catch { /* 不是 JSON，忽略 */ }
                  return { ok: true, target, text: text.slice(0, 300) }
                } catch (e) { return { ok: false, error: String(e && e.message || e) } }
              })()
            `, true)
            // 拿到跳转地址就导航；拿不到说明 pcportal 会自己打开（等下一次加载）。
            if (result && result.target) {
              const target = new URL(result.target, url).toString()
              if (version === queryVersion) await load(target)
            } else if (result && !result.ok) {
              // 504 等临时错误：回退状态，让下一次 did-stop-loading 重试整条链路。
              openRedirected = ''
            }
          } catch { /* 页面跳转中 executeJavaScript 可能失败，忽略 */ }
        }
      }
      return
    }
    queryRunning = true
    try {
      const res = await runQuery(id, t, q, version)
      if (version === queryVersion && (res.ok || res.reason !== 'login_required')) {
        pendingQuery = null
      }
    } finally {
      queryRunning = false
      if (version !== queryVersion && pendingQuery) void maybeRunPendingQuery()
    }
  }

  // 组装 free-list 请求体并调接口，成功则回传后端。
  async function runQuery(taskId, token, spec, version = queryVersion) {
    const date = String(spec.start_date || spec.date || '').slice(0, 10)
    const units = spec.units || (spec.period && campusApi.PERIOD_UNITS[spec.period]) || campusApi.UNITS
    const body = {
      buildingId: String(spec.building_id || spec.buildingId || ''),
      campusId: Number.isInteger(spec.campus_id) ? spec.campus_id : (spec.campusId || 3),
      roomId: '',
      dateTimeSegmentCmd: {
        startDateTime: date, endDateTime: spec.end_date || date,
        startTime: '', endTime: '', weekdays: spec.weekdays || [], units,
      },
      seatsForLessonGte: spec.seats ? String(spec.seats) : '',
      hasDataPermission: false,
    }
    const web = view.webContents
    // 内网域名从服务配置取；取不到就用当前页面 URL 里的真实 host（WebVPN 场景）。
    const settings = config.campus.services[task?.service] || {}
    const intranetHost = settings.intranetHost || ''
    const targetHash = settings.targetHash || ''
    const current = () => version === queryVersion && task?.task_id === taskId && view?.webContents === web
    let result
    try {
      const select = (items, name, kind) => {
        if (!Array.isArray(items)) throw new Error(`学校未返回有效的${kind}列表`)
        const value = String(name).trim().toLowerCase()
        const exact = items.filter(item => [item.nameZh, item.code].some(x => String(x || '').trim().toLowerCase() === value))
        const matches = exact.length ? exact : items.filter(item => String(item.nameZh || '').toLowerCase().includes(value))
        if (matches.length !== 1) throw new Error(matches.length ? `${kind}名称不唯一，请提供完整名称` : `未找到指定${kind}，请核对名称`)
        return matches[0]
      }
      if (spec.building) {
        const buildings = await campusApi.call(web, intranetHost, '/student/ws/room/get-buildings', { query: { campusId: body.campusId, hasDataPermission: false }, targetHash })
        if (!current()) return { ok: false, reason: 'cancelled' }
        body.buildingId = String(select(buildings, spec.building, '教学楼').id)
      }
      if (spec.room) {
        if (!body.buildingId) throw new Error('指定教室时请一并说明教学楼')
        const rooms = await campusApi.call(web, intranetHost, '/student/ws/room/get-rooms', { query: { buildingId: body.buildingId, hasDataPermission: false }, targetHash })
        if (!current()) return { ok: false, reason: 'cancelled' }
        body.roomId = String(select(rooms, spec.room, '教室').id)
      }
      if (spec.room_type) {
        if (!Object.hasOwn(campusApi.ROOM_TYPES, spec.room_type)) throw new Error('无法识别教室类型，请使用学校页面上的类型名称')
        body.roomType = campusApi.ROOM_TYPES[spec.room_type]
      }
      result = await campusApi.call(web, intranetHost, '/student/ws/room-borrow/free-list', { method: 'POST', body, targetHash })
      if (!current()) return { ok: false, reason: 'cancelled' }
    } catch (error) {
      if (!current()) return { ok: false, reason: 'cancelled' }
      if (error.message === 'LOGIN_REQUIRED') {
        report('awaiting_login', '请在学校页面完成登录，登录后会自动查询')
        return { ok: false, reason: 'login_required' }
      }
      report('failed', '空教室查询未完成：' + error.message)
      return { ok: false, reason: 'query_failed' }
    }
    // 查询成功，进入「精简 + 回传」阶段。整段兜底 try，任何一步异常都明确上报，
    // 避免「查询成功但结果没回传」这类静默失败。
    let summary
    try { summary = campusApi.summarize(result.roomList) }
    catch (error) {
      report('failed', '学校返回的查询结果不完整，无法确认空教室情况')
      return { ok: false, reason: 'invalid_response' }
    }
    try {
      const response = await fetch(new URL(`/api/campus/tasks/${taskId}/result`, backend), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ date, units, summary, query: spec }),
        signal: AbortSignal.timeout(10000),
      })
      if (!response.ok) throw new Error('回传接口返回 ' + response.status)
    } catch (error) {
      report('failed', '查询已成功，但结果回传后端失败：' + (error && error.message || error))
      return { ok: false, reason: 'report_failed', summary }
    }
    if (current()) report('completed', `已查到 ${summary.total} 间空闲教室`, { result: summary, resultDate: date })
    return { ok: true, summary }
  }

  // —— 直接调学校接口（空教室查询），凭据不出本机 ——
  // payload: { taskId, token, query: { date, period, campusId, buildingId, seats } }
  handle('campus:query', async payload => {
    const taskId = String(payload?.taskId || '')
    const token = String(payload?.token || '')
    if (!/^[0-9a-f]{6,32}$/.test(taskId) || !token || !view) throw new Error('缺少有效的校园任务或登录状态')
    return runQuery(taskId, token, payload.query || {})
  })

  window.on('resize', applyBounds)
  return { dispose() {
    destroyView()
    disposed = true
    window.removeListener('resize', applyBounds)
    channels.forEach(name => ipcMain.removeHandler(name))
  } }
}

module.exports = { createCampusController }
