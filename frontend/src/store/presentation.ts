import { defineStore } from 'pinia'
import { deletePresentation, getPresentation, listPresentations, renamePresentation } from '../api/presentation'
import type { PptTask, PptTaskStatus, PptTaskSummary } from '../types/presentation'
let loadRun = 0, listRun = 0

/** 独立工作区状态，不占用当前聊天的消息或 PPT 状态。 */
export const usePresentationStore = defineStore('presentation', {
  state: () => ({ task: null as PptTask | null, tasks: [] as PptTaskSummary[], listError: '', listLoading: false, busy: false }),
  actions: {
    accept(status: PptTaskStatus) { ++loadRun; this.task = { ...status, taskId: status.task_id } },
    async loadList() {
      const run = ++listRun
      this.listLoading = true; this.listError = ''
      try { const tasks = await listPresentations(); if (run === listRun) this.tasks = tasks }
      catch (e: any) { if (run === listRun) this.listError = e.message || '读取任务失败，请重试' }
      finally { if (run === listRun) this.listLoading = false }
    },
    async load(taskId: string) {
      const run = ++loadRun
      const status = await getPresentation(taskId)
      if (run === loadRun) this.task = { ...status, taskId }
    },
    clearTask() { ++loadRun; this.task = null },
    async renameTask(taskId: string, title: string) {
      const status = await renamePresentation(taskId, title)
      this.tasks = this.tasks.map(task => task.task_id === taskId ? { ...task, title: status.title || title } : task)
      if (this.task?.taskId === taskId) this.accept(status)
      await this.loadList()
    },
    async removeTask(taskId: string) {
      await deletePresentation(taskId)
      ++listRun; this.listLoading = false
      this.tasks = this.tasks.filter(task => task.task_id !== taskId)
      if (this.task?.taskId === taskId) this.clearTask()
    },
  },
})
