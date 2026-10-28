// store/chat.ts —— 会话与消息状态
import { defineStore } from 'pinia'
import { listConversations, getMessages, renameConversation, deleteConversation } from '../api/chat'
import { getPresentation, getPresentationDownload } from '../api/presentation'
import type { Conversation, Message } from '../types/chat'
import type { PptTask } from '../types/presentation'

function readPinnedIds(): number[] {
  try {
    return JSON.parse(localStorage.getItem('pinned_conversations') || '[]')
  } catch {
    return []
  }
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversations: [] as Conversation[],
    messages: [] as Message[],
    currentId: null as number | null,
    streaming: false,
    pptRunning: false,
    pptTask: null as PptTask | null,
    pinnedIds: readPinnedIds(),
  }),
  actions: {
    async loadConversations() {
      this.conversations = await listConversations()
    },
    async loadMessages(id: number) {
      const messages = await getMessages(id)
      this.currentId = id
      this.messages = messages
      this.pptTask = null
      this.pptRunning = false
      const taskId = localStorage.getItem(`ppt_task_${id}`)
      if (taskId) {
        try {
          await this.syncPpt(taskId)
        } catch {
          localStorage.removeItem(`ppt_task_${id}`)
        }
      }
    },
    async syncPpt(taskId: string): Promise<void> {
      const status = await getPresentation(taskId)
      const base: PptTask = {
        taskId, stage: status.stage, pages: status.pages,
        slides: status.slides, sources: status.sources, revision: status.revision,
        previews: status.previews, stale_slide_ids: status.stale_slide_ids,
        revision_error: status.revision_error,
        brief: status.brief, materials: status.materials, workflow_version: status.workflow_version,
        renderer: status.renderer, pending_user: status.pending_user,
      }
      if (status.stage === 'ready') {
        const ready = await getPresentationDownload(taskId)
        this.pptTask = { ...base, ready }
      } else if (status.stage === 'await_clarify') {
        this.pptTask = { ...base, stage: 'clarify', question: status.pending_user?.question }
      } else if (status.stage === 'failed') {
        this.pptTask = { ...base, stage: 'failed',
          error: { stage: 'author', error: 'ppt_generation_failed', recoverable: false } }
      } else if (['draft', 'await_materials', 'materials_changed', 'brief_changed', 'generating', 'await_design', 'master_continue'].includes(status.stage)) {
        this.pptTask = base
      } else {
        this.pptTask = { ...base, stage: 'paused',
          error: { stage: status.stage, error: 'task_paused', recoverable: true } }
      }
      // 待确认 / 已确认 / 等澄清都不算「正在跑」—— 输入框和发送键据此解禁。
      this.pptRunning = !['draft', 'ready', 'clarify', 'await_design', 'await_materials', 'materials_changed', 'brief_changed', 'failed', 'paused'].includes(this.pptTask.stage)
    },
    newConversation() {
      this.currentId = null
      this.messages = []
      this.pptTask = null
      this.pptRunning = false
    },
    async rename(id: number, title: string) {
      await renameConversation(id, title)
      await this.loadConversations()
    },
    async remove(id: number) {
      await deleteConversation(id)
      if (this.currentId === id) this.newConversation()
      await this.loadConversations()
    },
    togglePin(id: number) {
      const i = this.pinnedIds.indexOf(id)
      if (i >= 0) this.pinnedIds.splice(i, 1)
      else this.pinnedIds.unshift(id)
      localStorage.setItem('pinned_conversations', JSON.stringify(this.pinnedIds))
    },
  },
})
