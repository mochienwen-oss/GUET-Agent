<template>
  <Teleport to="body">
    <div class="source-reader-mask" @click.self="emit('close')">
      <aside ref="panel" class="source-reader" role="dialog" aria-modal="true" aria-labelledby="source-reader-title" tabindex="-1" @keydown="onKeydown">
        <header class="reader-heading">
          <div class="reader-title"><p>资料原文<template v-if="current.citation_id"> · {{ current.citation_id }}</template></p><h2 id="source-reader-title">{{ current.title || current.source }}</h2></div>
          <button class="reader-icon" aria-label="关闭资料原文" @click="emit('close')"><Icon name="close" :size="20" /></button>
        </header>
        <!-- ★ 阅读控件紧贴标题：切换「引用片段 / 查看全文」与重新定位都在首屏，
             不再被资料信息挤到面板下半区。 -->
        <div class="reader-toolbar">
          <div class="reader-tabs" aria-label="阅读范围"><button :aria-pressed="!full" @click="full = false">引用片段</button><button :aria-pressed="full" :disabled="!canFetch || preview?.state === 'unavailable'" @click="full = true">查看全文</button></div>
          <button class="reader-locate" :disabled="loading || !displayLocation" @click="locate"><Icon name="search" :size="14" />重新定位</button>
        </div>
        <p v-if="current.heading_path?.length" class="reader-path">{{ current.heading_path.join(' › ') }}</p>
        <!-- ★ 资料信息默认收起：摘要行先给两条关键值（发布单位、时效等），
             需要核对依据时再展开。这样正文从首屏就开始，而不是被一张信息表推到下面。 -->
        <details v-if="metadataItems.length" class="reader-info">
          <summary>
            <span class="info-label">资料信息</span>
            <span class="info-peek">{{ infoPeek }}</span>
            <span class="info-count">{{ metadataItems.length }} 项</span>
          </summary>
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
            <details v-if="detailMetadata.length" class="metadata-more">
              <summary>更多信息</summary><dl>
                <div v-for="item in detailMetadata" :key="item.key"><dt>{{ item.label }}</dt><dd>{{ item.value }}<span v-if="item.state === 'conflict'" class="metadata-conflict"> · 存在差异，待核对</span>
                  <p v-for="(evidence, index) in item.evidence" :key="index" class="metadata-detail-evidence">{{ evidenceLabel(evidence) }}：{{ evidence.text }}<button v-if="canLocateEvidence(evidence)" :disabled="loading" @click="showEvidence(evidence)">定位原文</button></p>
                </dd></div>
              </dl>
            </details>
            <p v-if="metadata.maintained" class="reader-caption">含管理员维护的信息</p>
          </div>
        </details>
        <p v-else class="reader-caption reader-caption-top">暂未识别到可确认的文件信息，可查看原文。</p>
        <div v-if="loading" class="reader-notice" role="status">正在读取原文…</div>
        <div v-else-if="error" class="reader-notice reader-error" role="alert">{{ error }} <button @click="load">重试</button></div>
        <div v-else class="reader-notice" role="status">{{ notice }}<span v-if="displayLocation"> · 解析正文第 {{ displayLocation.line }} 行{{ !metadataLocation && preview?.row_number ? `，表格第 ${preview.row_number} 行` : '' }}</span></div>
        <div ref="body" class="reader-body" :aria-busy="loading">
          <div class="reader-body-in">
            <template v-if="!loading && preview?.text">
              <div v-if="parts.before" class="source-prose source-context" v-html="renderSourceText(parts.before)" />
              <section v-if="parts.hit" class="source-hit-block">
                <span class="hit-label">引用片段</span>
                <div class="source-prose source-hit" :class="{ 'table-hit': !metadataLocation && !!preview.row_number }" v-html="renderSourceText(parts.hit, metadataLocation ? undefined : preview.row_number)" />
              </section>
              <div v-if="parts.after" class="source-prose source-context" v-html="renderSourceText(parts.after)" />
            </template>
            <template v-else-if="!loading">
              <section class="saved-excerpt-card">
                <span class="hit-label">回答时保存的引用摘录</span>
                <pre class="saved-excerpt">{{ current.text || '此来源没有可展示的原文摘录。' }}</pre>
              </section>
            </template>
          </div>
        </div>
        <footer class="reader-footer">{{ preview?.view === 'full' && preview.text ? '正在查看知识库保存的全文' : '本次引用为资料节选，展开上下文不等于模型已审阅全文' }}</footer>
      </aside>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { RetrievedDoc, SourceMetadataEvidence, SourcePreview } from '../../types/chat'
import { getMessageSource } from '../../api/chat'
import { renderSourceText } from '../../utils/chatReferences'
import { sourceMetadataItems } from '../../utils/sourceMetadata'
import Icon from '../common/Icon.vue'

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
// 收起状态下的摘要：取前两条「非分类/覆盖」的取值，够判断这份资料是什么、谁发的。
const infoPeek = computed(() => primaryMetadata.value
  .filter(item => !['audience', 'coverage'].includes(item.key))
  .slice(0, 2).map(item => item.value).join(' · '))
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
.source-reader { width: min(720px, 62vw); height: 100dvh; background: var(--surface); border-left: 1px solid var(--border); box-shadow: var(--shadow-lg); display: flex; flex-direction: column; color: var(--text); outline: none; }
.reader-heading { padding: 24px 28px 18px; display: flex; align-items: flex-start; gap: 20px; justify-content: space-between; border-bottom: 1px solid var(--border); }
.reader-heading p { margin: 0 0 8px; font-size: 12px; color: var(--text-muted); }.reader-heading h2 { margin: 0; font-size: 18px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.reader-icon { display: grid; place-items: center; min-width: 40px; height: 40px; background: transparent; color: var(--text-muted); border-radius: 6px; }.reader-icon:hover { background: var(--surface-2); }
.reader-meta { padding: 2px 28px 16px; flex-shrink: 0; max-height: 34dvh; overflow-y: auto; }
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
/* ── 顶部阅读控件 ──
   顺序：标题 → 片段/全文切换 + 重新定位 → 层级路径 → 资料信息（可折叠）→ 正文。
   控件全部落在首屏，正文也从首屏开始。 */
.reader-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 8px 28px 0; border-bottom: 1px solid var(--border); }
.reader-path { display: flex; gap: 6px; align-items: baseline; margin: 0; padding: 8px 28px 0; font-size: 12px; line-height: 1.6; color: var(--text-muted); overflow-wrap: anywhere; }
.reader-path::before { content: '位置'; flex: none; font-size: 11px; letter-spacing: .06em; color: var(--text-faint); }
.reader-info { border-bottom: 1px solid var(--border); }
.reader-info > summary { display: flex; align-items: center; gap: 10px; padding: 11px 28px; cursor: pointer; list-style: none; }
.reader-info > summary::-webkit-details-marker { display: none; }
.reader-info > summary:hover { background: var(--surface-2); }
.reader-info > summary::after { content: '展开'; margin-left: auto; flex: none; font-size: 11px; color: var(--text-faint); }
.reader-info[open] > summary::after { content: '收起'; }
.info-label { flex: none; font-size: 12px; font-weight: 600; color: var(--text); }
.info-peek { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 12px; color: var(--text-muted); }
.info-count { flex: none; font-size: 11px; color: var(--text-faint); }
.reader-caption-top { padding: 10px 28px; border-bottom: 1px solid var(--border); margin: 0; }
.reader-tabs { display: flex; gap: 16px; }.reader-tabs button,.reader-locate { padding: 10px 0; background: transparent; color: var(--text-muted); font-size: 13px; }.reader-tabs button { border-bottom: 2px solid transparent; }.reader-tabs button[aria-pressed='true'] { color: var(--accent-ink); border-bottom-color: var(--accent); }.reader-locate { display: inline-flex; align-items: center; gap: 5px; }button:disabled { opacity: .45; cursor: default; }
.reader-notice { padding: 12px 28px; font-size: 12px; line-height: 1.6; color: var(--text-muted); }.reader-error { color: var(--danger); }.reader-error button { color: inherit; background: transparent; text-decoration: underline; margin-left: 6px; }
/* 正文：按「阅读」而不是「展示」来排 —— 行宽锁 68ch、行高 1.95、
   上下文段落降一档灰、引用片段单独成块并带标签，眼睛一眼能落回被引用的那几句。 */
.reader-body { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; padding: 6px 28px 40px; scrollbar-gutter: stable; }
.reader-body-in { max-width: 70ch; margin: 0 auto; }
.source-prose { font-size: 15.5px; line-height: 1.95; color: var(--text); overflow-wrap: anywhere; }
.source-context { color: var(--text-muted); }
.source-context :deep(h1), .source-context :deep(h2), .source-context :deep(h3) { color: var(--text-muted); }
.source-hit-block { margin: 18px 0; padding: 4px 0 4px 16px; border-left: 3px solid var(--accent); border-radius: 0 10px 10px 0; background: var(--accent-soft); }
.source-hit-block .hit-label { display: block; padding: 12px 16px 0 0; }
.source-hit { padding: 6px 16px 12px 0; }
.source-hit-block .source-hit { background: transparent; }
.hit-label { font-size: 11px; font-weight: 600; letter-spacing: .08em; color: var(--accent-ink); }
.source-hit.table-hit { padding: 0; }
.source-prose :deep(.source-row-hit) { background: var(--accent-soft); outline: 2px solid var(--accent-border); outline-offset: -2px; }
.source-prose :deep(table) { width: 100%; border-collapse: collapse; font-size: 13.5px; display: block; overflow-x: auto; margin: 8px 0 18px; }
.source-prose :deep(th),.source-prose :deep(td) { border: 1px solid var(--border); padding: 9px 12px; min-width: 64px; text-align: left; }
.source-prose :deep(th) { background: var(--surface-2); font-weight: 600; position: sticky; top: 0; }
.source-prose :deep(tbody tr:nth-child(even)) { background: color-mix(in srgb, var(--surface-2) 55%, transparent); }
.source-prose :deep(h1),.source-prose :deep(h2),.source-prose :deep(h3) { font-size: 18px; line-height: 1.55; margin: 26px 0 12px; letter-spacing: -0.01em; }
.source-prose :deep(h1:first-child),.source-prose :deep(h2:first-child),.source-prose :deep(h3:first-child) { margin-top: 4px; }
.source-prose :deep(p) { margin: 0 0 14px; }
.source-prose :deep(ul),.source-prose :deep(ol) { margin: 0 0 14px; padding-left: 22px; }
.source-prose :deep(li) { margin: 4px 0; }
.source-prose :deep(blockquote) { margin: 16px 0; padding: 4px 0 4px 14px; border-left: 2px solid var(--border-strong); color: var(--text-muted); }
.source-prose :deep(a) { color: var(--accent-ink); text-decoration: underline; text-underline-offset: 2px; }
.source-prose :deep(code) { padding: 1px 5px; border-radius: 4px; background: var(--surface-2); font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-size: .92em; }
.source-prose :deep(pre) { overflow-x: auto; padding: 14px; border-radius: 8px; background: var(--surface-2); }
/* 没有原文、只有保存摘录时的兜底：也给同样的「被引用」标记，不至于像一段裸文本 */
.saved-excerpt-card { margin: 12px 0; padding: 14px 18px 4px; border-left: 3px solid var(--accent-border); border-radius: 0 10px 10px 0; background: var(--surface-2); }
.saved-excerpt { white-space: pre-wrap; font: inherit; font-size: 15px; line-height: 1.9; color: var(--text); overflow-wrap: anywhere; margin: 10px 0 12px; }.reader-footer { border-top: 1px solid var(--border); padding: 12px 28px; font-size: 12px; line-height: 1.6; color: var(--text-muted); }
button:focus-visible,select:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 900px) { .source-reader { width: 100%; }.reader-heading { padding: 18px 20px 14px; }.reader-toolbar { padding: 8px 20px 0; }.reader-path { padding: 8px 20px 0; }.reader-info > summary { padding: 11px 20px; }.reader-meta { padding: 2px 20px 14px; }.reader-caption-top { padding: 10px 20px; }.reader-notice,.reader-footer { padding: 12px 20px; }.reader-body { padding: 4px 20px 24px; } }
@media (max-height: 650px) { .reader-meta { max-height: 140px; overflow-y: auto; }.reader-heading { padding-block: 12px; } }
</style>
