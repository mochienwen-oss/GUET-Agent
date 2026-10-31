<template>
  <div class="login-page">
    <!-- 左栏：品牌陈述。用纯底 + 颗粒，**不用渐变** ——
         "蓝→紫 45° 渐变"是一眼可辨的 AI 生成痕迹，
         而原来的登录页背景正是一条 135° 的双色渐变。 -->
    <aside class="login-aside">
      <!-- 母题：同心双环（取自校徽的"电子环绕"）。放在右边缘外溢、被裁掉一半，
           让左栏有纵深，但不抢文字。纯描边、无渐变。 -->
      <span class="ring ring-outer" aria-hidden="true"></span>
      <span class="ring ring-inner" aria-hidden="true"></span>

      <div class="login-brand">
        <BrandLogo :size="42" />
        <div>
          <div class="brand-name">SAI</div>
          <div class="brand-sub">桂林电子科技大学</div>
        </div>
      </div>

      <div class="login-hero">
        <p class="hero-kicker">SAI · 校园智能助手</p>
        <h2>让学习与工作，<br />从一个想法开始。</h2>
        <ul class="login-facts">
          <li><span class="fact-no">01</span>校园问答，查看回答的资料来源</li>
          <li><span class="fact-no">02</span>从对话开始制作演示文稿</li>
          <li><span class="fact-no">03</span>上传表格，整理和处理数据</li>
        </ul>
      </div>

      <div class="login-foot">
        <div class="band" aria-hidden="true"></div>
        <p class="login-note">桂林电子科技大学 · 校园智能助手</p>
      </div>
    </aside>

    <main class="login-main">
      <div class="login-mobile-brand">
        <BrandLogo :size="38" />
        <div><strong>SAI</strong><span>桂林电子科技大学</span></div>
      </div>
      <div class="login-card">
        <div class="card-rule" aria-hidden="true"></div>
        <h1>登录</h1>
        <p class="sub">{{ mode === 'school' ? '使用学校统一身份认证账号登录' : '使用管理员账号登录' }}</p>

        <div class="tabs">
          <button type="button" :disabled="loading" :aria-pressed="mode === 'school'" :class="{ on: mode === 'school' }" @click="changeMode('school')">学校账号</button>
          <button type="button" :disabled="loading" :aria-pressed="mode === 'admin'" :class="{ on: mode === 'admin' }" @click="changeMode('admin')">管理员</button>
        </div>

        <form :key="mode" autocomplete="off" @submit.prevent="submit">
          <div class="stack">
            <div>
              <label class="label" :for="`login-${mode}-username`">用户名</label>
              <input
                :id="`login-${mode}-username`"
                :name="`${mode}-username`"
                v-model="username"
                class="field"
                :placeholder="mode === 'school' ? '学号 / 工号' : '管理员账号'"
                autocomplete="off"
              />
            </div>
            <div>
              <label class="label" :for="`login-${mode}-password`">密码</label>
              <div class="password-field"><input
                :id="`login-${mode}-password`"
                :name="`${mode}-password`"
                v-model="password"
                class="field"
                :type="showPassword ? 'text' : 'password'"
                placeholder="请输入密码"
                autocomplete="off"
              /><button type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :title="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" @click="showPassword = !showPassword"><Icon :name="showPassword ? 'eyeOff' : 'eye'" :size="19" /></button></div>
            </div>
            <div v-if="mode === 'school' && challenge?.captcha_required">
              <label class="label" for="login-captcha">学校验证码</label>
              <div class="captcha-row">
                <input id="login-captcha" v-model="captcha" class="field" autocomplete="off" placeholder="请输入图中验证码" />
                <button type="button" class="captcha-image" title="换一张验证码" @click="refreshChallenge">
                  <img v-if="challenge.captcha_image" :src="challenge.captcha_image" alt="点击更换验证码" />
                </button>
              </div>
            </div>

            <p v-if="error" class="error" role="alert">
              <Icon name="alert" :size="14" /> {{ error }}
            </p>

            <button class="btn-accent" type="submit" :disabled="loading">
              {{ loading ? '请稍候…' : '登录' }}
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
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../store/auth'
import { enableDevMock } from '../mock/devMock'
import BrandLogo from '../components/common/BrandLogo.vue'
import Icon from '../components/common/Icon.vue'

const router = useRouter()
const auth = useAuthStore()
const isDev = import.meta.env.DEV
const mode = ref<'school' | 'admin'>('school')
const credentials = reactive({
  school: { username: '', password: '' },
  admin: { username: '', password: '' },
})
const username = computed({
  get: () => credentials[mode.value].username,
  set: value => { credentials[mode.value].username = value },
})
const password = computed({
  get: () => credentials[mode.value].password,
  set: value => { credentials[mode.value].password = value },
})
const showPassword = ref(false)
const captcha = ref('')
const challenge = ref<{ challenge_id: string; captcha_required: boolean; captcha_image: string | null } | null>(null)
const error = ref('')
const loading = ref(false)
watch(username, () => { challenge.value = null; captcha.value = '' })

function changeMode(value: 'school' | 'admin') {
  if (loading.value) return
  mode.value = value; challenge.value = null; captcha.value = ''; error.value = ''; showPassword.value = false
}
async function refreshChallenge() {
  if (!username.value.trim() || loading.value) return
  loading.value = true; error.value = ''; captcha.value = ''
  try { challenge.value = await auth.schoolChallenge(username.value.trim()) }
  catch (e: any) { challenge.value = null; error.value = e.message || '获取验证码失败' }
  finally { loading.value = false }
}

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (!username.value.trim() || !password.value) throw new Error('请输入账号和密码')
    if (mode.value === 'admin') {
      await auth.login(username.value, password.value)
    } else {
      if (!challenge.value) {
        challenge.value = await auth.schoolChallenge(username.value.trim())
        if (challenge.value.captcha_required) return
      }
      if (challenge.value.captcha_required && !captcha.value.trim()) throw new Error('请输入学校验证码')
      await auth.schoolLogin(username.value.trim(), password.value, challenge.value.challenge_id, captcha.value.trim())
    }
    password.value = ''; captcha.value = ''; challenge.value = null
    router.push('/')
  } catch (e: any) {
    error.value = e.message || '操作失败'
    challenge.value = null; captcha.value = ''; password.value = ''
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
.captcha-row { display: flex; align-items: center; gap: 10px; }
.captcha-image { width: 112px; height: 42px; flex: none; border: 1px solid var(--border); border-radius: 6px; background: white; cursor: pointer; }
.captcha-image img { max-width: 100%; max-height: 100%; }
/* 左文右表单的分栏：不做"全屏居中一张卡"的对称结构。
   左栏是**深海蓝封面**（#1A3459 = 色板的「主色深版」，全应用最重的一块颜色）——这是全应用
   第一眼的地方，值得用最重的颜色。右栏保持浅色表单，一深一浅形成强对比。
   浅色/深色主题下左栏都用这一套：封面页不受主题切换影响，像印刷品。 */
.login-page {
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  height: 100dvh;
  background: var(--bg);
}
.login-aside {
  position: relative;
  overflow: hidden;
  background: #1a3459;
  color: #fff;
  padding: 56px 60px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 32px;
}
/* 环改白色低透明描边：深底上读作细密的轨道纹理，而不是灰线 */
.ring {
  position: absolute;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.16);
  pointer-events: none;
}
.ring-outer {
  width: 560px;
  height: 560px;
  right: -280px;
  top: 50%;
  transform: translateY(-50%);
}
.ring-inner {
  width: 360px;
  height: 360px;
  right: -180px;
  top: 50%;
  transform: translateY(-50%);
  border-color: rgba(255, 255, 255, 0.09);
}
/* 环上一点：校旗橙。深蓝 + 橙是学校视觉体系里现成的搭配，也是整块唯一点睛 */
.ring-outer::after {
  content: '';
  position: absolute;
  left: 14.6%;
  top: 14.6%;
  width: 9px;
  height: 9px;
  margin: -4.5px 0 0 -4.5px;
  border-radius: 50%;
  background: var(--data-fill, #f5a623);
  box-shadow: 0 0 0 4px rgba(245, 166, 35, 0.2);
}
/* 徽章托片：白线院徽配半透明白圆角底，与欢迎横幅同一处理（两页视觉接力） */
.login-brand :deep(.logo) {
  padding: 7px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 10px;
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
  color: rgba(255, 255, 255, 0.62);
  letter-spacing: 0.02em;
}
/* 分类标签：给标题一个"这是哪一类东西"的上文，比直接甩大标题有秩序 */
.hero-kicker {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: #9fc0e4;
  margin-bottom: 14px;
}
.login-hero h2 {
  font-size: 36px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.22;
  text-wrap: balance;
  color: #fff;
}
/* 三条编号事实：比一段不分行的说明更容易被读完 */
.login-facts {
  list-style: none;
  margin-top: 28px;
  display: grid;
  gap: 13px;
  max-width: 40ch;
}
.login-facts li {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  align-items: baseline;
  font-size: 14px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.78);
}
.fact-no {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: rgba(255, 255, 255, 0.42);
  font-variant-numeric: tabular-nums;
}
.login-foot {
  display: grid;
  gap: 12px;
}
.login-foot .band {
  max-width: 180px;
}
.login-note {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
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
/* 卡片顶部一小段四色色带：把品牌标记带进表单区，长度只够"点到为止" */
.card-rule {
  width: 48px;
  height: 3px;
  border-radius: 2px;
  margin-bottom: 20px;
  background: linear-gradient(
    90deg,
    var(--band-1) 0 25%,
    var(--band-2) 25% 50%,
    var(--band-3) 50% 75%,
    var(--band-4) 75% 100%
  );
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

/* 与登录后的澄蓝工作台使用同一套表面、文字和焦点颜色。 */
.login-aside { background: var(--accent-soft); color: var(--text); }
.login-brand :deep(.logo) { background: var(--surface); border-color: var(--accent-border); }
.brand-sub, .login-note { color: var(--text-muted); }
.ring { border-color: var(--accent-border); }
.ring-inner { border-color: var(--border-strong); }
.ring-outer::after { background: var(--accent); box-shadow: 0 0 0 4px var(--surface); }
.hero-kicker { color: var(--accent-ink); }
.login-hero h2 { color: var(--text); }
.login-facts li { color: var(--text-muted); }
.fact-no { color: var(--accent-ink); }
.login-main { flex-direction: column; background: var(--bg); overflow-y: auto; }
.login-card { max-width: 430px; padding: 36px; border: 1px solid var(--border); border-radius: 18px; background: var(--surface); box-shadow: var(--shadow-md); }
.login-card h1 { font-size: 26px; }
.card-rule { background: var(--accent); }
.tabs button { min-height: 44px; }
.password-field { position: relative; }
.password-field .field { padding-right: 62px; }
.password-field button { position: absolute; right: 4px; top: 0; min-width: 48px; height: 44px; color: var(--accent-ink); font-size: 12px; }
.password-field button { display: grid; place-items: center; border-radius: 8px; }
.password-field button:hover { background: var(--surface-2); }
.password-field button:focus-visible, .tabs button:focus-visible, .btn-accent:focus-visible, .captcha-image:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.error { align-items: flex-start; line-height: 1.5; }
.login-mobile-brand { display: none; }

@media (max-width: 1000px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .login-aside {
    display: none;
  }
  .login-main { justify-content: center; padding: 24px; }
  .login-mobile-brand { width: 100%; max-width: 430px; display: flex; align-items: center; gap: 12px; margin-bottom: 22px; }
  .login-mobile-brand div { display: grid; gap: 3px; }
  .login-mobile-brand strong { font-size: 19px; line-height: 1; }
  .login-mobile-brand span { color: var(--text-muted); font-size: 12px; }
}
@media (max-width: 480px) {
  .login-page { min-height: 100dvh; height: auto; }
  .login-main { min-height: 100dvh; padding: 24px 16px calc(24px + env(safe-area-inset-bottom)); }
  .login-card { padding: 26px 20px; border-radius: 16px; }
  .login-card h1 { font-size: 23px; }
  .field, .btn-accent { height: 48px; }
  .password-field button { height: 48px; }
}
</style>
