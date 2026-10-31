<template>
  <span class="logo" :class="{ 'logo--light': variant === 'light' }"
    :style="{ width: size + 'px', height: size + 'px' }">
    <img class="badge" src="/brand/ai-badge-white.png" alt="SAI" />
  </span>
</template>

<script setup lang="ts">
// BrandLogo.vue —— 学院院徽（六边形电路章）
//
// ★ 直接使用学院官网的院徽原图（从 logo_y.png 中裁出的正方形徽章部分，
//   白色线条透明底），不再自绘。这既是用户的选择，也解决了"自绘像不像"的问题。
//
// ★ 反色策略：原图是白线，只适合深色底。
//   - variant 缺省 'auto'：跟随主题 —— 深色主题用白线；浅色主题用
//     filter: brightness(0) 压成深墨线（否则白线在白底上隐形）。
//   - variant='light'：强制白线。用于「浅色主题里的深色底」场景 ——
//     登录封面、欢迎横幅都是深紫底，若按主题判定会被错误压暗，
//     深线落在深紫上等于隐形。这正是 hero 图标"显示效果不好"的根因。
import { computed } from 'vue'

const props = withDefaults(defineProps<{ size?: number; variant?: 'auto' | 'light' }>(),
  { size: 32, variant: 'auto' })
</script>

<style scoped>
.logo {
  display: inline-flex;
  flex-shrink: 0;
}
.badge {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
/* 浅色主题浅底：白线不可见 → 压成深墨线。
   仅 auto 生效；深色底场景用 variant='light' 显式豁免。 */
html:not([data-theme='dark']) .badge {
  filter: brightness(0);
  opacity: 0.88;
}
.logo--light .badge {
  filter: none;
  opacity: 1;
}
</style>

