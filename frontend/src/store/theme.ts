// store/theme.ts —— 暗黑/浅色主题（持久化到 localStorage + 挂 data-theme）
import { defineStore } from 'pinia'

export type Theme = 'light' | 'dark'

const THEME_KEY = 'theme'

function initialTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  // 未设置时跟随系统偏好
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

function apply(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

export const useThemeStore = defineStore('theme', {
  state: () => ({
    theme: initialTheme(),
  }),
  actions: {
    init() {
      apply(this.theme)
    },
    toggle() {
      this.set(this.theme === 'light' ? 'dark' : 'light')
    },
    set(theme: Theme) {
      this.theme = theme
      localStorage.setItem(THEME_KEY, theme)
      apply(theme)
    },
  },
})