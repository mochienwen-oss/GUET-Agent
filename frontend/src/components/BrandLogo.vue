<template>
  <span class="logo" :style="{ width: size + 'px', height: size + 'px', borderRadius: radius + 'px' }">
    <svg
      :width="Math.round(size * 0.58)"
      :height="Math.round(size * 0.58)"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M5 13.18v4L12 21l7-3.82v-4L12 17l-7-3.82zM12 3L1 9l11 6 9-4.91V17h2V9L12 3z" />
    </svg>
  </span>
</template>

<script setup lang="ts">
// BrandLogo.vue —— 品牌标记：单色强调底 + 学士帽。
// ★ 原来这里是 `linear-gradient(135deg, primary, gradient-2)` 的紫→蓝渐变，
//   而"紫→蓝渐变"正是两个设计 skill 一致点名的**头号 AI 生成痕迹**。
//   改为**平涂单一强调色** —— 保留蓝紫，去掉渐变这个形态。
//   梯形圆角：小尺寸偏方、大尺寸略圆，与外层容器半径成比例而不是固定值。
import { computed } from 'vue'

const props = withDefaults(defineProps<{ size?: number }>(), { size: 32 })

const radius = computed(() => Math.round(props.size * 0.3))
</script>

<style scoped>
.logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--accent);
  color: #fff;
}
</style>