<template>
  <div v-if="task" class="ppt-status" :class="{ 'workspace-review': workspace }">
    <p v-if="task.error" class="ppt-error" aria-live="polite">{{ errorText }}</p>
    <PptDesignReview v-if="task.renderer === 'ppt-master'" :task="task" @refresh="emit('refresh')" @running="emit('running', $event)" />

    <!-- ① 已生成（草稿 / 已确认）：逐页审阅 —— 两种态共用同一份内容 -->
    <template v-if="hasReview">
      <div class="ppt-head">
        <span class="ppt-head-title">{{ isReady ? '演示文稿已确认' : '演示文稿草稿' }}</span>
        <span class="ppt-head-meta">
          <span>{{ pageCount }} 页</span>
          <span v-if="task.revision">第 {{ task.revision }} 版</span>
          <span v-if="claimCount">{{ claimCount }} 条要点</span>
          <span v-if="gapCount" class="ppt-head-gap">{{ gapCount }} 处待补证</span>
        </span>
      </div>
      <p class="ppt-hint">
        {{ isReady
          ? '点「下载 PPTX」取回文件。文件有效期有限，过期后重新确认即可再取。'
          : '核对实际页面、要点与依据。确认当前版本后即可下载，也可以只修改指定页面。' }}
      </p>
      <p v-if="task.stale_slide_ids?.length" class="ppt-src-gap">材料已更新，请先修订相关页面再确认。</p>
      <p v-if="task.revision_error" class="ppt-src-gap">{{ task.revision_error }}</p>

      <div :class="{ 'slide-workbench': workspace }">
      <nav v-if="workspace" class="slide-thumbnails" aria-label="选择预览页面">
        <button v-for="(page, pi) in review" :key="page.slide_id || pi" :class="{ selected: selectedPage === pi }"
          :aria-current="selectedPage === pi ? 'page' : undefined" :aria-label="`查看第 ${pi + 1} 页：${page.title}`" @click="selectPage(pi)">
          <img v-if="page.slide_id && previewImages[page.slide_id]" :src="previewImages[page.slide_id]" alt="" />
          <span v-else class="thumbnail-placeholder"><Icon name="presentation" :size="24" /></span>
          <span class="thumbnail-title">{{ pad(pi + 1) }} · {{ page.title }}</span>
        </button>
      </nav>
      <ol class="ppt-pages">
        <li v-for="(page, pi) in review" v-show="!workspace || selectedPage === pi" :key="pi" class="ppt-page">
          <div class="ppt-page-head">
            <span class="ppt-page-no">{{ pad(pi + 1) }}</span>
            <span class="ppt-page-title">{{ page.title || '（无标题）' }}</span>
            <span class="ppt-page-kind">{{ kindLabel(page.kind) }}</span>
          </div>
          <img v-if="page.slide_id && previewImages[page.slide_id]" :src="previewImages[page.slide_id]"
               :alt="`第${pi + 1}页：${page.title}`" class="ppt-preview" />
          <p v-if="page.purpose" class="ppt-page-sub">{{ page.purpose }}</p>
          <p v-if="page.subtitle" class="ppt-page-sub">{{ page.subtitle }}</p>
          <ul v-if="page.items?.length" class="ppt-nav">
            <li v-for="(it, k) in page.items" :key="k">{{ it }}</li>
          </ul>

          <ul v-if="page.claims?.length" class="ppt-claims">
            <li v-for="(c, ci) in page.claims" :key="ci">
              <!-- 有依据 → 可展开看是哪份文件；没有依据 → 只能是缺口，不可展开。 -->
              <button
                v-if="c.evidence_ids?.length"
                class="ppt-claim"
                type="button"
                :aria-expanded="isOpen(pi, ci)"
                @click="toggle(pi, ci)"
              >
                <span class="ppt-claim-text">{{ c.text }}</span>
                <span class="ppt-tag">依据 {{ c.evidence_ids.length }}</span>
                <Icon :name="isOpen(pi, ci) ? 'chevronDown' : 'chevronRight'" :size="14" />
              </button>
              <div v-else class="ppt-claim ppt-claim-plain">
                <span class="ppt-claim-text">{{ c.text }}</span>
                <span class="ppt-tag ppt-tag-gap">{{ c.kind === 'suggestion' ? '建议' : c.kind === 'analogy' ? '类比' : '待补证' }}</span>
              </div>

              <div v-if="isOpen(pi, ci)" class="ppt-src">
                <p v-if="c.gap" class="ppt-src-gap">{{ c.gap }}</p>
                <div v-for="(s, si) in sourcesOf(c)" :key="si" class="ppt-src-item">
                  <div class="ppt-src-head">
                    <Icon name="file" :size="13" />
                    <span class="ppt-src-title">{{ s.title || '未命名来源' }}</span>
                    <span v-if="s.loc" class="ppt-src-loc">{{ s.loc }}</span>
                  </div>
                  <p v-if="s.text" class="ppt-src-text">{{ s.text }}</p>
                  <p v-if="s.scope || s.date" class="ppt-src-loc">{{ [s.scope, s.date].filter(Boolean).join(' · ') }}</p>
                  <a v-if="s.url" :href="s.url" target="_blank" rel="noopener noreferrer">查看原始资料</a>
                </div>
                <p v-if="!sourcesOf(c).length && !c.gap" class="ppt-src-gap">
                  来源已不在本次任务的证据里。
                </p>
              </div>
            </li>
          </ul>
          <p v-else-if="task.renderer !== 'ppt-master'" class="ppt-empty">这一页没有正文要点。</p>
          <p v-if="page.chart" class="ppt-line">{{ page.chart.categories.map((c, i) => `${c}：${page.chart!.values[i]}${page.chart!.unit || ''}`).join('；') }}</p>
          <table v-if="page.table" class="ppt-data-table"><thead><tr><th v-for="c in page.table.columns" :key="c">{{ c }}</th></tr></thead>
            <tbody><tr v-for="(row, ri) in page.table.rows" :key="ri"><td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td></tr></tbody></table>

          <div v-if="page.notes?.length" class="ppt-notes">
            <span class="ppt-notes-label">备注</span>
            <p v-for="(n, ni) in page.notes" :key="ni">{{ n.text }}</p>
          </div>
        </li>
      </ol>
      </div>
      <div v-if="workspace" class="page-navigation"><button :disabled="selectedPage === 0" @click="selectPage(selectedPage - 1)"><Icon name="arrowLeft" :size="14" />上一页</button><span>{{ selectedPage + 1 }} / {{ review.length }}</span><button :disabled="selectedPage >= review.length - 1" @click="selectPage(selectedPage + 1)">下一页<Icon name="chevronRight" :size="14" /></button></div>
      <p v-if="previewError" class="ppt-src-gap">{{ previewError }}</p>

      <form v-if="['draft', 'ready', 'materials_changed'].includes(task.stage) && task.revision && review.some(p => p.slide_id)" class="ppt-revision" @submit.prevent="revise">
        <label>修改页面<select v-model="targetId" aria-label="修改页面"><option value="">请选择页面（追加可不选）</option>
          <option v-for="(page, i) in review" :key="page.slide_id" :value="page.slide_id">{{ i + 1 }} · {{ page.title }}</option></select></label>
        <label>修改方式<select v-model="revisionAction"><option value="replace">修改选定页面</option><option value="split">拆分选定页</option><option value="append">追加页面</option></select></label>
        <div class="revision-suggestions" aria-label="常用修改意见"><button v-for="suggestion in revisionSuggestions" :key="suggestion" type="button" :disabled="revisionBusy || busy" @click="feedback = suggestion">{{ suggestion }}</button></div>
        <label>具体要求<textarea v-model="feedback" required rows="3" placeholder="例如：减少文字，突出一条核心结论；保留当前数据与其他页面" /></label>
        <button class="ppt-action" :disabled="revisionBusy || busy">{{ revisionBusy ? '正在修订…' : '生成修订草稿' }}</button>
        <p v-if="revisionError" role="alert" class="ppt-src-gap">{{ revisionError }}</p>
      </form>

      <div v-if="['draft', 'ready'].includes(task.stage)" class="ppt-actions">
        <button
          v-if="!isReady"
          class="ppt-action"
          type="button"
          :disabled="busy || revisionBusy || !!task.stale_slide_ids?.length"
          @click="emit('confirm')"
        >
          {{ busy ? '正在确认…' : '确认草稿并获取下载' }}
        </button>
        <button v-else-if="workspace || task.ready" class="ppt-action" type="button" @click="emit('download')">
          下载 PPTX
        </button>
      </div>
    </template>

    <!-- ② 还没生成出内容：等澄清 / 进行中 / 出错待续 -->
    <template v-else-if="!['await_design', 'master_continue'].includes(task.stage) && !(task.renderer === 'ppt-master' && ['paused', 'generating'].includes(task.stage))">
      <div class="ppt-head">
        <span class="ppt-head-title">演示文稿</span>
      </div>
      <p v-if="task.stage === 'clarify'" class="ppt-line" aria-live="polite">
        {{ task.question || '请补充制作所需信息。' }}
      </p>
      <p v-else-if="task.stage === 'await_materials'" class="ppt-line">{{ task.brief?.material_policy === 'provided_only' ? '添加参考材料后，选择「开始策划」。' : '确认主题与制作需求后即可开始，参考材料可选。' }}</p>
      <p v-else-if="task.stage === 'failed'" class="ppt-src-gap">{{ task.issues?.length ? task.issues[task.issues.length - 1].detail : '本次制作未完成，请调整需求后重新策划。' }}</p>
      <p v-else-if="task.stage === 'brief_changed'" class="ppt-line">制作简报已更新，请重新生成草稿。</p>
      <p v-else-if="!task.error" class="ppt-line" aria-live="polite">
        {{ task.progress?.label || `正在${stageLabel(task.stage)}` }}<template v-if="task.progress"> ·
          已调用工具 {{ task.progress.tool_calls }} 次</template>
      </p>
      <button
        v-if="task.error?.recoverable && task.taskId"
        class="ppt-action"
        type="button"
        @click="emit('resume')"
      >
        继续未完成的任务
      </button>
    </template>
  </div>
</template>

<script setup lang="ts">
// PptReview.vue —— PPT 任务的**审阅与确认**面板。
//
// ★ 这里的「确认」按钮不是提示，而是后端的唯一放行口：后端生成完成只到 `draft`
//   （已生成待确认），下载凭证**只能**由 `POST /ppt/task/{id}/confirm` 签发
//   （见 backend/ppt/delivery.py 模块说明）。前端拿不到地址是拿不到，不是没显示。
import { computed, ref, watch } from 'vue'
import { revisePresentation } from '../../api/presentation'
import { usePptPreview } from '../../composables/usePptPreview'
import type { PptClaim, PptSlide, PptTask } from '../../types/presentation'
import Icon from '../common/Icon.vue'
import PptDesignReview from './PptDesignReview.vue'

const props = defineProps<{ task: PptTask | null; busy?: boolean; workspace?: boolean }>()
const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'download'): void
  (e: 'resume'): void
  (e: 'refresh'): void
  (e: 'running', value: boolean): void
}>()

const KIND_LABELS: Record<string, string> = {
  cover: '封面', agenda: '目录', bullets: '要点', compare: '对比', end: '结束', chart: '图表', table: '表格', master: '演示页面',
}
const STAGE_LABELS: Record<string, string> = {
  intake: '理解需求', plan: '规划资料', researching: '检索资料',
  checking: '核对依据', structured: '组织内容',
  generating: '生成页面与预览',
}

function kindLabel(kind: string) {
  return KIND_LABELS[kind] || kind || '页面'
}
function stageLabel(stage: string) {
  return STAGE_LABELS[stage] || stage
}
function pad(n: number) {
  return String(n).padStart(2, '0')
}

const review = computed<PptSlide[]>(() => props.task?.slides || [])
const hasReview = computed(() => review.value.length > 0)
const isReady = computed(() => props.task?.stage === 'ready')
const pageCount = computed(() => props.task?.pages || review.value.length)
const feedback = ref(''), targetId = ref(''), revisionAction = ref('replace'), revisionBusy = ref(false), revisionError = ref('')
const revisionSuggestions = ['精简文字，保留核心信息与已有依据', '突出本页核心结论，增强文字层级', '调整图文布局，保留内容与数据']
const selectedPage = ref(0)
function selectPage(index: number) {
  selectedPage.value = Math.max(0, Math.min(index, review.value.length - 1))
  targetId.value = review.value[selectedPage.value]?.slide_id || ''
}
watch(() => [props.task?.taskId, props.task?.revision, review.value.length], () => {
  if (props.workspace) selectPage(selectedPage.value)
}, { immediate: true })
watch(targetId, id => {
  const index = review.value.findIndex(page => page.slide_id === id)
  if (index >= 0) selectedPage.value = index
})
const { previewImages, previewError } = usePptPreview(() => props.task)
async function revise() {
  if (!props.task || revisionBusy.value) return
  if (props.task.renderer === 'ppt-master' && revisionAction.value !== 'append' && !targetId.value) {
    revisionError.value = '请先选择要修改的页面'
    return
  }
  revisionBusy.value = true; revisionError.value = ''
  emit('running', true)
  try {
    await revisePresentation(props.task.taskId, { expected_revision: props.task.revision,
      feedback: feedback.value, target_ids: targetId.value ? [targetId.value] : revisionAction.value === 'append' ? [] : null,
      action: revisionAction.value })
    feedback.value = ''; emit('refresh')
  } catch (e: any) { revisionError.value = e.response?.data?.error || e.message || '修订未完成' }
  finally { revisionBusy.value = false; emit('running', false) }
}

/** 备注里的事实也要按 claim 绑定（后端质检强制），所以计数一并算上。 */
function claimsIn(page: PptSlide): PptClaim[] {
  return [...(page.claims || []), ...(page.notes || [])]
}
const claimCount = computed(() => review.value.reduce((n, p) => n + claimsIn(p).length, 0))
const gapCount = computed(() =>
  review.value.reduce((n, p) => n + claimsIn(p).filter((c) => !c.evidence_ids?.length && (!c.kind || c.kind === 'fact')).length, 0)
)

const errorText = computed(() => {
  const e = props.task?.error
  if (!e) return ''
  return `制作未完成（${e.stage}）。${e.recoverable ? '可稍后续做。' : '请调整需求后重新发起。'}`
})

// 展开项用「页号-条号」当键 —— 用 claim 对象本身的话，重取一次状态就全丢了。
const open = ref<Set<string>>(new Set())
const keyOf = (pageIndex: number, claimIndex: number) => `${pageIndex}-${claimIndex}`
function isOpen(pageIndex: number, claimIndex: number) {
  return open.value.has(keyOf(pageIndex, claimIndex))
}
function toggle(pageIndex: number, claimIndex: number) {
  const key = keyOf(pageIndex, claimIndex)
  const next = new Set(open.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  open.value = next
}

function sourcesOf(claim: PptClaim) {
  const map = props.task?.sources || {}
  return (claim.evidence_ids || []).map((id) => {
    const s = map[id] || {}
    const loc = [...(s.heading_path || []), s.line ? `第 ${s.line} 行` : '']
      .filter(Boolean)
      .join(' · ')
    return { title: s.title, text: s.text, loc, scope: s.scope, date: s.date,
      url: s.url && /^https?:\/\//.test(s.url) ? s.url : undefined }
  })
}
</script>

<style scoped>
.ppt-status.workspace-review { margin: 0; padding: 24px; border-radius: 10px; }
.slide-workbench { display: grid; grid-template-columns: 152px minmax(0, 1fr); gap: 24px; margin-top: 24px; align-items: start; }
.slide-thumbnails { display: grid; grid-template-columns: minmax(0, 1fr); min-width: 0; gap: 16px; max-height: 720px; overflow-y: auto; padding: 2px; }
.slide-thumbnails button { display: block; min-width: 0; width: 100%; padding: 6px; border: 1px solid var(--border); border-radius: 6px; text-align: left; background: var(--bg); }
.slide-thumbnails button.selected { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-ink); }
.slide-thumbnails button:hover { border-color: var(--accent-border); }
.slide-thumbnails img, .thumbnail-placeholder { width: 100%; aspect-ratio: 16/9; display: grid; place-items: center; object-fit: contain; background: var(--surface-2); }
.slide-thumbnails img { height: auto; display: block; }
.thumbnail-title { display: block; font-size: 12px; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.workspace-review .ppt-pages { margin: 0; min-width: 0; }
.workspace-review .ppt-page + .ppt-page { margin: 0; padding: 0; border: 0; }
.workspace-review .ppt-preview { background: var(--surface-2); max-height: 65vh; object-fit: contain; }
.page-navigation { display: flex; justify-content: flex-end; align-items: center; gap: 16px; margin-top: 16px; font-size: 12px; color: var(--text-muted); }
.page-navigation button { display: flex; gap: 6px; align-items: center; min-height: 44px; padding: 8px; }
.workspace-review .ppt-revision { border-top: 1px solid var(--border); padding-top: 24px; }
@media(max-width: 700px) {
  .ppt-status.workspace-review { padding: 16px; }
  .slide-workbench { display: flex; flex-direction: column; gap: 20px; }
  .slide-thumbnails { display: flex; width: 100%; overflow-x: auto; padding-bottom: 8px; }
  .slide-thumbnails button { width: 110px; flex-shrink: 0; }
  .workspace-review .ppt-pages { width: 100%; }
  .workspace-review .ppt-claims { margin-left: 0; }
}
.ppt-preview { display: block; width: 100%; height: auto; margin: 12px 0; border: 1px solid var(--border); }
.ppt-revision { display: grid; gap: 10px; margin: 20px 0; }
.ppt-revision label { display: grid; gap: 6px; font-size: 13px; }
.ppt-revision select, .ppt-revision textarea { padding: 9px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; font: inherit; }
.ppt-data-table { display: block; overflow-x: auto; border-collapse: collapse; width: 100%; font-size: 13px; margin: 12px 0; }
.ppt-data-table th, .ppt-data-table td { border-bottom: 1px solid var(--border); padding: 8px; text-align: left; }
.ppt-status {
  margin: 16px 0 16px 44px; /* 头像 32 + gap 12，与消息正文对齐 */
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  overflow-wrap: anywhere;
}
.ppt-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 4px 12px;
}
.ppt-head-title {
  font-size: 16px;
  font-weight: 600;
}
.ppt-head-meta {
  display: flex;
  gap: 10px;
  font-size: 13px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.ppt-head-gap {
  color: var(--warn);
}
.ppt-hint {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-muted);
}
.ppt-error,
.ppt-line {
  margin: 0 0 12px;
  line-height: 1.7;
}
.ppt-error {
  color: var(--danger);
  font-size: 14px;
}

/* —— 逐页清单：序号 + 1px 细线分行（不用卡片、不用左侧色条） —— */
.ppt-pages {
  list-style: none;
  margin: 14px 0 0;
}
.ppt-page + .ppt-page {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.ppt-page-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.ppt-page-no {
  font-size: 13px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
}
.ppt-page-title {
  font-size: 15px;
  font-weight: 600;
  min-width: 0;
}
.ppt-page-kind {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-faint);
}
.ppt-page-sub {
  margin: 4px 0 0 26px;
  font-size: 13px;
  color: var(--text-muted);
}
.ppt-nav {
  list-style: none;
  margin: 6px 0 0 26px;
  font-size: 13px;
  color: var(--text-muted);
}
.ppt-nav li {
  font-variant-numeric: tabular-nums;
}

.ppt-claims {
  list-style: none;
  margin: 8px 0 0 26px;
}
/* 其余按钮基线（font/color/background/border/cursor/active 缩放/disabled）
   由 style.css 的全局 `button` 规则给，这里只写它没覆盖的几项。 */
.ppt-claim {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  min-height: 44px;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 14px;
  line-height: 1.6;
  text-align: left;
}
button.ppt-claim:hover {
  background: var(--surface-2);
}
button.ppt-claim:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.ppt-claim-plain {
  align-items: baseline;
}
.ppt-claim-text {
  flex: 1;
  min-width: 0;
}
.ppt-tag {
  flex-shrink: 0;
  align-self: center;
  padding: 2px 7px;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.ppt-tag-gap {
  border-color: var(--warn);
  color: var(--warn);
}
.ppt-claim svg {
  align-self: center;
  flex-shrink: 0;
  color: var(--text-faint);
}

/* 展开后的依据详情：--surface-2 平铺，不做第二层描边 */
.ppt-src {
  margin: 2px 0 6px;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--surface-2);
}
.ppt-src-item + .ppt-src-item {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.ppt-src-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted);
}
.ppt-src-title {
  font-weight: 600;
}
.ppt-src-loc {
  color: var(--text-faint);
}
.ppt-src-text {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-muted);
}
.ppt-src-gap {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--warn);
}

.ppt-notes {
  margin: 6px 0 0 26px;
  font-size: 13px;
  color: var(--text-muted);
}
.ppt-notes-label {
  font-size: 12px;
  color: var(--text-faint);
}
.ppt-notes p {
  margin: 2px 0 0;
  line-height: 1.7;
}
.ppt-empty {
  margin: 8px 0 0 26px;
  font-size: 13px;
  color: var(--text-faint);
}

.ppt-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}
.revision-suggestions { display: flex; flex-wrap: wrap; gap: 8px; }
.revision-suggestions button { min-height: 44px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; color: var(--text-muted); text-align: left; }
.revision-suggestions button:hover { background: var(--surface-2); color: var(--text); }
.revision-suggestions button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
</style>
