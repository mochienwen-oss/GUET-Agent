<template>
  <button
    class="theme-toggle"
    type="button"
    :title="theme.theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
    :aria-label="theme.theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
    @click="theme.toggle()"
  >
    <Icon :name="theme.theme === 'dark' ? 'sun' : 'moon'" :size="17" />
  </button>
</template>

<script setup lang="ts">
import { useThemeStore } from '../../store/theme'
import Icon from './Icon.vue'

const theme = useThemeStore()
</script>

<style scoped>
/* ★★ bug④ 的修复点就在下面这一行 `color`。
 *
 * 症状：深色模式下这个太阳图标与背景糊在一起，看不见。
 * 根因：这是全项目**唯一**没有声明 `color` 的 <button> —— 其余按钮（.new-btn /
 *   .logout / .foot-btn / .action-btn / .mini / .collapse-btn …）都显式设了颜色。
 *   浏览器 UA 样式表给 button 直接挂了一条 `color: buttontext`，而**直接声明永远压过继承**，
 *   所以它拿不到外壳的 --text-muted。又因为原来全项目没设 `color-scheme`，
 *   浏览器不知道页面处于深色模式，`buttontext` 一直是**黑色** → 黑图标压深色底 ≈ 1.1:1。
 *   （浅色模式下它是"黑字配白底"反而正常，所以只有深色暴露。）
 *
 * 修复是两处，缺一不可：
 *   ① 这里显式给 color；
 *   ② style.css 的 :root / [data-theme] 里声明 color-scheme —— 让滚动条、光标、
 *      表单控件的原生配色也一起跟随主题，避免下一个"忘了写 color"的控件重蹈覆辙。
 */
.theme-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  color: var(--text-muted);
  background: transparent;
}
.theme-toggle:hover {
  background: var(--surface-2);
  color: var(--text);
}
</style>