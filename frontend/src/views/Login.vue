<template>
  <div class="login-page">
    <!-- 左栏：品牌陈述。用纯底 + 颗粒，**不用渐变** ——
         "紫→蓝 45° 渐变"是两个设计 skill 一致点名的头号 AI 生成痕迹，
         而原来的登录页背景正是 `linear-gradient(135deg, #667eea, #764ba2)`。 -->
    <aside class="login-aside">
      <div class="login-brand">
        <BrandLogo :size="38" />
        <div>
          <div class="brand-name">SAI</div>
          <div class="brand-sub">桂林电子科技大学</div>
        </div>
      </div>

      <div class="login-hero">
        <h2>校园制度，<br />问一句就有出处。</h2>
        <p>回答只依据校内已归档的制度文件生成，每条结论都附上它出自哪份文件。查不到就直说查不到，不猜。</p>
      </div>

      <div class="login-note">桂林电子科技大学 · 智能问答平台</div>
    </aside>

    <main class="login-main">
      <div class="login-card">
        <h1>{{ mode === 'login' ? '登录' : '注册' }}</h1>
        <p class="sub">使用学号或工号登录</p>

        <div class="tabs">
          <button :class="{ on: mode === 'login' }" @click="mode = 'login'">登录</button>
          <button :class="{ on: mode === 'register' }" @click="mode = 'register'">注册</button>
        </div>

        <form @submit.prevent="submit">
          <div class="stack">
            <div>
              <label class="label" for="login-username">用户名</label>
              <input
                id="login-username"
                v-model="username"
                class="field"
                placeholder="学号 / 工号"
                autocomplete="username"
              />
            </div>
            <div v-if="mode === 'register'">
              <label class="label" for="login-name">昵称</label>
              <input id="login-name" v-model="name" class="field" placeholder="怎么称呼你" />
            </div>
            <div>
              <label class="label" for="login-password">密码</label>
              <input
                id="login-password"
                v-model="password"
                class="field"
                type="password"
                placeholder="请输入密码"
                autocomplete="current-password"
              />
            </div>

            <p v-if="error" class="error">
              <Icon name="alert" :size="14" /> {{ error }}
            </p>

            <button class="btn-accent" type="submit" :disabled="loading">
              {{ loading ? '请稍候…' : mode === 'login' ? '登录' : '注册' }}
            </button>
          </div>
        </form>

        <template v-if="isDev">
          <div class="divider">或</div>
          <button type="button" class="preview-btn" @click="enterPreview">
            <Icon name="flask" :size="15" /> 预览模式（无需后端）
          </button>
        </template>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../store/auth'
import { enableDevMock } from '../mock/devMock'
import BrandLogo from '../components/BrandLogo.vue'
import Icon from '../components/Icon.vue'

const router = useRouter()
const auth = useAuthStore()
const isDev = import.meta.env.DEV
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const name = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (mode.value === 'login') {
      await auth.login(username.value, password.value)
    } else {
      await auth.register(username.value, password.value, name.value)
    }
    router.push('/')
  } catch (e: any) {
    error.value = e.message || '操作失败'
  } finally {
    loading.value = false
  }
}

function enterPreview() {
  auth.setAuth('preview', { id: 0, username: 'preview', name: '预览用户', role: 'admin' })
  enableDevMock()
  router.push('/')
}
</script>

<style scoped>
/* 左文右表单的分栏：不做"全屏居中一张卡"的对称结构 */
.login-page {
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  height: 100dvh;
  background: var(--bg);
}

.login-aside {
  background: var(--bg);
  border-right: 1px solid var(--border);
  padding: 56px 60px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 32px;
}
.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand-name {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.2;
}
.brand-sub {
  font-size: 11px;
  color: var(--text-faint);
  letter-spacing: 0.02em;
}
.login-hero h2 {
  font-size: 32px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.25;
  margin-bottom: 14px;
  text-wrap: balance;
}
.login-hero p {
  font-size: 15px;
  color: var(--text-muted);
  max-width: 42ch;
}
.login-note {
  font-size: 12px;
  color: var(--text-faint);
}

.login-main {
  background: var(--surface);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}
.login-card {
  width: 100%;
  max-width: 368px;
}
.login-card h1 {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin-bottom: 4px;
}
.login-card .sub {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 26px;
}

/* 页签用下划线，不用填充块 —— 少一层色块 */
.tabs {
  display: flex;
  gap: 22px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}
.tabs button {
  padding: 0 0 11px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-faint);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tabs button:hover {
  color: var(--text-muted);
}
.tabs button.on {
  color: var(--accent-ink);
  border-bottom-color: var(--accent);
}

.stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: 6px;
}
.field {
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 15px;
}
.field::placeholder {
  color: var(--text-faint);
}
.field:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--danger);
  font-size: 13px;
}
.btn-accent {
  width: 100%;
  height: 44px;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 15px;
  font-weight: 500;
}
.btn-accent:hover:not(:disabled) {
  background: var(--accent-hover);
}
.btn-accent:disabled {
  background: var(--surface-2);
  color: var(--text-faint);
}

.divider {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--text-faint);
  margin: 20px 0 12px;
}
.divider::before,
.divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border);
}
.preview-btn {
  width: 100%;
  height: 44px;
  border: 1px dashed var(--border-strong);
  border-radius: 10px;
  color: var(--text-muted);
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.preview-btn:hover {
  color: var(--accent-ink);
  border-color: var(--accent);
  background: var(--accent-soft);
}

@media (max-width: 1000px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .login-aside {
    display: none;
  }
}
</style>