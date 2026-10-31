// store/theme.ts —— 外观偏好（浅色 / 深色 / 跟随系统）
//
// ★ 从「两档 toggle」升级为「三档偏好」：用户能明确选择跟随系统，而不是只能在
//   浅/深之间来回切。`theme` 始终是**解析后的实际主题**（供组件读），`pref` 才是
//   用户的选择 —— 混用这两个概念会导致「选了跟随系统后，系统变了界面不变」。
import { defineStore } from 'pinia'

export type Theme = 'light' | 'dark'
export type ThemePref = Theme | 'system'

const THEME_KEY = 'theme'

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function resolve(pref: ThemePref): Theme {
  return pref === 'system' ? systemTheme() : pref
}

function initialPref(): ThemePref {
  const saved = localStorage.getItem(THEME_KEY)
  if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  return 'system'
}

function apply(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

export const useThemeStore = defineStore('theme', {
  state: () => {
    // 只解析一次：pref 与 theme 必须来自同一个快照，否则首帧可能出现
    // 「偏好是跟随系统、实际主题却是另一个」的错位。
    const pref = initialPref()
    return { pref, theme: resolve(pref) as Theme }
  },
  actions: {
    init() {
      apply(this.theme)
      // 选了「跟随系统」时，系统主题变化要实时跟上
      window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (this.pref !== 'system') return
        this.theme = systemTheme()
        apply(this.theme)
      })
    },
    toggle() {
      this.set(this.theme === 'light' ? 'dark' : 'light')
    },
    set(pref: ThemePref) {
      this.pref = pref
      this.theme = resolve(pref)
      localStorage.setItem(THEME_KEY, pref)
      apply(this.theme)
    },
  },
})
