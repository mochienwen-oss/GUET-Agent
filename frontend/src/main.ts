import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import router from './router'
import { useThemeStore } from './store/theme'
import { initDevMock } from './mock/devMock'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)

// 尽早应用主题，避免首屏闪烁
useThemeStore(pinia).init()
// 开发环境：若上次启用了预览模式，则恢复前端 Mock
initDevMock()

app.mount('#app')