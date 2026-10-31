import { defineStore } from 'pinia'
import type { Pinia } from 'pinia'
import { computed, ref, watch } from 'vue'
import api from '../api'
import { useAuthStore } from './auth'
import { useChatStore } from './chat'
import { desktopBridge, isDesktop, type CampusQuery, type CampusRect, type CampusStatus, type CampusSummary } from '../api/desktop'
import { queryFreeClassroom } from '../api/campus'

const STATUS_TEXT: Record<string, string> = {
  awaiting_login: '等待登录', navigating: '正在前往查询页面', at_query_page: '已到达查询页',
  filled: '已填写可识别的条件', completed: '查询已完成', needs_user: '需要你接手', paused: '自动导航已暂停', failed: '未能完成',
}
const STATUS_HINT: Record<string, string> = {
  awaiting_login: '请在学校页面完成登录，完成后会自动继续寻找空教室查询入口。',
  navigating: '正在从学校页面中寻找教务和空教室查询入口。',
  at_query_page: '已打开空教室查询表单，请核对日期、校区和节次后查询。',
  filled: '已填写能够明确匹配的条件，其他条件请在页面核对后查询。',
  completed: '查询已完成，结果以学校页面实际显示为准。',
  needs_user: '自动导航需要你协助一步，处理后可以继续导航。',
  paused: '你可以在学校页面自行操作，或恢复自动导航。',
  failed: '学校页面未能完成加载，请检查网络或刷新重试。',
}

export const useCampusStore = defineStore('campus', () => {
  const auth = useAuthStore(), chat = useChatStore()
  const available = ref(isDesktop()), taskId = ref(''), service = ref('')
  const conversationId = ref<number | null>(null)
  const status = ref<CampusStatus['status'] | ''>(''), detail = ref(''), visible = ref(false)
  const panelWidth = ref(680), expanded = ref(false), url = ref(''), zoom = ref(1), autoZoom = ref(true)
  const result = ref<CampusSummary | null>(null), resultDate = ref(''), querying = ref(false)
  let bound = false, sequence = 0
  let pendingQuery: CampusQuery | null = null
  let statusSave: Promise<void> = Promise.resolve()
  const serviceName = computed(() => service.value === 'empty_classroom' ? '空教室查询' : '校园服务')
  const statusText = computed(() => STATUS_TEXT[status.value] || '准备打开学校页面')
  const statusHint = computed(() => STATUS_HINT[status.value] || '正在准备校园服务')
  const awaiting = computed(() => ['awaiting_login', 'navigating'].includes(status.value))
  const active = computed(() => !!taskId.value && conversationId.value === chat.currentId)

  function clear() {
    sequence++
    taskId.value = ''; service.value = ''; conversationId.value = null
    status.value = ''; detail.value = ''; visible.value = false; expanded.value = false; url.value = ''
    result.value = null; resultDate.value = ''; querying.value = false; pendingQuery = null
  }
  function attach() {
    const bridge = desktopBridge()
    available.value = !!bridge
    if (!bridge || bound) return
    bound = true
    bridge.onCampusStatus(payload => {
      if (!auth.token || !taskId.value || payload.taskId !== taskId.value) return
      status.value = payload.status; detail.value = payload.detail || ''
      // 查询完成：后端已把结果作为一条消息落库，重新拉取消息列表让结果进入对话流。
      if (payload.status === 'completed') {
        if (chat.currentId) void chat.loadMessages(chat.currentId)
      }
      // 登录完成后，若有待执行的查询条件，自动补一次查询。
      if (pendingQuery && !['awaiting_login', 'navigating'].includes(payload.status)) {
        const q = pendingQuery
        pendingQuery = null
        void query(q)
      }
      const token = auth.token
      statusSave = statusSave.then(async () => {
        if (auth.token !== token) return
        await api.post(`/campus/tasks/${payload.taskId}/status`, {
          status: payload.status, detail: payload.detail || '',
        })
      }).catch(() => {
        if (taskId.value === payload.taskId && auth.token === token) detail.value = `${payload.detail || ''}（进度保存失败）`
      })
    })
    bridge.onCampusVisible(payload => { visible.value = !!payload.visible })
    bridge.onCampusUrl(payload => { url.value = payload.url || '' })
    bridge.onCampusZoom(payload => { zoom.value = payload.factor; autoZoom.value = payload.auto })
    watch(() => auth.token, () => { clear(); void bridge.resetCampus() })
    watch(() => chat.currentId, async id => {
      if (id === conversationId.value) return
      const request = ++sequence
      if (visible.value) await hide()
      if (!id || !auth.token) return
      try {
        const { data } = await api.get(`/conversations/${id}/campus-task`)
        if (request !== sequence || !data.task) return
        taskId.value = data.task.task_id; service.value = data.task.service
        conversationId.value = id; status.value = 'paused'
        detail.value = '校园页面尚未恢复，可以重新打开继续。'
      } catch { /* A conversation without an available task keeps the normal chat UI. */ }
    }, { immediate: true })
  }
  async function open(id: string, svc: string, pendingQuery?: CampusQuery) {
    const bridge = desktopBridge()
    // 网页端没有内嵌学校视图，但查询本身是 HTTP 接口 —— 直接查，不在这里拦住。
    // （Electron 里仍会打开视图，那是给"后续爬别的接口"留的能力，不是查询的前置条件。）
    if (!bridge) {
      available.value = false
      taskId.value = id
      service.value = svc
      if (pendingQuery) void query(pendingQuery)
      else detail.value = '缺少查询条件，请把时间和时段再说一次'
      return
    }
    const request = ++sequence, token = auth.token
    try {
      const { data } = await api.get(`/campus/tasks/${id}`)
      if (request !== sequence || token !== auth.token) return
      taskId.value = id; service.value = svc; conversationId.value = data.task.conversation_id
      status.value = 'navigating'; detail.value = ''
      // 查询条件已由后端翻译进 task.query，主进程 open 后据此自动查询；前端无需再传。
      // 任务已有查询结果，且本次没有新的查询条件（pendingQuery 为空）：结果已在
      // 消息流里展示，这里只需不再重新打开学校页面。若带新条件则继续重新查询。
      if (data.task.result && data.task.result.summary && !pendingQuery) {
        status.value = 'completed'
        detail.value = `已查到 ${data.task.result.summary.total} 间空闲教室`
        visible.value = false
        return
      }
      const opened = await bridge.openCampus({ taskId: id, token })
      if (request === sequence && opened.ok) {
        visible.value = true
        setWidth(panelWidth.value)
        // 查询由主进程在登录后自动触发（campus:open 已读取 task.query 并设 pendingQuery），
        // 前端不再重复调用 query，避免与主进程自动查询冲突、导致结果重复回传。
      }
    } catch (error: any) {
      if (request !== sequence) return
      status.value = 'failed'; detail.value = error?.message || '无法打开学校页面'
    }
  }
  async function hide() { await desktopBridge()?.hideCampus(); visible.value = false; expanded.value = false }
  async function close() { await hide() }
  async function reload() { await desktopBridge()?.reloadCampus() }
  async function back() { await desktopBridge()?.backCampus() }
  async function forward() { await desktopBridge()?.forwardCampus() }
  async function resume() { await desktopBridge()?.resumeCampus() }
  async function pause() { await desktopBridge()?.pauseCampus() }
  async function changeZoom(direction: 'in' | 'out' | 'fit') { await desktopBridge()?.zoomCampus(direction) }

  /** 用桌面端本机会话直接查空教室，结果回传后端后展示。 */
  // ★ 查询走服务端接口，**不判断是否在桌面端**。
  //   教务的 free-list 是纯 HTTP JSON 接口，服务端在用户登录学校账号时已经把会话
  //   留下来了（remember_session），所以浏览器里直接调就行。
  //   Electron 只负责内嵌学校网页视图（后续爬别的接口用），不再参与查询。
  async function query(spec: CampusQuery) {
    if (!auth.token) return
    querying.value = true
    try {
      const date = String(spec.date || spec.start_date || '').slice(0, 10)
      const data = await queryFreeClassroom({
        date,
        units: spec.units,
        period: spec.period,
        buildingId: spec.building,
        seats: spec.seats ?? undefined,
        campusId: spec.campusId,
      })
      status.value = 'completed'
      detail.value = `已查到 ${data.summary.total} 间空闲教室`
      // 结果落库成一条助手消息，刷新页面/切会话后仍在（复用现有的 result 接口）
      if (taskId.value) {
        await api.post(`/campus/tasks/${taskId.value}/result`, {
          date: data.date, units: data.units, summary: data.summary, query: spec,
        }).catch(() => { /* 落库失败不影响本次展示 */ })
        if (chat.currentId) await chat.loadMessages(chat.currentId)
      }
    } catch (error: any) {
      const message = error?.response?.data?.error || error?.message || '查询失败'
      // 会话缺失或过期 → 引导去登录，而不是笼统报错
      status.value = /登录|会话/.test(message) ? 'awaiting_login' : 'failed'
      detail.value = message
    } finally {
      querying.value = false
    }
  }
  function setWidth(width: number) {
    const sidebar = document.getElementById('app-sidebar')?.getBoundingClientRect().width || 0
    const limit = Math.max(340, Math.min(1100, window.innerWidth - sidebar - 380))
    panelWidth.value = Math.max(340, Math.min(Math.round(width), limit))
  }
  function toggleExpanded() { expanded.value = !expanded.value }
  function pushBounds(rect: CampusRect) { void desktopBridge()?.setCampusBounds(rect) }
  return { available, taskId, service, conversationId, status, detail, visible, panelWidth, expanded, url,
    zoom, autoZoom, result, resultDate, querying, serviceName, statusText, statusHint, awaiting, active,
    attach, open, hide, close, reload, back, forward, resume, pause, changeZoom, query,
    setWidth, toggleExpanded, pushBounds }
})

export function installCampusBridge(pinia?: Pinia) { useCampusStore(pinia).attach() }
