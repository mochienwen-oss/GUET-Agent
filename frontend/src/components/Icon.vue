<template>
  <svg
    class="ic"
    :width="px"
    :height="px"
    viewBox="0 0 24 24"
    aria-hidden="true"
  >
    <path v-for="(d, i) in icon.d || []" :key="'p' + i" :d="d" />
    <circle
      v-for="(c, i) in icon.circle || []"
      :key="'c' + i"
      :cx="c[0]"
      :cy="c[1]"
      :r="c[2]"
    />
    <rect
      v-for="(r, i) in icon.rect || []"
      :key="'r' + i"
      :x="r[0]"
      :y="r[1]"
      :width="r[2]"
      :height="r[3]"
      :rx="r[4]"
    />
  </svg>
</template>

<script setup lang="ts">
// Icon.vue —— 全站内联 SVG 图标（**单一来源**：全项目只在这里定义图形）。
//
// 约定（改这里时一并遵守）：
//   · **统一 1.8 线宽、统一描边型**（不用填充型）—— 混用线宽是最容易被看出来的粗糙点；
//   · 颜色一律 `currentColor`，由外层的 color 决定，图标自身不写色值；
//   · 线宽/端点/连接由 style.css 的 `svg.ic` 统一给，**不在每个图标上重复写**。
//   · 24×24 网格。`M x y h.01` 这类"零长度线"配 round 端点即为一个圆点。
import { computed } from 'vue'

interface IconDef {
  d?: string[]
  /** [cx, cy, r] */
  circle?: [number, number, number][]
  /** [x, y, w, h, rx] */
  rect?: [number, number, number, number, number][]
}

const props = withDefaults(defineProps<{ name: string; size?: number | string }>(), {
  size: 16,
})

const ICONS: Record<string, IconDef> = {
  // —— 导航 / 通用 ——
  menu: { d: ['M3 12h18', 'M3 6h18', 'M3 18h18'] },
  close: { d: ['M6 6l12 12', 'M18 6L6 18'] },
  chat: { d: ['M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-1 1V11.5A8.5 8.5 0 0 1 21 11.5Z', 'M7 10h10', 'M7 14h6'] },
  presentation: { d: ['M3 3h18', 'M5 3v13h14V3', 'M12 16v5', 'M8 21h8', 'M8 8h8', 'M8 11h5'] },
  plus: { d: ['M12 5v14', 'M5 12h14'] },
  send: { d: ['M12 19V5', 'M5 12l7-7 7 7'] },
  search: { d: ['M20.5 20.5L16.8 16.8'], circle: [[11, 11, 7]] },
  overview: { d: ['M4 20V11', 'M10 20V4', 'M16 20v-6'] },
  docs: { d: ['M5 20a2 2 0 0 1 0-4h14V3H7a2 2 0 0 0-2 2z', 'M5 20V5'] },
  settings: {
    d: ['M12 2v3', 'M12 19v3', 'M4.2 4.2l2.1 2.1', 'M17.7 17.7l2.1 2.1',
        'M2 12h3', 'M19 12h3', 'M4.2 19.8l2.1-2.1', 'M17.7 6.3l2.1-2.1'],
    circle: [[12, 12, 3]],
  },
  // —— 主题 ——
  sun: {
    d: ['M12 1.5v2.4', 'M12 20.1v2.4', 'M3.5 3.5l1.7 1.7', 'M18.8 18.8l1.7 1.7',
        'M1.5 12h2.4', 'M20.1 12h2.4', 'M3.5 20.5l1.7-1.7', 'M18.8 5.2l1.7-1.7'],
    circle: [[12, 12, 4.2]],
  },
  moon: { d: ['M20.5 13.2A8.5 8.5 0 1 1 10.8 3.5a6.6 6.6 0 0 0 9.7 9.7z'] },
  // —— 会话列表 ——
  pin: { d: ['M15 4v5l2 2v2H7v-2l2-2V4', 'M12 13v7'] },
  edit: { d: ['M4 20h4l10-10-4-4L4 16z', 'M14 6l4 4'] },
  trash: { d: ['M4 7h16', 'M9 7V5h6v2', 'M6 7l1 13h10l1-13'] },
  // —— 文件 / 来源 ——
  tag: {
    d: ['M3 12V3h9l8.5 8.5a2 2 0 0 1 0 2.8l-5.2 5.2a2 2 0 0 1-2.8 0L3 12z'],
    circle: [[7.5, 7.5, 1.3]],
  },
  file: { d: ['M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z', 'M14 3v5h5'] },
  folder: { d: ['M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'] },
  upload: {
    d: ['M12 15V3', 'M7.5 7.5L12 3l4.5 4.5',
        'M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3'],
  },
  // —— 消息操作 ——
  copy: { d: ['M5 15V5a2 2 0 0 1 2-2h8'], rect: [[9, 9, 11, 11, 2]] },
  check: { d: ['M20 6L9 17l-5-5'] },
  refresh: { d: ['M20 11a8 8 0 1 0-2.3 5.7', 'M20 4v7h-7'] },
  stop: { rect: [[6.5, 6.5, 11, 11, 1.5]] },
  // —— 头像 / 其他 ——
  user: { d: ['M4.5 20a7.5 7.5 0 0 1 15 0'], circle: [[12, 8, 3.6]] },
  bot: {
    d: ['M12 3v4', 'M9 12h.01', 'M15 12h.01', 'M9.5 16h5'],
    rect: [[4, 7, 16, 12, 3]],
  },
  flask: { d: ['M9 3h6', 'M10 3v6L5 19a1 1 0 0 0 .9 1.5h12.2A1 1 0 0 0 19 19L14 9V3', 'M7.5 14h9'] },
  // —— 方向 ——
  chevronRight: { d: ['M9 18l6-6-6-6'] },
  chevronDown: { d: ['M6 9l6 6 6-6'] },
  arrowLeft: { d: ['M19 12H5', 'M12 19l-7-7 7-7'] },
  alert: { d: ['M12 7.5v5.5', 'M12 16.5h.01'], circle: [[12, 12, 9]] },
}

const icon = computed<IconDef>(() => ICONS[props.name] || { d: [] })
const px = computed(() => (typeof props.size === 'number' ? `${props.size}px` : props.size))
</script>
