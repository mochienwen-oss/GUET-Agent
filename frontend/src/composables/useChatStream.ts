import { ref, type Ref } from 'vue'
import { createConversation } from '../api/chat'
import { streamChat } from '../api/stream'
import { useAuthStore } from '../store/auth'
import { useChatStore } from '../store/chat'

/** 聊天操作与流事件应用；DOM 滚动交由页面提供。 */
export function useChatStream(input: Ref<string>, scrollToBottom: (force?: boolean) => Promise<void>) {
  const auth = useAuthStore()
  const chat = useChatStore()
  const abortController = ref<AbortController | null>(null)

  async function send(text?: string, pptTaskId?: string) {
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
    // 立即刷新侧边栏：新会话马上出现、已有会话即时置顶，而不是等流式回答结束才刷
    await chat.loadConversations()
    await scrollToBottom(true)
    await runStream(convId, question, chat.messages.length - 1, pptTaskId)
  }

  async function runStream(convId: number, question: string, assistantIndex: number,
    pptTaskId?: string, resumeUrl?: string) {
    const controller = new AbortController()
    abortController.value = controller
    chat.streaming = true
    try {
      await receiveStream(convId, question, assistantIndex, controller, pptTaskId, resumeUrl)
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
    controller: AbortController, pptTaskId?: string, resumeUrl?: string
  ) {
    await streamChat({ conversationId, message, token: auth.token,
      signal: controller.signal, pptTaskId, resumeUrl }, async obj => {
      // ★ 追加：流式增量
      if (obj.content) chat.messages[assistantIndex].content += obj.content
      // 整体替换正文；空串用于清除工具调用前的操作说明，最终正文与落库一致。
      if (obj.replace !== undefined) {
        chat.messages[assistantIndex].content = obj.replace
        // Keep the conversation busy until the server releases its turn lease.
      }
      if (obj.message_id !== undefined) chat.messages[assistantIndex].id = obj.message_id
      if (obj.retrieved_docs !== undefined) chat.messages[assistantIndex].retrieved_docs = obj.retrieved_docs
      // ★ 会话标题：后端在首答定稿后用模型生成的短标题（见 backend/titles.py）。
      //   就地更新，不必再拉一次会话列表。
      if (obj.title) {
        const conv = chat.conversations.find((c) => c.id === conversationId)
        if (conv) conv.title = obj.title
      }
      if (obj.error) chat.messages[assistantIndex].content = obj.error
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
      await scrollToBottom()
    })
  }

  return { send, runStream, stopStreaming, regenerate }
}
