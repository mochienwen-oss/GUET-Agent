// Temporary, scoped capture of classroom API field shapes and safe query values.
// Does not persist URLs, headers, arbitrary response values or non-classroom traffic.
const fs = require('node:fs')

const FIELDS = new Set(['campusId', 'buildingId', 'roomId', 'roomType', 'roomTypeId',
  'seatsForLessonGte', 'startDateTime', 'endDateTime', 'startTime', 'endTime',
  'weekdays', 'units', 'hasDataPermission'])
const SECRET = /password|passwd|captcha|cookie|authorization|csrf|token|ticket|session|secret|identity|student|user|phone|email/i

function cleanValue(value) {
  if (value === null || value === '') return value
  if (typeof value === 'boolean') return value
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && /^(?:\d{1,12}|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?)$/.test(value)) return value
  if (Array.isArray(value)) return value.slice(0, 30).map(cleanValue)
  return '[omitted]'
}

function fieldShape(value, depth = 0) {
  if (depth > 6) return '[depth limit]'
  if (value === null) return 'null'
  if (Array.isArray(value)) return value.length ? [fieldShape(value[0], depth + 1)] : []
  if (typeof value !== 'object') return typeof value
  const result = {}
  for (const [key, item] of Object.entries(value).slice(0, 100)) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,49}$/.test(key) || SECRET.test(key)) continue
    result[key] = fieldShape(item, depth + 1)
  }
  return result
}

function parameters(value, result = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return result
  for (const [key, item] of Object.entries(value)) {
    if (FIELDS.has(key)) result[key] = cleanValue(item)
    else if (!SECRET.test(key) && item && typeof item === 'object' && !Array.isArray(item)) parameters(item, result)
  }
  return result
}

function attachParameterTrace(web, { file }) {
  if (!web || web.isDestroyed() || !file) return () => {}
  const dbg = web.debugger
  try { dbg.attach('1.3') } catch { return () => {} }
  const pending = new Map()
  const write = row => { try { fs.appendFileSync(file, JSON.stringify(row) + '\n') } catch {} }
  function handle(_event, method, params) {
    if (method === 'Network.requestWillBeSent') {
      if (!['XHR', 'Fetch'].includes(params.type)) return
      let url
      try { url = new URL(params.request.url) } catch { return }
      if (url.hostname !== 'v.guet.edu.cn' && url.hostname !== 'bkjwtest.guet.edu.cn') return
      const match = url.pathname.match(/\/student\/ws\/(room-borrow|room)\/([a-z-]+)\/?$/i)
      if (!match) return
      const row = { time: new Date().toISOString(), endpoint: `/student/ws/${match[1]}/${match[2]}`,
        method: params.request.method, query: {}, payloadFields: {}, payloadValues: {}, status: null }
      for (const [key, value] of url.searchParams) if (FIELDS.has(key)) row.query[key] = cleanValue(value)
      const body = params.request.postData
      if (body) {
        try { const parsed = JSON.parse(body); row.payloadFields = fieldShape(parsed); row.payloadValues = parameters(parsed) }
        catch { row.payloadFields = { format: 'non-json-or-unavailable' } }
      }
      pending.set(params.requestId, row)
    } else if (method === 'Network.responseReceived') {
      const row = pending.get(params.requestId)
      if (row) { row.status = params.response.status; row.mimeType = params.response.mimeType || '' }
    } else if (method === 'Network.loadingFinished') {
      const row = pending.get(params.requestId)
      if (!row) return
      pending.delete(params.requestId)
      void dbg.sendCommand('Network.getResponseBody', { requestId: params.requestId }).then(body => {
        try {
          const raw = body.base64Encoded ? Buffer.from(body.body || '', 'base64').toString('utf8') : String(body.body || '')
          row.responseFields = fieldShape(JSON.parse(raw))
        } catch { row.responseFields = 'non-json-or-unavailable' }
        write(row)
      }).catch(() => { row.responseFields = 'unavailable'; write(row) })
    } else if (method === 'Network.loadingFailed') {
      const row = pending.get(params.requestId)
      if (row) { pending.delete(params.requestId); row.status = 'failed'; write(row) }
    }
  }
  dbg.on('message', handle)
  void dbg.sendCommand('Network.enable', { maxPostDataSize: 65536 }).catch(() => {})
  return () => { try { dbg.removeListener('message', handle); if (dbg.isAttached()) dbg.detach() } catch {} }
}
module.exports = { attachParameterTrace }
