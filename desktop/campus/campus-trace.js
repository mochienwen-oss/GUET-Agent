// campus-trace.js —— 抓学校页面自己的 XHR / Fetch 请求，用来反推"功能接口"。
//
// 为什么单独一个文件：这是调试能力，默认关闭（config.campus.trace）；
// 开的时候只在本地写日志，不联网、不改页面、不碰 cookie 值。
//
// 抓什么：
//   · 请求：method / url / type / postData（表单或 JSON 正文）
//   · 响应：status / mimeType / 正文前 4000 字符（仅 JSON 与文本）
// 不抓什么：Cookie、Authorization、token 之类的请求头一律不写，避免把凭据落到磁盘。
const fs = require('node:fs')

const SKIP_TYPES = new Set(['Image', 'Stylesheet', 'Script', 'Font', 'Media', 'Other', 'Manifest', 'SignedExchange', 'Ping', 'CSPViolationReport', 'Preflight'])
const KEEP_TYPES = new Set(['XHR', 'Fetch', 'Document'])
// 响应正文上限：调大，避免空教室这类大 JSON 被截断、丢字段。
// 4000 会截断 free-list 的 roomList，改成 200000（约 200KB）。
const MAX_BODY = 200000

function shouldKeep(entry) {
  if (!entry || SKIP_TYPES.has(entry.type) || !KEEP_TYPES.has(entry.type)) return false
  if (!/^https?:/i.test(entry.url)) return false
  // 文档请求只记地址，静态资源（图片/字体/JS）已经在类型阶段过滤掉
  return true
}

function attachTrace(web, { file } = {}) {
  if (!web || web.isDestroyed() || !file) return () => {}
  const dbg = web.debugger
  try {
    dbg.attach('1.3')
  } catch {
    return () => {}
  }

  const pending = new Map()
  const startedAt = Date.now()

  const write = (record) => {
    try {
      fs.appendFileSync(file, JSON.stringify({ t: new Date().toISOString(), ...record }) + '\n')
    } catch { /* 磁盘写不进去不影响页面 */ }
  }

  const handle = async (_event, method, params) => {
    if (method === 'Network.requestWillBeSent') {
      const { requestId, type, request, documentURL } = params
      const entry = {
        type, url: request.url, method: request.method,
        postData: request.postData ? String(request.postData).slice(0, MAX_BODY) : '',
        initiator: documentURL || '', status: null, mimeType: '',
      }
      if (!shouldKeep(entry)) return
      pending.set(requestId, entry)
      return
    }
    if (method === 'Network.responseReceived') {
      const entry = pending.get(params.requestId)
      if (!entry) return
      entry.status = params.response.status
      entry.mimeType = params.response.mimeType || ''
      // 记录响应头里与"下一页/总量"有关的字段，帮助理解分页与权限
      const headers = params.response.headers || {}
      for (const name of ['content-length', 'x-total-count', 'x-total', 'content-range']) {
        if (headers[name] != null) entry['h_' + name] = headers[name]
      }
      return
    }
    if (method === 'Network.loadingFinished') {
      const entry = pending.get(params.requestId)
      if (!entry) return
      pending.delete(params.requestId)
      if (entry.type !== 'XHR' && entry.type !== 'Fetch') { write(entry); return }
      const textLike = /json|text|javascript|xml/i.test(entry.mimeType)
      if (!textLike) { write(entry); return }
      try {
        const body = await dbg.sendCommand('Network.getResponseBody', { requestId: params.requestId })
        const raw = body?.base64Encoded
          ? Buffer.from(body.body || '', 'base64').toString('utf8')
          : String(body?.body || '')
        entry.body = raw.slice(0, MAX_BODY)
        entry.bodyTruncated = raw.length > MAX_BODY
      } catch { /* 重定向或已释放的响应取不到正文 */ }
      write(entry)
      return
    }
  }

  dbg.on('message', handle)
  Promise.resolve(dbg.sendCommand('Network.enable', { maxPostDataSize: 65536 })).catch(() => {})
  write({ kind: 'session-start', elapsedMs: 0 })

  return function detach() {
    try { dbg.removeListener('message', handle) } catch { /* ignore */ }
    try { if (dbg.isAttached()) dbg.detach() } catch { /* ignore */ }
    write({ kind: 'session-end', elapsedMs: Date.now() - startedAt })
  }
}

module.exports = { attachTrace }
