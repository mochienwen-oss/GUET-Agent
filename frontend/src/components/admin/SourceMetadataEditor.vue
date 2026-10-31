<template>
  <div class="metadata-mask" @click.self="emit('close')">
    <form ref="form" class="metadata-editor" role="dialog" aria-modal="true" aria-labelledby="metadata-editor-title" tabindex="-1" @keydown="onKeydown" @submit.prevent="save">
      <header><div><h2 id="metadata-editor-title">来源信息</h2><p>{{ document.title }}</p></div><button type="button" aria-label="关闭来源信息" @click="emit('close')"><Icon name="close" :size="18" /></button></header>
      <p class="metadata-help">人工确认的信息优先展示；留空时使用可确认的自动识别结果。适用年级、打印日期均不等于发布日期。</p>
      <label>文件发布年份<input v-model="year" type="number" min="1000" max="9999" step="1" placeholder="原文未标明时留空" /></label>
      <label>适用对象<input v-model="audience" maxlength="300" placeholder="例如：文件明确适用的本科生、学院或专业" /></label>
      <label>适用范围<textarea v-model="coverage" rows="2" maxlength="400" placeholder="填写原文明确说明的适用事项和限制" /></label>
      <label>信息依据<textarea v-model="basis" rows="2" maxlength="500" placeholder="填写原文中的章节、日期落款或核验说明" /></label>
      <p v-if="error" class="metadata-error" role="alert">{{ error }}</p>
      <footer><span>仅更新展示信息，不重算向量或 BM25</span><button type="button" :disabled="saving" @click="emit('close')">取消</button><button type="submit" class="metadata-save" :disabled="saving">{{ saving ? '保存中…' : '保存信息' }}</button></footer>
    </form>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import api from '../../api'
import Icon from '../common/Icon.vue'
import type { SourceMetadata } from '../../types/chat'

const props = defineProps<{ document: { id: number; title: string; source_metadata?: SourceMetadata } }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'saved'): void }>()
const meta = props.document.source_metadata || {}
const year = ref(meta.published_year ? String(meta.published_year) : '')
const audience = ref(meta.audience || ''), coverage = ref(meta.coverage || ''), basis = ref(meta.basis || '')
const saving = ref(false), error = ref(''), form = ref<HTMLElement>()
const restoreFocus = document.activeElement as HTMLElement | null
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); if (!saving.value) emit('close'); return }
  if (event.key !== 'Tab') return
  const controls = Array.from(form.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input,textarea') || [])
  const first = controls[0], last = controls[controls.length - 1]
  if (event.shiftKey && (document.activeElement === first || document.activeElement === form.value)) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
async function save() {
  if (saving.value) return
  saving.value = true; error.value = ''
  try {
    await api.patch(`/admin/documents/${props.document.id}/source-metadata`, {
      published_year: year.value === '' ? null : Number(year.value),
      audience: audience.value.trim(), coverage: coverage.value.trim(), basis: basis.value.trim(),
    })
    emit('saved')
  } catch (failure: any) { error.value = failure.message || '来源信息保存失败，请重试。' }
  finally { saving.value = false }
}
onMounted(() => form.value?.focus({ preventScroll: true }))
onBeforeUnmount(() => { if (restoreFocus?.isConnected) restoreFocus.focus({ preventScroll: true }) })
</script>

<style scoped>
.metadata-mask { position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center; padding: 20px; background: rgb(15 18 30 / 24%); }
.metadata-editor { width: min(560px, 100%); max-height: 90dvh; overflow: auto; padding: 24px; background: var(--surface); border: 1px solid var(--border); border-radius: 14px; color: var(--text); outline: none; }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }h2 { margin: 0 0 8px; font-size: 18px; }header p { font-size: 13px; color: var(--text-muted); line-height: 1.6; margin: 0; }header button { min-width: 40px; height: 40px; display: grid; place-items: center; background: transparent; color: var(--text-muted); }
.metadata-help { font-size: 13px; line-height: 1.7; color: var(--text-muted); padding: 12px 0; margin: 8px 0; border-bottom: 1px solid var(--border); }
label { display: block; font-size: 13px; margin-top: 16px; }input,textarea { display: block; width: 100%; box-sizing: border-box; margin-top: 7px; padding: 10px 12px; color: var(--text); background: var(--surface); border: 1px solid var(--border-strong); border-radius: 6px; font: inherit; line-height: 1.6; }textarea { resize: vertical; }
footer { display: flex; align-items: center; gap: 8px; margin-top: 24px; }footer span { margin-right: auto; font-size: 11px; line-height: 1.5; color: var(--text-muted); }footer button { padding: 9px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); flex-shrink: 0; }.metadata-save { background: var(--accent) !important; color: white !important; border-color: var(--accent) !important; }.metadata-error { color: var(--danger); font-size: 13px; }button:disabled { opacity: .5; }button:focus-visible,input:focus-visible,textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 480px) { .metadata-editor { padding: 20px; }footer { flex-wrap: wrap; }footer span { flex-basis: 100%; margin-bottom: 6px; } }
</style>
