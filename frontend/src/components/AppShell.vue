<template>
  <div class="app-shell" @keydown.esc="collapsed = true">
    <button v-if="!collapsed" class="nav-backdrop" aria-label="关闭导航" @click="collapsed = true" />
    <div id="app-sidebar" class="sidebar-wrap" :class="{ collapsed }">
      <Sidebar @navigate="closeMobile" @close="collapsed = true" />
    </div>
    <main class="app-main">
      <header class="app-header">
        <button class="nav-toggle" :aria-expanded="!collapsed" aria-controls="app-sidebar"
          :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'" @click="collapsed = !collapsed"><Icon name="menu" :size="19" /></button>
        <span class="section-title">{{ isPpt ? 'PPT 制作' : '校园问答' }}</span>
        <span class="header-divider">/</span>
        <span class="page-title">{{ title }}</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useChatStore } from '../store/chat'
import { usePresentationStore } from '../store/presentation'
import Sidebar from './Sidebar.vue'
import Icon from './Icon.vue'
const route = useRoute(), chat = useChatStore(), ppt = usePresentationStore()
ppt.clearTask()
const collapsed = ref(window.matchMedia('(max-width: 820px)').matches)
const isPpt = computed(() => route.path.startsWith('/presentations'))
const title = computed(() => isPpt.value
  ? (route.params.taskId ? ppt.task?.brief?.goal || '制作工作区' : '我的演示文稿')
  : chat.conversations.find(c => c.id === chat.currentId)?.title || '新对话')
function closeMobile() { if (window.matchMedia('(max-width: 820px)').matches) collapsed.value = true }
onMounted(() => { void chat.loadConversations(); void ppt.loadList() })
</script>

<style scoped>
.app-shell { display: flex; height: 100dvh; overflow: hidden; }
.sidebar-wrap { display: flex; width: 252px; flex-shrink: 0; }
.sidebar-wrap.collapsed { display: none; }
.app-main { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
.app-header { height: 64px; flex-shrink: 0; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; padding: 0 24px; background: var(--surface); }
.nav-toggle { width: 44px; height: 44px; flex-shrink: 0; display: grid; place-items: center; border-radius: 6px; }
.nav-toggle:hover { background: var(--surface-2); }
.section-title { font-size: 14px; font-weight: 600; white-space: nowrap; }
.header-divider { color: var(--border-strong); }
.page-title { font-size: 13px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nav-backdrop { display: none; }
@media(max-width: 820px) {
  .sidebar-wrap { position: fixed; inset: 0 auto 0 0; z-index: 50; width: min(300px, calc(100vw - 56px)); box-shadow: var(--shadow-lg); }
  .nav-backdrop { display: block; position: fixed; inset: 0; z-index: 40; background: rgba(12,14,22,.42); }
  .app-header { padding: 0 12px; gap: 8px; height: 58px; }
}
</style>
