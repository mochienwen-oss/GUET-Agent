<template>
  <div :class="['message', msg.role, { streaming: isStreamingMsg }]">
    <span v-if="msg.role === 'assistant'" class="avatar">
      <Icon name="bot" :size="17" />
    </span>

    <div class="body">
      <template v-if="msg.role === 'assistant'">
        <div v-if="isStreamingMsg && !msg.content" ref="thinkingEl" class="thinking" role="status" aria-label="正在生成回答">
          <span class="drop"></span><span class="drop"></span><span class="drop"></span>
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

      <!-- 会话文件：用户上传的表格、工具产出的结果，都挂在本条消息**下方**。
           用户消息里它是「刚发出去的那个附件」，助手消息里它是「生成的结果」。 -->
      <div v-if="msg.files?.length" class="result-files">
        <button v-for="file in msg.files" :key="file.file_id" class="result-file"
          :title="`下载 ${file.filename}`" @click="download(file)">
          <span class="result-file-ic"><Icon name="file" :size="13" /></span>
          <span class="result-file-text">
            <span class="result-file-name">{{ file.filename }}</span>
            <span class="result-file-meta">{{ file.role === 'result' ? '生成结果' : '已上传' }}<template v-if="file.size"> · {{ fileSize(file.size) }}</template></span>
          </span>
        </button>
      </div>

      <!-- 校园空教室查询结果：作为一条消息展示。数字是主角（橙色数据色），
           楼栋用刻度条表现占比，教室号是可复制的 chip。 -->
      <div v-if="msg.role === 'assistant' && msg.campus_result" class="campus-result">
        <div class="campus-result-top">
          <span class="campus-result-total tabular">{{ msg.campus_result.summary.total }}</span>
          <span class="campus-result-cap">
            间空闲教室
            <span v-if="msg.campus_result.date" class="campus-result-date">{{ msg.campus_result.date }}<template v-if="msg.campus_result.query?.end_date && msg.campus_result.query.end_date !== msg.campus_result.date"> 至 {{ msg.campus_result.query.end_date }}</template></span>
          </span>
        </div>
        <p v-if="msg.campus_result.query" class="campus-result-date campus-result-query">
          <span v-if="msg.campus_result.query.weekdays?.length">星期 {{ msg.campus_result.query.weekdays.join('、') }} · </span>
          <span v-if="msg.campus_result.query.building">{{ msg.campus_result.query.building }} · </span>
          <span v-if="msg.campus_result.query.room">{{ msg.campus_result.query.room }} · </span>
          <span v-if="msg.campus_result.query.room_type">{{ msg.campus_result.query.room_type }} · </span>
          <span v-if="msg.campus_result.query.seats != null">不少于 {{ msg.campus_result.query.seats }} 座 · </span>
          {{ campusUnits(msg.campus_result.units) }}
        </p>
        <div class="campus-result-groups">
          <div v-for="group in msg.campus_result.summary.buildings" :key="group.name" class="campus-result-group">
            <div class="campus-result-group-head">
              <p class="campus-result-group-name">{{ group.name }}</p>
              <span class="campus-result-count tabular">{{ group.rooms.length }}</span>
            </div>
            <span class="campus-result-bar" aria-hidden="true"><i :style="{ width: barWidth(group.rooms.length) }" /></span>
            <div class="campus-result-chips">
              <span v-for="room in group.rooms" :key="room.code" class="campus-result-chip"
                :title="`${room.type} · ${room.seats} 座${room.remark ? ' · ' + room.remark : ''}`">
                {{ room.code || room.name }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="actions">
        <button class="action-btn" :title="copied ? '已复制' : '复制'" @click="copy">
          <Icon v-if="!copied" name="copy" :size="15" />
          <Icon v-else name="check" :size="15" />
        </button>
        <template v-if="msg.role === 'assistant'">
          <button
            v-if="canRegenerate && !isStreamingMsg"
            class="action-btn"
            title="重新生成"
            @click="emit('regenerate', index)"
          >
            <Icon name="refresh" :size="15" />
          </button>
        </template>
      </div>
      <div v-if="exportError" class="export-error">{{ exportError }}</div>
    </div>
  </div>
  <Teleport to="body">
    <div v-if="citationPreview" :id="previewId" class="citation-preview" role="tooltip"
      :style="{ top: `${citationPreview.top}px`, left: `${citationPreview.left}px`, width: `${citationPreview.width}px` }"
      @mouseenter="cancelHide" @mouseleave="scheduleHide" @click="openPreviewSource"
      @mousedown="holdPreview" @mouseup="releasePreview" @wheel="onPreviewWheel">
      <div class="preview-heading">
        <span class="preview-icon"><Icon name="file" :size="16" /></span>
        <div class="preview-title">
          <span class="preview-label">引用资料<template v-if="citationPreview.source.citation_id"> · {{ citationPreview.source.citation_id }}</template></span>
          <strong>{{ citationPreview.source.title || citationPreview.source.source }}</strong>
        </div>
      </div>
      <div v-if="sourceMetadataItems(citationPreview.source.metadata).some(item => !item.detail)" class="preview-metadata">
        <span v-for="item in sourceMetadataItems(citationPreview.source.metadata).filter(item => !item.detail).slice(0, 2)" :key="item.key" class="preview-meta-item">
          {{ item.label }} <b>{{ item.value }}<template v-if="item.state === 'conflict'">（待核对）</template></b>
        </span>
      </div>
      <div v-if="citationPreview.source.text" class="preview-excerpt">
        <span class="preview-excerpt-label">引用摘录</span>
        <p>{{ citationPreview.source.text.slice(0, 360) }}</p>
      </div>
      <div class="preview-footer">点击卡片查看完整原文与上下文 <Icon name="chevronRight" :size="14" /></div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ChatFileRef, Message, RetrievedDoc } from '../../types/chat'
import { downloadChatFile } from '../../api/files'
import { renderCitedAnswer } from '../../utils/chatReferences'
import { renderMarkdownLite } from '../../utils/markdown'
import { sourceMetadataItems, sourceMetadataSummary } from '../../utils/sourceMetadata'
import Icon from '../common/Icon.vue'

const props = defineProps<{
  msg: Message
  index: number
  streaming: boolean
  streamingIndex: number
  lastAssistantIndex: number
}>()

const emit = defineEmits<{
  (e: 'regenerate', index: number): void
  (e: 'open-source', source: RetrievedDoc): void
}>()

const copied = ref(false)
function campusUnits(units: string[]) {
  const names: Record<string, string> = { '1': '第1节', '2': '第2节', '3': '第3节', '4': '第4节', '5': '中午', '6': '第5节', '7': '第6节', '8': '第7节', '9': '第8节', '10': '第9节', '11': '第10节', '12': '第11节' }
  return units.map(unit => names[unit] || unit).join('、')
}
// 楼栋刻度条：以间数最多的楼栋为 100%，最少也留 6% 让"有但很少"看得出来
function barWidth(count: number) {
  const buildings = props.msg.campus_result?.summary?.buildings || []
  const max = Math.max(1, ...buildings.map(b => b.rooms.length))
  return `${Math.max(6, Math.round((count / max) * 100))}%`
}
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
  if (!target || !source) { scheduleHide(); return }
  const rect = target.getBoundingClientRect(), width = Math.min(400, window.innerWidth - 24)
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
  const top = rect.bottom + 288 < window.innerHeight ? rect.bottom + 8 : Math.max(12, rect.top - 288)
  cancelHide()
  previewMarker = target
  target.setAttribute('aria-describedby', previewId)
  citationPreview.value = { source, top, left, width }
}
/** 立刻收起（滚动、点击、组件卸载时用）。 */
function hidePreview() {
  cancelHide()
  previewMarker?.removeAttribute('aria-describedby')
  previewMarker = null
  citationPreview.value = null
}
/** ★ 延后收起：卡片是可以把鼠标移上去阅读的，所以离开引用角标后不能立即销毁。
 *  留 200ms 窗口让指针从角标移到卡片；进入卡片会调用 cancelHide 取消。 */
let hideTimer: ReturnType<typeof setTimeout> | null = null
function scheduleHide() {
  if (holding) return
  cancelHide()
  hideTimer = setTimeout(() => { hideTimer = null; hidePreview() }, 200)
}
function cancelHide() {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null }
}
/** ★ 在卡片上按下（拖选文字、拖动内部滚动条）期间不收起：
 *  这类操作会先触发 mouseleave，若照常计时，卡片会在拖到一半时消失。 */
let holding = false
function holdPreview() { holding = true; cancelHide() }
function releasePreview() { holding = false }
/** ★ 滚轮：优先滚卡片自身内容；内容不够高时阻止默认，避免把页面滚走
 *  （页面一滚，卡片会跟着锚点位置失效）。 */
function onPreviewWheel(event: WheelEvent) {
  cancelHide()
  const el = event.currentTarget as HTMLElement
  if (el.scrollHeight <= el.clientHeight + 1) event.preventDefault()
}
/** ★ 滚动监听改为「忽略卡片内部的滚动」——原来 capture 监听会把卡片自身的
 *  滚动也当成页面滚动，一滚就关。 */
function onWindowScroll(event: Event) {
  const card = document.getElementById(previewId)
  const target = event.target
  if (card && target instanceof Node && card.contains(target)) return
  hidePreview()
}
function leaveCitation(event: MouseEvent) {
  const target = citationTarget(event)
  const next = event.relatedTarget as Node | null
  if (target?.contains(next)) return
  // 指针移进浮层本身时也要保持打开，否则读不完一屏就被收走。
  const card = document.getElementById(previewId)
  if (card && next && card.contains(next)) return
  scheduleHide()
}
function openPreviewSource() {
  const source = citationPreview.value?.source
  if (!source) return
  hidePreview()
  emit('open-source', source)
}
function openCitation(event: MouseEvent) {
  const target = citationTarget(event)
  const source = props.msg.retrieved_docs?.find(item => item.citation_id === target?.dataset.citationId)
  if (source) { event.preventDefault(); hidePreview(); emit('open-source', source) }
}
onMounted(() => { window.addEventListener('scroll', onWindowScroll, true); window.addEventListener('resize', hidePreview); window.addEventListener('mouseup', releasePreview) })
onBeforeUnmount(() => { cancelHide(); window.removeEventListener('scroll', onWindowScroll, true); window.removeEventListener('resize', hidePreview); window.removeEventListener('mouseup', releasePreview) })

const isStreamingMsg = computed(() => props.streaming && props.index === props.streamingIndex)

// —— 等待态：三个点上下跳动（CSS 优先，JS 兜底）——
//
// ★ 为什么还要一层 JS 兜底：CSS keyframes 能不能跑起来，取决于一串外部条件 ——
//   scoped 重写后的动画名是否与引用一致、系统的"减少动效"是否把时长压成 0、
//   合成层是否被禁用。任何一条不满足，用户看到的就是**三个静止的点**；
//   而"静止"与"已经卡死"在视觉上无法区分，这比动画不好看严重得多。
//   所以这里读一次 computed animationName：CSS 没接管就用 WAAPI 补一份等价动画。
//   WAAPI 由合成器直接驱动，不受 CSS 优先级与 scoped 重写影响。
const thinkingEl = ref<HTMLElement>()
const BOUNCE_KEYFRAMES: Keyframe[] = [
  { transform: 'translateY(0) scale(1.16, 0.84)', opacity: 0.82, offset: 0 },
  { transform: 'translateY(-2.5px) scale(0.85, 1.18)', opacity: 0.92, offset: 0.15 },
  { transform: 'translateY(-6.5px) scale(0.92, 1.1)', opacity: 1, offset: 0.4 },
  { transform: 'translateY(-7px) scale(1, 1)', opacity: 1, offset: 0.55 },
  { transform: 'translateY(-3px) scale(0.88, 1.14)', opacity: 0.86, offset: 0.75 },
  { transform: 'translateY(-1px) scale(0.94, 1.07)', opacity: 0.84, offset: 0.9 },
  { transform: 'translateY(0) scale(1.16, 0.84)', opacity: 0.82, offset: 1 },
]

async function ensureBounce() {
  await nextTick()
  const root = thinkingEl.value
  if (!root?.querySelectorAll) return
  root.querySelectorAll<HTMLElement>('.drop').forEach((node, index) => {
    // CSS 已经接管（名字会被 scoped 重写成 dot-bounce-xxxx）就不再插手
    if (getComputedStyle(node).animationName.includes('dot-bounce')) return
    if (typeof node.animate !== 'function') return
    // 重复进入时先清掉自己上一次的动画，避免叠加成倍速
    node.getAnimations().filter(a => a.id === 'thinking-bounce').forEach(a => a.cancel())
    node.animate(BOUNCE_KEYFRAMES, {
      id: 'thinking-bounce',
      duration: 1500,
      delay: index * 160,
      iterations: Infinity,
      easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
      fill: 'backwards',
    })
  })
}
watch(isStreamingMsg, on => { if (on) void ensureBounce() }, { immediate: true })

const exportError = ref('')

async function download(file: ChatFileRef) {
  try {
    await downloadChatFile(file)
  } catch (error: any) {
    exportError.value = error?.message || '下载失败'
  }
}

/** 文件体积：B / KB / MB，只保留有意义的小数位。 */
function fileSize(bytes: number) {
  const size = Number(bytes || 0)
  if (size < 1024) return `${size} B`
  if (size < 1048576) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`
  return `${(size / 1048576).toFixed(1)} MB`
}

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
  const t = splitSource.value.main
  if (!t) return ''
  // ★ 流式期间走轻量管线（跳过 KaTeX 与引用注入）：这几帧用户看的是"字在长"，
  //   而完整管线要对全文重排公式、给每个引用打按钮，单次 10–40ms 就会把主线程
  //   占满，表现为回答"一顿一顿地跳"。定稿后立刻切回完整渲染。
  //   超长回答（>12k 字）即使定稿也不上 KaTeX —— 那种长度下公式渲染的收益
  //   远小于几秒白屏的代价。
  if (isStreamingMsg.value) return renderMarkdownLite(t)
  return renderCitedAnswer(t, props.msg.retrieved_docs || [])
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
.message.assistant .md {
  padding: 18px 20px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  color: var(--text);
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.message.assistant .md :deep(table) { display: block; max-width: 100%; overflow-x: auto; }
@media (max-width: 600px) {
  .message { gap: 8px; margin-bottom: 24px; }
  .message.user .body { max-width: 90%; }
  .message.assistant .md { padding: 14px; font-size: 15px; }
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
/* ★ 浮层可交互：pointer-events 必须是 auto，否则鼠标一离开角标就点不到它；
   配合 JS 里的 200ms 延时收起，指针可以从角标平滑移到卡片上继续读。
   内容超高时允许内部滚动（原来 overflow:hidden 会把长摘录直接切掉）。 */
.citation-preview { position: fixed; z-index: 900; padding: 16px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); color: var(--text); box-shadow: var(--shadow-lg, 0 16px 48px -12px rgb(24 26 40 / 18%)); max-height: min(320px, calc(100dvh - 24px)); overflow: auto; overscroll-behavior: contain; cursor: pointer; }
.citation-preview:hover { border-color: var(--accent-border); }
.preview-heading { display: flex; align-items: flex-start; gap: 12px; }
.preview-icon { width: 34px; height: 34px; flex: 0 0 34px; display: grid; place-items: center; color: var(--accent-ink); background: var(--accent-soft); border-radius: 9px; }
.preview-title { min-width: 0; }
.preview-label { display: block; color: var(--text-muted); font-size: 12px; line-height: 1.3; }
.preview-title strong { display: -webkit-box; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; margin-top: 4px; font-size: 14px; font-weight: 600; line-height: 1.5; }
.preview-metadata { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.preview-meta-item { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; color: var(--text-muted); font-size: 12px; line-height: 1.4; }
.preview-meta-item b { color: var(--text); font-weight: 500; }
.preview-excerpt { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.preview-excerpt-label { color: var(--text-muted); font-size: 12px; }
.preview-excerpt p { display: -webkit-box; overflow: hidden; -webkit-line-clamp: 6; -webkit-box-orient: vertical; margin: 6px 0 0; color: var(--text); font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.preview-footer { display: flex; align-items: center; gap: 2px; margin-top: 12px; color: var(--accent-ink); font-size: 12px; }
/* 6px 圆角的小标签，**不做胶囊** —— 满屏胶囊是另一个通用痕迹 */
.source-tag {
  font-size: 13px;
  background: var(--surface);
  color: var(--text-muted);
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  max-width: 100%;
  text-align: left;
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

/* —— 生成中：三个圆点上下跳动 ——
   ★ 形状就是普通圆点（`border-radius: 50%`），不做水滴外形。水的质感靠**弹跳中的
     形变**给：触底压扁（宽而矮）→ 起跳被拉长 → 顶点回圆 → 回落再拉长 → 触底压扁。

   ★ transform 顺序 `translateY() scale()`：CSS 从右往左作用，先 scale 再位移。
     纯圆点没有 rotate，少一个变量；压扁时横向变宽、纵向变矮，体积感才不会塌。

   ★ 0% 与 100% 帧完全一致 ⇒ 循环处无跳变；三个点错开 0.16s 形成波浪接力。

   ★ 缓动写**字面量**而不是 `var(--ease)`：动画简写里嵌 var() 时，各浏览器对
     "变量替换后再拆分简写"的处理并不一致，一旦解析失败整条 animation 声明
     会被丢弃 —— 表现就是"形状对了但完全不动"，且不报任何错误。这类地方不值得赌。 */
.thinking {
  display: inline-flex;
  align-items: flex-end;
  gap: 6px;
  /* 点 6px、上跳 7px：容器 22 足够容纳纵向拉伸后的包围盒（约 4px 余量） */
  height: 22px;
  padding: 0 2px 4px;
}
.thinking .drop {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  /* backwards：第 2、3 滴有 delay，delay 期间直接套用 0% 帧，避免先露出未形变的样子 */
  animation: dot-bounce 1.5s cubic-bezier(0.2, 0.8, 0.2, 1) 0s infinite backwards;
  will-change: transform, opacity;
}
.thinking .drop:nth-child(2) {
  animation-delay: 0.16s;
}
.thinking .drop:nth-child(3) {
  animation-delay: 0.32s;
}
@keyframes dot-bounce {
  /* 触底：压扁、略暗 */
  0%,
  100% {
    transform: translateY(0) scale(1.16, 0.84);
    opacity: 0.82;
  }
  /* 起跳：拉长 */
  15% {
    transform: translateY(-2.5px) scale(0.85, 1.18);
    opacity: 0.92;
  }
  /* 接近顶点 */
  40% {
    transform: translateY(-6.5px) scale(0.92, 1.1);
    opacity: 1;
  }
  /* 顶点：回圆、最亮 */
  55% {
    transform: translateY(-7px) scale(1, 1);
    opacity: 1;
  }
  /* 回落：再次拉长 */
  75% {
    transform: translateY(-3px) scale(0.88, 1.14);
    opacity: 0.86;
  }
  /* 将触底 */
  90% {
    transform: translateY(-1px) scale(0.94, 1.07);
    opacity: 0.84;
  }
}
.result-files {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0 2px;
}
/* 用户消息右对齐：附件卡跟着气泡靠右，读起来才是「这条消息带的」 */
.message.user .result-files {
  justify-content: flex-end;
}
.result-file {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 300px;
  padding: 6px 10px 6px 6px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-2);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
  text-align: left;
}
.result-file-ic {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--accent-ink);
}
.result-file-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.35;
}
.result-file-name {
  font-weight: 500;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.result-file-meta {
  font-size: 11px;
  color: var(--text-faint);
}
.result-file:hover {
  border-color: var(--accent-border);
  color: var(--accent-ink);
}
/* ── 空教室结果：橙色数据区 ──
   数字是主角（32px 等宽橙），楼栋用 2px 刻度条表达占比，教室号是 chip。
   这里的数据色是"橙色只给数据"这条规则的唯一落点。 */
.campus-result {
  margin: 14px 0 2px;
  padding: 16px 18px 6px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-top: 2px solid var(--data-fill);
  border-radius: 12px;
}
.campus-result-top {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}
.campus-result-total {
  font-size: 32px;
  font-weight: 500;
  line-height: 0.95;
  letter-spacing: -0.03em;
  color: var(--data);
}
.campus-result-cap {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-bottom: 2px;
  font-size: 12px;
  color: var(--text-muted);
}
.campus-result-date {
  font-size: 11px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
}
.campus-result-query {
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-muted);
}
.campus-result-groups {
  margin-top: 2px;
}
.campus-result-group {
  padding: 12px 0;
  border-top: 1px solid var(--border);
}
.campus-result-group:first-child {
  border-top: none;
}
.campus-result-group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.campus-result-group-name {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.campus-result-count {
  font-size: 13px;
  font-weight: 600;
  color: var(--data);
}
/* 刻度条：轨道用极淡橙，填充用橙 —— 长度即占比，比再画一组柱子轻 */
.campus-result-bar {
  display: block;
  height: 2px;
  margin: 8px 0 10px;
  border-radius: 1px;
  background: var(--data-soft);
  overflow: hidden;
}
.campus-result-bar i {
  display: block;
  height: 100%;
  border-radius: 1px;
  background: var(--data-fill);
}
.campus-result-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.campus-result-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 5px;
  border: 1px solid transparent;
  background: var(--data-soft);
  font-size: 12px;
  color: var(--data);
  font-variant-numeric: tabular-nums;
  cursor: default;
}
.campus-result-chip:hover {
  border-color: var(--data-fill);
}
.export-error {
  margin-top: 6px;
  font-size: 12px;
  color: var(--danger, var(--danger));
}
.action-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
