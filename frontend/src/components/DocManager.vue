<template>
  <div class="doc-manager">
    <datalist id="cat-list">
      <option v-for="c in categories" :key="c" :value="c" />
    </datalist>

    <div class="toolbar">
      <label class="btn btn-accent upload-btn">
        <Icon name="upload" :size="15" /> 上传文档
        <input
          type="file"
          ref="fileInput"
          accept=".txt,.md,.pdf,.docx,.html,.htm"
          multiple
          @change="onUpload"
          hidden
        />
      </label>
      <span class="hint">支持 .pdf / .docx / .md / .txt / .html，可一次选多份；解析后确认再入库</span>
      <div class="actions">
        <button class="btn btn-line" :disabled="syncing" @click="onSync">
          <Icon name="refresh" :size="14" /> {{ syncing ? '对账中…' : '同步文档列表' }}
        </button>
        <button class="btn btn-line" @click="load"><Icon name="refresh" :size="14" /> 刷新</button>
      </div>
    </div>

    <p v-if="msg" class="msg">{{ msg }}</p>

    <!-- ★ bug② 的落地：多选 → 队列 → 逐条可编辑 → 批量入库。
         原来 `files?.[0]` 只取第一份、input 也没有 multiple，所以一次只能传一个文件。 -->
    <section v-if="queue.length" class="panel queue">
      <div class="panel-head">
        <div>
          <h2>待入库 · {{ queue.length }} 份</h2>
          <p>解析完成后可逐条改标题与分类，也可以一起入库</p>
        </div>
        <span class="chip chip-accent">{{ parsingCount }} 份解析中</span>
      </div>

      <div v-for="(it, idx) in queue" :key="it.key" class="qrow">
        <span class="dot" :class="dotClass(it.status)"></span>
        <span class="qn" :title="it.filename">{{ it.title || it.filename }}</span>
        <span class="qs" :class="statusClass(it.status)">{{ statusText(it) }}</span>
        <span class="qacts">
          <button
            class="btn btn-quiet btn-sm"
            :disabled="it.status !== 'ready'"
            @click="editing = idx"
          >
            编辑
          </button>
          <button class="btn btn-quiet btn-sm" @click="removeItem(idx)">移除</button>
        </span>
      </div>

      <div class="queue-foot">
        <span class="qsum">{{ readyCount }} 份已就绪 · {{ parsingCount }} 份解析中</span>
        <div class="qfoot-acts">
          <button class="btn btn-line" @click="clearQueue">全部取消</button>
          <button class="btn btn-accent" :disabled="!readyCount || savingAll" @click="saveAll">
            {{ savingAll ? '入库中…' : `全部入库（${readyCount}）` }}
          </button>
        </div>
      </div>
    </section>

    <!-- 按分类分组 -->
    <div v-if="docs.length === 0" class="empty-state">
      <strong>知识库还是空的</strong>
      上传校规、制度或培养方案文件，即可作为问答依据
    </div>

    <div v-for="g in groups" :key="g.name" class="panel cat-group">
      <div class="cat-head" @click="toggle(g.name)">
        <span class="cat-fold">
          <Icon :name="isCollapsed(g.name) ? 'chevronRight' : 'chevronDown'" :size="13" />
        </span>
        <Icon name="folder" :size="15" class="cat-icon" />
        <span class="cat-name">{{ g.name || '未分类' }}</span>
        <span class="cat-count num">{{ g.items.length }} 篇</span>
        <button
          v-if="g.name"
          class="cat-rename"
          title="重命名分类"
          @click.stop="openRenameCategory(g.name)"
        >
          <Icon name="edit" :size="13" />
        </button>
      </div>
      <div v-if="!isCollapsed(g.name)" class="cat-body">
        <div v-for="d in g.items" :key="d.id" class="doc-row">
          <Icon name="file" :size="15" class="doc-ic" />
          <span class="doc-title" :title="d.title">{{ d.title }}</span>
          <span class="doc-meta">{{ d.source_type }} · <b class="num">{{ d.chunk_count }}</b> 块</span>
          <span class="doc-time num">{{ (d.uploaded_at || '').slice(0, 10) }}</span>
          <div class="row-actions">
            <button class="mini" @click="onPreview(d)">预览</button>
            <button class="mini" @click="metadataEditing = d">来源信息</button>
            <button class="mini" @click="openMove(d)">移动</button>
            <button class="mini danger" @click="confirmDelete(d)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <SourceMetadataEditor v-if="metadataEditing" :document="metadataEditing" @close="metadataEditing = null" @saved="metadataSaved" />

    <!-- 分类重命名弹窗 -->
    <div v-if="renamingCat" class="modal-mask" @click.self="cancelRenameCategory">
      <div class="modal">
        <div class="modal-title">重命名分类</div>
        <div class="modal-desc">将分类「{{ renamingCat.from }}」重命名为：</div>
        <input
          v-model="renamingCat.to"
          class="modal-input"
          placeholder="新分类名"
          @keydown.enter.prevent="confirmRenameCategory"
        />
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="cancelRenameCategory">取消</button>
          <button class="modal-btn ok" :disabled="!renamingCat.to.trim()" @click="confirmRenameCategory">
            确定
          </button>
        </div>
      </div>
    </div>

    <!-- 处理预览／编辑弹窗 -->
    <div v-if="editing !== null && queue[editing]" class="modal-mask" @click.self="editing = null">
      <div class="modal modal-editor">
        <div class="modal-title">处理预览 · {{ queue[editing].filename }}</div>
        <div class="editor-fields">
          <label class="editor-field">
            <span>标题</span>
            <input v-model="queue[editing].title" placeholder="文档标题" />
          </label>
          <label class="editor-field">
            <span>分类</span>
            <input v-model="queue[editing].category" list="cat-list" placeholder="未分类（可输入新分类）" />
          </label>
        </div>
        <div class="editor-preview-label">提取结果（Markdown，可编辑后入库）</div>
        <textarea v-model="queue[editing].markdown" class="editor-textarea" spellcheck="false"></textarea>
        <p v-if="queue[editing].message" class="hint" role="status">{{ queue[editing].message }}</p>
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="previewPending(editing)">预览实际切块</button>
          <button class="modal-btn cancel" @click="editing = null">取消</button>
          <button
            class="modal-btn ok"
            :disabled="queue[editing].status === 'saving'"
            @click="saveItem(editing)"
          >
            {{ queue[editing].status === 'saving' ? '入库中…' : '确认入库' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 移动分类弹窗 -->
    <div v-if="moving" class="modal-mask" @click.self="cancelMove">
      <div class="modal">
        <div class="modal-title">移动分类</div>
        <div class="modal-desc">将「{{ moving.doc.title }}」移动到：</div>
        <input
          v-model="moving.category"
          class="modal-input"
          list="cat-list"
          placeholder="未分类"
          @keydown.enter.prevent="confirmMove"
        />
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="cancelMove">取消</button>
          <button class="modal-btn ok" @click="confirmMove">确定</button>
        </div>
      </div>
    </div>

    <!-- 分块预览弹窗 -->
    <div v-if="previewing" class="modal-mask" @click.self="closePreview">
      <div class="modal modal-chunks">
        <div class="modal-title">分块预览 · {{ previewing.title }}</div>
        <div class="modal-hint">共 {{ chunks.length }} 个分块 · 显示检索及 LLM 使用的证据文本</div>
        <div class="chunks-list">
          <div v-for="(c, i) in chunks" :key="i" class="chunk">
            <span class="chunk-idx num">#{{ c.chunk_idx }}</span>
            <span class="chunk-text">{{ c.heading_path?.length ? c.heading_path.join(' / ') + '\n\n' : '' }}{{ c.text }}</span>
          </div>
          <div v-if="chunksLoading" class="chunks-loading">加载中…</div>
          <div v-else-if="chunks.length === 0" class="chunks-loading">无分块数据</div>
        </div>
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="closePreview">关闭</button>
        </div>
      </div>
    </div>

    <!-- 通用确认弹窗 -->
    <div v-if="confirmBox" class="modal-mask" @click.self="cancelConfirm">
      <div class="modal">
        <div class="modal-title">{{ confirmBox.title }}</div>
        <div class="modal-desc">{{ confirmBox.desc }}</div>
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="cancelConfirm">取消</button>
          <button class="modal-btn danger" :disabled="confirmBox.running" @click="runConfirm">
            {{ confirmBox.running ? '处理中…' : '确定' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import Icon from './Icon.vue'
import SourceMetadataEditor from './SourceMetadataEditor.vue'
import type { SourceMetadata } from '../types/chat'

interface Doc {
  id: number
  title: string
  source_type: string
  chunk_count: number
  status: string
  category: string
  uploaded_at?: string
  source_metadata?: SourceMetadata
}

type QStatus = 'parsing' | 'ready' | 'saving' | 'error'

interface QItem {
  key: number
  file: File
  filename: string
  source_type: string
  title: string
  category: string
  markdown: string
  status: QStatus
  message: string
}

interface ConfirmState {
  title: string
  desc: string
  running: boolean
  run: () => Promise<void>
}

const emit = defineEmits<{ (e: 'changed'): void }>()
const docs = ref<Doc[]>([])
const metadataEditing = ref<Doc | null>(null)
async function metadataSaved() { metadataEditing.value = null; await load() }
const msg = ref('')
const fileInput = ref<HTMLInputElement>()
const syncing = ref(false)

const queue = ref<QItem[]>([])
const savingAll = ref(false)
let keySeq = 0

const editing = ref<number | null>(null)
const moving = ref<{ doc: Doc; category: string } | null>(null)
const previewing = ref<Doc | null>(null)
const chunks = ref<Array<{ chunk_idx: number; parent_id?: string | number; heading_path?: string[]; text: string }>>([])
const chunksLoading = ref(false)
const confirmBox = ref<ConfirmState | null>(null)
const collapsed = ref<Record<string, boolean>>({})
const renamingCat = ref<{ from: string; to: string } | null>(null)

const categories = computed(() =>
  Array.from(new Set(docs.value.map((d) => d.category || '').filter(Boolean)))
)

const groups = computed(() => {
  const map = new Map<string, Doc[]>()
  for (const d of docs.value) {
    const cat = d.category || ''
    if (!map.has(cat)) map.set(cat, [])
    map.get(cat)!.push(d)
  }
  return [...map.entries()]
    .map(([name, items]) => ({ name, items }))
    .sort((a, b) => {
      if (a.name === '') return 1
      if (b.name === '') return -1
      return a.name.localeCompare(b.name, 'zh')
    })
})

const parsingCount = computed(() => queue.value.filter((q) => q.status === 'parsing').length)
const readyCount = computed(() => queue.value.filter((q) => q.status === 'ready').length)

function dotClass(s: QStatus) {
  return s === 'error' ? 'dot-bad' : s === 'ready' ? 'dot-ok' : 'dot-warn'
}
function statusClass(s: QStatus) {
  return s === 'error' ? 'bad' : s === 'parsing' || s === 'saving' ? 'run' : 'wait'
}
function statusText(it: QItem) {
  if (it.status === 'parsing') return '解析中…'
  if (it.status === 'saving') return '入库中…'
  if (it.status === 'error') return it.message || '失败'
  return '待入库'
}

function toggle(name: string) {
  collapsed.value = { ...collapsed.value, [name]: !collapsed.value[name] }
}

function isCollapsed(name: string) {
  return !!collapsed.value[name]
}

async function load() {
  const res = await api.get('/admin/documents')
  docs.value = res.data
}

/** bug③ 的入口：把灌库脚本写进 Qdrant、却没进 MySQL 的文档补出来。 */
async function onSync() {
  syncing.value = true
  msg.value = ''
  try {
    const res = await api.post('/admin/documents/sync')
    const d = res.data || {}
    const parts = [`补录 ${d.created ?? 0} 篇`, `修正块数 ${d.repaired ?? 0} 篇`]
    if (d.orphans?.length) parts.push(`${d.orphans.length} 篇在向量库已无对应向量（未自动删除）`)
    msg.value = `对账完成：${parts.join('，')}`
    await load()
    emit('changed')
  } catch (e: any) {
    msg.value = e.message || '同步失败'
  } finally {
    syncing.value = false
  }
}

// ★ 一次可以选多份；解析**串行**执行（解析 PDF 是 CPU 密集的，并发会把后端打满）
async function onUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length) return

  const start = queue.value.length
  queue.value.push(
    ...files.map((f) => ({
      key: ++keySeq,
      file: f,
      filename: f.name,
      source_type: '',
      title: f.name.replace(/\.[^.]+$/, ''),
      category: '',
      markdown: '',
      status: 'parsing' as QStatus,
      message: '',
    }))
  )

  for (let i = 0; i < files.length; i++) {
    const idx = start + i
    try {
      const form = new FormData()
      form.append('file', files[i])
      const res = await api.post('/admin/documents/parse', form)
      const it = queue.value[idx]
      if (!it) continue
      it.source_type = res.data.source_type || 'txt'
      it.markdown = res.data.markdown || ''
      it.title = String(res.data.filename || it.filename).replace(/\.[^.]+$/, '')
      it.message = (res.data.warnings || []).join('；')
      it.status = 'ready'
    } catch (err: any) {
      const it = queue.value[idx]
      if (it) {
        it.status = 'error'
        it.message = err.message || '解析失败'
      }
    }
  }
}

function removeItem(idx: number) {
  queue.value.splice(idx, 1)
  if (editing.value !== null) {
    if (editing.value === idx) editing.value = null
    else if (editing.value > idx) editing.value -= 1
  }
}

function clearQueue() {
  queue.value = []
  editing.value = null
}

async function saveOne(idx: number): Promise<boolean> {
  const it = queue.value[idx]
  if (!it) return false
  if (!it.title.trim() || !it.markdown.trim()) {
    it.status = 'error'
    it.message = '标题和内容不能为空'
    return false
  }
  it.status = 'saving'
  try {
    await api.post('/admin/documents', {
      title: it.title.trim(),
      source_filename: it.filename,
      source_type: it.source_type,
      category: it.category.trim(),
      content: it.markdown,
    })
    return true
  } catch (e: any) {
    it.status = 'error'
    it.message = e.message || '入库失败'
    return false
  }
}

async function previewPending(idx: number) {
  const it = queue.value[idx]
  if (!it) return
  chunks.value = []
  previewing.value = { id: 0, title: it.title, source_type: it.source_type,
    category: it.category, status: 'preview', chunk_count: 0 }
  chunksLoading.value = true
  try {
    const res = await api.post('/admin/documents/preview', {
      title: it.title, content: it.markdown, category: it.category, source_type: it.source_type,
    })
    chunks.value = res.data.chunks
  } catch (err: any) {
    previewing.value = null
    it.message = err.message || '切块预览失败，请检查表格结构'
  } finally {
    chunksLoading.value = false
  }
}

async function saveItem(idx: number) {
  const ok = await saveOne(idx)
  if (!ok) return
  const title = queue.value[idx]?.title || ''
  removeItem(idx)
  msg.value = `「${title}」已提交后台入库，请刷新查看处理结果`
  await load()
  emit('changed')
}

async function saveAll() {
  savingAll.value = true
  msg.value = ''
  let ok = 0
  const failed: string[] = []
  // 倒序走：成功即从队列里摘掉，不会影响还没处理的那些下标
  for (let i = queue.value.length - 1; i >= 0; i--) {
    if (queue.value[i].status !== 'ready') continue
    const title = queue.value[i].title
    if (await saveOne(i)) {
      removeItem(i)
      ok += 1
    } else {
      failed.push(title)
    }
  }
  msg.value = failed.length
    ? `已提交 ${ok} 篇后台入库；${failed.length} 篇提交失败：${failed.join('、')}`
    : `已提交 ${ok} 篇后台入库，请刷新查看处理结果`
  savingAll.value = false
  await load()
  emit('changed')
}

function openMove(d: Doc) {
  moving.value = { doc: d, category: d.category }
}

function cancelMove() {
  moving.value = null
}

async function confirmMove() {
  if (!moving.value) return
  try {
    await api.patch(`/admin/documents/${moving.value.doc.id}`, {
      category: moving.value.category.trim(),
    })
    moving.value = null
    await load()
  } catch (e: any) {
    msg.value = e.message || '移动失败'
  }
}

async function onPreview(d: Doc) {
  previewing.value = d
  chunks.value = []
  chunksLoading.value = true
  try {
    const res = await api.get(`/admin/documents/${d.id}/chunks`)
    chunks.value = res.data.chunks || []
  } catch (e: any) {
    msg.value = e.message || '读取分块失败'
  } finally {
    chunksLoading.value = false
  }
}

function closePreview() {
  previewing.value = null
}

function openRenameCategory(name: string) {
  renamingCat.value = { from: name, to: name }
}

function cancelRenameCategory() {
  renamingCat.value = null
}

async function confirmRenameCategory() {
  if (!renamingCat.value) return
  const from = renamingCat.value.from
  const to = renamingCat.value.to.trim()
  if (!to || to === from) {
    renamingCat.value = null
    return
  }
  try {
    await api.patch('/admin/categories', { old_name: from, new_name: to })
    msg.value = `分类「${from}」已重命名为「${to}」`
    renamingCat.value = null
    await load()
  } catch (e: any) {
    msg.value = e.message || '重命名失败'
  }
}

function confirmDelete(d: Doc) {
  confirmBox.value = {
    title: '删除文档',
    desc: `删除文档「${d.title}」？将同步删除其全部向量。`,
    running: false,
    run: async () => {
      try {
        await api.delete(`/admin/documents/${d.id}`)
        msg.value = '删除成功'
        await load()
        emit('changed')
      } catch (e: any) {
        msg.value = e.message || '删除失败'
      }
    },
  }
}

async function runConfirm() {
  if (!confirmBox.value || confirmBox.value.running) return
  confirmBox.value.running = true
  await confirmBox.value.run()
  confirmBox.value = null
}

function cancelConfirm() {
  if (confirmBox.value?.running) return
  confirmBox.value = null
}

onMounted(load)
</script>

<style scoped>
.doc-manager {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.upload-btn {
  cursor: pointer;
}
.hint {
  font-size: 13px;
  color: var(--text-muted);
  flex: 1;
  min-width: 200px;
}
.actions {
  display: flex;
  gap: 8px;
}

/* —— 通用按钮 —— */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 38px;
  padding: 0 15px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}
.btn-accent {
  background: var(--accent);
  color: #fff;
}
.btn-accent:hover:not(:disabled) {
  background: var(--accent-hover);
}
.btn-accent:disabled {
  background: var(--surface-2);
  color: var(--text-faint);
}
.btn-line {
  border: 1px solid var(--border-strong);
  color: var(--text);
}
.btn-line:hover:not(:disabled) {
  background: var(--surface-2);
}
.btn-line:disabled {
  color: var(--text-faint);
}
.btn-quiet {
  color: var(--text-muted);
}
.btn-quiet:hover:not(:disabled) {
  background: var(--surface-2);
  color: var(--text);
}
.btn-quiet:disabled {
  color: var(--text-faint);
  opacity: 0.55;
}
.btn-sm {
  height: 32px;
  padding: 0 12px;
  font-size: 13px;
}

.msg {
  color: var(--accent-ink);
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 13px;
}

.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
}
.panel-head {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.panel-head h2 {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.panel-head p {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 2px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-muted);
  background: var(--surface);
  white-space: nowrap;
}
.chip-accent {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent-ink);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-ok {
  background: var(--success);
}
.dot-warn {
  background: var(--warn);
}
.dot-bad {
  background: var(--danger);
}

/* —— 上传队列 —— */
.queue .panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.qrow {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 20px;
  border-bottom: 1px solid var(--border);
}
.qn {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}
.qs {
  flex-shrink: 0;
  font-size: 12px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qs.wait {
  color: var(--text-faint);
}
.qs.run {
  color: var(--accent-ink);
}
.qs.bad {
  color: var(--danger);
}
.qacts {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}
.queue-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 20px;
  background: var(--surface-2);
}
.qsum {
  font-size: 13px;
  color: var(--text-muted);
}
.qfoot-acts {
  display: flex;
  gap: 8px;
}

/* —— 空状态 —— */
.empty-state {
  padding: 44px 20px;
  text-align: center;
  color: var(--text-faint);
  font-size: 13px;
  line-height: 1.7;
  background: var(--surface);
  border: 1px dashed var(--border-strong);
  border-radius: 14px;
}
.empty-state strong {
  display: block;
  color: var(--text-muted);
  font-size: 15px;
  font-weight: 500;
  margin-bottom: 4px;
}

/* —— 分类分组 —— */
.cat-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 12px 16px;
  background: var(--surface-2);
  cursor: pointer;
  user-select: none;
  font-size: 14px;
}
.cat-fold {
  display: inline-flex;
  align-items: center;
  color: var(--text-muted);
  width: 14px;
}
.cat-icon {
  color: var(--text-faint);
}
.cat-name {
  font-weight: 500;
}
.cat-count {
  margin-left: auto;
  color: var(--text-faint);
  font-size: 12px;
  font-weight: 400;
}
.cat-rename {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  color: var(--text-muted);
  background: transparent;
}
.cat-rename:hover {
  background: var(--surface-3);
  color: var(--accent-ink);
}
.cat-body {
  background: var(--surface);
}
.doc-row {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  font-size: 14px;
}
.doc-ic {
  color: var(--text-faint);
}
.doc-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-meta {
  flex-shrink: 0;
  color: var(--text-faint);
  font-size: 12px;
}
.doc-meta b {
  font-weight: 600;
  color: var(--text-muted);
}
.doc-time {
  flex-shrink: 0;
  color: var(--text-faint);
  font-size: 12px;
}
.row-actions {
  flex-shrink: 0;
  display: flex;
  gap: 2px;
}
.mini {
  height: 32px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
}
.mini:hover {
  border-color: var(--border-strong);
  color: var(--text);
  background: var(--surface-2);
}
.mini.danger:hover {
  color: var(--danger);
  border-color: var(--danger);
  background: transparent;
}

/* —— 弹窗 —— */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(12, 14, 22, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal);
}
[data-theme='dark'] .modal-mask {
  background: rgba(0, 0, 0, 0.6);
}
.modal {
  width: 360px;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 22px;
  box-shadow: var(--shadow-lg);
}
.modal-chunks {
  width: 660px;
  max-width: 92vw;
  max-height: 82vh;
  display: flex;
  flex-direction: column;
}
.modal-editor {
  width: 780px;
  max-width: 94vw;
  height: 84vh;
  display: flex;
  flex-direction: column;
}
.modal-title {
  font-weight: 600;
  font-size: 15px;
  margin-bottom: 8px;
}
.modal-desc {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 14px;
  line-height: 1.7;
}
.modal-hint {
  font-size: 12px;
  color: var(--text-faint);
  margin-bottom: 10px;
}
.modal-input {
  width: 100%;
  height: 42px;
  padding: 0 13px;
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
  margin-bottom: 16px;
}
.modal-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.editor-fields {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.editor-field {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted);
}
.editor-field input {
  height: 42px;
  padding: 0 13px;
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
}
.editor-field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.editor-preview-label {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.editor-textarea {
  flex: 1;
  width: 100%;
  padding: 13px;
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-family: ui-monospace, 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  line-height: 1.7;
  resize: none;
  margin-bottom: 14px;
}
.editor-textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.chunks-list {
  overflow-y: auto;
  flex: 1;
  margin-bottom: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 120px;
}
.chunk {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 11px 13px;
  display: flex;
  gap: 11px;
}
.chunk-idx {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--accent-ink);
  font-size: 12px;
}
.chunk-text {
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.chunks-loading {
  color: var(--text-faint);
  text-align: center;
  padding: 20px;
  font-size: 13px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.modal-btn {
  height: 38px;
  padding: 0 16px;
  border-radius: 8px;
  font-size: 14px;
}
.modal-btn.cancel {
  background: var(--surface-2);
  color: var(--text);
}
.modal-btn.cancel:hover {
  background: var(--surface-3);
}
.modal-btn.ok {
  background: var(--accent);
  color: #fff;
}
.modal-btn.ok:hover:not(:disabled) {
  background: var(--accent-hover);
}
.modal-btn.ok:disabled {
  background: var(--surface-2);
  color: var(--text-faint);
}
.modal-btn.danger {
  background: var(--danger);
  color: #fff;
}
.modal-btn.danger:disabled {
  opacity: 0.6;
}
</style>
