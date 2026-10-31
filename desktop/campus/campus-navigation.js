// School page content is untrusted data; model output never becomes JavaScript.
function observePage() {
  const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim()
    .replace(/https?:\/\/\S+/gi, '[网址]')
    .replace(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/gi, '[邮箱]')
    .replace(/\b1\d{10}\b/g, '[电话]').replace(/\b\d{15,18}[\dXx]?\b/g, '[号码]').slice(0, 160)
  if (Array.from(document.querySelectorAll('input[type="password"]')).some(visible)) return { login: true }
  document.querySelectorAll('[data-sai-element]').forEach(el => el.removeAttribute('data-sai-element'))
  const nodes = Array.from(document.querySelectorAll('a,button,select,input,textarea,[role="button"],[role="menuitem"],[role="tab"],[role="combobox"],[onclick]'))
    .filter(el => visible(el) && !el.disabled && el.type !== 'hidden' && el.type !== 'password')
  const elements = []
  for (const node of nodes.slice(0, 120)) {
    const label = node.labels?.length ? Array.from(node.labels).map(el => el.textContent).join(' ') : ''
    const text = clean(node.getAttribute('aria-label') || label || node.getAttribute('placeholder') || node.innerText || node.title || (['button', 'submit'].includes(node.type) ? node.value : ''))
    const type = node.tagName.toLowerCase()
    if (!text && type !== 'select') continue
    const id = String(elements.length)
    node.setAttribute('data-sai-element', id)
    const item = { id, type, text, inputType: clean(node.getAttribute('type')) }
    if (type === 'select') item.options = Array.from(node.options).filter(o => !o.disabled).map(o => clean(o.textContent)).filter(Boolean).slice(0, 50)
    elements.push(item)
  }
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,legend,caption')).filter(visible).map(el => clean(el.textContent)).filter(Boolean).slice(0, 20)
  const tables = Array.from(document.querySelectorAll('table')).filter(visible).slice(0, 3).map(table => {
    const rows = Array.from(table.rows).slice(0, 20)
    if (!rows.length) return []
    const headings = Array.from(rows[0].cells).map(cell => clean(cell.innerText))
    const keep = headings.map((heading, index) => /教室|校区|楼|节次|时间|日期|容量|座位|状态|空闲|room|building|capacity/i.test(heading) ? index : -1).filter(i => i >= 0)
    return rows.map(row => keep.map(i => clean(row.cells[i]?.innerText)))
  }).filter(rows => rows.length && rows[0].length)
  return { login: false, title: clean(document.title), headings, elements, tables }
}

function executeAction(action) {
  const node = document.querySelector(`[data-sai-element="${action.index}"]`)
  if (!node || !node.getClientRects().length || node.disabled) return '页面已变化，操作未执行'
  const text = String(node.getAttribute('aria-label') || node.innerText || node.value || '').replace(/\s+/g, ' ').trim()
  if (action.kind === 'click') {
    // This prototype only navigates and queries; never submit other forms.
    if (/提交|申请|报名|删除|退选|保存|确认|支付|修改|注销|退出|withdraw|delete|submit|save/i.test(text)) return '该操作可能修改数据，未执行'
    if ((node.type === 'submit' || node.closest('form') && node.tagName === 'BUTTON') && !/查询|搜索|查找|检索|search|query/i.test(text)) return '该表单操作未执行'
    node.click()
    return '已点击 ' + text.slice(0, 80)
  }
  if (action.kind === 'select' && node.tagName === 'SELECT') {
    const options = Array.from(node.options).filter(o => !o.disabled && o.textContent.trim() === action.value)
    if (options.length !== 1) return '选项无法唯一匹配，未填写'
    node.value = options[0].value
  } else if (action.kind === 'fill' && ['INPUT', 'TEXTAREA'].includes(node.tagName) && !['password', 'hidden', 'submit', 'button', 'file'].includes(node.type)) {
    if (/密码|验证码|账号|用户名|学号|password|captcha|username/i.test(node.outerHTML.slice(0, 500))) return '登录或身份字段由用户填写'
    node.value = action.value
  } else return '控件类型不支持该操作'
  node.dispatchEvent(new Event('input', { bubbles: true }))
  node.dispatchEvent(new Event('change', { bubbles: true }))
  return '已填写 ' + action.value.slice(0, 80)
}

class CampusNavigator {
  constructor({ web, allowed, report, decide }) {
    Object.assign(this, { web, allowed, report, decide })
    this.timer = setInterval(() => { void this.inspect() }, 1500)
    this.running = false; this.paused = false; this.disposed = false; this.guard = false
    this.history = []; this.seen = new Set(); this.generation = 0; this.pending = null
    this.report('navigating', '正在打开学校系统')
  }
  async inspect() {
    if (this.disposed || this.paused || this.running || this.web.isDestroyed() || this.web.isLoading()) return
    this.running = true
    const generation = this.generation
    try {
      const pages = [], frames = []
      for (const frame of this.web.mainFrame.framesInSubtree) {
        if (!this.allowed(frame.url)) continue
        let page
        try { page = await frame.executeJavaScript(`(${observePage.toString()})()`) } catch { continue }
        if (this.disposed || this.paused || generation !== this.generation) return
        if (page.login) { this.guard = false; this.report('awaiting_login', '请在学校页面自行完成账号、密码及验证码认证'); return }
        const n = frames.length
        frames.push(frame)
        for (const element of page.elements) element.id = `${n}:${element.id}`
        pages.push(page)
      }
      if (!pages.length) return
      this.report('navigating', '正在理解学校页面并确定下一步')
      this.pending = new AbortController()
      const decision = await this.decide({ pages }, this.history, this.pending.signal)
      this.pending = null
      if (this.disposed || this.paused || generation !== this.generation) return
      if (decision.action === 'done' || decision.action === 'ask') {
        if (decision.action === 'done' && !pages.some(page => page.tables.some(table => table.length > 1))) {
          this.paused = true; this.guard = false; this.report('needs_user', '尚未读取到可确认的查询结果，请查看学校页面'); return
        }
        this.paused = true; this.guard = false
        this.report(decision.action === 'done' ? 'completed' : 'needs_user', decision.value || '请查看学校页面')
        return
      }
      const match = /^(\d+):(\d+)$/.exec(decision.element || '')
      const frame = match && frames[Number(match[1])]
      const item = match && pages[Number(match[1])]?.elements.find(el => el.id === decision.element)
      if (!frame || !item) throw new Error('页面元素已失效')
      const signature = JSON.stringify({ pages, action: decision.action, element: decision.element, value: decision.value })
      if (this.seen.has(signature)) {
        this.paused = true; this.guard = false; this.report('needs_user', '页面操作未推进，请检查学校页面后继续'); return
      }
      this.seen.add(signature)
      const action = { kind: decision.action, index: match[2], value: decision.value }
      this.guard = true
      const outcome = await frame.executeJavaScript(`(${executeAction.toString()})(${JSON.stringify(action)})`)
      if (this.disposed || this.paused || generation !== this.generation) return
      this.history.push({ action: decision.action, target: item.text, value: decision.value, result: outcome })
      if (this.history.length > 6) this.history.shift()
      if (outcome.includes('未执行') || outcome.includes('不支持') || outcome.includes('用户填写')) {
        this.paused = true; this.guard = false; this.report('needs_user', outcome); return
      }
      this.report('navigating', outcome)
    } catch (error) {
      if (!this.disposed && !this.paused && generation === this.generation) {
        this.paused = true; this.guard = false; this.report('needs_user', '页面操作或模型请求未完成，请检查后继续导航')
      }
    } finally { this.running = false }
  }
  resume() { this.paused = false; this.generation++; this.history = []; this.seen.clear(); this.report('navigating', '正在继续查看学校页面'); void this.inspect() }
  blocked() { this.paused = true; this.guard = false; this.generation++; this.pending?.abort(); this.report('needs_user', '学校页面需要提交数据，请自行确认并操作，之后可继续导航') }
  pause() { this.paused = true; this.guard = false; this.generation++; this.pending?.abort(); this.report('paused', '已暂停自动导航，可在学校页面自行操作') }
  dispose() { this.disposed = true; this.guard = false; this.generation++; this.pending?.abort(); clearInterval(this.timer) }
}
module.exports = { CampusNavigator }
