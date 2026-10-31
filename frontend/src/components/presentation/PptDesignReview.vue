<template>
  <section aria-label="演示文稿制作方案">
    <template v-if="task.stage === 'await_design' && task.pending_user">
      <h3>{{ task.pending_user.stage === 'stage1' ? '确认内容方向与模板' : task.pending_user.stage === 'stage2' ? '确认大纲与设计方案' : '制作需要你的意见' }}</h3>
      <p>{{ task.pending_user.question }}</p>
      <div class="design-layout">
      <div class="design-proposal">
        <label v-if="editing">编辑当前方案<textarea v-model="proposal" :disabled="busy" rows="16" maxlength="6000" /></label>
        <template v-else><p v-for="(paragraph, i) in (task.pending_user.proposal || '').split(/\n\s*\n/)" :key="i">{{ paragraph }}</p></template>
        <button v-if="!isDialogue" type="button" class="edit-proposal" :disabled="busy" :aria-expanded="editing" @click="editing = !editing">{{ editing ? '返回原方案阅读' : '编辑当前大纲与方案' }}</button>
        <p v-if="proposal !== (task.pending_user.proposal || '')" class="proposal-note">提交时会一并发送你的方案修改。</p>
      </div>
      <form class="ppt-revision" @submit.prevent="answer(isDialogue ? false : decision === 'approve')">
        <fieldset v-if="!isDialogue" :disabled="busy"><legend>下一步怎么做</legend>
          <label class="decision"><input v-model="decision" type="radio" value="approve" />采用当前方案，继续制作</label>
          <label class="decision"><input v-model="decision" type="radio" value="adjust" />先调整方案，再次确认</label>
        </fieldset>
        <label>{{ isDialogue ? '补充信息' : decision === 'adjust' ? '需要调整的内容' : '补充意见（可选）' }}<textarea v-model="feedback" :disabled="busy" :required="(isDialogue || decision === 'adjust') && !proposalChanged" rows="4" placeholder="请输入补充信息或修改意见" /></label>
        <div class="ppt-actions">
          <button class="ppt-action" :disabled="busy || ((isDialogue || decision === 'adjust') && !feedback.trim() && !proposalChanged)">{{ isDialogue ? '回复并继续' : decision === 'adjust' ? '提交调整意见' : '确认并继续' }}</button>
        </div>
      </form>
      </div>
    </template>
    <template v-else-if="task.stage === 'paused'">
      <p>{{ task.progress?.label || '本轮制作已暂停，可核对已保存的进度后继续。' }}</p>
      <p v-if="task.issues?.length" class="ppt-error" role="alert">{{ task.issues[task.issues.length - 1].detail }}</p>
      <button class="ppt-action" :disabled="busy" @click="resume">继续制作</button>
    </template>
    <p v-if="busy || ['generating', 'master_continue'].includes(task.stage)" role="status">{{ task.progress?.label || '正在策划与制作，并保存进度…' }}<span v-if="task.progress?.tool_calls"> · 已调用工具 {{ task.progress.tool_calls }} 次</span></p>
    <p v-if="error" role="alert" class="ppt-error">{{ error }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { answerPresentationDesign, continuePresentation } from '../../api/presentation'
import type { PptTask, PptTaskStatus } from '../../types/presentation'

const props = defineProps<{ task: PptTask }>()
const isDialogue = computed(() => props.task.pending_user?.stage === 'dialogue')
const emit = defineEmits<{ (e: 'refresh'): void; (e: 'running', value: boolean): void }>()
const feedback = ref(''), busy = ref(false), error = ref('')
const decision = ref('approve')
const editing = ref(false), proposal = ref('')
const proposalChanged = computed(() => proposal.value !== (props.task.pending_user?.proposal || ''))
let active = true
onBeforeUnmount(() => { active = false })

async function finishSteps(status: PptTaskStatus) {
  while (active && props.task.taskId === status.task_id && status.stage === 'master_continue') {
    emit('refresh')
    status = await continuePresentation(status.task_id, status.revision)
  }
  emit('refresh')
}
async function perform(operation: () => Promise<PptTaskStatus>) {
  if (busy.value) return
  const taskId = props.task.taskId
  busy.value = true; error.value = ''
  emit('running', true)
  try { await finishSteps(await operation()); feedback.value = '' }
  catch (e: any) { error.value = e.message || '本次制作未完成'; emit('refresh') }
  finally {
    busy.value = false
    emit('running', false)
    if (active && props.task.taskId !== taskId && props.task.stage === 'master_continue') void resume()
  }
}
async function answer(approved: boolean) {
  const id = props.task.pending_user?.confirmation_id
  if (!id) return
  const changed = proposalChanged.value
  if (changed && !proposal.value.trim()) { error.value = '编辑后的方案不能为空'; return }
  const reply = [changed ? '我编辑后的方案如下，请以此更新制作内容：\n' + proposal.value : '', feedback.value.trim()].filter(Boolean).join('\n\n')
  if (reply.length > 6000) { error.value = '方案修改与补充意见合计超过6000字，请精简后提交'; return }
  await perform(() => answerPresentationDesign(props.task.taskId, props.task.revision || 0,
    id, approved, reply))
}
async function resume() {
  await perform(() => continuePresentation(props.task.taskId, props.task.revision || 0))
}
watch(() => [props.task.stage, props.task.taskId], ([stage]) => {
  if (stage === 'master_continue') void resume()
}, { immediate: true })
watch(() => props.task.pending_user?.confirmation_id, () => {
  decision.value = 'approve'; feedback.value = ''; editing.value = false
  proposal.value = props.task.pending_user?.proposal || ''
}, { immediate: true })
</script>

<style scoped>
h3 { font-size: 18px; margin: 0 0 12px; }
p { line-height: 1.7; }
.design-layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 32px; margin-top: 24px; align-items: start; }
.design-proposal { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; line-height: 1.8; min-width: 0; }
.design-proposal p + p { margin-top: 16px; }
.edit-proposal { min-height: 44px; margin-top: 16px; color: var(--accent-ink); text-decoration: underline; text-underline-offset: 3px; }
.proposal-note { color: var(--text-muted); font-size: 13px; }
fieldset { border: 0; padding: 0; min-width: 0; }
legend { font-weight: 600; font-size: 14px; margin-bottom: 12px; }
.decision { display: flex; align-items: center; gap: 8px; font-size: 13px; min-height: 44px; cursor: pointer; }
.decision input { accent-color: var(--accent); }
@media(max-width: 1100px) { .design-layout { grid-template-columns: minmax(0, 1fr); gap: 16px; } }
.ppt-revision { display: grid; gap: 12px; margin-top: 16px; }
label { display: grid; gap: 8px; }
textarea { width: 100%; box-sizing: border-box; padding: 10px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; font: inherit; }
.ppt-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.ppt-error { color: var(--danger); }
</style>
