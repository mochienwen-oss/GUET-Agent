<template>
  <div class="app-shell" :class="{ 'campus-expanded': showCampus && campus.expanded }" @keydown.esc="onEscape">
    <button v-if="!collapsed" class="nav-backdrop" aria-label="关闭导航" @click="collapsed = true" />
    <div id="app-sidebar" class="sidebar-wrap" :class="{ collapsed }">
      <Sidebar :profile-open="profileOpen" @navigate="closeMobile" @close="collapsed = true" @open-profile="openProfile" />
    </div>
    <main class="app-main">
      <header class="app-header">
        <button class="nav-toggle" :aria-expanded="!collapsed" aria-controls="app-sidebar"
          :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'" @click="collapsed = !collapsed"><Icon name="menu" :size="19" /></button>
        <span class="section-title">{{ isPpt ? 'PPT 制作' : route.path === '/profile' ? '个人资料' : '对话' }}</span>
        <span class="header-divider">/</span>
        <span class="page-title">{{ title }}</span>
        <span v-if="online !== null" class="link-state" :class="{ off: !online }">
          <i class="dot" />{{ online ? '服务已连接' : '服务未连接' }}
        </span>
      </header>
      <RouterView />
    </main>

    <!-- 手机端底栏：≤820px 时替代抽屉侧栏承担主导航，像原生 App 一样伸手可及 -->
    <nav class="mobile-nav" aria-label="底部导航">
      <RouterLink to="/" :class="{ on: route.path === '/' }"><Icon name="chat" :size="20" /><span>对话</span></RouterLink>
      <RouterLink to="/presentations" :class="{ on: route.path.startsWith('/presentations') }"><Icon name="presentation" :size="20" /><span>演示文稿</span></RouterLink>
      <RouterLink to="/profile" :class="{ on: route.path === '/profile' }"><Icon name="user" :size="20" /><span>我的</span></RouterLink>
    </nav>

    <!-- 校园服务：右侧内嵌学校网页；分隔线可拖动改变宽度 -->
    <template v-if="showCampus">
      <div v-if="!campus.expanded" class="split-handle" :class="{ dragging }" role="separator" aria-orientation="vertical"
        :aria-label="`调整聊天区与学校网页宽度，当前 ${campus.panelWidth} 像素`"
        @pointerdown="startDrag" />
      <CampusPanel />
    </template>
    <div v-if="profileOpen" class="profile-overlay" @click.self="closeProfile">
      <aside ref="profilePanel" class="profile-drawer" role="dialog" aria-modal="true" aria-label="个人资料" tabindex="-1" @keydown.tab="trapProfileFocus">
        <button ref="profileCloseButton" class="profile-close" type="button" aria-label="关闭个人资料" @click="closeProfile"><Icon name="close" :size="19" /></button>
        <Profile embedded @logout="closeProfile" />
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../../api'
import { useChatStore } from '../../store/chat'
import { useCampusStore } from '../../store/campus'
import { usePresentationStore } from '../../store/presentation'
import CampusPanel from './CampusPanel.vue'
import Sidebar from './Sidebar.vue'
import Icon from '../common/Icon.vue'
import Profile from '../../views/Profile.vue'
const route = useRoute(), chat = useChatStore(), ppt = usePresentationStore()
const campus = useCampusStore()
ppt.clearTask()
const collapsed = ref(window.matchMedia('(max-width: 820px)').matches)
const profileOpen = ref(false)
const profilePanel = ref<HTMLElement>()
const profileCloseButton = ref<HTMLButtonElement>()
let profileReturnFocus: HTMLElement | null = null
const isPpt = computed(() => route.path.startsWith('/presentations'))
const showCampus = computed(() => !isPpt.value && route.path === '/' && campus.active && campus.visible)
const title = computed(() => {
  if (route.path === '/profile') return '个人资料'
  return isPpt.value
    ? (route.params.taskId ? ppt.task?.brief?.goal || '制作工作区' : '我的演示文稿')
    : chat.conversations.find(c => c.id === chat.currentId)?.title || '新对话'
})
function closeMobile() { if (window.matchMedia('(max-width: 820px)').matches) collapsed.value = true }
async function openProfile() {
  if (route.path === '/profile') { closeMobile(); return }
  profileReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  closeMobile()
  profileOpen.value = true
  await nextTick()
  profileCloseButton.value?.focus()
}
function closeProfile() {
  profileOpen.value = false
  void nextTick(() => { if (profileReturnFocus?.isConnected) profileReturnFocus.focus() })
}
function onEscape() {
  if (profileOpen.value) closeProfile()
  else collapsed.value = true
}
function trapProfileFocus(event: KeyboardEvent) {
  const controls = Array.from(profilePanel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])') || [])
    .filter(element => element.offsetParent !== null)
  const first = controls[0], last = controls[controls.length - 1]
  if (!first || !last) return
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

// 顶部连接状态：只反映后端健康检查的真实结果，不猜
const online = ref<boolean | null>(null)
async function checkHealth() {
  try {
    await api.get('/health', { timeout: 8000 })
    online.value = true
  } catch {
    online.value = false
  }
}

// —— 分隔线拖拽：只改宽度，学校视图的位置由 CampusPanel 量出矩形后交给主进程 ——
const dragging = ref(false)
let dragStartX = 0
let dragStartWidth = 0
function startDrag(event: PointerEvent) {
  dragging.value = true
  dragStartX = event.clientX
  dragStartWidth = campus.panelWidth
  window.addEventListener('pointermove', moveDrag)
  window.addEventListener('pointerup', endDrag)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
function moveDrag(event: PointerEvent) {
  // 往左拖 → 学校网页变宽
  campus.setWidth(dragStartWidth + (dragStartX - event.clientX))
}
function endDrag() {
  dragging.value = false
  window.removeEventListener('pointermove', moveDrag)
  window.removeEventListener('pointerup', endDrag)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

function clampOnResize() {
  if (campus.visible) campus.setWidth(campus.panelWidth)
}
watch(collapsed, async () => { await nextTick(); clampOnResize() })

onMounted(() => {
  void chat.loadConversations()
  void ppt.loadList()
  void checkHealth()
  window.addEventListener('resize', clampOnResize)
})
onBeforeUnmount(() => {
  endDrag()
  window.removeEventListener('resize', clampOnResize)
})
</script>

<style scoped>
.app-shell { display: flex; height: 100dvh; overflow: hidden; }
.sidebar-wrap { display: flex; width: 264px; flex-shrink: 0; }
.sidebar-wrap.collapsed { display: none; }
.app-main { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
.campus-expanded .sidebar-wrap, .campus-expanded .app-main { display: none; }
.app-header { position: relative; height: 64px; flex-shrink: 0; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; padding: 0 24px; background: var(--surface); }
.nav-toggle { width: 44px; height: 44px; flex-shrink: 0; display: grid; place-items: center; border-radius: 6px; }
.nav-toggle:hover { background: var(--surface-2); }
.section-title { font-size: 14px; font-weight: 600; white-space: nowrap; }
.header-divider { color: var(--border-strong); }
.page-title { font-size: 13px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.link-state { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-muted); white-space: nowrap; }
.link-state .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--success); }
.link-state.off .dot { background: var(--danger); }
.nav-backdrop { display: none; }
.profile-overlay { position: fixed; inset: 0; z-index: 100; display: flex; justify-content: flex-end; background: rgb(15 23 42 / 24%); }
.profile-drawer { position: relative; width: min(480px, 100vw); height: 100dvh; display: flex; background: var(--surface); box-shadow: var(--shadow-lg); outline: none; }
.profile-close { position: absolute; z-index: 1; top: 14px; right: 14px; width: 44px; height: 44px; display: grid; place-items: center; border-radius: 10px; color: var(--text-muted); }
.profile-close:hover { background: var(--surface-2); color: var(--text); }
.profile-close:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* 分隔线：视觉只有 1px，热区 8px，拖动时高亮 */
.split-handle { position: relative; flex: none; width: 8px; margin: 0 -4px; z-index: 5; cursor: col-resize; }
.split-handle::after { content: ''; position: absolute; inset: 0 3px; background: transparent; transition: background var(--fast) var(--ease); }
.split-handle:hover::after, .split-handle.dragging::after { background: var(--accent); }

/* 手机端底栏：只在 ≤820px 出现。毛玻璃太容易廉价，用实底 + hairline；
   当前项用靛墨字 + 顶部 2px 短线标记，不做整块填充。 */
.mobile-nav { display: none; }
@media (max-width: 820px) {
  .mobile-nav {
    position: fixed;
    left: 0; right: 0; bottom: 0;
    z-index: 45;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    background: var(--surface);
    border-top: 1px solid var(--border);
    padding: 6px 10px calc(6px + env(safe-area-inset-bottom));
  }
  .mobile-nav a {
    display: grid;
    place-items: center;
    gap: 2px;
    padding: 4px 0 2px;
    border-radius: 8px;
    text-decoration: none;
    color: var(--text-faint);
    font-size: 11px;
    position: relative;
  }
  .mobile-nav a.on { color: var(--accent-ink); font-weight: 500; }
  .mobile-nav a.on::before {
    content: '';
    position: absolute;
    top: -7px;
    width: 22px;
    height: 2px;
    border-radius: 1px;
    background: var(--accent);
  }
  /* 给固定底栏让位，防止最后一条消息/按钮被盖住 */
  .app-main { padding-bottom: calc(62px + env(safe-area-inset-bottom)); }
}

@media(max-width: 820px) {
  .sidebar-wrap { position: fixed; inset: 0 auto 0 0; z-index: 50; width: min(300px, calc(100vw - 56px)); box-shadow: var(--shadow-lg); }
  .nav-backdrop { display: block; position: fixed; inset: 0; z-index: 40; background: rgba(12,14,22,.42); }
  .app-header { padding: 0 12px; gap: 8px; height: 58px; }
  .link-state { display: none; }
}
</style>
