<template>
  <div :class="['message', msg.role, { streaming: isStreamingMsg }]">
    <span v-if="msg.role === 'assistant'" class="avatar">
      <Icon name="bot" :size="17" />
    </span>

    <div class="body">
      <template v-if="msg.role === 'assistant'">
        <div v-if="isStreamingMsg && !msg.content" class="thinking">
          <span></span><span></span><span></span>
        </div>
        <template v-else>
          <div v-if="mainHtml" class="md" @click="openCitation" @mouseover="previewCitation" @mouseout="leaveCitation" @focusin="previewCitation" @focusout="hidePreview" v-html="mainHtml"></div>
          <!-- 后端注入的「资料来源：」块（纯文字）由前端接管渲染：单独成块 + SVG 图标 -->
          <div v-if="sourceBody && !msg.retrieved_docs?.some(source => source.citation_id)" class="answer-sources">
            <Icon name="tag" :size="13" class="src-ic" />
            <span class="src-text">资料来源：{{ sourceBody }}</span>
          </div>
        </template>
      </template>
      <div v-else class="bubble">{{ msg.content }}</div>

      <div v-if="msg.role === 'assistant' && citedGroups.length" class="sources" aria-label="答案引用资料">
        <span class="source-label">引用资料</span>
        <button v-for="group in citedGroups" :key="group.key" class="source-tag" @click="emit('open-source', group.source)"><Icon name="file" :size="12" /><span>{{ group.source.title || group.source.source }}</span><small v-if="sourceMetadataSummary(group.source.metadata)">{{ sourceMetadataSummary(group.source.metadata) }}</small></button>
      </div>
      <details v-if="msg.role === 'assistant' && consultedGroups.length" class="consulted-sources">
        <summary>{{ citedGroups.length ? '本轮还查阅了' : '本轮查阅资料' }} {{ consultedGroups.length }} 份<span v-if="!citedGroups.length"> · 未标注到具体结论</span></summary>
        <div class="sources"><button v-for="group in consultedGroups" :key="group.key" class="source-tag" @click="emit('open-source', group.source)"><Icon name="file" :size="12" />{{ group.source.title || group.source.source }}</button></div>
      </details>

      <div class="actions">
        <button class="action-btn" :title="copied ? '已复制' : '复制'" @click="copy">
          <Icon v-if="!copied" name="copy" :size="15" />
          <Icon v-else name="check" :size="15" />
        </button>
        <template v-if="msg.role === 'assistant'">
          <button v-if="isStreamingMsg" class="action-btn" title="停止生成" @click="emit('stop')">
            <Icon name="stop" :size="15" />
          </button>
          <button
            v-else-if="canRegenerate"
            class="action-btn"
            title="重新生成"
            @click="emit('regenerate', index)"
          >
            <Icon name="refresh" :size="15" />
          </button>
        </template>
      </div>
    </div>
  </div>
  <Teleport to="body">
    <div v-if="citationPreview" :id="previewId" class="citation-preview" role="tooltip" :style="{ top: `${citationPreview.top}px`, left: `${citationPreview.left}px`, width: `${citationPreview.width}px` }">
      <strong>{{ citationPreview.source.title || citationPreview.source.source }}</strong>
      <p v-for="item in sourceMetadataItems(citationPreview.source.metadata).filter(item => !item.detail).slice(0, 2)" :key="item.key" class="citation-preview-meta">{{ item.label }}：{{ item.value }}<template v-if="item.state === 'conflict'">（待核对）</template></p>
      <p class="citation-preview-excerpt">{{ citationPreview.source.text?.slice(0, 360) || '点击查看来源详情' }}</p>
      <span>点击阅读原文与上下文</span>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { Message, RetrievedDoc } from '../types/chat'
import { renderCitedAnswer } from '../utils/chatReferences'
import { sourceMetadataItems, sourceMetadataSummary } from '../utils/sourceMetadata'
import Icon from './Icon.vue'

const props = defineProps<{
  msg: Message
  index: number
  streaming: boolean
  streamingIndex: number
  lastAssistantIndex: number
}>()

const emit = defineEmits<{
  (e: 'stop'): void
  (e: 'regenerate', index: number): void
  (e: 'open-source', source: RetrievedDoc): void
}>()

const copied = ref(false)
const previewId = `citation-preview-${props.index}`
const citationPreview = ref<{ source: RetrievedDoc; top: number; left: number; width: number } | null>(null)
let previewMarker: HTMLElement | null = null
const groups = computed(() => {
  const grouped = new Map<string, { key: string; source: RetrievedDoc; cited: boolean }>()
  for (const source of props.msg.retrieved_docs || []) {
    const key = source.doc_id || source.source
    const group = grouped.get(key)
    if (!group) grouped.set(key, { key, source, cited: !!source.cited })
    else if (source.cited && !group.cited) { group.source = source; group.cited = true }
  }
  return Array.from(grouped.values())
})
const citedGroups = computed(() => groups.value.filter(group => group.cited))
const consultedGroups = computed(() => groups.value.filter(group => !group.cited))

function citationTarget(event: Event) {
  return (event.target as HTMLElement)?.closest<HTMLElement>('button[data-citation-id]') || null
}
function previewCitation(event: Event) {
  const target = citationTarget(event)
  const source = props.msg.retrieved_docs?.find(item => item.citation_id === target?.dataset.citationId)
  if (!target || !source) { hidePreview(); return }
  const rect = target.getBoundingClientRect(), width = Math.min(360, window.innerWidth - 24)
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
  const top = rect.bottom + 260 < window.innerHeight ? rect.bottom + 8 : Math.max(12, rect.top - 260)
  hidePreview()
  previewMarker = target
  target.setAttribute('aria-describedby', previewId)
  citationPreview.value = { source, top, left, width }
}
function hidePreview() { previewMarker?.removeAttribute('aria-describedby'); previewMarker = null; citationPreview.value = null }
function leaveCitation(event: MouseEvent) {
  const target = citationTarget(event)
  if (!target?.contains(event.relatedTarget as Node | null)) hidePreview()
}
function openCitation(event: MouseEvent) {
  const target = citationTarget(event)
  const source = props.msg.retrieved_docs?.find(item => item.citation_id === target?.dataset.citationId)
  if (source) { event.preventDefault(); hidePreview(); emit('open-source', source) }
}
onMounted(() => { window.addEventListener('scroll', hidePreview, true); window.addEventListener('resize', hidePreview) })
onBeforeUnmount(() => { window.removeEventListener('scroll', hidePreview, true); window.removeEventListener('resize', hidePreview) })

const isStreamingMsg = computed(() => props.streaming && props.index === props.streamingIndex)

// 后端注入的「资料来源：」块（纯文字，位于答案末尾）由前端**接管渲染**：
//   从 markdown 里切出来，正文照常渲染，来源块单独成块并配 SVG 图标。
//   前缀与 backend/citations.py 的 SOURCE_PREFIX 一致（单一来源）。
const SOURCE_PREFIX = '资料来源：'
const splitSource = computed(() => {
  const text = props.msg.content || ''
  const m = new RegExp(`\\n{2,}${SOURCE_PREFIX}([\\s\\S]*)$`).exec(text)
  if (m) return { main: text.slice(0, m.index), src: m[1].trim() }
  if (text.startsWith(SOURCE_PREFIX)) return { main: '', src: text.slice(SOURCE_PREFIX.length).trim() }
  return { main: text, src: '' }
})
const sourceBody = computed(() => splitSource.value.src)
const mainHtml = computed(() => {
  let t = splitSource.value.main
  if (isStreamingMsg.value && t) t += '<span class="typing-cursor"></span>'
  return t ? renderCitedAnswer(t, props.msg.retrieved_docs || []) : ''
})
const canRegenerate = computed(
  () => props.msg.role === 'assistant' && !props.streaming && props.index === props.lastAssistantIndex
)

async function copy() {
  const text = props.msg.content
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}
</script>

<style scoped>
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 32px;
  animation: rise var(--slow) var(--ease);
}
@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
/* 用户发言：靠右 + 淡强调色气泡（唯一的"容器"） */
.message.user {
  justify-content: flex-end;
}
.body {
  flex: 1;
  min-width: 0;
}
.message.user .body {
  flex: 0 1 auto;
  max-width: 85%;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.avatar {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ★ 助手回复**不做气泡** —— 去掉容器让文字自己立住。
   原来两边都是"白底 + 圆角 + 边框"，一屏下来全是卡片；现在只有用户发言有容器，
   两者靠**对齐**和**底色**区分，不靠边框。 */
.bubble {
  max-width: 100%;
  padding: 11px 16px;
  border-radius: 14px;
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 16px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: normal;
  -webkit-user-select: text;
  user-select: text;
  cursor: text;
}
.bubble::selection {
  background: var(--accent);
  color: var(--surface);
}

.md :deep(p) {
  margin: 0 0 12px;
}
.md :deep(p:last-child) {
  margin-bottom: 0;
}
.md :deep(ol),
.md :deep(ul) {
  margin: 0 0 12px;
  padding-left: 22px;
}
.md :deep(li) {
  margin-bottom: 4px;
}
.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  margin: 18px 0 8px;
  font-weight: 600;
  line-height: 1.35;
  letter-spacing: -0.01em;
}
.md :deep(h1) {
  font-size: 1.4em;
}
.md :deep(h2) {
  font-size: 1.25em;
}
.md :deep(h3) {
  font-size: 1.1em;
}
.md :deep(h4) {
  font-size: 1em;
}
.md :deep(a) {
  color: var(--accent-ink);
}
.md :deep(blockquote) {
  border-left: 2px solid var(--border-strong);
  padding: 2px 0 2px 14px;
  margin: 0 0 12px;
  color: var(--text-muted);
}
.md :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 18px 0;
}
.md :deep(code) {
  background: var(--surface-2);
  border-radius: 5px;
  padding: 1px 5px;
  font-size: 0.9em;
}

/* 表格 —— 用细线，不用满格边框 */
.md :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0 0 12px;
  font-size: 14px;
}
.md :deep(th),
.md :deep(td) {
  border-bottom: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
}
.md :deep(th) {
  font-weight: 600;
  border-bottom-color: var(--border-strong);
}
.md :deep(tr:last-child td) {
  border-bottom: none;
}

/* —— 来源 —— */
.sources {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.source-label { width: 100%; color: var(--text-muted); font-size: 12px; margin-bottom: 2px; }
.source-tag span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-tag small { flex-shrink: 0; font-size: 11px; color: var(--text-muted); }
.consulted-sources { margin-top: 12px; font-size: 12px; color: var(--text-muted); }
.consulted-sources summary { cursor: pointer; padding: 6px 0; }
.md :deep(.answer-citation) { display: inline-flex; align-items: center; justify-content: center; min-width: 25px; min-height: 25px; padding: 0 5px; margin: 0 3px; vertical-align: baseline; border: 1px solid var(--accent-border); border-radius: 5px; background: var(--accent-soft); color: var(--accent-ink); font-size: 11px; font-weight: 600; line-height: 1.4; cursor: pointer; }
.md :deep(.answer-citation:hover) { border-color: var(--accent); }
.md :deep(.answer-citation:focus-visible),.source-tag:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.citation-preview { position: fixed; z-index: 900; padding: 16px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--surface); color: var(--text); box-shadow: var(--shadow-md, 0 4px 16px rgb(24 26 40 / 10%)); pointer-events: none; max-height: 250px; overflow: hidden; }
.citation-preview strong { display: block; font-size: 13px; line-height: 1.6; }.citation-preview-meta { font-size: 12px; color: var(--text-muted); margin: 6px 0; line-height: 1.5; }.citation-preview-excerpt { font-size: 13px; line-height: 1.75; white-space: pre-wrap; max-height: 110px; overflow: hidden; margin: 10px 0; }.citation-preview > span { font-size: 11px; color: var(--accent-ink); }
/* 6px 圆角的小标签，**不做胶囊** —— 满屏胶囊是另一个通用痕迹 */
.source-tag {
  font-size: 12px;
  background: var(--surface);
  color: var(--text-muted);
  padding: 3px 9px;
  border-radius: 6px;
  border: 1px solid var(--border);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
}
.source-tag:hover {
  border-color: var(--accent-border);
  color: var(--accent-ink);
  background: var(--accent-soft);
}
/* 后端注入的「资料来源：」块 */
.answer-sources {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 14px;
  padding: 12px 14px;
  background: var(--surface-2);
  border-radius: 10px;
  font-size: 13px;
  color: var(--text-muted);
  line-height: 1.75;
}
.answer-sources .src-ic {
  margin-top: 3px;
  color: var(--accent);
}
.answer-sources .src-text {
  white-space: pre-wrap;
  word-break: break-word;
}

/* —— 操作条 —— */
/* 40×40 视觉 + 目标区域已接近 44px 的触摸下限；hover 才出现，不占视线 */
.actions {
  display: flex;
  gap: 2px;
  margin-top: 8px;
  opacity: 0;
  transition: opacity var(--fast) var(--ease);
}
.message:hover .actions,
.message.streaming .actions {
  opacity: 1;
}
.message.user .actions {
  justify-content: flex-end;
}
.action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: transparent;
  color: var(--text-faint);
}
.action-btn:hover {
  background: var(--surface-2);
  color: var(--text);
}
.message.user .action-btn:hover {
  background: var(--accent-border);
  color: var(--accent-ink);
}

/* —— 生成中 —— */
/* 三个点随**真实的流式状态**呼吸（不是常驻装饰），且用平滑曲线而非弹跳
   —— `bounce` + `ease-in-out` 是 impeccable 点名的过时廉价动效。 */
.thinking {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  height: 26px;
}
.thinking span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: breathe 1.4s var(--ease) infinite;
}
.thinking span:nth-child(2) {
  animation-delay: 0.18s;
}
.thinking span:nth-child(3) {
  animation-delay: 0.36s;
}
@keyframes breathe {
  0%,
  100% {
    opacity: 0.22;
    transform: scale(0.86);
  }
  45% {
    opacity: 1;
    transform: scale(1);
  }
}

/* 流式光标：平滑呼吸而非硬闪烁（`steps()` 的硬闪是另一个被点名的痕迹） */
.md :deep(.typing-cursor) {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 3px;
  border-radius: 1px;
  background: var(--accent);
  vertical-align: text-bottom;
  animation: breathe 1.4s var(--ease) infinite;
}
</style>
