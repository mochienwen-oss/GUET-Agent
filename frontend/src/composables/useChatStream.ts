import { ref, type Ref } from 'vue'
import { createConversation } from '../api/chat'
import { streamChat } from '../api/stream'
import type { ChatFileRef } from '../types/chat'
import { useAuthStore } from '../store/auth'
import { useCampusStore } from '../store/campus'
import { useChatStore } from '../store/chat'

/** 聊天操作与流事件应用；DOM 滚动交由页面提供。 */
export function useChatStream(input: Ref<string>, scrollToBottom: (force?: boolean) => Promise<void>) {
  const auth = useAuthStore()
  const chat = useChatStore()
  const campus = useCampusStore()
  const abortController = ref<AbortController | null>(null)

  async function send(text?: string, pptTaskId?: string, attachments?: ChatFileRef[]) {
    const question = typeof text === 'string' ? text.trim() : input.value.trim()
    if (!question || chat.streaming || chat.pptRunning) return
    input.value = ''

    // 首次提问：先建会话拿到 id
    let convId: number | null = chat.currentId
    if (convId === null) {
      // ★ 传**原始问题**，由后端统一生成占位标题（`backend/titles.py` 是单一来源）。
      //   原先前端截 30 字、后端也截 30 字，两处各写一份，改一处就会漂移；
      //   而且 30 字这个数本身就是错的 —— 侧边栏只看得见约 15 个字。
      const conversation = await createConversation(question)
      convId = Number(conversation.id)
      chat.currentId = convId
    }

    chat.messages.push({ role: 'user', content: question })
    chat.messages.push({ role: 'assistant', content: '' })
    // 附件跟着本轮走：界面显示文件名，网络只传 id，后端会再校验归属
    if (attachments?.length) chat.messages[chat.messages.length - 2].files = attachments
    // 立即刷新侧边栏：新会话马上出现、已有会话即时置顶，而不是等流式回答结束才刷
    await chat.loadConversations()
    await scrollToBottom(true)
    await runStream(convId, question, chat.messages.length - 1, pptTaskId, undefined,
      attachments?.map(file => file.file_id))
  }

  async function runStream(convId: number, question: string, assistantIndex: number,
    pptTaskId?: string, resumeUrl?: string, attachments?: string[]) {
    const controller = new AbortController()
    abortController.value = controller
    chat.streaming = true
    try {
      await receiveStream(convId, question, assistantIndex, controller, pptTaskId, resumeUrl, attachments)
      await chat.loadConversations()
    } catch (e: any) {
      if (e?.name === 'AbortError') {
        if (!chat.messages[assistantIndex].content.trim()) {
          chat.messages[assistantIndex].content = '（已停止生成）'
        }
      } else {
        chat.messages[assistantIndex].content = e.message || '请求失败'
      }
    } finally {
      // 只有当前流可以清空自己的控制器。
      if (abortController.value === controller) {
        chat.streaming = false
        abortController.value = null
      }
      await scrollToBottom()
    }
  }

  function stopStreaming() {
    abortController.value?.abort()
    if (chat.pptRunning && chat.pptTask) {
      chat.pptRunning = false
      chat.pptTask.stage = 'paused'
      chat.pptTask.error = { stage: 'paused', error: '用户中断', recoverable: true }
    }
  }

  async function regenerate(index: number) {
    if (chat.streaming || chat.currentId === null) return
    // 找到该 assistant 之前最近的一条 user 消息作为重新提问的问题
    let userIndex = index
    while (userIndex >= 0 && chat.messages[userIndex].role !== 'user') userIndex--
    if (userIndex < 0) return
    const question = chat.messages[userIndex].content

    // 删除这条 assistant 及其之后所有消息，重新生成
    chat.messages.splice(index)
    chat.messages.push({ role: 'assistant', content: '' })
    await scrollToBottom(true)
    await runStream(chat.currentId, question, chat.messages.length - 1)
  }

  async function receiveStream(
    conversationId: number, message: string, assistantIndex: number,
    controller: AbortController, pptTaskId?: string, resumeUrl?: string, attachments?: string[]
  ) {
    const target = chat.messages[assistantIndex]

    // ★ 流式刷新的调度策略（这是「回答动画经常卡住」的根因修复）。
    //
    //   原来用 `requestAnimationFrame` + `if (!frame)` 判断是否已排程。两个坑：
    //     ① rAF 在窗口失焦/最小化/被系统节流时**不回调**，而 `frame` 已经写入了
    //        非 0 的 id —— 之后所有 chunk 都撞上 `if (!frame)` 而不再排程，
    //        界面就**永久停在那一段**，直到流结束才一次性补齐；
    //     ② 每个 chunk 都触发一次 markdown 全文重解析 + 一次强制重排（读
    //        scrollHeight），回答越长单帧越扛不住，最后表现为"打字一格一格跳"。
    //
    //   改成**时间戳节流 + setTimeout**：setTimeout 在后台只会被降到 1s 一次，
    //   不会丢失；再配 `visibilitychange` 立即补齐，切回前台马上看到最新文本。
    const FLUSH_MS = 60
    let pending = ''
    let timer = 0
    let lastFlush = 0
    let scrollTimer = 0

    function flush() {
      if (timer) {
        clearTimeout(timer)
        timer = 0
      }
      lastFlush = performance.now()
      if (!pending) return
      target.content += pending
      pending = ''
      scheduleScroll()
    }

    function schedule() {
      if (timer) return
      const wait = Math.max(0, FLUSH_MS - (performance.now() - lastFlush))
      timer = window.setTimeout(() => {
        timer = 0
        flush()
      }, wait)
    }

    // 滚动也节流：每帧写 scrollTop 会强制同步重排（读 scrollHeight 触发 layout），
    // 和文本刷新叠加就是肉眼可见的抖动。
    function scheduleScroll() {
      if (scrollTimer) return
      scrollTimer = window.setTimeout(() => {
        scrollTimer = 0
        void scrollToBottom()
      }, 90)
    }

    // 切回前台立即补齐积压文本，不等下一个节流窗口
    function onVisible() {
      if (document.visibilityState === 'visible') flush()
    }
    document.addEventListener('visibilitychange', onVisible)

    try {
      await streamChat({ conversationId, message, token: auth.token,
        signal: controller.signal, pptTaskId, resumeUrl, attachments }, async obj => {
        if (obj.content) {
          pending += obj.content
          schedule()
        }
        // 整体替换正文；空串用于清除工具调用前的操作说明，最终正文与落库一致。
        if (obj.replace !== undefined) {
          if (timer) { clearTimeout(timer); timer = 0 }
          pending = ''
          if (target.content !== obj.replace) target.content = obj.replace
          // Keep the conversation busy until the server releases its turn lease.
          scheduleScroll()
        }
        if (obj.message_id !== undefined) target.id = obj.message_id
        if (obj.retrieved_docs !== undefined) target.retrieved_docs = obj.retrieved_docs
        // 表格结果文件：挂到本条回答下；刷新后由消息的 files 字段恢复。
        if (obj.file) {
          target.files = [...(target.files || []), obj.file]
          scheduleScroll()
        }
        // 校园服务任务：打开学校页面；带 query 时登录后直接触发查询。
        if (obj.campus_action) {
          const a = obj.campus_action
          void campus.open(a.task_id, a.service, a.query || undefined)
        }
        // ★ 会话标题：后端在首答定稿后用模型生成的短标题（见 backend/titles.py）。
        //   就地更新，不必再拉一次会话列表。
        if (obj.title) {
          const conv = chat.conversations.find((c) => c.id === conversationId)
          if (conv) conv.title = obj.title
        }
        if (obj.error) {
          if (timer) { clearTimeout(timer); timer = 0 }
          pending = ''
          target.content = obj.error
          scheduleScroll()
        }
        if (obj.ppt_task) {
          const task = obj.ppt_task
          localStorage.setItem(`ppt_task_${conversationId}`, task.task_id)
          chat.pptTask = { taskId: task.task_id, stage: task.stage,
            progress: task.progress, question: task.question }
          chat.pptRunning = !['clarify', 'draft', 'ready'].includes(task.stage)
          if (task.renderer === 'ppt-master') void chat.syncPpt(task.task_id)
        }
        // ★ 已生成待确认：**事件里没有下载地址**（那是确认之后的事），页数之外的
        //   逐页内容与来源要另取一次 —— 走 store 的 `syncPpt`（唯一映射点）。
        if (obj.ppt_draft) {
          localStorage.setItem(`ppt_task_${conversationId}`, obj.ppt_draft.task_id)
          chat.pptRunning = false
          void chat.syncPpt(obj.ppt_draft.task_id).catch((e: Error) => {
            if (chat.pptTask) {
              chat.pptTask.error = { stage: 'draft', error: e.message, recoverable: true }
            }
          })
        }
        if (obj.ppt_error) {
          chat.pptTask = { taskId: obj.ppt_error.task_id || chat.pptTask?.taskId || '',
            stage: 'failed', error: obj.ppt_error }
          chat.pptRunning = false
        }
      })
    } finally {
      document.removeEventListener('visibilitychange', onVisible)
      if (timer) { clearTimeout(timer); timer = 0 }
      if (scrollTimer) { clearTimeout(scrollTimer); scrollTimer = 0 }
      // 收尾必须把最后一段一次性落盘，否则会丢掉尾部几十毫秒的内容
      if (pending) {
        target.content += pending
        pending = ''
      }
      void scrollToBottom()
    }
  }

  return { send, runStream, stopStreaming, regenerate }
}
