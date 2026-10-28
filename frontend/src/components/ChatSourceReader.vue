<template>
  <Teleport to="body">
    <div class="source-reader-mask" @click.self="emit('close')">
      <aside ref="panel" class="source-reader" role="dialog" aria-modal="true" aria-labelledby="source-reader-title" tabindex="-1" @keydown="onKeydown">
        <header class="reader-heading">
          <div><p>资料原文</p><h2 id="source-reader-title">{{ current.title || current.source }}</h2></div>
          <button class="reader-icon" aria-label="关闭资料原文" @click="emit('close')"><Icon name="close" :size="20" /></button>
        </header>
        <div class="reader-meta">
          <dl v-if="primaryMetadata.length">
            <div v-for="item in primaryMetadata" :key="item.key" :class="{ 'metadata-wide': ['audience', 'coverage'].includes(item.key) }">
              <dt>{{ item.label }}</dt><dd>{{ item.value }}<span v-if="item.state === 'conflict'" class="metadata-conflict"> · 存在差异，待核对</span>
                <details v-if="item.evidence.length" class="metadata-evidence">
                  <summary>查看依据</summary>
                  <p v-for="(evidence, index) in item.evidence" :key="index">{{ evidenceLabel(evidence) }}：{{ evidence.text }}<button v-if="canLocateEvidence(evidence)" :disabled="loading" @click="showEvidence(evidence)">定位原文</button></p>
                </details>
              </dd>
            </div>
          </dl>
          <p v-if="!metadataItems.length" class="reader-caption">暂未识别到可确认的文件信息，可查看原文。</p>
          <details v-if="detailMetadata.length" class="metadata-more">
            <summary>更多信息</summary><dl>
              <div v-for="item in detailMetadata" :key="item.key"><dt>{{ item.label }}</dt><dd>{{ item.value }}<span v-if="item.state === 'conflict'" class="metadata-conflict"> · 存在差异，待核对</span>
                <p v-for="(evidence, index) in item.evidence" :key="index" class="metadata-detail-evidence">{{ evidenceLabel(evidence) }}：{{ evidence.text }}<button v-if="canLocateEvidence(evidence)" :disabled="loading" @click="showEvidence(evidence)">定位原文</button></p>
              </dd></div>
            </dl>
          </details>
          <p v-if="metadata.maintained" class="reader-caption">含管理员维护的信息</p>
        </div>
        <div class="reader-navigation">
          <label for="source-location">引用位置</label>
          <select id="source-location" :value="sourceKey(current)" @change="selectSource">
            <option v-for="(item, index) in sources" :key="sourceKey(item)" :value="sourceKey(item)">{{ item.citation_id ? item.citation_id.replace('S', '来源 ') : `旧来源 ${index + 1}` }} · {{ item.title || item.source }}{{ item.row_number ? ` · 表格第 ${item.row_number} 行` : '' }}</option>
          </select>
          <p v-if="current.heading_path?.length" class="reader-path">{{ current.heading_path.join(' › ') }}</p>
          <div class="reader-toolbar">
            <div class="reader-tabs" aria-label="阅读范围"><button :aria-pressed="!full" @click="full = false">引用片段</button><button :aria-pressed="full" :disabled="!canFetch || preview?.state === 'unavailable'" @click="full = true">查看全文</button></div>
            <button class="reader-locate" :disabled="loading || !displayLocation" @click="locate"><Icon name="search" :size="14" />重新定位</button>
          </div>
        </div>
        <div v-if="loading" class="reader-notice" role="status">正在读取原文…</div>
        <div v-else-if="error" class="reader-notice reader-error" role="alert">{{ error }} <button @click="load">重试</button></div>
        <div v-else class="reader-notice" role="status">{{ notice }}<span v-if="displayLocation"> · 解析正文第 {{ displayLocation.line }} 行{{ !metadataLocation && preview?.row_number ? `，表格第 ${preview.row_number} 行` : '' }}</span></div>
        <div ref="body" class="reader-body" :aria-busy="loading">
          <template v-if="!loading && preview?.text">
            <div v-if="parts.before" class="source-prose" v-html="renderSourceText(parts.before)" />
            <div v-if="parts.hit" class="source-prose source-hit" :class="{ 'table-hit': !metadataLocation && !!preview.row_number }" v-html="renderSourceText(parts.hit, metadataLocation ? undefined : preview.row_number)" />
            <div v-if="parts.after" class="source-prose" v-html="renderSourceText(parts.after)" />
          </template>
          <template v-else-if="!loading">
            <p class="excerpt-label">回答时保存的引用摘录</p>
            <pre class="saved-excerpt">{{ current.text || '此来源没有可展示的原文摘录。' }}</pre>
          </template>
        </div>
        <footer class="reader-footer">{{ preview?.view === 'full' && preview.text ? '正在查看知识库保存的全文' : '本次引用为资料节选，展开上下文不等于模型已审阅全文' }}</footer>
      </aside>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { RetrievedDoc, SourceMetadataEvidence, SourcePreview } from '../types/chat'
import { getMessageSource } from '../api/chat'
import { renderSourceText } from '../utils/chatReferences'
import { sourceMetadataItems } from '../utils/sourceMetadata'
import Icon from './Icon.vue'

const props = defineProps<{ source: RetrievedDoc; sources: RetrievedDoc[]; messageId?: number; conversationId: number | null }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const current = ref(props.source), full = ref(false), preview = ref<SourcePreview | null>(null)
const loading = ref(false), error = ref(''), panel = ref<HTMLElement>(), body = ref<HTMLElement>()
const restoreFocus = document.activeElement as HTMLElement | null
let requestVersion = 0
const canFetch = computed(() => !!props.conversationId && !!props.messageId && !!current.value.citation_id)
const metadata = computed(() => preview.value?.metadata || current.value.metadata || {})
const metadataItems = computed(() => sourceMetadataItems(metadata.value))
const primaryMetadata = computed(() => metadataItems.value.filter(item => !item.detail))
const detailMetadata = computed(() => metadataItems.value.filter(item => item.detail))
const selectedEvidence = ref<SourceMetadataEvidence | null>(null)
const metadataLocation = computed(() => {
  const evidence = selectedEvidence.value, value = preview.value
  if (!evidence || value?.view !== 'full' || !value.text || evidence.start == null || evidence.end == null) return null
  const chars = Array.from(value.text)
  if (evidence.start < 0 || evidence.end > chars.length || chars.slice(evidence.start, evidence.end).join('').trim() !== evidence.text.trim()) return null
  return { start: evidence.start, end: evidence.end, line: evidence.line }
})
const displayLocation = computed(() => metadataLocation.value || preview.value?.location)
const notice = computed(() => metadataLocation.value ? '已定位到文件信息的原文依据。' : preview.value?.message || '旧记录未保存可验证的定位信息，保留原有来源。')
const parts = computed(() => {
  const value = preview.value
  if (!value?.text) return { before: '', hit: '', after: '' }
  if (!displayLocation.value) return { before: value.text, hit: '', after: '' }
  // Backend offsets count Unicode code points; JavaScript offsets otherwise split non-BMP text.
  const chars = Array.from(value.text), { start, end } = displayLocation.value
  return { before: chars.slice(0, start).join(''), hit: chars.slice(start, end).join(''), after: chars.slice(end).join('') }
})
function sourceKey(source: RetrievedDoc) { return source.citation_id || `legacy:${props.sources.indexOf(source)}` }
function evidenceLabel(evidence: SourceMetadataEvidence) {
  return { content: '正文', title: '文件标题', filename: '原始文件名', catalog: '文档登记', manual: '人工核验' }[evidence.source]
}
function canLocateEvidence(evidence: SourceMetadataEvidence) {
  return evidence.source === 'content' && evidence.start != null && evidence.end != null && canFetch.value && preview.value?.state !== 'unavailable'
}
async function showEvidence(evidence: SourceMetadataEvidence) {
  selectedEvidence.value = evidence
  full.value = true
  await nextTick()
  if (metadataLocation.value) locate()
}
function selectSource(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  const source = props.sources.find(item => sourceKey(item) === value)
  if (source) { selectedEvidence.value = null; current.value = source; full.value = false }
}
async function load() {
  const version = ++requestVersion
  error.value = ''; preview.value = null
  if (!canFetch.value) { loading.value = false; return }
  loading.value = true
  try {
    const value = await getMessageSource(props.conversationId!, props.messageId!, current.value.citation_id!, full.value)
    if (version !== requestVersion) return
    preview.value = value
    loading.value = false
    await nextTick()
    if (version === requestVersion) locate()
  } catch (failure: any) {
    if (version === requestVersion) error.value = failure.message || '原文读取未完成，请稍后重试。'
  } finally { if (version === requestVersion) loading.value = false }
}
function locate() {
  const target = body.value?.querySelector<HTMLElement>('.source-row-hit') || body.value?.querySelector<HTMLElement>('.source-hit')
  if (body.value && target) {
    const top = target.getBoundingClientRect().top - body.value.getBoundingClientRect().top + body.value.scrollTop - 24
    body.value.scrollTo({ top: Math.max(0, top), behavior: 'auto' })
  }
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); emit('close'); return }
  if (event.key !== 'Tab') return
  const focusable = Array.from(panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), select, summary, a[href]') || [])
  const first = focusable[0], last = focusable[focusable.length - 1]
  if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.value)) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
watch([current, full], load, { immediate: true })
watch(full, value => { if (!value) selectedEvidence.value = null })
onMounted(() => panel.value?.focus({ preventScroll: true }))
onBeforeUnmount(() => { requestVersion++; if (restoreFocus?.isConnected) restoreFocus.focus({ preventScroll: true }) })
</script>

<style scoped>
.source-reader-mask { position: fixed; inset: 0; z-index: 1000; background: rgb(15 18 30 / 18%); display: flex; justify-content: flex-end; }
.source-reader { width: min(720px, 62vw); height: 100dvh; background: var(--surface); border-left: 1px solid var(--border); display: flex; flex-direction: column; color: var(--text); outline: none; }
.reader-heading { padding: 24px 28px 18px; display: flex; align-items: flex-start; gap: 20px; justify-content: space-between; border-bottom: 1px solid var(--border); }
.reader-heading p { margin: 0 0 8px; font-size: 12px; color: var(--text-muted); }.reader-heading h2 { margin: 0; font-size: 18px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.reader-icon { display: grid; place-items: center; min-width: 40px; height: 40px; background: transparent; color: var(--text-muted); border-radius: 6px; }.reader-icon:hover { background: var(--surface-2); }
.reader-meta { padding: 16px 28px; border-bottom: 1px solid var(--border); flex-shrink: 0; max-height: 40dvh; overflow-y: auto; }.reader-meta dl { margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 12px 24px; }.reader-meta dl div { min-width: 0; }.reader-meta dt { font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }.reader-meta dd { margin: 0; font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; }.reader-caption { font-size: 12px; color: var(--text-muted); margin: 12px 0 0; line-height: 1.6; }
.reader-meta .metadata-wide { grid-column: 1 / -1; }
.metadata-evidence,.metadata-more { margin-top: 8px; font-size: 12px; line-height: 1.6; color: var(--text-muted); }
.metadata-evidence summary,.metadata-more summary { cursor: pointer; width: fit-content; padding-block: 4px; border-radius: 4px; }
.metadata-evidence summary:hover,.metadata-more summary:hover { color: var(--accent-ink); }
.metadata-evidence p,.metadata-detail-evidence { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.metadata-more dl { margin-top: 12px; }
.metadata-evidence button,.metadata-detail-evidence button { padding: 4px 8px; margin-left: 4px; background: transparent; color: var(--accent-ink); text-decoration: underline; text-underline-offset: 3px; border-radius: 4px; }
.metadata-evidence button:hover,.metadata-detail-evidence button:hover { background: var(--accent-soft); }
.metadata-evidence button:active,.metadata-detail-evidence button:active { background: var(--surface-2); }
.metadata-conflict { color: var(--warn); }
.metadata-evidence summary:focus-visible,.metadata-more summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.reader-navigation { padding: 14px 28px 0; }.reader-navigation label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }.reader-navigation select { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; font-size: 13px; }.reader-path { margin: 8px 0; font-size: 12px; line-height: 1.6; color: var(--text-muted); overflow-wrap: anywhere; }
.reader-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; border-bottom: 1px solid var(--border); }.reader-tabs { display: flex; gap: 16px; }.reader-tabs button,.reader-locate { padding: 10px 0; background: transparent; color: var(--text-muted); font-size: 13px; }.reader-tabs button { border-bottom: 2px solid transparent; }.reader-tabs button[aria-pressed='true'] { color: var(--accent-ink); border-bottom-color: var(--accent); }.reader-locate { display: inline-flex; align-items: center; gap: 5px; }button:disabled { opacity: .45; cursor: default; }
.reader-notice { padding: 12px 28px; font-size: 12px; line-height: 1.6; color: var(--text-muted); }.reader-error { color: var(--danger); }.reader-error button { color: inherit; background: transparent; text-decoration: underline; margin-left: 6px; }
.reader-body { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; padding: 4px 28px 32px; scrollbar-gutter: stable; }.source-prose { font-size: 15px; line-height: 1.85; overflow-wrap: anywhere; }.source-hit { background: var(--accent-soft); border-left: 2px solid var(--accent); padding: 12px 16px; margin: 16px 0; }.source-hit.table-hit { background: transparent; padding: 0; border: 0; }.source-prose :deep(.source-row-hit) { background: var(--accent-soft); outline: 2px solid var(--accent-border); outline-offset: -2px; }.source-prose :deep(table) { width: 100%; border-collapse: collapse; font-size: 13px; display: block; overflow-x: auto; }.source-prose :deep(th),.source-prose :deep(td) { border: 1px solid var(--border); padding: 8px 10px; min-width: 64px; }.source-prose :deep(h1),.source-prose :deep(h2),.source-prose :deep(h3) { font-size: 18px; line-height: 1.5; margin: 24px 0 12px; }.source-prose :deep(p) { margin: 0 0 12px; }.source-prose :deep(a) { color: var(--accent-ink); text-decoration: underline; }.source-prose :deep(pre) { overflow-x: auto; padding: 12px; background: var(--surface-2); }.saved-excerpt { white-space: pre-wrap; font: inherit; font-size: 15px; line-height: 1.85; overflow-wrap: anywhere; margin: 12px 0; }.excerpt-label { font-size: 12px; color: var(--text-muted); }.reader-footer { border-top: 1px solid var(--border); padding: 12px 28px; font-size: 12px; line-height: 1.6; color: var(--text-muted); }
button:focus-visible,select:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 900px) { .source-reader { width: 100%; }.reader-heading { padding: 18px 20px 14px; }.reader-meta { padding: 14px 20px; }.reader-navigation { padding: 12px 20px 0; }.reader-notice,.reader-footer { padding: 12px 20px; }.reader-body { padding: 4px 20px 24px; } }
@media (max-height: 650px) { .reader-meta { max-height: 140px; overflow-y: auto; }.reader-heading { padding-block: 12px; } }
</style>
