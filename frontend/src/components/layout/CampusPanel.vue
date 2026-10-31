<template>
  <aside class="campus-panel" :class="{ expanded: campus.expanded }" :style="{ width: campus.expanded ? '100%' : `${campus.panelWidth}px` }">
    <header class="panel-heading">
      <strong>学校网页 <span>· {{ campus.serviceName }}</span></strong>
      <button type="button" @click="campus.toggleExpanded()">{{ campus.expanded ? '恢复分栏' : '展开页面' }}</button>
      <button class="nav" title="关闭学校页面" @click="campus.close()"><Icon name="close" :size="15" /></button>
    </header>
    <!-- 浏览器工具条：和效果图一致，返回 / 前进 / 刷新 / 地址 / 关闭 -->
    <div class="bar">
      <button class="nav" title="后退" @click="campus.back()"><Icon name="arrowLeft" :size="16" /></button>
      <button class="nav" title="前进" @click="campus.forward()"><Icon name="chevronRight" :size="16" /></button>
      <button class="nav" title="刷新" @click="campus.reload()"><Icon name="refresh" :size="15" /></button>
      <span class="url" :title="campus.url || '学校官网'">
        <Icon name="pin" :size="12" />
        <span class="url-text">{{ displayUrl }}</span>
      </span>
    </div>

    <div class="page-tools">
      <span class="page-status" role="status">{{ campus.statusText }}</span>
      <button v-if="campus.awaiting" type="button" @click="campus.pause()">暂停导航</button>
      <button v-else-if="['paused', 'needs_user', 'failed'].includes(campus.status)" type="button" @click="campus.resume()">继续导航</button>
      <div class="zoom-tools" aria-label="学校网页缩放">
        <button type="button" aria-label="缩小学校网页" @click="campus.changeZoom('out')">−</button>
        <span>{{ Math.round(campus.zoom * 100) }}%</span>
        <button type="button" aria-label="放大学校网页" @click="campus.changeZoom('in')">+</button>
        <button type="button" :aria-pressed="campus.autoZoom" @click="campus.changeZoom('fit')">适应宽度</button>
      </div>
    </div>
    <p v-if="campus.detail" class="page-detail">{{ campus.detail }}</p>

    <!-- 学校网页由主进程的 WebContentsView 覆盖在这块区域上；这里只负责量出矩形 -->
    <div ref="host" class="host">
      <p v-if="!campus.available" class="host-note">
        当前在浏览器中打开，无法显示学校页面。<br />请使用 SAI 桌面客户端。
      </p>
      <p v-else class="host-note">正在打开学校页面…</p>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../common/Icon.vue'
import { useCampusStore } from '../../store/campus'

const campus = useCampusStore()
const host = ref<HTMLElement>()
let observer: ResizeObserver | null = null
let frame = 0

const displayUrl = computed(() => {
  if (!campus.url) return '学校官网'
  try {
    const parsed = new URL(campus.url)
    return parsed.host + (parsed.pathname === '/' ? '' : parsed.pathname)
  } catch {
    return campus.url
  }
})

/** 把右侧这块区域的矩形交给主进程；节流到每帧一次，拖拽时也不抖动。 */
function sync() {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    const element = host.value
    if (!element || !campus.visible) return
    const rect = element.getBoundingClientRect()
    campus.pushBounds({ x: rect.left, y: rect.top, width: rect.width, height: rect.height })
  })
}

watch(() => [campus.panelWidth, campus.visible, campus.expanded, campus.detail], () => sync())

onMounted(() => {
  if (host.value && 'ResizeObserver' in window) {
    observer = new ResizeObserver(() => sync())
    observer.observe(host.value)
  }
  window.addEventListener('resize', sync)
  sync()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('resize', sync)
  if (frame) cancelAnimationFrame(frame)
  // 离开聊天页时收起学校视图，避免它盖在别的页面上
  void campus.hide()
})
</script>

<style scoped>
.campus-panel {
  display: flex;
  flex-direction: column;
  flex: none;
  min-width: 0;
  background: var(--surface);
  border-left: 1px solid var(--border);
}
.campus-panel.expanded { flex: 1; border-left: 0; }
.panel-heading { height: 52px; display: flex; align-items: center; gap: 8px; padding: 0 12px; flex: none; border-bottom: 1px solid var(--border); }
.panel-heading strong { flex: 1; font-size: 14px; }
.panel-heading strong span { font-size: 12px; color: var(--text-muted); font-weight: 400; }
.panel-heading > button:not(.nav), .page-tools button { background: var(--surface); border: 1px solid var(--border); color: var(--text-muted); padding: 5px 8px; border-radius: 6px; font-size: 12px; }
.page-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border); flex: none; }
.page-status { color: var(--text-muted); font-size: 12px; }
.zoom-tools { display: flex; align-items: center; gap: 4px; margin-left: auto; font-size: 12px; font-variant-numeric: tabular-nums; }
.zoom-tools > span { min-width: 38px; text-align: center; }
.page-detail { margin: 0; padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--text-muted); font-size: 12px; line-height: 1.5; }
.bar {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 44px;
  flex: none;
  padding: 0 10px;
  border-bottom: 1px solid var(--border);
}
.nav {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 6px;
  color: var(--text-muted);
}
.nav:hover {
  background: var(--surface-2);
  color: var(--text);
}
.url {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  height: 30px;
  padding: 0 10px;
  margin: 0 4px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 12px;
}
.url-text {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  direction: rtl;
  text-align: left;
}
.host {
  position: relative;
  flex: 1;
  min-height: 0;
  display: grid;
  place-items: center;
  background: var(--surface);
}
.host-note {
  max-width: 260px;
  color: var(--text-faint);
  font-size: 13px;
  line-height: 1.8;
  text-align: center;
}
</style>
