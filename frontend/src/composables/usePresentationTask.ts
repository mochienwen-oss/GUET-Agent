import { ref } from 'vue'
import { confirmPresentation, getPresentationBlob, presentationResumeUrl } from '../api/presentation'
import { useChatStore } from '../store/chat'
import type { useChatStream } from './useChatStream'

/** PPT 状态恢复、版本确认与文件下载。 */
export function usePresentationTask(
  runStream: ReturnType<typeof useChatStream>['runStream'],
  scrollToBottom: () => Promise<void>
) {
  const chat = useChatStore()
  const pptBusy = ref(false)

  async function syncPresentation(taskId: string) {
    await chat.syncPpt(taskId)
    if (chat.currentId !== null) localStorage.setItem(`ppt_task_${chat.currentId}`, taskId)
    await scrollToBottom()
  }
  async function refreshPresentation() { if (chat.pptTask) await chat.syncPpt(chat.pptTask.taskId) }

  async function downloadPpt() {
    const ready = chat.pptTask?.ready
    if (!ready) return
    try {
      const blob = await getPresentationBlob(ready.download_url)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = ready.filename
      link.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      if (chat.pptTask) chat.pptTask.error = { stage: 'download', error: e.message, recoverable: true }
    }
  }

  async function resumePpt() {
    const taskId = chat.pptTask?.taskId
    if (!taskId || chat.currentId === null || chat.streaming || chat.pptRunning) return
    try {
      await chat.syncPpt(taskId)
    } catch (e: any) {
      if (chat.pptTask) chat.pptTask.error = { stage: 'resume', error: e.message, recoverable: true }
      return
    }
    // 已经能看（待确认 / 已确认）或仍在等澄清 → 刷新状态就够，不需要续跑图。
    if (['draft', 'ready', 'clarify'].includes(chat.pptTask?.stage || '')) return
    const assistantIndex = chat.messages.length - 1
    await runStream(chat.currentId, '', assistantIndex, undefined,
      presentationResumeUrl(taskId))
  }

  /** 人工确认位：**只有这一步之后**后端才会签发下载地址（`ppt/delivery.py::confirm`）。 */
  async function confirmPpt() {
    const task = chat.pptTask
    if (!task?.taskId || pptBusy.value) return
    pptBusy.value = true
    try {
      const res = await confirmPresentation(task.taskId, task.revision ?? 0)
      chat.pptTask = { ...task, stage: 'ready', pages: res.pages, ready: res }
    } catch (e: any) {
      // 审阅内容留在原地，只在上面加一行错误 —— 确认失败不该让人丢掉刚看的东西。
      chat.pptTask = { ...task, error: { stage: 'confirm', error: e.message, recoverable: true } }
    } finally {
      pptBusy.value = false
    }
  }


  return { pptBusy, syncPresentation, refreshPresentation, downloadPpt, resumePpt, confirmPpt }
}
