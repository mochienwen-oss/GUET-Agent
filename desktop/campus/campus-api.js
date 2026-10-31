// campus-api.js —— 用校园视图自己的会话调学校教务接口。
//
// 两种网络场景（用户可能在校内网，也可能在校外经 WebVPN）：
//
//   1. 内网直连：直接 POST 到内网域名（如 bkjwtest.guet.edu.cn），带会话 Cookie。
//      适用于校园网内，域名可达。
//   2. WebVPN：内网域名校外不可达，必须经 v.guet.edu.cn 网关转发。此时不能
//      主进程直连，而是在校园页面自己的 JS 上下文里用「同 hash 前缀的完整 URL」
//      fetch，让 WebVPN 网关透明完成 URL 重写和 Cookie 注入。
//
// 统一出口 call()：先看当前页面 URL 是不是 WebVPN 转发形态；是则直接走页面内
// fetch，否则（校内）先试内网直连、失败再回退 WebVPN。
// 凭据（Cookie）始终留在本机，不传后端、不传模型。

const UNITS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12']

// 自然语言时段 → 节次号（与 docs/guides/guet-empty-classroom-api.md 一致）
const PERIOD_UNITS = {
  morning: ['1', '2', '3', '4'],
  noon: ['5'],
  afternoon: ['6', '7', '8', '9'],
  evening: ['10', '11', '12'],
}

const ROOM_TYPES = { '普通': 1, '电教室': 2, '语音室': 3, '机房': 4, '实验室': 5,
  '专业实验室': 21, '基础实验室': 22, '实验场所': 23, '其他': 24 }

// 从当前页面 URL 解析 WebVPN 网关前缀，用于内网直连失败后回退。
//
// 深澜 wengine-vpn 的转发 URL 形态（抓包证实）：
//   https://v.guet.edu.cn/https/<encryptedHost>/<path>?vpn-12-o2-<真实host>
// 其中 <encryptedHost> 是 AES-CFB 加密后的内网 host（十六进制串），即「hash」。
// `vpn-12-o2-<真实host>` 是 wengine 网关识别「转发目标内网 host」的标记，参数名
// 直接拼接 host（无 `=`）。
//
// 关键陷阱（踩过两次）：不同服务的 hash 不同。
//   CAS 认证服务：...f3f652d220...?vpn-12-o2-cas.guet.edu.cn
//   教务服务：    ...f2fc4b8b33...?vpn-12-o2-bkjwtest.guet.edu.cn
//   pcportal 门户：...e0f451933524...?vpn-12-o2-pcportal.guet.edu.cn
// 登录跳转过程中，页面 URL 可能短暂停留在「CAS hash + /student/ 路径」或「pcportal
// hash + bkjwtest host 参数」等中间态（后者是门户重定向时改了 host 参数但 hash 没
// 同步）。若照单全收就会拿错 hash 去拼教务接口，网关 404。
//
// 因此这里双重校验：当前 URL 的 hash 前缀必须等于 expectHash，且 vpn-12-o2- 参数
// 必须指向 expectHost，两者都匹配才返回有效前缀；否则返回 null（还没到目标域）。
function vpnGateway(web, expectHost, expectHash) {
  try {
    const current = new URL(web.getURL())
    const m = current.pathname.match(/^(\/https\/[0-9a-f]+)\/?/)
    if (!m) return null
    const prefix = m[1]                       // 含 /https/ 前缀，用于拼 URL
    const hash = prefix.slice('/https/'.length)  // 纯 hash，用于与 targetHash 比较
    // 提取 vpn-12-o2- 后面的 host（参数名直接拼 host，无 =）
    const vpnHost = [...current.searchParams.keys()]
      .find(key => key.startsWith('vpn-12-o2-'))
      ?.slice('vpn-12-o2-'.length) || ''
    // 校验：hash 是唯一可靠的判据（每个内网服务 hash 固定且唯一，且教务 student
    // 页面的 Document URL 不带 vpn-12-o2- 参数）。expectHash 给了就以它为准；否则
    // 回退用 vpn-12-o2- 的 host 参数判断。
    if (expectHash) {
      if (hash !== expectHash) return null
    } else if (expectHost) {
      if (!vpnHost || vpnHost !== expectHost) return null
    }
    const vpn = [...current.searchParams.entries()]
      .filter(([key]) => /^vpn-12-o2/.test(key))
      .map(([key, value]) => value === '' ? key : `${key}=${value}`)
      .join('&')
    return { origin: current.origin, prefix, vpn, vpnHost }
  } catch {
    return null
  }
}

/**
 * 内网直连：直接请求内网域名。带该 session 的 Cookie。
 * 域名不可达时（3 秒超时）抛 error，调用方据此回退 WebVPN。
 */
async function directFetch(web, host, path, { method = 'GET', query = {}, body } = {}) {
  if (!host) throw new Error('未配置内网域名')
  const url = new URL(path, `https://${host}`)
  for (const [key, value] of Object.entries(query)) {
    if (value === '' || value === undefined || value === null) continue
    url.searchParams.set(key, String(value))
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 3000)
  try {
    const response = await web.session.fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
    return response
  } finally {
    clearTimeout(timer)
  }
}

/**
 * WebVPN：在校园页面 JS 上下文里 fetch，用「当前页面的 hash 前缀 + 目标 path」
 * 拼成完整转发 URL（与页面内真实请求同前缀），让 WebVPN 网关透明处理。
 *
 * 为什么不用相对路径：根相对路径 /student/... 会丢掉 hash 前缀，直接打到
 * v.guet.edu.cn/student/...（不是 WebVPN 转发路径）。所以必须从当前页面 URL
 * 解析出 hash 前缀，拼成和页面实际请求一致的完整 URL。
 */
async function vpnFetch(web, path, { method = 'GET', query = {}, body, host, targetHash } = {}) {
  const gw = vpnGateway(web, host, targetHash)
  if (!gw) throw new Error('LOGIN_REQUIRED')
  // 复用当前页面已有的 hash 前缀拼完整转发 URL。vpn-12-o2-<host> 参数是 wengine 网关
  // 识别「转发目标内网 host」的标记，教务请求必需；页面 Document URL 常不带它（只有
  // 页面内 XHR 带），所以这里优先复用页面已有的，否则用 intranetHost 主动补上。
  const url = new URL(gw.prefix + path, gw.origin)
  if (gw.vpn) {
    url.search = gw.vpn
  } else if (host) {
    // vpn-12-o2-<host> 是「参数名直接拼 host、无 = 无值」的形态，不能走 searchParams
    // （会多出一个 =）。直接拼进 search 字符串，与页面内真实请求一致。
    url.search = 'vpn-12-o2-' + host
  }
  for (const [key, value] of Object.entries(query)) {
    if (value === '' || value === undefined || value === null) continue
    url.searchParams.set(key, String(value))
  }
  const script = `
    (async () => {
      try {
        const resp = await fetch(${JSON.stringify(url.toString())}, {
          method: ${JSON.stringify(method)},
          headers: ${body ? "{'Content-Type':'application/json'}" : 'undefined'},
          body: ${body ? JSON.stringify(JSON.stringify(body)) : 'undefined'},
        })
        const text = await resp.text()
        return { ok: resp.ok, status: resp.status, text }
      } catch (e) {
        return { ok: false, status: 0, text: '', error: String(e && e.message || e) }
      }
    })()
  `
  const result = await web.executeJavaScript(script, true)
  if (!result || result.error) throw new Error('页面内请求失败：' + (result?.error || '未知'))
  if (!result.ok) {
    if (result.status === 401) throw new Error('LOGIN_REQUIRED')
    throw new Error(`学校接口返回 HTTP ${result.status}`)
  }
  return { text: result.text, status: result.status }
}

/** 判断响应体是不是"被重定向到登录页"的 HTML。 */
function isLoginRedirect(text) {
  return text.trim().startsWith('<!') || text.trim().startsWith('<')
}

/**
 * 统一入口：校外走 WebVPN（页面 URL 已是转发形态），校内先直连、失败回退 WebVPN。
 * @param {import('electron').WebContents} web
 * @param {string} host 内网域名（如 bkjwtest.guet.edu.cn）
 * @param {string} path 教务路径（如 /student/ws/room-borrow/free-list）
 */
async function call(web, host, path, options = {}) {
  // 把目标内网 host 注入 options，供 vpnFetch/vpnGateway 校验转发域是否匹配。
  const opts = { ...options, host }
  const targetHash = options.targetHash || ''
  // 校外走 WebVPN：只有当前页面 URL 已经落到「目标内网 host」对应的转发域
  // （vpn-12-o2- 指向 host，且 hash 前缀等于目标 hash）时，才直接用页面内 fetch。
  const gw = host ? vpnGateway(web, host, targetHash) : vpnGateway(web, host)
  if (gw) {
    return await callViaVpn(web, path, opts)
  }

  // 页面已处于 WebVPN 转发形态（/https/<hash>/）但 host/hash 不匹配目标服务，
  // 说明还停在 CAS/pcportal 等其它服务，此时内网域名校外必然不可达，别再白等
  // 3 秒直连，直接视为登录未完成。
  if (host && vpnGateway(web)) {
    throw new Error('LOGIN_REQUIRED')
  }

  // 校内：先试内网直连，失败再回退 WebVPN。
  let resp
  try { resp = await directFetch(web, host, path, options) } catch { /* 网络不可达时尝试 WebVPN */ }
  if (resp) {
    if (resp.status === 401) throw new Error('LOGIN_REQUIRED')
    if (!resp.ok) throw new Error(`学校接口返回 HTTP ${resp.status}`)
    const text = await resp.text()
    if (isLoginRedirect(text)) throw new Error('LOGIN_REQUIRED')
    try { return JSON.parse(text) } catch { throw new Error('学校接口返回了无法解析的内容') }
  }

  // 回退 WebVPN：此时若页面 URL 的 vpn-12-o2- 仍不指向目标 host，说明还没登录
  // 到教务域，视为登录未完成（vpnFetch 内部会抛 LOGIN_REQUIRED）。
  return callViaVpn(web, path, opts)
}

/** WebVPN 路径：页面内 fetch，结果做登录态判定与 JSON 解析。 */
async function callViaVpn(web, path, options) {
  const { text } = await vpnFetch(web, path, options)
  if (isLoginRedirect(text)) throw new Error('LOGIN_REQUIRED')
  try {
    return JSON.parse(text)
  } catch {
    throw new Error('学校接口返回了无法解析的内容')
  }
}

/** 把 free-list 的 roomList 精简成回传后端用的结构化结果，按楼栋分组。 */
function summarize(roomList) {
  if (!Array.isArray(roomList)) throw new Error('学校接口未返回有效的教室列表')
  const groups = new Map()
  for (const room of roomList || []) {
    const building = (room.building || {}).nameZh || '未知楼栋'
    const item = {
      code: room.code || room.nameZh,
      name: room.nameZh || '',
      type: (room.roomType || {}).nameZh || '',
      seats: room.seatsForLesson,
      remark: room.remark || '',
    }
    if (!groups.has(building)) groups.set(building, [])
    groups.get(building).push(item)
  }
  const buildings = [...groups.entries()].map(([name, rooms]) => ({ name, rooms }))
  buildings.sort((a, b) => b.rooms.length - a.rooms.length)
  return { total: (roomList || []).length, buildings }
}

module.exports = { UNITS, PERIOD_UNITS, ROOM_TYPES, call, summarize, vpnGateway }
