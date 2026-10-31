<template>
  <section ref="workspaceElement" class="ppt-workspace" :class="[`mobile-${mobilePane}`, { 'is-compact': compact }]" :style="panelStyle" aria-label="演示文稿制作工作区">
    <div class="mobile-switch" aria-label="工作区视图与制作控制">
      <button :aria-pressed="mobilePane === 'chat'" @click="mobilePane = 'chat'">对话</button>
      <button :aria-pressed="mobilePane === 'pages'" @click="mobilePane = 'pages'">页面<span v-if="slides.length"> · {{ slides.length }}</span></button>
    </div>

    <aside id="ppt-creation-chat" class="creation-chat" aria-label="制作对话">
      <header class="chat-header">
        <div><h2>制作助手</h2><p>围绕这份演示继续沟通</p><p v-if="task?.progress?.model_requests">累计调用 {{ task.progress.model_requests }} 次 · 输入 {{ (task.progress.input_tokens || 0).toLocaleString() }} / 输出 {{ (task.progress.output_tokens || 0).toLocaleString() }} token<span v-if="task.progress.reasoning_tokens"> · 其中思考 {{ task.progress.reasoning_tokens.toLocaleString() }}</span></p></div>
        <button class="icon-button" aria-label="新建演示文稿" @click="emit('new')"><Icon name="plus" :size="19" /></button>
      </header>
      <div ref="chatScroll" class="chat-history" @scroll="trackChatScroll" aria-live="polite" aria-relevant="additions text">
        <p v-if="loading" class="chat-notice" role="status">正在恢复制作对话…</p>
        <div v-else-if="!messages.length" class="chat-welcome">
          <Icon name="presentation" :size="28" /><h3>{{ task ? '继续完成这份演示' : '从你的汇报想法开始' }}</h3>
          <p>{{ task ? '已有资料与成品会保留在这个工作区。你可以继续讨论大纲，或选择页面提出修改。' : '告诉我主题、用途和你想强调的内容。已有的大纲、文档或配图也可以一起发来。' }}</p>
          <button v-if="!task" class="example-request" @click="draft = example">{{ example }}</button>
        </div>
        <article v-for="message in messages" :key="message.id" class="creation-message" :class="message.role">
          <PptMarkdown v-if="message.content && message.role === 'assistant'" class="assistant-content" :content="message.content" />
          <div v-else-if="message.content" class="message-text">{{ message.content }}</div>
          <ul v-if="message.attachments?.length" class="message-attachments"><li v-for="file in message.attachments" :key="file.material_id"><Icon name="file" :size="15" /><span>{{ file.title }}</span></li></ul>
          <div v-if="message.updated_pages?.length" class="updated-pages"><template v-for="page in message.updated_pages" :key="updatedPageKey(page)"><button v-if="updatedIndex(page) >= 0" @click="openUpdatedPage(page)">{{ updatedPageLabel(page) }}<Icon name="chevronRight" :size="13" /></button><span v-else>{{ updatedPageLabel(page) }}</span></template></div>
          <span v-if="message.id === pendingMessage?.id" class="sending-note">正在发送…</span>
        </article>
        <button v-if="outlineText" class="outline-entry" @click="openOutline"><Icon name="file" :size="18" /><span>查看与修改大纲</span><Icon name="chevronRight" :size="15" /></button>
        <section v-if="!busy && task?.stage === 'await_design' && task.pending_user" class="chat-confirmation" aria-label="待确认方案">
          <PptMarkdown v-if="proposalNotInHistory" class="confirmation-proposal" :content="proposalText" />
          <p class="confirmation-hint">{{ confirmationHint }}</p>
          <button v-if="!isDialogue && task.pending_user.action_label && task.pending_user.action_message" class="primary-action" :disabled="busy" @click="approve">{{ confirmationLabel }}</button>
        </section>
        <p v-if="busy || task?.stage === 'generating'" class="work-progress" role="status"><Icon name="bot" :size="16" />{{ progressText }}</p>
        <div v-if="failureText" class="work-error" role="alert"><p>{{ failureText }}</p><p class="error-help">{{ task ? '已登记的对话、资料和已有页面仍保留在当前任务中。' : '你的输入仍保留在下方，可调整后重新发送。' }}</p></div>
        <div v-if="canResume" class="resume-section"><p class="resume-hint">从已保存的项目接着制作，已有资料、页面和累计用量会保留。</p><button class="resume-action" @click="resume"><Icon name="refresh" :size="15" />继续已保存制作</button></div>
      </div>

      <form class="creation-composer" @submit.prevent="send()">
        <div v-if="selectedSlide" class="discussion-context"><span>正在讨论：第 {{ selectedIndex + 1 }} 页</span><button type="button" aria-label="清除页面选择，讨论整份演示" @click="choosePage(null)"><Icon name="close" :size="13" /></button></div>
        <ul v-if="pendingFiles.length || attached.length" class="queued-files">
          <li v-for="(file, index) in pendingFiles" :key="`file-${index}`"><Icon name="file" :size="13" /><span>{{ file.name }}</span><button type="button" :disabled="busy" :aria-label="`移除${file.name}`" @click="pendingFiles.splice(index, 1)"><Icon name="close" :size="12" /></button></li>
          <li v-for="file in attached" :key="file.material_id"><Icon name="check" :size="13" /><span>{{ file.title }}</span></li>
        </ul>
        <div class="input-surface">
          <label class="sr-only" for="ppt-message">制作需求或修改意见</label>
          <textarea id="ppt-message" v-model="draft" rows="3" maxlength="12000" :disabled="busy || loading" :placeholder="task ? '继续描述你想怎么做、怎么改…' : '描述你的主题、受众、页数或汇报时长…'" @keydown="onComposerKey" />
          <div class="composer-controls">
            <input ref="fileInput" type="file" class="sr-only" tabindex="-1" multiple accept=".txt,.md,.docx,.pdf,.csv,.xlsx,.png,.jpg,.jpeg,.webp" @change="pickFiles" />
            <button type="button" class="icon-button" :disabled="busy || loading" aria-label="添加参考资料或配图" @click="fileInput?.click()"><Icon name="upload" :size="19" /></button>
            <button v-if="task?.background_running && (task.can_pause || task.pause_requested)" type="button" class="send-button pause-button" :disabled="pauseInProgress" :aria-label="pauseInProgress ? '正在暂停制作' : '暂停制作'" @click="pauseCreation"><Icon name="stop" :size="19" /></button>
            <button v-else class="send-button" :disabled="busy || loading || (!!props.taskId && !task) || (!draft.trim() && !pendingFiles.length && !attached.length)" aria-label="发送制作需求"><Icon name="send" :size="20" /></button>
          </div>
        </div>
        <p class="composer-help">{{ pauseInProgress ? '正在暂停当前任务，完成后可继续制作。' : task?.background_running ? '正在后台制作，可切换页面，回来后查看进度。' : '可上传资料，也可以直接描述需求 · Shift + Enter 换行' }}</p>
      </form>
    </aside>

    <div class="panel-separator chat-divider" role="separator" tabindex="0" aria-label="调整对话区宽度，左右方向键微调" aria-orientation="vertical" aria-controls="ppt-creation-chat" :aria-valuenow="widths.chat" :aria-valuemin="panelBounds('chat').min" :aria-valuemax="panelBounds('chat').max" @pointerdown="startResize($event, 'chat')" @keydown="resizeKey($event, 'chat')" />

    <section class="presentation-editor" aria-label="演示页面与大纲">
      <header class="editor-header">
        <div class="work-title"><h1 :title="title">{{ title }}</h1><span class="save-status"><Icon :name="busy ? 'refresh' : 'check'" :size="14" />{{ pauseInProgress ? '暂停中' : busy ? '制作中' : task?.stage === 'paused' ? '已暂停' : task ? '已保存' : '尚未创建' }}</span><span v-if="task?.revision" class="version-label">第 {{ task.revision }} 版</span></div>
        <div class="header-actions"><button class="quiet-button" :disabled="loading || (!task && !props.taskId)" aria-label="刷新制作进度" @click="refresh"><Icon name="refresh" :size="16" /></button><button v-if="canDownload" class="primary-action download-action" :disabled="busy || downloading || !!task?.stale_slide_ids?.length" @click="download"><svg class="ic" width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" /></svg>{{ downloading ? '正在准备…' : task?.stage === 'draft' ? '确认并下载 PPTX' : '下载 PPTX' }}</button></div>
      </header>
      <div class="editor-toolbar"><div class="canvas-tabs" role="tablist" aria-label="查看内容"><button role="tab" :aria-selected="view === 'page'" @click="view = 'page'">页面</button><button role="tab" :aria-selected="view === 'outline'" @click="view = 'outline'">大纲</button></div><div v-if="view === 'page' && livePages.length" class="page-version-switch" aria-label="页面版本"><button v-if="savedSlides.length" :aria-pressed="!showingLive" @click="pageVersion = 'saved'">已保存页面</button><button :aria-pressed="showingLive" @click="pageVersion = 'live'">制作中预览</button></div><button v-if="view === 'page' && currentSlide" class="quiet-button text-edit-toggle" :aria-pressed="editingTexts" :disabled="showingLive" :title="showingLive ? '制作中预览不能直接改字，请切换到已保存页面' : '编辑本页文字'" @click="toggleTextEditor"><Icon name="edit" :size="15" />{{ editingTexts ? '收起文字' : '编辑文字' }}</button><button class="quiet-button source-toggle" :aria-expanded="showSources" @click="showSources = !showSources"><Icon name="folder" :size="16" />资料<span v-if="task?.materials?.length"> {{ task.materials.length }}</span></button></div>
      <div ref="editorBodyElement" class="editor-body" :class="{ 'sources-overlay': sourcesOverlay }">
        <nav v-if="view === 'page'" id="ppt-page-rail" class="page-rail" aria-label="演示文稿页面">
          <p v-if="canReorder" class="rail-hint">拖动缩略图可调整页序</p>
          <button v-for="(slide, index) in slides" :key="slide.slide_id || index" :class="{ selected: canvasIndex === index, dragging: dragIndex === index }" :aria-current="canvasIndex === index ? 'page' : undefined" :aria-label="`查看第${index + 1}页：${slide.title}`" :draggable="canReorder" @click="showPage(index)" @dragstart="onDragStart(index)" @dragover.prevent @drop.prevent="onDrop(index)" @dragend="dragIndex = null">
            <span class="rail-number">{{ index + 1 }}</span><div class="thumbnail-image"><img v-if="slidePreview(slide.slide_id)" :src="slidePreview(slide.slide_id)" alt="" /><span v-else><Icon name="presentation" :size="23" />预览待生成</span></div><span class="thumbnail-title">{{ String(index + 1).padStart(2, '0') }} · {{ slide.title }}</span>
          </button>
          <p v-if="!slides.length" class="rail-empty">页面生成后<br />会显示在这里</p>
        </nav>
        <div v-if="view === 'page'" class="panel-separator rail-divider" role="separator" tabindex="0" aria-label="调整页面缩略栏宽度，左右方向键微调" aria-orientation="vertical" aria-controls="ppt-page-rail" :aria-valuenow="widths.rail" :aria-valuemin="panelBounds('rail').min" :aria-valuemax="panelBounds('rail').max" @pointerdown="startResize($event, 'rail')" @keydown="resizeKey($event, 'rail')" />
        <div class="canvas-column" :class="{ 'outline-mode': view === 'outline' }">
          <template v-if="view === 'page'">
            <div class="canvas-stage">
              <p class="saved-version-note">{{ showingLive ? '制作中预览 · 来自实际写出的页面，尚未发布为成品' : busy && currentSlide ? '正在制作，当前显示已保存版本' : '' }}</p>
              <img v-if="currentSlide && slidePreview(currentSlide.slide_id)" :src="slidePreview(currentSlide.slide_id)" :alt="`${showingLive ? '制作中预览 · ' : ''}第${canvasIndex + 1}页：${currentSlide.title}`" class="actual-slide" />
              <div v-else class="canvas-empty"><Icon name="presentation" :size="44" /><h2>{{ currentSlide ? '页面预览尚未就绪' : '你的演示，从这里展开' }}</h2><p>{{ currentSlide ? '下方显示已保存的文字内容。实际页面预览未提供或读取失败时，可刷新制作进度后再查看。' : '在左侧说说你想做什么。讨论大纲、补充资料，再把想法变成实际页面。' }}</p><article v-if="currentSlide" class="page-content-fallback"><h3>{{ currentSlide.title }}</h3><p v-if="currentSlide.subtitle || currentSlide.purpose">{{ currentSlide.subtitle || currentSlide.purpose }}</p><ul v-if="currentSlide.items?.length"><li v-for="(item, index) in currentSlide.items" :key="index">{{ item }}</li></ul><ul v-if="currentSlide.claims?.length"><li v-for="(claim, index) in currentSlide.claims" :key="index">{{ claim.text }}</li></ul><p v-if="currentSlide.chart">{{ currentSlide.chart.categories.map((category, index) => `${category}：${currentSlide!.chart!.values[index]}${currentSlide!.chart!.unit || ''}`).join('；') }}</p><table v-if="currentSlide.table"><thead><tr><th v-for="column in currentSlide.table.columns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row, rowIndex) in currentSlide.table.rows" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td></tr></tbody></table></article></div>
            </div>
            <div v-if="slides.length" class="page-navigation"><button :disabled="canvasIndex <= 0" aria-label="上一页" @click="stepPage(-1)"><Icon name="arrowLeft" :size="16" /></button><span>第 {{ canvasIndex + 1 }} 页 / 共 {{ slides.length }} 页</span><button :disabled="canvasIndex >= slides.length - 1" aria-label="下一页" @click="stepPage(1)"><Icon name="chevronRight" :size="17" /></button></div>
            <p v-if="previewError" class="preview-error" role="alert">{{ previewError }}</p>
            <section v-if="editingTexts && currentSlide" class="text-editor" aria-label="编辑本页文字">
              <header><div><h3>本页文字</h3><p>直接改文字并重新导出，不改版式；写得太长可能超出原来的位置。</p></div><button class="quiet-button" @click="editingTexts = false">关闭</button></header>
              <p v-if="textLoading" class="panel-empty">正在读取本页文字…</p>
              <template v-else>
                <label v-for="unit in textUnits" :key="unit.id" class="text-unit">
                  <span>{{ unit.size || '—' }}{{ unit.weight ? ' · ' + unit.weight : '' }}</span>
                  <textarea v-model="textDraft[unit.id]" rows="2" maxlength="400" />
                </label>
                <p v-if="!textUnits.length" class="panel-empty">这一页没有可直接编辑的文字。</p>
                <div class="text-editor-actions"><button class="quiet-button" :disabled="savingTexts" @click="editingTexts = false">取消</button><button class="primary-action" :disabled="savingTexts || !textChanged" @click="saveTexts">{{ savingTexts ? '正在保存并重新导出…' : '保存并重新导出' }}</button></div>
              </template>
            </section>
            <details v-if="currentSlide && !showingLive" class="speaker-notes"><summary><Icon name="file" :size="15" />讲稿与备注</summary><div class="notes-body"><p v-if="currentSlide.speaker_notes">{{ currentSlide.speaker_notes }}</p><template v-else-if="currentSlide.notes?.length"><p v-for="(note, index) in currentSlide.notes" :key="index">{{ note.text }}</p></template><p v-else>当前页尚未提供讲稿或备注。可以在对话中提出需要补充的内容。</p></div></details>
          </template>
          <section v-else class="outline-pane" aria-label="演示文稿大纲"><div class="outline-heading"><div><h2>内容大纲</h2><p>{{ outlineEditing ? '编辑不会立即改动页面，发送后由助手继续制作。' : task?.live_outline ? '制作过程中实际写出的大纲，会随进度更新。' : '查看当前大纲，也可以编辑后交给助手调整。' }}</p></div><div class="outline-actions"><template v-if="outlineEditing"><button class="quiet-button" @click="cancelOutlineEdit">取消编辑</button><button class="primary-action" :disabled="busy || loading || !outlineDraft.trim()" @click="submitOutline">发送大纲修改</button></template><button v-else class="quiet-button" :disabled="loading" @click="editOutline"><Icon name="edit" :size="15" />编辑大纲</button></div></div><template v-if="outlineEditing"><p v-if="outlineChanged" class="outline-update-note">制作助手已更新大纲。你尚未发送的编辑已保留；取消编辑可查看最新大纲。</p><label class="sr-only" for="ppt-outline">编辑内容大纲</label><textarea id="ppt-outline" v-model="outlineDraft" :disabled="loading" rows="18" placeholder="可以先粘贴你已有的大纲，再发送给助手。" /></template><PptMarkdown v-else-if="outlineText" class="outline-reading" :content="outlineText" /><p v-else class="panel-empty">讨论后形成的大纲会显示在这里。也可以点击“编辑大纲”加入已有内容。</p></section>
        </div>
        <template v-if="showSources"><div class="panel-separator sources-divider" role="separator" tabindex="0" aria-label="调整资料栏宽度，左右方向键微调" aria-orientation="vertical" aria-controls="ppt-materials-panel" :aria-valuenow="widths.sources" :aria-valuemin="panelBounds('sources').min" :aria-valuemax="panelBounds('sources').max" @pointerdown="startResize($event, 'sources')" @keydown="resizeKey($event, 'sources')" />
        <aside id="ppt-materials-panel" class="materials-panel" aria-label="本次制作资料"><header><h2>本次制作资料</h2><button class="icon-button" aria-label="关闭资料面板" @click="showSources = false"><Icon name="close" :size="17" /></button></header><div class="materials-content"><template v-for="group in sourceGroups" :key="group.label"><h3 v-if="group.items.length">{{ group.label }}</h3><article v-for="source in group.items" :key="source.id" class="source-record"><a v-if="webUrl(source.url)" :href="webUrl(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title || '网页资料' }}<Icon name="chevronRight" :size="13" /></a><strong v-else>{{ source.title || '参考资料' }}</strong><p v-if="source.used === false" class="source-state">尚未关联页面</p><p v-if="source.published_at || source.date">发布日期：{{ source.published_at || source.date }}</p><p v-if="source.accessed_at">访问时间：{{ dateTime(source.accessed_at) }}</p><p v-if="source.content_scope">{{ scopeLabel(source.content_scope) }}</p><p v-if="source.reading_status">{{ readingLabel(source.reading_status) }}</p><p v-if="source.text" class="source-excerpt">{{ source.text }}</p></article></template><h3 v-if="task?.materials?.length">已加入任务的材料</h3><ul class="task-materials"><li v-for="material in task?.materials || []" :key="material.material_id"><Icon name="file" :size="16" /><div><a v-if="webUrl(material.final_url || material.url)" :href="webUrl(material.final_url || material.url)" target="_blank" rel="noopener noreferrer">{{ material.title }}</a><strong v-else>{{ material.title }}</strong><span>{{ material.source_type === 'user_image' ? '图片素材' : material.url || material.final_url ? '网页或公开文档' : '用户资料' }}</span><span v-if="material.published_at">发布日期：{{ material.published_at }}</span><span v-if="material.accessed_at">访问时间：{{ dateTime(material.accessed_at) }}</span><span v-if="material.content_scope">{{ scopeLabel(material.content_scope) }}</span><span v-if="material.reading_status">{{ readingLabel(material.reading_status) }}</span></div></li></ul><p v-if="!allSources.length && !task?.materials?.length" class="panel-empty">当前还没有资料。可以在对话中上传文件；制作中实际采用的网页与引用也会记录在这里。</p><p class="source-help">搜索摘要用于发现候选。具体表述应以实际读取并登记的资料为依据。</p></div></aside></template>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '../common/Icon.vue'
import PptMarkdown from './PptMarkdown.vue'
import { usePresentationStore } from '../../store/presentation'
import { usePptPreview } from '../../composables/usePptPreview'
import { usePptPanels } from '../../composables/usePptPanels'
import { confirmPresentation, continuePresentation, pausePresentation, createPresentationChat, getPresentation, getPresentationBlob,
  getPresentationDownload, getPageTexts, reorderPresentationPages, savePageTexts, selectPresentationSlide,
  sendPresentationMessage, uploadPresentationMaterials } from '../../api/presentation'
import type { PptChatAttachment, PptChatMessage, PptMessageRequest, PptPageText, PptSlide, PptTaskStatus, PptUpdatedPage } from '../../types/presentation'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ (e: 'created', id: string): void; (e: 'new'): void }>()
const ppt = usePresentationStore()
const task = computed(() => ppt.task), savedSlides = computed(() => task.value?.slides || [])
const livePages = computed(() => task.value?.live_pages || []), pageVersion = ref<'saved' | 'live'>('saved')
const showingLive = computed(() => !!livePages.value.length && (pageVersion.value === 'live' || !savedSlides.value.length))
const slides = computed<PptSlide[]>(() => showingLive.value ? livePages.value.map(page => ({ slide_id: page.slide_id, title: page.title, kind: 'live' })) : savedSlides.value)
const draft = ref(''), pendingFiles = ref<File[]>([]), attached = ref<PptChatAttachment[]>([])
const submitting = ref(false), loading = ref(false), downloading = ref(false), error = ref(''), operationLabel = ref('')
const requestingPause = ref(false)
const pauseInProgress = computed(() => requestingPause.value || !!(task.value?.background_running && task.value.pause_requested))
const busy = computed(() => submitting.value || !!task.value?.background_running || task.value?.stage === 'generating')
const canResume = computed(() => !busy.value && !!task.value?.can_continue)
const editingTexts = ref(false), textUnits = ref<PptPageText[]>([]), textDraft = ref<Record<string, string>>({})
const textLoading = ref(false), savingTexts = ref(false), dragIndex = ref<number | null>(null)
const canReorder = computed(() => !busy.value && !showingLive.value && slides.value.length > 1 && slides.value.every(slide => !!slide.slide_id))
const textChanged = computed(() => textUnits.value.some(unit => (textDraft.value[unit.id] ?? '').trim() !== unit.text.trim()))
const followChat = ref(true)
const fileInput = ref<HTMLInputElement | null>(null), chatScroll = ref<HTMLElement | null>(null)
const pendingMessage = ref<PptChatMessage | null>(null), selectedId = ref<string | null>(null), canvasId = ref<string | null>(null)
const canvasOrdinal = ref(0)
const mobilePane = ref<'chat' | 'pages'>('chat'), view = ref<'page' | 'outline'>('page'), showSources = ref(false), outlineDraft = ref('')
const outlineEditing = ref(false), outlineBase = ref('')
const outlineText = computed(() => task.value?.live_outline || task.value?.outline || '')
const outlineChanged = computed(() => outlineEditing.value && outlineText.value !== outlineBase.value)
const workspaceElement = ref<HTMLElement | null>(null), editorBodyElement = ref<HTMLElement | null>(null)
const { widths, compact, sourcesOverlay, style: panelStyle, bounds: panelBounds, start: startResize, keydown: resizeKey } = usePptPanels(workspaceElement, editorBodyElement, () => view.value === 'page', () => showSources.value)
const example = '帮我做一份关于 AI 辅助学习的课堂汇报，6 页，讲 5 分钟，结合实际案例，文字不要太多。'
const title = computed(() => task.value?.title || task.value?.brief?.goal || '新建演示文稿')
const selectedIndex = computed(() => savedSlides.value.findIndex(slide => slide.slide_id === selectedId.value))
const selectedSlide = computed(() => selectedIndex.value >= 0 ? savedSlides.value[selectedIndex.value] : null)
const canvasIndex = computed(() => { const index = slides.value.findIndex(slide => slide.slide_id === canvasId.value); return index >= 0 ? index : Math.max(0, Math.min(canvasOrdinal.value, slides.value.length - 1)) })
const currentSlide = computed(() => slides.value[canvasIndex.value])
const isDialogue = computed(() => task.value?.pending_user?.stage === 'dialogue')
const proposalText = computed(() => task.value?.pending_user?.proposal || task.value?.pending_user?.question || '')
const confirmationLabel = computed(() => task.value?.pending_user?.action_label || '')
const confirmationHint = computed(() => task.value?.pending_user?.action_hint || (isDialogue.value ? '在下方补充信息后，继续讨论。' : task.value?.pending_user?.action_message ? '可以在下方提出调整意见；点击按钮后才会确认当前方案。' : '请在下方继续讨论，让助手更新当前方案后再确认。'))
const canDownload = computed(() => !!(savedSlides.value.length || task.value?.pages) && ['draft', 'ready'].includes(task.value?.stage || ''))
const progressText = computed(() => pauseInProgress.value ? '正在暂停，等待当前步骤结束；已有内容会保留。' : operationLabel.value || task.value?.progress?.label || '正在整理你的需求。')
const failureText = computed(() => error.value || (!task.value?.background_running
  ? task.value?.background_error || (['paused', 'failed'].includes(task.value?.stage || '') && !task.value?.pause_requested ? task.value?.issues?.slice(-1)[0]?.detail || '' : '') : ''))
const messages = computed(() => {
  const stored = task.value?.chat_messages || []
  const pending = pendingMessage.value
  if (!pending || stored.some(message => message.role === 'user' && message.content === pending.content && message.created_at && message.created_at >= (pending.created_at || ''))) return stored
  return [...stored, pending]
})
const proposalNotInHistory = computed(() => {
  const proposal = proposalText.value
  return proposal && !(task.value?.chat_messages || []).some(message => message.role === 'assistant' && message.content === proposal)
})
const allSources = computed(() => Object.entries(task.value?.sources || {}).map(([id, source]) => ({ id, ...source })))
const sourceGroups = computed(() => [{ label: '已引用资料', items: allSources.value.filter(source => source.used !== false) }, { label: '已登记的参考来源', items: allSources.value.filter(source => source.used === false) }])
const { previewImages, livePreviewImages, previewError } = usePptPreview(() => task.value)
function slidePreview(id?: string) { return id ? (showingLive.value ? livePreviewImages.value : previewImages.value)[id] : undefined }
let mounted = true, viewRun = 0, selectionRun = 0, stateEpoch = 0, pollBusy = false, createdTaskId = ''
let pollTimer: ReturnType<typeof setInterval> | undefined

function apply(status: PptTaskStatus) {
  if (task.value?.taskId === status.task_id && (task.value.revision || 0) > status.revision) return
  stateEpoch++
  ppt.accept(status)
  const ids = new Set((status.slides || []).map(slide => slide.slide_id))
  if (selectedId.value && !ids.has(selectedId.value)) selectedId.value = null
  for (const page of status.live_pages || []) ids.add(page.slide_id)
  if (!status.live_pages?.length) pageVersion.value = 'saved'
  if (canvasId.value && !ids.has(canvasId.value)) canvasId.value = null
  const lastUser = [...(status.chat_messages || [])].reverse().find(message => message.role === 'user')
  if (pendingMessage.value && lastUser?.content === pendingMessage.value.content) pendingMessage.value = null
}
function trackChatScroll() {
  const element = chatScroll.value
  if (element) followChat.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
}
async function scrollChat(force = false) {
  if (force) followChat.value = true
  if (!followChat.value) return
  await nextTick()
  if (followChat.value && chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
}
watch(() => [task.value?.chat_messages?.length, task.value?.pending_user?.confirmation_id, pendingMessage.value?.id], () => { void scrollChat() })
watch(outlineText, outline => {
  if (outlineEditing.value && outlineDraft.value !== outlineBase.value) return
  outlineBase.value = outline; outlineDraft.value = outline
})
watch(() => props.taskId, async id => {
  if (id && id === createdTaskId && task.value?.taskId === id) return
  const run = ++viewRun
  requestingPause.value = false
  error.value = ''; draft.value = ''; pendingFiles.value = []; attached.value = []; pendingMessage.value = null
  submitting.value = false; ppt.busy = false; followChat.value = true; selectedId.value = null; canvasId.value = null; canvasOrdinal.value = 0; outlineDraft.value = ''; outlineBase.value = ''; outlineEditing.value = false; pageVersion.value = 'saved'
  editingTexts.value = false; textUnits.value = []; textDraft.value = {}; textLoading.value = false; savingTexts.value = false; dragIndex.value = null
  mobilePane.value = 'chat'; view.value = 'page'; showSources.value = false
  ppt.clearTask()
  if (!id) { loading.value = false; return }
  loading.value = true
  try {
    const status = await getPresentation(id)
    if (run !== viewRun || !mounted) return
    apply(status); selectedId.value = status.selected_slide_id || null; canvasId.value = selectedId.value
    await scrollChat(true)
  } catch (failure: any) { if (run === viewRun) error.value = failure.message || '任务读取未完成，请刷新重试。' }
  finally { if (run === viewRun) loading.value = false }
}, { immediate: true })

watch(() => [busy.value, task.value?.stage, task.value?.taskId], () => {
  ppt.busy = busy.value
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = undefined
  if (!task.value || (!busy.value && task.value.stage !== 'generating')) return
  pollTimer = setInterval(async () => {
    const id = task.value?.taskId, run = viewRun, epoch = stateEpoch
    if (!id || pollBusy) return
    pollBusy = true
    try {
      const status = await getPresentation(id)
      if (mounted && run === viewRun && epoch === stateEpoch && task.value?.taskId === id) {
        apply(status)
        if (!status.background_running) void ppt.loadList()
      }
    }
    catch (failure: any) { if (run === viewRun) error.value = failure.message || '进度读取未完成，可稍后刷新。' }
    finally { pollBusy = false }
  }, 3000)
})
onBeforeUnmount(() => { mounted = false; viewRun++; if (pollTimer) clearInterval(pollTimer); ppt.busy = false })

async function finish(status: PptTaskStatus, run: number) {
  if (!mounted || run !== viewRun) return
  apply(status)
  if (!status.background_running) pendingMessage.value = null
  void ppt.loadList()
  await scrollChat()
}
async function operate(operation: (run: number) => Promise<void>) {
  if (busy.value || loading.value) return
  const run = viewRun
  submitting.value = true; ppt.busy = true; error.value = ''
  try { await operation(run) }
  catch (failure: any) {
    if (run === viewRun && mounted) {
      error.value = failure.message || '本轮制作未完成，可核对已保存进度后继续。'
      pendingMessage.value = null
      if (task.value) try { const status = await getPresentation(task.value.taskId); if (run === viewRun) apply(status) } catch {}
    }
  } finally { if (run === viewRun && mounted) { submitting.value = false; operationLabel.value = '' } }
}
async function send(approval = false) {
  if (busy.value || loading.value || (props.taskId && !task.value)) return
  if (!approval && !draft.value.trim() && !pendingFiles.value.length && !attached.value.length) return
  const content = approval ? task.value?.pending_user?.action_message || '' : draft.value.trim() || '请根据我刚上传的资料，先帮我整理适合汇报的大纲。'
  if (!content) return
  const messageSelection = selectedId.value
  pendingMessage.value = { id: `pending-${Date.now()}`, role: 'user', content, created_at: new Date().toISOString(), attachments: approval ? [] : [...attached.value, ...pendingFiles.value.map((file, i) => ({ material_id: `pending-${i}`, title: file.name }))] }
  void scrollChat(true)
  await operate(async run => {
    if (!task.value) {
      operationLabel.value = '正在建立制作工作区。'
      const created = await createPresentationChat()
      if (run !== viewRun || !mounted) return
      createdTaskId = created.task_id; apply(created); emit('created', created.task_id)
    }
    const id = task.value!.taskId
    if (!approval && pendingFiles.value.length) {
      operationLabel.value = '正在上传并整理参考资料。'
      const before = new Set((task.value?.materials || []).map(material => material.material_id))
      const uploaded = await uploadPresentationMaterials(id, pendingFiles.value)
      if (run !== viewRun || !mounted) return
      attached.value.push(...uploaded.materials.filter(material => !before.has(material.material_id)).map(material => ({ material_id: material.material_id, title: material.title })))
      pendingFiles.value = []; if (fileInput.value) fileInput.value.value = ''; apply(uploaded)
    }
    const body: PptMessageRequest = { message: content, expected_revision: task.value?.revision || 0, selected_slide_id: messageSelection, attachments: approval ? [] : [...attached.value] }
    if (task.value?.stage === 'await_design' && task.value.pending_user?.confirmation_id) { body.confirmation_id = task.value.pending_user.confirmation_id; if (approval) body.approved = true }
    operationLabel.value = ''
    const status = await sendPresentationMessage(id, body)
    if (run !== viewRun || !mounted) return
    if (!approval) { draft.value = ''; attached.value = [] }
    await finish(status, run)
  })
}
async function approve() {
  if (task.value?.pending_user?.confirmation_id) await send(true)
}
async function resume() {
  if (!task.value || !canResume.value) return
  const current = task.value
  await operate(async run => { await finish(await continuePresentation(current.taskId, current.revision || 0), run) })
}
async function pauseCreation() {
  const current = task.value, run = viewRun
  if (!current?.can_pause || pauseInProgress.value) return
  stateEpoch++
  requestingPause.value = true; error.value = ''
  try { await finish(await pausePresentation(current.taskId, current.revision || 0), run) }
  catch (failure: any) { if (run === viewRun && mounted) error.value = failure.message || '暂停请求未完成，请重试。' }
  finally { if (run === viewRun && mounted) requestingPause.value = false }
}
async function refresh() {
  if (loading.value) return
  const id = task.value?.taskId || props.taskId, run = viewRun, epoch = stateEpoch, initial = !task.value
  if (!id) return
  try { const status = await getPresentation(id); if (run === viewRun && epoch === stateEpoch) { apply(status); error.value = ''; if (initial) { selectedId.value = status.selected_slide_id || null; canvasId.value = selectedId.value } } }
  catch (failure: any) { if (run === viewRun) error.value = failure.message || '刷新未完成。' }
}
async function choosePage(id: string | null) {
  selectedId.value = id; if (id) canvasId.value = id
  if (!task.value) return
  const run = ++selectionRun, taskId = task.value.taskId, pageRun = viewRun, epoch = stateEpoch
  try {
    const status = await selectPresentationSlide(taskId, id)
    if (run === selectionRun && pageRun === viewRun && epoch === stateEpoch && mounted) { if (status.revision === task.value?.revision) apply(status) }
  } catch (failure: any) { if (run === selectionRun && pageRun === viewRun) error.value = failure.message || '页面上下文尚未保存，请重试选择。' }
}
function showPage(index: number) { const slide = slides.value[index]; if (slide) { canvasOrdinal.value = index; canvasId.value = slide.slide_id || null; if (!showingLive.value) void choosePage(slide.slide_id || null) } }
function stepPage(direction: number) { showPage(canvasIndex.value + direction) }
function openOutline() { view.value = 'outline'; mobilePane.value = 'pages' }
function editOutline() { outlineBase.value = outlineText.value; outlineDraft.value = outlineText.value; outlineEditing.value = true }
function cancelOutlineEdit() { outlineEditing.value = false; outlineBase.value = outlineText.value; outlineDraft.value = outlineText.value }
async function submitOutline() {
  if (!outlineDraft.value.trim()) return
  draft.value = '请按以下大纲调整整份演示：\n' + outlineDraft.value
  await choosePage(null); mobilePane.value = 'chat'; await send()
  if (!draft.value) cancelOutlineEdit()
}
function pickFiles(event: Event) { pendingFiles.value.push(...Array.from((event.target as HTMLInputElement).files || [])); (event.target as HTMLInputElement).value = '' }
function onComposerKey(event: KeyboardEvent) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!busy.value) void send() } }
function updatedPageKey(value: PptUpdatedPage | string | number) { return typeof value === 'object' ? value.slide_id || (value.page_number ? String(value.page_number) : value.title || 'updated') : String(value) }
function updatedIndex(value: PptUpdatedPage | string | number) {
  if (typeof value === 'object' && value.removed) return -1
  const index = typeof value === 'object'
    ? value.slide_id ? savedSlides.value.findIndex(slide => slide.slide_id === value.slide_id) : (value.page_number || 0) - 1
    : typeof value === 'number' ? value - 1 : savedSlides.value.findIndex(slide => slide.slide_id === value)
  return index >= 0 && index < savedSlides.value.length ? index : -1
}
function updatedPageLabel(value: PptUpdatedPage | string | number) {
  if (typeof value === 'object' && value.removed) return value.page_number ? `原第 ${value.page_number} 页已移除` : '页面已移除'
  const index = updatedIndex(value)
  return index >= 0 ? `第 ${index + 1} 页已更新` : '页面已更新'
}
function openUpdatedPage(value: PptUpdatedPage | string | number) { const slide = savedSlides.value[updatedIndex(value)]; if (slide?.slide_id) { pageVersion.value = 'saved'; void choosePage(slide.slide_id); view.value = 'page'; mobilePane.value = 'pages' } }
function webUrl(value?: string) { try { const url = new URL(value || ''); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined } catch { return undefined } }
function dateTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN') }
function scopeLabel(scope: string) { return ({ webpage_text: '已读取公开网页正文', public_pdf_text: '已读取公开 PDF 正文', publication_page: '公开文献页面，不代表已读取论文全文', search_summary: '搜索摘要，仅用于发现候选资料', full_text: '已读取正文', abstract: '仅读取摘要', metadata: '仅取得题录信息', excerpt: '已读取页面节选' } as Record<string, string>)[scope] || '已登记资料，具体阅读范围以正文说明为准' }
function readingLabel(status: string) { return ({ read: '已读取', success: '已读取', partial: '仅读取部分内容', failed: '读取未完成', unavailable: '当前无法读取', not_read: '尚未读取正文' } as Record<string, string>)[status] || '读取情况待确认' }
async function toggleTextEditor() {
  if (editingTexts.value) { editingTexts.value = false; return }
  const slide = currentSlide.value, current = task.value
  if (!slide?.slide_id || !current || busy.value) return
  textLoading.value = true; error.value = ''
  try {
    const data = await getPageTexts(current.taskId, slide.slide_id)
    textUnits.value = data.texts
    textDraft.value = Object.fromEntries(data.texts.map(unit => [unit.id, unit.text]))
    editingTexts.value = true
  } catch (failure: any) {
    error.value = failure.message || '读取本页文字未完成，请稍后重试。'
  } finally { textLoading.value = false }
}
async function saveTexts() {
  const slide = currentSlide.value, current = task.value
  if (!slide?.slide_id || !current || savingTexts.value) return
  const edits = textUnits.value
    .filter(unit => (textDraft.value[unit.id] ?? '').trim() !== unit.text.trim())
    .map(unit => ({ id: unit.id, text: (textDraft.value[unit.id] || '').trim(), original: unit.text }))
  if (!edits.length) { editingTexts.value = false; return }
  savingTexts.value = true; error.value = ''
  try {
    apply(await savePageTexts(current.taskId, slide.slide_id, current.revision || 0, edits))
    editingTexts.value = false
    await refresh()
  } catch (failure: any) {
    error.value = failure.message || '保存文字未完成，页面已保持原样。'
  } finally { savingTexts.value = false }
}
function onDragStart(index: number) { if (canReorder.value) dragIndex.value = index }
async function onDrop(index: number) {
  const from = dragIndex.value; dragIndex.value = null
  const current = task.value
  if (from === null || from === index || !current) return
  const ids = slides.value.map(slide => slide.slide_id).filter(Boolean) as string[]
  if (ids.length !== slides.value.length) return
  const [moved] = ids.splice(from, 1)
  ids.splice(index, 0, moved)
  try {
    apply(await reorderPresentationPages(current.taskId, current.revision || 0, ids))
    await refresh()
  } catch (failure: any) {
    error.value = failure.message || '调整页序未完成，已保持原来的顺序。'
  }
}
async function download() {
  if (!task.value || downloading.value || busy.value) return
  downloading.value = true; error.value = ''
  try {
    const ready = task.value.stage === 'draft' ? await confirmPresentation(task.value.taskId, task.value.revision || 0) : await getPresentationDownload(task.value.taskId)
    const blob = await getPresentationBlob(ready.download_url), url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = ready.filename; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    await refresh()
  } catch (failure: any) { error.value = failure.message || '文件下载未完成，请重试。' }
  finally { downloading.value = false }
}
</script>

<style scoped>
.ppt-workspace { flex: 1; min-width: 0; min-height: 0; display: grid; grid-template-columns: var(--chat-width, 350px) 8px minmax(0, 1fr); background: var(--surface); }
.creation-chat { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.chat-header { display: flex; justify-content: space-between; align-items: center; min-height: 86px; padding: 20px 22px; border-bottom: 1px solid var(--border); }
.chat-header h2 { font-size: 19px; font-weight: 600; }.chat-header p { color: var(--text-muted); font-size: 12px; margin-top: 5px; }
.icon-button, .quiet-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-width: 40px; min-height: 40px; border-radius: 6px; color: var(--text-muted); }
.icon-button:hover, .quiet-button:hover { background: var(--surface-2); color: var(--text); }
.chat-history { flex: 1; min-height: 0; overflow-y: auto; padding: 28px 20px 20px; overscroll-behavior: contain; }
.chat-welcome { padding-top: 24px; }.chat-welcome > svg { color: var(--accent); margin-bottom: 18px; }.chat-welcome h3 { font-size: 19px; font-weight: 500; line-height: 1.5; }.chat-welcome p { margin-top: 10px; color: var(--text-muted); font-size: 14px; line-height: 1.8; }
.example-request { text-align: left; margin-top: 24px; padding: 16px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); color: var(--accent-ink); font-size: 14px; line-height: 1.8; }.example-request:hover { color: var(--accent); }
.creation-message { margin-bottom: 26px; font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; }.message-text { white-space: pre-wrap; }.creation-message.user .message-text { margin-left: 24px; padding: 12px 14px; border-radius: 10px 10px 2px 10px; background: var(--accent-soft); }.creation-message.assistant .message-text { padding: 0 2px; }
.message-attachments, .queued-files, .task-materials { list-style: none; padding: 0; }.message-attachments { display: grid; gap: 8px; margin-top: 10px; }.message-attachments li { display: flex; gap: 8px; align-items: center; padding: 9px 11px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }.message-attachments span { min-width: 0; overflow-wrap: anywhere; }
.sending-note { display: block; color: var(--text-faint); font-size: 12px; text-align: right; margin-top: 5px; }.updated-pages { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }.updated-pages button, .updated-pages > span { display: flex; align-items: center; min-height: 36px; gap: 6px; color: var(--accent-ink); font-size: 12px; border-bottom: 1px solid var(--accent-border); }
.outline-entry { width: 100%; display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px; text-align: left; font-size: 13px; margin: 0 0 24px; }.outline-entry span { flex: 1; }.outline-entry:hover { border-color: var(--accent-border); color: var(--accent-ink); }
.chat-confirmation { padding-top: 18px; margin-bottom: 20px; border-top: 1px solid var(--border); }.confirmation-proposal { font-size: 14px; line-height: 1.8; }.confirmation-hint { color: var(--text-muted); font-size: 13px; line-height: 1.7; margin: 12px 0; }
.primary-action { display: inline-flex; justify-content: center; align-items: center; gap: 8px; min-height: 42px; padding: 9px 16px; background: var(--accent); color: var(--surface); border-radius: 6px; font-size: 13px; font-weight: 500; }.primary-action:hover { background: var(--accent-hover); }
.work-progress { display: flex; align-items: flex-start; gap: 8px; color: var(--text-muted); font-size: 13px; line-height: 1.7; padding: 12px 0; }.work-progress svg { flex-shrink: 0; margin-top: 3px; }.work-error { font-size: 13px; line-height: 1.7; color: var(--danger); padding: 12px 0; }.error-help { color: var(--text-muted); margin-top: 6px; }.resume-action { display: flex; align-items: center; gap: 8px; color: var(--accent-ink); min-height: 44px; font-size: 13px; }.chat-notice { color: var(--text-muted); font-size: 13px; }
.creation-composer { flex-shrink: 0; padding: 12px 16px 14px; background: var(--surface); }.input-surface { border: 1px solid var(--border-strong); border-radius: 10px; overflow: hidden; }.input-surface:focus-within { border-color: var(--accent); }.input-surface textarea { display: block; width: 100%; min-height: 84px; max-height: 220px; padding: 13px 14px 6px; background: transparent; border: 0; outline: 0; resize: vertical; font: inherit; font-size: 14px; line-height: 1.7; color: var(--text); }.input-surface textarea::placeholder { color: var(--text-faint); }.composer-controls { display: flex; justify-content: space-between; align-items: center; padding: 2px 8px 8px; }.send-button { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 6px; background: var(--accent); color: var(--surface); }.send-button:hover { background: var(--accent-hover); }.composer-help { color: var(--text-faint); font-size: 11px; line-height: 1.7; margin-top: 7px; }
.discussion-context { display: inline-flex; align-items: center; gap: 8px; background: var(--surface-2); color: var(--text-muted); padding: 3px 8px; border-radius: 4px; font-size: 12px; margin-bottom: 8px; }.discussion-context button { min-width: 28px; min-height: 28px; display: grid; place-items: center; }.queued-files { display: grid; gap: 6px; margin-bottom: 8px; }.queued-files li { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text-muted); }.queued-files li span { flex: 1; min-width: 0; overflow-wrap: anywhere; }.queued-files button { min-width: 28px; min-height: 28px; }
.presentation-editor { min-width: 0; min-height: 0; display: flex; flex-direction: column; }.editor-header { min-height: 86px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 24px; border-bottom: 1px solid var(--border); }.work-title { min-width: 0; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }.work-title h1 { font-size: 19px; line-height: 1.5; font-weight: 600; max-width: 50ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.save-status, .version-label { color: var(--text-faint); font-size: 12px; white-space: nowrap; }.save-status { display: inline-flex; align-items: center; gap: 5px; }.header-actions { display: flex; gap: 8px; flex-shrink: 0; }.editor-toolbar { min-height: 52px; display: flex; align-items: center; justify-content: space-between; padding-right: 18px; border-bottom: 1px solid var(--border); }.canvas-tabs { display: flex; height: 52px; }.canvas-tabs button { min-width: 86px; padding: 12px 22px; font-size: 14px; color: var(--text-muted); border-bottom: 2px solid transparent; }.canvas-tabs button[aria-selected=true] { color: var(--accent-ink); border-bottom-color: var(--accent); font-weight: 600; }
.editor-body { flex: 1; min-height: 0; display: flex; position: relative; }.page-rail { width: 166px; flex-shrink: 0; overflow-y: auto; border-right: 1px solid var(--border); padding: 18px 12px; background: var(--surface); }.page-rail > button { position: relative; display: block; width: 100%; margin-bottom: 18px; text-align: left; }.rail-number { position: absolute; left: 0; top: 8px; color: var(--text-faint); font-size: 10px; }.thumbnail-image { margin-left: 15px; aspect-ratio: 16 / 9; border: 1px solid var(--border); border-radius: 4px; overflow: hidden; background: var(--bg); }.thumbnail-image img { display: block; width: 100%; height: 100%; object-fit: contain; }.thumbnail-image > span { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; height: 100%; font-size: 10px; color: var(--text-faint); }.selected .thumbnail-image { border-color: var(--accent); outline: 1px solid var(--accent); outline-offset: 1px; }.selected .rail-number { color: var(--accent); }.thumbnail-title { display: block; margin: 8px 0 0 15px; color: var(--text-muted); font-size: 11px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }.rail-empty { color: var(--text-faint); font-size: 12px; line-height: 1.8; text-align: center; padding-top: 32px; }
.canvas-column { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; padding: 28px 24px 20px; background: var(--bg); display: flex; flex-direction: column; }.canvas-stage { flex: 1; min-height: 220px; display: flex; flex-direction: column; align-items: center; justify-content: center; }.actual-slide { display: block; width: 100%; max-width: 1440px; height: auto; object-fit: contain; border: 1px solid var(--border); background: white; }.canvas-empty { max-width: 480px; text-align: center; padding: 48px 20px; }.canvas-empty > svg { color: var(--text-faint); margin-bottom: 22px; }.canvas-empty h2 { font-size: 20px; font-weight: 500; color: var(--text); }.canvas-empty p { color: var(--text-muted); font-size: 14px; line-height: 1.9; margin-top: 12px; }.canvas-empty .page-content-fallback { color: var(--text); }.saved-version-note { align-self: flex-start; margin-bottom: 12px; color: var(--text-muted); font-size: 12px; }.page-navigation { display: flex; justify-content: center; align-items: center; gap: 18px; padding: 12px 0; color: var(--text-muted); font-size: 12px; }.page-navigation button { width: 40px; height: 40px; display: grid; place-items: center; }.preview-error { color: var(--warn); font-size: 13px; line-height: 1.7; }
.speaker-notes { flex-shrink: 0; margin-top: 8px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }.speaker-notes summary { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 14px; color: var(--text-muted); font-size: 13px; }.notes-body { padding: 0 16px 16px; font-size: 14px; line-height: 1.8; white-space: pre-wrap; }.notes-body p + p { margin-top: 8px; }.outline-pane { flex: 1; min-height: 0; display: flex; flex-direction: column; background: var(--surface); padding: 24px; border: 1px solid var(--border); border-radius: 6px; }.outline-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 20px; }.outline-heading h2 { font-size: 18px; font-weight: 500; }.outline-heading p { font-size: 12px; color: var(--text-muted); margin-top: 7px; }.outline-pane textarea { flex: 1; min-height: 300px; width: 100%; resize: vertical; border: 0; outline: 0; color: var(--text); background: transparent; font: inherit; font-size: 14px; line-height: 1.9; }
.materials-panel { width: 300px; flex-shrink: 0; min-height: 0; overflow-y: auto; border-left: 1px solid var(--border); background: var(--surface); }.materials-panel header { display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; border-bottom: 1px solid var(--border); }.materials-panel h2 { font-size: 15px; font-weight: 500; }.materials-content { padding: 20px 16px; }.materials-content h3 { font-size: 13px; font-weight: 600; margin: 24px 0 12px; }.materials-content h3:first-child { margin-top: 0; }.source-record { padding: 14px 0; border-bottom: 1px solid var(--border); }.source-record a, .source-record strong { font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }.source-record a { color: var(--accent-ink); }.source-record strong { font-weight: 500; }.source-record p { font-size: 11px; line-height: 1.8; color: var(--text-muted); margin-top: 6px; }.source-record .source-excerpt { font-size: 12px; }.task-materials li { display: flex; align-items: flex-start; gap: 9px; margin-bottom: 16px; }.task-materials li svg { flex-shrink: 0; margin-top: 2px; }.task-materials strong { display: block; font-size: 13px; font-weight: 500; line-height: 1.6; overflow-wrap: anywhere; }.task-materials span { display: block; color: var(--text-faint); font-size: 11px; margin-top: 5px; }.panel-empty, .source-help { color: var(--text-muted); font-size: 12px; line-height: 1.8; }.source-help { padding-top: 20px; margin-top: 20px; border-top: 1px solid var(--border); }
.mobile-switch { display: none; }.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
button { transition: background 140ms, color 140ms, border-color 140ms; }button:disabled { opacity: .45; cursor: not-allowed; }button:focus-visible, summary:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }textarea { caret-color: var(--accent); }::selection { color: var(--text); background: var(--accent-soft); }
@media (min-width: 1600px) { .chat-history { padding-inline: 24px; } }
@media (max-width: 1200px) { .editor-header { padding-inline: 16px; }.work-title h1 { font-size: 17px; max-width: 30ch; }.save-status { display: none; }.page-rail { padding-inline: 8px; }.canvas-column { padding: 18px 16px; }.download-action { padding-inline: 12px; }.work-title { gap: 6px; } }
@media (max-width: 900px) { .ppt-workspace { display: flex; flex-direction: column; }.mobile-switch { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }.mobile-switch button { flex: 1; min-height: 44px; color: var(--text-muted); border-bottom: 2px solid transparent; font-size: 13px; }.mobile-switch button[aria-pressed=true] { color: var(--accent-ink); border-bottom-color: var(--accent); }.creation-chat, .presentation-editor { flex: 1; min-height: 0; }.creation-chat { border-right: 0; }.mobile-chat .presentation-editor, .mobile-pages .creation-chat { display: none; }.chat-header { min-height: 70px; padding: 12px 20px; }.chat-history { padding: 24px 20px; }.chat-welcome { max-width: 540px; margin: auto; }.creation-message { max-width: 600px; margin-inline: auto; }.creation-composer { padding: 12px 20px; }.editor-header { min-height: 72px; }.work-title h1 { max-width: 38ch; }.outline-heading { align-items: flex-start; flex-direction: column; }.download-action { font-size: 12px; } }
@media (max-width: 550px) { .editor-header { gap: 8px; padding: 12px; }.work-title h1 { font-size: 15px; max-width: 19ch; }.version-label { font-size: 10px; }.header-actions { gap: 2px; }.download-action { min-height: 36px; padding: 7px 9px; font-size: 11px; }.download-action svg { display: none; }.page-rail { width: 92px; padding: 14px 6px; }.thumbnail-image { margin-left: 10px; }.rail-number { font-size: 9px; }.thumbnail-title { margin-left: 10px; font-size: 10px; }.thumbnail-image > span { font-size: 8px; }.thumbnail-image > span svg { width: 17px; }.canvas-column { padding: 16px 10px 12px; }.canvas-stage { min-height: 180px; }.canvas-empty { padding-inline: 8px; }.canvas-empty h2 { font-size: 16px; }.canvas-empty p { font-size: 12px; }.page-navigation { gap: 4px; font-size: 10px; }.outline-pane { padding: 16px; }.canvas-tabs button { min-width: 72px; }.composer-help { font-size: 10px; } }
.page-content-fallback { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--border); text-align: left; overflow-x: auto; }
.page-content-fallback h3 { font-size: 17px; font-weight: 500; }
.page-content-fallback ul { padding-left: 20px; font-size: 13px; line-height: 1.8; }
.page-content-fallback table { margin-top: 12px; border-collapse: collapse; width: 100%; font-size: 12px; }
.page-content-fallback th, .page-content-fallback td { padding: 8px; border-bottom: 1px solid var(--border); }
.resume-hint { color: var(--text-muted); font-size: 12px; line-height: 1.8; margin-top: 12px; }
.page-rail { width: var(--rail-width, 166px); border-right: 0; }
.materials-panel { width: var(--sources-width, 300px); border-left: 0; }
.sources-overlay .materials-panel { position: absolute; inset: 0 0 0 auto; width: min(var(--sources-width, 300px), calc(100% - 32px)); z-index: 3; border-left: 1px solid var(--border); box-shadow: var(--shadow-md); }
.panel-separator { position: relative; flex: 0 0 8px; width: 8px; cursor: col-resize; touch-action: none; outline-offset: -2px; }
.panel-separator::before { content: ''; position: absolute; inset: 0 auto 0 3px; width: 1px; background: var(--border); }
.panel-separator::after { content: ''; position: absolute; left: 2px; top: calc(50% - 18px); height: 36px; width: 3px; border-radius: 2px; background: var(--border-strong); }
.panel-separator:hover, .panel-separator:focus-visible { background: var(--accent-soft); }.panel-separator:focus-visible { outline: 2px solid var(--accent); }.panel-separator:hover::after, .panel-separator:focus-visible::after { background: var(--accent); }
.sources-overlay .sources-divider { position: absolute; inset: 0 min(var(--sources-width, 300px), calc(100% - 32px)) 0 auto; z-index: 4; }
.assistant-content { padding: 0 2px; }.page-version-switch { display: flex; flex-wrap: wrap; gap: 4px; padding: 5px 0; margin-left: 8px; }.page-version-switch button { min-height: 32px; padding: 5px 9px; border-radius: 4px; color: var(--text-muted); font-size: 11px; }.page-version-switch button:hover { background: var(--surface-2); }.page-version-switch button[aria-pressed=true] { color: var(--accent-ink); background: var(--accent-soft); }.source-toggle { margin-left: auto; flex-shrink: 0; }
.outline-actions { display: flex; gap: 8px; flex-shrink: 0; }.outline-reading { max-width: 75ch; }.outline-update-note { font-size: 12px; color: var(--text-muted); line-height: 1.8; padding: 10px 12px; margin-bottom: 16px; background: var(--surface-2); }.task-materials a { color: var(--accent-ink); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }.source-record .source-state { color: var(--text-faint); }
.ppt-workspace.is-compact { display: flex; flex-direction: column; }.is-compact .panel-separator { display: none; }.is-compact .mobile-switch { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }.is-compact .mobile-switch button { flex: 1; min-height: 44px; color: var(--text-muted); border-bottom: 2px solid transparent; font-size: 13px; }.is-compact .mobile-switch button[aria-pressed=true] { color: var(--accent-ink); border-bottom-color: var(--accent); }.is-compact .creation-chat, .is-compact .presentation-editor { flex: 1; min-height: 0; }.is-compact.mobile-chat .presentation-editor, .is-compact.mobile-pages .creation-chat { display: none; }.is-compact .page-rail { width: clamp(92px, 19vw, 150px); border-right: 1px solid var(--border); }.is-compact .materials-panel { width: min(330px, 100%); }.is-compact .outline-heading { align-items: flex-start; flex-direction: column; }.is-compact .outline-actions { flex-wrap: wrap; }.is-compact .editor-toolbar { flex-wrap: wrap; }.is-compact .page-version-switch { order: 3; width: 100%; padding: 6px 12px; margin: 0; border-top: 1px solid var(--border); }.is-compact .page-version-switch button { min-height: 40px; }
@media (max-width: 900px) { .icon-button, .quiet-button, .send-button, .discussion-context button, .queued-files button, .page-navigation button { min-width: 44px; min-height: 44px; }.updated-pages button, .download-action { min-height: 44px; } }
</style>

<!-- 页面顺序与就地改字 -->
<style scoped>
.rail-hint { color: var(--text-faint); font-size: 10px; line-height: 1.6; margin-bottom: 10px; padding-left: 2px; }
.page-rail > button[draggable='true'] { cursor: grab; }
.page-rail > button.dragging { opacity: .45; }
.text-edit-toggle { margin-left: auto; flex-shrink: 0; }
.source-toggle { margin-left: 8px; }
.text-editor { flex-shrink: 0; margin-top: 10px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px 16px; }
.text-editor header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.text-editor h3 { font-size: 15px; font-weight: 500; }
.text-editor header p { color: var(--text-muted); font-size: 11px; line-height: 1.7; margin-top: 5px; }
.text-unit { display: grid; gap: 4px; margin-bottom: 10px; }
.text-unit > span { color: var(--text-faint); font-size: 10px; }
.text-unit textarea { width: 100%; resize: vertical; padding: 8px 10px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); color: var(--text); font: inherit; font-size: 13px; line-height: 1.6; }
.text-unit textarea:focus { outline: 0; border-color: var(--accent); }
.text-editor-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
/* 大纲视图与画布同底色，避免白卡片嵌在浅紫底上的突兀边界 */
.canvas-column.outline-mode { padding: 0; background: var(--surface); }
.canvas-column.outline-mode .outline-pane { flex: 1; min-height: 0; padding: 24px; overflow-y: auto; background: transparent; border: 0; border-radius: 0; }
/* 两种页面版本都占位，切换时下方的导航与备注不再上下跳动 */
.saved-version-note { min-height: 18px; }
/* 制作中时发送按钮切换为暂停 */
.send-button.pause-button { background: var(--warn); }
.send-button.pause-button:hover { filter: brightness(.94); }
</style>
