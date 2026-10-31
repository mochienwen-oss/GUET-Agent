<template>
  <div class="presentation-shell">
    <section v-if="!taskId && !creating" class="presentation-list-view">
      <header class="workspace-title"><div><h1>我的演示文稿</h1><p>围绕你的汇报想法，边聊边制作。</p></div><button class="ppt-action" @click="newPresentation"><Icon name="plus" :size="16" />新建演示文稿</button></header>
      <div class="list-toolbar"><label class="search"><Icon name="search" :size="16" /><input v-model="search" aria-label="搜索演示文稿" placeholder="搜索演示文稿" /></label><button class="quiet-btn" :disabled="ppt.listLoading" @click="ppt.loadList()"><Icon name="refresh" :size="15" />刷新</button></div>
      <p v-if="ppt.listError" class="error" role="alert">{{ ppt.listError }} <button @click="ppt.loadList()">重新读取</button></p>
      <p v-else-if="ppt.listLoading && !ppt.tasks.length" class="empty" role="status">正在读取演示文稿…</p>
      <div v-else-if="!ppt.tasks.length" class="empty"><Icon name="presentation" :size="40" /><h2>把你的想法做成演示文稿</h2><p>从一句需求开始。课堂汇报、课程展示、论文汇报或答辩，都可以在同一个工作区继续讨论和修改。</p><button class="ppt-action" @click="newPresentation">开始制作</button></div>
      <div v-else class="document-list">
        <div class="document-labels"><span>演示文稿</span><span>状态</span><span>最近更新</span><span></span></div>
        <div v-for="item in filtered" :key="item.task_id" class="document-row-wrap ppt-action-row">
          <RouterLink :to="`/presentations/${item.task_id}`" class="document-row">
            <div class="document-name"><Icon name="presentation" :size="24" /><div><strong>{{ item.title }}</strong><small>{{ item.pages ? `${item.pages} 页 · ` : '' }}{{ item.revision ? `第 ${item.revision} 版` : '尚未生成页面' }}</small></div></div>
            <span class="document-stage">{{ item.background_running ? '后台制作中' : stageLabel(item.stage) }}</span><time>{{ dateLabel(item.updated_at) }}</time><Icon name="chevronRight" :size="17" />
          </RouterLink>
          <PresentationActions :task="item" />
        </div>
        <p v-if="!filtered.length" class="empty">没有找到匹配的演示文稿，试试其他名称。</p>
      </div>
    </section>
    <PptChatWorkspace v-else :key="workspaceKey" :task-id="taskId" @created="openCreated" @new="newPresentation" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePresentationStore } from '../store/presentation'
import PptChatWorkspace from '../components/presentation/PptChatWorkspace.vue'
import Icon from '../components/common/Icon.vue'
import PresentationActions from '../components/presentation/PresentationActions.vue'

const route = useRoute(), router = useRouter(), ppt = usePresentationStore()
const search = ref(''), workspaceKey = ref(0)
const taskId = computed(() => String(route.params.taskId || ''))
const creating = computed(() => route.query.new === '1')
const filtered = computed(() => ppt.tasks.filter(task => task.title.toLowerCase().includes(search.value.trim().toLowerCase())))
watch(() => [taskId.value, creating.value], () => { if (!taskId.value && !creating.value) void ppt.loadList() }, { immediate: true })
function newPresentation() { if (!taskId.value && creating.value) workspaceKey.value++; else void router.push('/presentations?new=1') }
function openCreated(id: string) { void router.replace(`/presentations/${id}`) }
function stageLabel(stage: string) { return ({ await_materials: '可继续讨论', brief_changed: '需求已更新', await_design: '待确认方案', draft: '待审阅', ready: '可下载', generating: '正在制作', master_continue: '可继续制作', paused: '可继续制作', failed: '制作未完成', materials_changed: '材料已更新', await_clarify: '待补充信息' } as Record<string, string>)[stage] || '制作中' }
function dateLabel(value: string) { return new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
</script>

<style scoped>
.presentation-shell { flex: 1; display: flex; min-width: 0; min-height: 0; }
.presentation-list-view { flex: 1; min-width: 0; overflow-y: auto; padding: 40px 40px 64px; }
.workspace-title { display: flex; align-items: center; justify-content: space-between; gap: 24px; max-width: 1200px; margin: 0 auto 32px; }
h1 { font-size: 24px; line-height: 1.4; font-weight: 600; letter-spacing: -.02em; }.workspace-title p { font-size: 14px; color: var(--text-muted); margin-top: 8px; }
.workspace-title > button { flex-shrink: 0; }.ppt-action, .quiet-btn { display: inline-flex; gap: 8px; align-items: center; justify-content: center; min-height: 44px; }
.quiet-btn { color: var(--text-muted); font-size: 13px; padding: 8px 12px; border-radius: 6px; }.quiet-btn:hover { background: var(--surface-2); color: var(--text); }
.list-toolbar { max-width: 1200px; display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 0 auto 24px; }.search { display: flex; align-items: center; gap: 8px; width: 320px; padding: 8px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }.search input { min-width: 0; width: 100%; font-size: 14px; }
.document-list { max-width: 1200px; margin: auto; }.document-labels, .document-row { display: grid; grid-template-columns: minmax(0, 1fr) 120px 140px 20px; gap: 24px; align-items: center; }.document-labels { color: var(--text-faint); font-size: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }.document-row { min-height: 96px; text-decoration: none; color: var(--text); border-bottom: 1px solid var(--border); padding: 20px 16px; }.document-row:hover { background: var(--surface); }.document-name { display: flex; align-items: center; gap: 16px; min-width: 0; }.document-name > svg { color: var(--text-muted); flex-shrink: 0; }.document-name > div { min-width: 0; }.document-name strong { display: block; font-size: 16px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.document-name small { display: block; color: var(--text-faint); font-size: 12px; margin-top: 4px; }.document-stage { font-size: 13px; color: var(--accent-ink); }time { font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.empty { text-align: center; padding: 80px 24px; color: var(--text-muted); }.empty > svg { margin-bottom: 20px; color: var(--text-faint); }.empty h2 { color: var(--text); font-size: 20px; font-weight: 500; }.empty p { font-size: 14px; line-height: 1.8; margin: 12px auto 24px; max-width: 480px; }.error { color: var(--danger); margin: 16px 0; }.error button { text-decoration: underline; padding: 8px; }
button:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media(max-width: 1100px) { .presentation-list-view { padding: 24px; }.document-labels, .document-row { grid-template-columns: minmax(0, 1fr) 100px 20px; gap: 12px; }.document-row time, .document-labels span:nth-child(3) { display: none; } }
@media(max-width: 600px) { .presentation-list-view { padding: 24px 16px 40px; }.workspace-title { align-items: flex-start; flex-direction: column; gap: 16px; }h1 { font-size: 20px; }.document-row { grid-template-columns: minmax(0, 1fr) 20px; padding-inline: 0; }.document-stage { grid-column: 1; margin-left: 40px; font-size: 12px; }.document-row > svg { grid-column: 2; grid-row: 1 / 3; }.document-labels { display: none; }.document-name { gap: 12px; }.document-name strong { font-size: 14px; }.list-toolbar { gap: 8px; }.search { width: 100%; } }
.document-row-wrap { position: relative; }
.document-labels, .document-row { grid-template-columns: minmax(0, 1fr) 120px 140px 80px; }
.document-row > svg { justify-self: end; }
.document-row-wrap:hover .document-row > svg, .document-row-wrap:focus-within .document-row > svg { opacity: 0; }
@media (max-width: 1100px) { .document-labels, .document-row { grid-template-columns: minmax(0, 1fr) 100px 80px; } }
@media (hover: none), (pointer: coarse) { .document-labels, .document-row { grid-template-columns: minmax(0, 1fr) 100px 120px 108px; }.document-row > svg { visibility: hidden; } }
@media (max-width: 1100px) and (hover: none), (max-width: 1100px) and (pointer: coarse) { .document-labels, .document-row { grid-template-columns: minmax(0, 1fr) 100px 108px; } }
@media (max-width: 600px) { .document-row { grid-template-columns: minmax(0, 1fr); padding-right: 108px; }.document-stage { grid-column: 1; }.document-row > svg { display: none; } }
</style>
