<template>
  <aside class="sidebar">
    <div class="side-top">
      <div class="brand">
        <BrandLogo :size="32" />
        <div class="brand-text">
          <div class="brand-name">SAI</div>
          <div class="brand-sub">桂林电子科技大学</div>
        </div>
        <button class="mobile-close" aria-label="关闭侧边栏" @click="emit('close')"><Icon name="close" :size="18" /></button>
      </div>
      <nav class="primary-nav" aria-label="主要功能">
        <RouterLink to="/" :class="{ selected: !isPpt }" @click="emit('navigate')"><Icon name="chat" :size="18" />校园问答</RouterLink>
        <RouterLink to="/presentations" :class="{ selected: isPpt }" @click="emit('navigate')"><Icon name="presentation" :size="18" />PPT 制作</RouterLink>
      </nav>
      <button class="new-btn" :disabled="chat.streaming" @click="onNew">
        <Icon name="plus" :size="15" /> {{ isPpt ? '新建演示文稿' : '新对话' }}
      </button>
    </div>

    <div class="history-heading">
      <button :aria-expanded="!historyClosed" @click="historyClosed = !historyClosed"><Icon :name="historyClosed ? 'chevronRight' : 'chevronDown'" :size="14" />{{ isPpt ? '最近演示文稿' : '最近对话' }}</button>
      <span>{{ isPpt ? ppt.tasks.length : chat.conversations.length }}</span>
    </div>
    <div v-if="!historyClosed && isPpt" class="conv-list">
      <div v-for="task in ppt.tasks.slice(0, 5)" :key="task.task_id" class="recent-ppt-row ppt-action-row" :class="{ active: route.params.taskId === task.task_id }">
        <RouterLink :to="`/presentations/${task.task_id}`" class="recent-ppt" :class="{ active: route.params.taskId === task.task_id }" @click="emit('navigate')">
          <Icon name="presentation" :size="15" /><span>{{ task.title }}</span>
        </RouterLink>
        <PresentationActions :task="task" compact />
      </div>
      <p v-if="ppt.listLoading" class="no-conv">正在读取…</p>
      <p v-else-if="ppt.listError" class="no-conv">{{ ppt.listError }}</p>
      <p v-else-if="!ppt.tasks.length" class="no-conv">从材料开始，制作第一份演示文稿</p>
      <RouterLink class="history-more" to="/presentations" @click="emit('navigate')">查看全部演示文稿 <Icon name="chevronRight" :size="14" /></RouterLink>
    </div>
    <div v-else-if="!historyClosed" class="conv-list">
      <template v-for="g in groups" :key="g.key">
        <div class="group-label">
          <Icon v-if="g.pinned" name="pin" :size="11" />
          {{ g.pinned ? '置顶' : g.label }}
        </div>
        <div
          v-for="c in g.items"
          :key="c.id"
          :class="['conv-item', { active: c.id === chat.currentId }]"
          tabindex="0" role="button" @keydown.enter.self="select(c)" @keydown.space.self.prevent="select(c)"
          @click="select(c)"
        >
          <span class="conv-title" :title="c.title">{{ c.title }}</span>
          <span class="conv-actions">
            <button
              :class="['pin', { pinned: g.pinned }]"
              :title="g.pinned ? '取消置顶' : '置顶'"
              @click.stop="onPin(c)"
            >
              <Icon name="pin" :size="13" />
            </button>
            <button title="重命名" @click.stop="openRename(c)"><Icon name="edit" :size="13" /></button>
            <button title="删除" @click.stop="openRemove(c)"><Icon name="trash" :size="13" /></button>
          </span>
        </div>
      </template>

      <div v-if="chat.conversations.length === 0" class="no-conv">暂无会话，开始提问吧</div>
      <button v-if="chat.conversations.length > 5" class="history-more" @click="showAll = !showAll">{{ showAll ? '收起历史记录' : '查看全部对话' }}<Icon :name="showAll ? 'chevronDown' : 'chevronRight'" :size="14" /></button>
    </div>

    <div class="side-foot">
      <button v-if="auth.isAdmin" class="foot-btn" @click="router.push('/admin')">
        <Icon name="settings" :size="14" /> 管理后台
      </button>
      <div class="user-row">
        <span class="user-name">{{ auth.user?.name }}</span>
        <div class="side-actions">
          <ThemeToggle />
          <button class="logout" @click="onLogout">退出</button>
        </div>
      </div>
    </div>

    <!-- 重命名弹窗 -->
    <div v-if="renaming" class="modal-mask" @click.self="cancelRename">
      <div class="modal">
        <div class="modal-title">重命名会话</div>
        <input
          v-model="renameInput"
          class="modal-input"
          autofocus
          @keydown.enter.prevent="confirmRename"
          @keydown.esc="cancelRename"
        />
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="cancelRename">取消</button>
          <button class="modal-btn ok" @click="confirmRename" :disabled="!renameInput.trim()">确定</button>
        </div>
      </div>
    </div>

    <!-- 删除确认弹窗 -->
    <div v-if="removing" class="modal-mask" @click.self="cancelRemove">
      <div class="modal">
        <div class="modal-title">删除会话</div>
        <div class="modal-desc">确定要删除「{{ removing.title }}」吗？删除后不可恢复。</div>
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="cancelRemove">取消</button>
          <button class="modal-btn danger" @click="confirmRemove">删除</button>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../store/auth'
import { useChatStore } from '../store/chat'
import type { Conversation } from '../types/chat'
import ThemeToggle from './ThemeToggle.vue'
import BrandLogo from './BrandLogo.vue'
import Icon from './Icon.vue'
import { usePresentationStore } from '../store/presentation'
import PresentationActions from './PresentationActions.vue'

const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const ppt = usePresentationStore(), route = useRoute()
const emit = defineEmits<{ (e: 'navigate'): void; (e: 'close'): void }>()
const isPpt = computed(() => route.path.startsWith('/presentations'))
const historyClosed = ref(false), showAll = ref(false)

interface ConvGroup {
  key: string
  label: string
  pinned?: boolean
  items: Conversation[]
}

function dateLabel(updatedAt?: string): string {
  if (!updatedAt) return '更早'
  const d = new Date(updatedAt)
  if (isNaN(d.getTime())) return '更早'
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  if (startOfDay >= startOfToday) return '今天'
  if (startOfDay >= startOfToday - 86400000) return '昨天'
  return '更早'
}

// 后端已按 updated_at 倒序返回；置顶组置顶，其余按日期分组
const groups = computed<ConvGroup[]>(() => {
  const result: ConvGroup[] = []
  const ordered = [...chat.conversations].sort((a, b) => Number(chat.pinnedIds.includes(b.id)) - Number(chat.pinnedIds.includes(a.id)))
  const visible = showAll.value ? ordered : ordered.slice(0, 5)
  const pinned = visible.filter((c) => chat.pinnedIds.includes(c.id))
  if (pinned.length) result.push({ key: 'pinned', label: '置顶', pinned: true, items: pinned })

  const unpinned = visible.filter((c) => !chat.pinnedIds.includes(c.id))
  for (const c of unpinned) {
    const label = dateLabel(c.updated_at)
    const last = result[result.length - 1]
    if (last && !last.pinned && last.label === label) {
      last.items.push(c)
    } else {
      result.push({ key: label, label, items: [c] })
    }
  }
  return result
})

async function select(c: Conversation) {
  if (chat.streaming) return
  await chat.loadMessages(c.id)
  await router.push('/')
  emit('navigate')
}

function onNew() {
  if (chat.streaming) return
  if (isPpt.value) router.push('/presentations?new=1')
  else { chat.newConversation(); router.push('/') }
  emit('navigate')
}

function onPin(c: Conversation) {
  chat.togglePin(c.id)
}

const renaming = ref<Conversation | null>(null)
const renameInput = ref('')
const removing = ref<Conversation | null>(null)

function openRename(c: Conversation) {
  renaming.value = c
  renameInput.value = c.title
}

function cancelRename() {
  renaming.value = null
  renameInput.value = ''
}

async function confirmRename() {
  const title = renameInput.value.trim()
  if (!renaming.value || !title) return
  await chat.rename(renaming.value.id, title)
  cancelRename()
}

function openRemove(c: Conversation) {
  removing.value = c
}

function cancelRemove() {
  removing.value = null
}

async function confirmRemove() {
  if (!removing.value) return
  await chat.remove(removing.value.id)
  removing.value = null
}

async function onLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
/* 侧边栏与页面同底（--bg），靠一条细线分界 ——
   这样"面板"才真的只是面板，而不是满屏都是一层层的卡片。 */
.sidebar {
  width: 100%;
  flex-shrink: 0;
  background: var(--bg);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 16px 12px 12px;
}
.side-top {
  padding: 0 4px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 4px 16px;
}
.brand-name {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.2;
}
.brand-sub {
  font-size: 11px;
  color: var(--text-faint);
  letter-spacing: 0.02em;
  line-height: 1.4;
}
.new-btn {
  width: 100%;
  height: 40px;
  margin-bottom: 14px;
  background: var(--accent);
  color: #fff;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 500;
}
.new-btn:hover {
  background: var(--accent-hover);
}

.conv-list {
  flex: 0 1 auto;
  min-height: 0;
  overflow-y: auto;
  margin: 0 -4px;
  padding: 0 4px;
}
.group-label {
  padding: 14px 8px 6px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-faint);
  letter-spacing: 0.06em;
  display: flex;
  align-items: center;
  gap: 5px;
}
.conv-item {
  position: relative;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  margin-bottom: 1px;
  font-size: 14px;
  color: var(--text-muted);
}
.conv-item:hover {
  background: var(--surface-2);
  color: var(--text);
}
/* ★ 选中项用**淡色底板 + 强调色文字**，不用整块强调色填充，也不加左侧彩色竖条
   —— 后者（卡片/列表项一侧的彩色边）正是 impeccable 点名的头号 AI 痕迹 `side-tab`。 */
.conv-item.active {
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-weight: 500;
}
.conv-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* ★ 操作按钮**绝对定位浮在标题之上**，而不是挤占它的宽度。
   原来它是 flex 里的一项，`display:none` → 悬停变 flex 会**把标题挤窄**，
   于是鼠标一划过，标题就当着用户的面重新折行 —— 正是用户要读它的时候。
   浮起来之后标题宽度恒定，按钮有序地盖住尾部（与 Slack / VS Code 同一手法）。 */
.conv-actions {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  display: none;
  gap: 0;
  padding: 1px;
  border-radius: 7px;
  background: var(--surface-3);
}
.conv-item:hover .conv-actions,
.conv-item.active .conv-actions {
  display: flex;
}
.conv-item.active .conv-actions {
  background: var(--accent-border);
}
.conv-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  color: inherit;
  opacity: 0.6;
  background: transparent;
}
.conv-actions button:hover {
  background: var(--surface-3);
  opacity: 1;
}
.conv-item.active .conv-actions button:hover {
  background: var(--accent-border);
}
.conv-actions button.pin.pinned {
  color: var(--accent);
  opacity: 1;
}
.no-conv {
  color: var(--text-faint);
  text-align: center;
  padding: 20px;
  font-size: 13px;
}

.side-foot {
  padding-top: 12px;
  margin-top: auto;
  border-top: 1px solid var(--border);
}
.primary-nav { display: grid; gap: 4px; margin: 8px 0 24px; }
.primary-nav a { display: flex; gap: 12px; align-items: center; min-height: 46px; padding: 10px 12px; color: var(--text-muted); text-decoration: none; border-radius: 6px; font-size: 14px; }
.primary-nav a:hover { color: var(--text); background: var(--surface-2); }
.primary-nav a.selected { background: var(--accent-soft); color: var(--accent-ink); font-weight: 600; }
.history-heading { display: flex; justify-content: space-between; align-items: center; margin: 12px 8px 4px; color: var(--text-faint); font-size: 12px; }
.history-heading button { display: flex; align-items: center; gap: 4px; min-height: 44px; font-size: inherit; color: inherit; }
.history-more { min-height: 44px; width: 100%; padding: 8px 10px; color: var(--text-muted); font-size: 12px; text-decoration: none; display: flex; align-items: center; gap: 4px; }
.history-more:hover { color: var(--accent-ink); }
.recent-ppt { min-height: 44px; display: flex; gap: 8px; align-items: center; padding: 8px 10px; text-decoration: none; color: var(--text-muted); border-radius: 6px; font-size: 13px; }
.recent-ppt span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-ppt svg { flex-shrink: 0; }
.recent-ppt:hover { background: var(--surface-2); }
.recent-ppt.active { background: var(--accent-soft); color: var(--accent-ink); }
.recent-ppt-row { position: relative; }
.recent-ppt-row:hover .recent-ppt, .recent-ppt-row:focus-within .recent-ppt { background: var(--surface-2); }
.recent-ppt-row.active:hover .recent-ppt, .recent-ppt-row.active:focus-within .recent-ppt { background: var(--accent-soft); }
@media (hover: none), (pointer: coarse) { .recent-ppt { padding-right: 108px; } }
.mobile-close { display: none; }
.conv-item { min-height: 44px; }
.conv-item:focus-within .conv-actions { display: flex; }
@media(max-width: 820px) { .mobile-close { display: grid; place-items: center; width: 44px; height: 44px; margin-left: auto; flex-shrink: 0; } }
.foot-btn {
  width: 100%;
  height: 34px;
  margin-bottom: 10px;
  background: transparent;
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 13px;
}
.foot-btn:hover {
  background: var(--surface-2);
}
.user-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 0 4px;
}
.user-name {
  font-size: 13px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}
.logout {
  height: 34px;
  padding: 0 10px;
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 13px;
  background: transparent;
}
.logout:hover {
  background: var(--surface-2);
  color: var(--text);
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
  width: 340px;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 22px;
  box-shadow: var(--shadow-lg);
}
.modal-title {
  font-weight: 600;
  font-size: 15px;
  margin-bottom: 14px;
}
.modal-desc {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 16px;
  line-height: 1.7;
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
.modal-btn.ok:hover {
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
</style>
