<template>
  <div class="presentation-actions" :class="{ compact }" @click.stop>
    <div class="action-buttons">
      <button type="button" :disabled="unavailable" :aria-label="`重命名演示文稿：${task.title}`" :title="unavailable ? '制作结束后可重命名' : '重命名'" @click="open('rename')"><Icon name="edit" :size="compact ? 13 : 15" /></button>
      <button type="button" :disabled="unavailable" :aria-label="`删除演示文稿：${task.title}`" :title="unavailable ? '制作结束后可删除' : '删除'" @click="open('delete')"><Icon name="trash" :size="compact ? 13 : 15" /></button>
    </div>
    <Teleport to="body">
      <dialog ref="dialog" class="presentation-dialog" :aria-label="mode === 'rename' ? '重命名演示文稿' : '删除演示文稿'" @cancel.prevent="close" @click.self="close">
        <form v-if="mode" @submit.prevent="submit">
          <h2>{{ mode === 'rename' ? '重命名演示文稿' : '删除演示文稿' }}</h2>
          <template v-if="mode === 'rename'">
            <p>只修改作品名称，不改变内容或制作要求。</p>
            <label for="presentation-title">作品名称</label><input id="presentation-title" ref="titleInput" v-model="name" maxlength="120" required :disabled="saving" autocomplete="off" />
          </template>
          <p v-else>确定删除「{{ task.title }}」吗？本次任务的对话、参考材料和所有版本将一并删除，删除后无法恢复。</p>
          <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
          <div class="dialog-actions"><button type="button" class="cancel" :disabled="saving" @click="close">取消</button><button :class="mode === 'delete' ? 'danger' : 'confirm'" :disabled="saving || (mode === 'rename' && !name.trim())">{{ saving ? '正在处理…' : mode === 'rename' ? '保存名称' : '删除演示文稿' }}</button></div>
        </form>
      </dialog>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '../common/Icon.vue'
import { usePresentationStore } from '../../store/presentation'
import type { PptTaskSummary } from '../../types/presentation'

const props = defineProps<{ task: PptTaskSummary; compact?: boolean }>()
const ppt = usePresentationStore(), router = useRouter(), route = useRoute()
const dialog = ref<HTMLDialogElement | null>(null), titleInput = ref<HTMLInputElement | null>(null)
const mode = ref<'rename' | 'delete' | null>(null), name = ref(''), error = ref(''), saving = ref(false)
const unavailable = computed(() => props.task.background_running || ['starting', 'generating'].includes(props.task.stage)
  || (ppt.task?.taskId === props.task.task_id && (ppt.busy
    || ['starting', 'generating'].includes(ppt.task.stage))))

async function open(operation: 'rename' | 'delete') {
  if (unavailable.value) return
  mode.value = operation; name.value = props.task.title; error.value = ''
  await nextTick()
  dialog.value?.showModal()
  if (operation === 'rename') { titleInput.value?.focus(); titleInput.value?.select() }
}
function close() { if (!saving.value) { dialog.value?.close(); mode.value = null; error.value = '' } }
async function submit() {
  if (!mode.value || saving.value) return
  saving.value = true; error.value = ''
  try {
    if (mode.value === 'rename') await ppt.renameTask(props.task.task_id, name.value.trim())
    else {
      const id = props.task.task_id
      await ppt.removeTask(id)
      if (route.params.taskId === id) await router.replace('/presentations')
    }
    saving.value = false; close()
  } catch (failure: any) { error.value = failure.message || '操作未完成，请稍后重试' }
  finally { saving.value = false }
}
</script>

<style scoped>
.presentation-actions { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); z-index: 1; }
.action-buttons { display: flex; gap: 2px; padding: 2px; border-radius: 7px; background: var(--surface-3); opacity: 0; pointer-events: none; transition: opacity 140ms; }
:global(.ppt-action-row:hover .action-buttons), :global(.ppt-action-row:focus-within .action-buttons), .action-buttons:focus-within { opacity: 1; pointer-events: auto; }
.action-buttons button { min-width: 34px; min-height: 34px; display: grid; place-items: center; border-radius: 6px; color: var(--text-muted); }
.compact .action-buttons button { min-width: 28px; min-height: 28px; }
.action-buttons button:hover { background: var(--surface-2); color: var(--text); }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button:disabled { opacity: .4; cursor: not-allowed; }
.presentation-dialog { width: min(380px, calc(100vw - 32px)); margin: auto; padding: 24px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 20px; box-shadow: var(--shadow-lg); }
.presentation-dialog::backdrop { background: rgba(12, 14, 22, .42); }
.presentation-dialog h2 { font-size: 17px; font-weight: 600; margin-bottom: 14px; }
.presentation-dialog p { font-size: 13px; line-height: 1.8; color: var(--text-muted); margin-bottom: 16px; overflow-wrap: anywhere; }
.presentation-dialog label { display: block; margin-bottom: 8px; font-size: 13px; }
.presentation-dialog input { width: 100%; height: 44px; padding: 0 12px; background: var(--surface); color: var(--text); border: 1px solid var(--border-strong); border-radius: 10px; font: inherit; font-size: 14px; }
.presentation-dialog input:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
.presentation-dialog .operation-error { margin-top: 14px; color: var(--danger); }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
.dialog-actions button { min-height: 42px; padding: 8px 16px; border-radius: 8px; font-size: 13px; }
.cancel { background: var(--surface-2); color: var(--text); }.cancel:hover { background: var(--surface-3); }
.confirm { background: var(--accent); color: #fff; }.confirm:hover { background: var(--accent-hover); }
.danger { background: var(--danger); color: #fff; }
@media (hover: none), (pointer: coarse) { .action-buttons { opacity: 1; pointer-events: auto; }.action-buttons button, .compact .action-buttons button { min-width: 44px; min-height: 44px; }.dialog-actions button { min-height: 44px; } }
</style>
