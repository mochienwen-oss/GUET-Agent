<template>
  <div class="admin-layout">
    <aside class="admin-side">
      <div class="side-brand">
        <BrandLogo :size="30" />
        <span>管理后台</span>
      </div>

      <nav class="side-nav">
        <button
          v-for="item in nav"
          :key="item.key"
          :class="['nav-item', { active: active === item.key }]"
          @click="active = item.key"
        >
          <Icon :name="item.icon" :size="16" />
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="side-foot">
        <button class="back" @click="router.push('/')">
          <Icon name="arrowLeft" :size="15" /> 返回聊天
        </button>
      </div>
    </aside>

    <main class="admin-body">
      <header class="admin-header">
        <h1>{{ currentTitle }}</h1>
        <p>{{ currentDesc }}</p>
      </header>

      <section class="pane">
        <Overview v-if="active === 'overview'" @navigate="active = $event" />
        <DocManager v-else-if="active === 'docs'" />
        <RetrievalPlayground v-else />
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import DocManager from '../components/DocManager.vue'
import Overview from '../components/Overview.vue'
import RetrievalPlayground from '../components/RetrievalPlayground.vue'
import BrandLogo from '../components/BrandLogo.vue'
import Icon from '../components/Icon.vue'

const router = useRouter()
const active = ref<'overview' | 'docs' | 'retrieval'>('overview')

const nav = [
  { key: 'overview', label: '总览', icon: 'overview', desc: '知识库规模与最近入库情况' },
  { key: 'docs', label: '知识库', icon: 'docs', desc: '上传、分类与维护校规制度文件' },
  { key: 'retrieval', label: '检索调试', icon: 'search', desc: '查看某个问题实际召回了哪些内容' },
] as const

const current = computed(() => nav.find((n) => n.key === active.value) ?? nav[0])
const currentTitle = computed(() => current.value.label)
const currentDesc = computed(() => current.value.desc)
</script>

<style scoped>
.admin-layout {
  display: flex;
  height: 100dvh;
  background: var(--bg);
}
/* 保留左侧栏（本次不改后台动线），只重做它的视觉 */
.admin-side {
  width: 236px;
  flex-shrink: 0;
  background: var(--bg);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
}
.side-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  padding: 2px 6px 16px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}
.side-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 11px;
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 14px;
  text-align: left;
  background: transparent;
}
.nav-item:hover {
  background: var(--surface-2);
  color: var(--text);
}
/* 与聊天侧栏同一套选中态：淡色底板 + 强调色文字（不加左侧彩色竖条） */
.nav-item.active {
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-weight: 500;
}
.side-foot {
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.back {
  width: 100%;
  height: 34px;
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
.back:hover {
  background: var(--surface-2);
}

.admin-body {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}
.admin-header {
  padding: 26px 32px 20px;
}
.admin-header h1 {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.admin-header p {
  font-size: 14px;
  color: var(--text-muted);
  margin-top: 4px;
}
.pane {
  padding: 0 32px 40px;
}

@media (max-width: 820px) {
  .admin-side {
    display: none;
  }
  .admin-header,
  .pane {
    padding-left: 20px;
    padding-right: 20px;
  }
}
</style>