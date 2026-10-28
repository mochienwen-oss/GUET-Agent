<template>
  <details class="presentation-composer" :open="expanded">
    <summary>{{ taskId ? '制作需求与参考资料' : '你想做一份怎样的演示文稿？' }}<span v-if="materials.length"> · {{ materials.length }} 份材料</span></summary>
    <fieldset :disabled="busy || locked">
    <form @submit.prevent="generate">
      <label>主题与目标<textarea v-model="goal" required maxlength="2000" rows="3" placeholder="例如：做一份介绍人工智能如何改变日常工作的演示，重点讲清实际应用和注意事项" /></label>
      <div class="brief-row">
        <label>受众（可选）<input v-model="audience" placeholder="未填写时面向一般听众" /></label>
        <label>总页数（可选）<input v-model.number="pages" type="number" min="3" max="20" placeholder="自动安排" /></label>
        <label>时长（分钟，可选）<input v-model="duration" type="number" min="1" max="120" /></label>
      </div>
      <label>希望听众理解或采取的行动<input v-model="outcome" placeholder="可选" /></label>
      <label>必须保留的内容<input v-model="mustKeep" placeholder="多项用分号分隔，可选" /></label>
      <label>内容来源<select v-model="policy">
        <option value="general">通用创作，可结合我的大纲与材料</option>
        <option value="provided_only">仅用我提供的材料</option>
        <option value="campus">结合校园知识库制作</option>
        <option v-if="policy === 'public'" value="public">通用创作（原公开资料模式）</option>
      </select></label>
      <p class="hint">{{ policy === 'provided_only' ? '请提供材料；缺少依据的内容会保留为待补充。' : policy === 'campus' ? '按需检索学校具体资料，其他内容围绕你的主题组织。' : '无需先上传材料。可创作一般主题；具体统计、近期信息和机构政策需要你提供依据。' }}当前不自动联网搜索或读取链接。</p>
      <fieldset class="style-references"><legend>风格参考（可选）</legend>
        <div class="style-options"><button v-for="item in styles" :key="item.id" type="button" :aria-pressed="style === item.brief" @click="style = item.brief">
          <img :src="`/api/ppt/style-reference/${item.id}`" :alt="`${item.name}风格参考`" loading="lazy" /><span>{{ item.name }}</span>
        </button></div>
        <p class="hint">参考图来自 PPT Master，用于表达视觉偏好，不是当前任务成品或固定模板。</p>
      </fieldset>
      <label>风格偏好<input v-model="style" placeholder="例如：大留白、清晰的对比关系，避免每页都用同一种布局" /></label>
      <label>参考内容或大纲（可选）<textarea v-model="text" rows="4" placeholder="粘贴已有大纲、重点内容或参考资料；没有也可以直接开始" /></label>
      <label>文档、数据或配图（可选）<input type="file" multiple accept=".txt,.md,.docx,.pdf,.csv,.xlsx,.png,.jpg,.jpeg,.webp" @change="selectFiles" /></label>
      <p class="hint">支持文字 PDF、DOCX、TXT、Markdown、CSV、数值型 XLSX 和 PNG/JPEG/WebP 图片。图片可作为页面素材，需解读图片时请补充文字说明。资料只用于本次任务。</p>
      <p v-if="files.length" class="hint">已选择 {{ files.map(file => file.name).join('、') }}</p>
      <div class="composer-actions"><button class="ppt-action" :disabled="busy || locked">{{ busy ? '正在处理…' : taskId ? '重新策划与制作' : '开始策划' }}</button>
        <button v-if="taskId" type="button" class="quiet-action" :disabled="busy || locked" @click="prepare">仅保存设置与材料</button></div>
    </form>

    <template v-if="taskId">
      <ul class="material-list"><li v-for="m in materials" :key="m.material_id">
        {{ m.title }}<span v-if="m.source_type === 'user_image'"> · 图片素材</span><span v-else-if="m.columns?.length"> · {{ m.row_count }}行数据</span>
      </li></ul>
      <form v-if="dataMaterials.length" @submit.prevent="calculate">
        <div class="brief-row">
          <label>数据表<select v-model="dataId"><option value="">选择材料</option><option v-for="m in dataMaterials" :key="m.material_id" :value="m.material_id">{{ m.title }}</option></select></label>
          <label>计算<select v-model="operation"><option value="sum">求和</option><option value="mean">均值</option><option value="count">计数</option><option value="share">占比</option></select></label>
        </div>
        <div class="brief-row">
          <label v-if="operation !== 'count'">数值字段<select v-model="field" required><option value="">请选择</option><option v-for="c in columns" :key="c">{{ c }}</option></select></label>
          <label>分组字段<select v-model="group"><option value="">不分组</option><option v-for="c in columns" :key="c">{{ c }}</option></select></label>
          <label v-if="operation === 'share'">分母字段<select v-model="denominator" required><option value="">请选择</option><option v-for="c in columns" :key="c">{{ c }}</option></select></label>
          <label v-if="operation === 'share'">分母口径<select v-model="denominatorAggregation" required><option value="">请选择</option><option value="sum">逐行分母合计</option><option value="single">组内重复填写的同一总量</option></select></label>
        </div>
        <div class="brief-row"><label>单位<input v-model="unit" placeholder="如：人、学时" /></label><label>图表<select v-model="chartType"><option value="bar">柱状图</option><option value="line">折线图</option></select></label></div>
        <div class="brief-row"><label>筛选字段（可选）<select v-model="filterField"><option value="">不筛选</option><option v-for="c in columns" :key="c">{{ c }}</option></select></label><label v-if="filterField">等于<input v-model="filterValue" required /></label></div>
        <button class="ppt-action" :disabled="busy || !dataId" type="submit">计算并加入制作材料</button>
        <p v-if="analysisSummary" class="hint">{{ analysisSummary }}</p>
      </form>
    </template>
    </fieldset>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </details>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  createPresentation, updatePresentationBrief, addPresentationText, uploadPresentationMaterials,
  analysePresentation, generatePresentation,
} from '../api/presentation'
import type { PptTask, PptMaterial, PublicCandidate } from '../types/presentation'
const props = defineProps<{ conversationId: number | null; task?: PptTask | null; expanded?: boolean; locked?: boolean }>()
const emit = defineEmits<{ (e: 'task', id: string): void; (e: 'running', value: boolean): void }>()
const goal = ref(''), audience = ref(''), outcome = ref(''), mustKeep = ref(''), text = ref('')
const pages = ref<number | string>(6), policy = ref('general'), busy = ref(false), error = ref('')
const style = ref('清晰、简洁的中文演示')
const styles = [
  { id: 'swiss-minimal', name: '简洁留白', brief: '简洁留白，清晰网格与文字层级，按页面作用变化构图' },
  { id: 'editorial', name: '编辑叙事', brief: '编辑叙事，以鲜明标题和图文关系组织信息，兼顾整份演示的节奏' },
  { id: 'blueprint', name: '结构图解', brief: '结构图解，用流程、关系和对比解释内容，强调清晰标注与逻辑顺序' },
]
const duration = ref('')
const taskId = ref(''), revision = ref(0), files = ref<File[]>([]), materials = ref<PptMaterial[]>([])
const dataId = ref(''), operation = ref('sum'), field = ref(''), group = ref(''), denominator = ref(''), unit = ref(''), chartType = ref('bar')
const filterField = ref(''), filterValue = ref('')
const denominatorAggregation = ref('')
const analysisSummary = ref(''), candidates = ref<PublicCandidate[]>([])
const dataMaterials = computed(() => materials.value.filter(m => m.columns?.length))
const columns = computed(() => materials.value.find(m => m.material_id === dataId.value)?.columns || [])
const savedText = ref('')
watch(() => props.task, task => {
  if (!task) {
    taskId.value = ''; revision.value = 0; materials.value = []; text.value = ''; savedText.value = ''; files.value = []
    goal.value = ''; audience.value = ''; outcome.value = ''; mustKeep.value = ''; pages.value = 6; duration.value = ''
    candidates.value = []; analysisSummary.value = ''; dataId.value = ''; policy.value = 'general'; style.value = '清晰、简洁的中文演示'
    return
  }
  if (!task || task.workflow_version !== 2) return
  if (task.taskId !== taskId.value && task.brief) {
    text.value = ''; savedText.value = ''; files.value = []
    taskId.value = task.taskId; goal.value = task.brief.goal; audience.value = task.brief.audience
    outcome.value = task.brief.outcome || ''; pages.value = task.brief.page_count ?? ''
    duration.value = task.brief.duration_minutes ? String(task.brief.duration_minutes) : ''
    policy.value = task.brief.material_policy || 'provided_only'; mustKeep.value = (task.brief.must_keep || []).join('；')
    style.value = task.brief.style || '清晰、简洁的中文演示'
    candidates.value = task.brief.public_candidates || []
  }
  revision.value = task.revision || 0; materials.value = task.materials || []
}, { immediate: true })
watch(() => props.conversationId, () => {
  if (!busy.value && props.task?.taskId !== taskId.value) {
    taskId.value = ''; revision.value = 0; materials.value = []; text.value = ''; savedText.value = ''; files.value = []
    candidates.value = []; analysisSummary.value = ''; dataId.value = ''; policy.value = 'general'
  }
})
function selectFiles(event: Event) { files.value = Array.from((event.target as HTMLInputElement).files || []) }
function message(e: any) { return e.response?.data?.error || e.message || '操作未完成' }
async function perform(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await fn() } catch (e) { error.value = message(e) } finally { busy.value = false }
}
async function prepare() {
  await perform(async () => {
    if (!goal.value.trim()) throw new Error('请填写演示主题与目标')
    if (policy.value === 'provided_only' && !materials.value.length && !text.value.trim() && !files.value.length) throw new Error('仅用提供材料时，请先粘贴内容或选择文件')
    const body = { conversation_id: props.conversationId, brief: { goal: goal.value.trim(), audience: audience.value.trim() || '一般听众', outcome: outcome.value,
      page_count: pages.value ? Number(pages.value) : null, duration_minutes: duration.value ? Number(duration.value) : null,
      style: style.value.trim() || '清晰、简洁的中文演示', must_keep: mustKeep.value.split(/[；;]/).map(s => s.trim()).filter(Boolean), material_policy: policy.value, public_candidates: candidates.value },
      materials: text.value.trim() ? [{ title: '粘贴材料', text: text.value, source_type: 'user_text' }] : [] }
    if (taskId.value) {
      const updated = await updatePresentationBrief(taskId.value, body.brief, revision.value)
      materials.value = updated.materials
      if (text.value.trim() && text.value !== savedText.value) {
        materials.value = (await addPresentationText(taskId.value, body.materials[0])).materials
      }
    } else {
      const created = await createPresentation(body)
      taskId.value = created.task_id; revision.value = 0; materials.value = created.materials
    }
    savedText.value = text.value
    if (files.value.length) {
      const uploaded = await uploadPresentationMaterials(taskId.value, files.value)
      materials.value = uploaded.materials
      files.value = []
    }
    emit('task', taskId.value)
  })
}
async function calculate() {
  await perform(async () => {
    const result = await analysePresentation(taskId.value, { material_id: dataId.value, operation: operation.value,
      field: field.value || null, group_by: group.value || null, denominator_field: denominator.value || null,
      denominator_aggregation: denominatorAggregation.value || null,
      filters: filterField.value ? { [filterField.value]: filterValue.value } : {},
      unit: unit.value, chart_type: chartType.value })
    analysisSummary.value = result.categories.map((c: string, i: number) => `${c}：${result.values[i]}${result.unit}`).join('；')
  })
}
async function generate() {
  await prepare()
  if (error.value || !taskId.value) return
  emit('running', true)
  await perform(async () => {
    const result = await generatePresentation(taskId.value, revision.value)
    revision.value = result.revision; emit('task', taskId.value)
    if (result.stage === 'failed' || result.stage === 'paused') throw new Error(result.issues?.slice(-1)[0]?.detail || '制作未完成，请查看任务状态')
  })
  emit('running', false)
}
</script>

<style scoped>
.presentation-composer { margin: 0 0 24px; padding: 20px 24px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
fieldset { border: 0; min-width: 0; }
fieldset:disabled { opacity: .65; }
summary { cursor: pointer; font-weight: 600; }
form { display: grid; gap: 12px; margin-top: 18px; }
label { display: grid; gap: 6px; font-size: 13px; color: var(--text); }
input, textarea, select { width: 100%; box-sizing: border-box; padding: 9px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); font: inherit; }
input[type=checkbox] { width: auto; }
.brief-row { display: flex; gap: 12px; }.brief-row > label { flex: 1; min-width: 0; }
.hint, .material-list { color: var(--text-muted); font-size: 12px; line-height: 1.7; margin: 0; }.material-list { margin-top: 16px; }
.error { color: var(--danger); font-size: 13px; }
.style-references { margin-top: 12px; }
.style-references legend { font-size: 13px; margin-bottom: 12px; }
.style-options { display: flex; gap: 16px; margin-bottom: 8px; }
.style-options button { flex: 1; min-width: 0; padding: 4px; border: 1px solid var(--border); border-radius: 6px; text-align: left; background: var(--bg); }
.style-options button[aria-pressed=true] { border-color: var(--accent); }
.style-options img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: contain; background: var(--surface); }
.style-options span { display: block; padding: 8px 4px; font-size: 13px; }
.style-options button:hover { border-color: var(--accent); }
.composer-actions { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.quiet-action { min-height: 44px; padding: 8px; color: var(--text-muted); }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (max-width: 600px) { .brief-row { flex-direction: column; } .presentation-composer { padding: 16px; } }
</style>
