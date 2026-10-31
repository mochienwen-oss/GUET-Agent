<template>
  <div class="profile" :class="{ 'profile--embedded': embedded }">
    <div class="p-scroll" ref="scrollEl" @scroll="onScroll">
      <header class="p-head">
        <div class="head-row">
          <div>
            <h1>个人资料</h1>
            <p class="head-sub">管理你的账号与使用体验</p>
          </div>
          <span class="head-mark">{{ roleLabel }}</span>
        </div>
      </header>

      <div class="p-body">
        <!-- 左栏：身份 + 栏目条索引 -->
        <aside class="p-aside">
          <div class="id-card">
            <!-- 头像本身就是入口：点一下选图，悬停给出「更换」提示 -->
            <button class="id-face" type="button" title="点击更换头像" aria-label="更换头像" @click="fileEl?.click()">
              <img v-if="auth.user?.avatar" :src="auth.user.avatar" alt="" />
              <span v-else class="id-initial">{{ initial }}</span>
              <span class="id-face-mask" aria-hidden="true"><Icon name="edit" :size="15" /><em>更换</em></span>
            </button>
            <input ref="fileEl" class="sr-only" type="file" accept="image/png,image/jpeg" @change="onAvatar" />
            <p class="id-name">{{ auth.user?.name || '未命名' }}</p>
            <p class="id-sub">{{ maskedUsername }}</p>
            <button v-if="auth.user?.avatar" class="id-reset" type="button" @click="removeAvatar">恢复默认头像</button>
          </div>

          <!-- ★ 索引是**栏目条**，不是描述型列表：
               一行一个条目，只放编号与标题，点哪儿去哪儿。描述性文字会让索引
               变成第二份正文，读起来比页面本身还累。 -->
          <nav class="p-index" aria-label="页面索引">
            <button
              v-for="s in sections"
              :key="s.id"
              class="index-row"
              :class="{ on: active === s.id }"
              type="button"
              :aria-current="active === s.id ? 'true' : undefined"
              @click="jump(s.id)"
            >
              <span class="index-no">{{ s.no }}</span>
              <span class="index-label">{{ s.label }}</span>
            </button>
          </nav>
        </aside>

        <!-- 右栏：分节事实 -->
        <div class="p-main">
          <section :id="'sec-identity'" class="p-sec">
            <div class="sec-head">
              <span class="sec-no">01</span>
              <h2>身份</h2>
            </div>

            <div class="row">
              <span class="row-k">昵称</span>
              <div class="row-v">
                <template v-if="!editing">
                  <span class="row-text">{{ auth.user?.name }}</span>
                  <button class="icon-btn" type="button" title="修改昵称" aria-label="修改昵称" @click="startEdit"><Icon name="edit" :size="14" /></button>
                </template>
                <template v-else>
                  <input
                    ref="nameEl"
                    v-model="nameInput"
                    class="inline-input"
                    maxlength="64"
                    aria-label="昵称"
                    @keydown.enter.prevent="saveName"
                    @keydown.esc.prevent="cancelEdit"
                  />
                  <button class="mini-btn" type="button" :disabled="busy" @click="saveName">保存</button>
                  <button class="mini-btn ghost" type="button" @click="cancelEdit">取消</button>
                </template>
              </div>
            </div>

            <div class="row">
              <span class="row-k">登录账号</span>
              <div class="row-v"><span class="row-text tabular">{{ maskedUsername }}</span></div>
            </div>

            <div class="row">
              <span class="row-k">身份</span>
              <div class="row-v"><span class="row-text">{{ roleLabel }}</span></div>
            </div>

            <p v-if="error" class="p-error" role="alert">{{ error }}</p>
          </section>

          <section v-if="!auth.isAdmin" id="sec-school" class="p-sec tint-blue">
            <div class="sec-head">
              <span class="sec-no">02</span>
              <h2>学校账号</h2>
            </div>

            <div class="row">
              <span class="row-k">认证方式</span>
              <div class="row-v"><span class="row-text">桂林电子科技大学统一身份认证（CAS）</span></div>
            </div>
            <div class="row">
              <span class="row-k">校园服务</span>
              <div class="row-v">
                <span class="row-text">支持空教室查询：登录后直接提问，结果会作为一条消息返回</span>
              </div>
            </div>

            <p class="note">
              学号与密码只用于学校统一身份认证，SAI 不保存你的密码；登录成功后，访问教务所需的会话凭据仅保存在服务端内存中（2 小时后自动失效），不会写入数据库；退出登录会立即清除。需要重新认证时，再次登录学校账号即可。
            </p>
          </section>

          <section id="sec-look" class="p-sec">
            <div class="sec-head">
              <span class="sec-no">03</span>
              <h2>外观</h2>
            </div>

            <div class="row">
              <span class="row-k">主题</span>
              <div class="row-v">
                <div class="seg" role="radiogroup" aria-label="主题">
                  <button
                    v-for="opt in themeOptions"
                    :key="opt.value"
                    class="seg-btn"
                    type="button"
                    role="radio"
                    :aria-checked="theme.pref === opt.value"
                    :class="{ on: theme.pref === opt.value }"
                    @click="theme.set(opt.value)"
                  >
                    <Icon :name="opt.icon" :size="15" />{{ opt.label }}
                  </button>
                </div>
              </div>
            </div>

            <div class="row">
              <span class="row-k">当前生效</span>
              <div class="row-v"><span class="row-text">{{ theme.theme === 'dark' ? '深色' : '浅色' }}</span></div>
            </div>
          </section>

          <section id="sec-data" class="p-sec">
            <div class="sec-head">
              <span class="sec-no">04</span>
              <h2>会话与登录</h2>
            </div>

            <div class="row">
              <span class="row-k">对话会话</span>
              <div class="row-v">
                <span class="row-count tabular">{{ chat.conversations.length }}</span>
                <span class="hint">个会话</span>
              </div>
            </div>

            <div class="row row-last">
              <span class="row-k">退出</span>
              <div class="row-v">
                <button class="link danger" type="button" @click="onLogout">退出登录</button>
                <span class="hint">清除本机保存的登录状态</span>
              </div>
            </div>
          </section>

          <p class="p-foot">桂电 · SAI v1.4</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// Profile.vue —— 个人资料页
//
// 结构：左侧「身份 + 栏目条索引」，右侧四段事实。索引是**栏目条**（点哪去哪、
// 滚动时高亮跟随），不是描述型目录 —— 描述会让索引变成第二份正文。
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '../components/common/Icon.vue'
import { useAuthStore } from '../store/auth'
import { useChatStore } from '../store/chat'
import { useThemeStore, type ThemePref } from '../store/theme'

defineProps<{ embedded?: boolean }>()
const emit = defineEmits<{ (e: 'logout'): void }>()
const auth = useAuthStore()
const chat = useChatStore()
const theme = useThemeStore()
const router = useRouter()

const initial = computed(() => (auth.user?.name || '用').slice(0, 1))
const roleLabel = computed(() => (auth.user?.role === 'admin' ? '管理员' : '校园用户'))
const maskedUsername = computed(() => {
  const value = auth.user?.username || ''
  return value.length > 4 ? value.slice(0, 2) + '****' + value.slice(-2) : value || '—'
})

const sections = computed(() => [
  { id: 'sec-identity', no: '01', label: '身份' },
  ...(!auth.isAdmin ? [{ id: 'sec-school', no: '02', label: '学校账号' }] : []),
  { id: 'sec-look', no: '03', label: '外观' },
  { id: 'sec-data', no: '04', label: '会话与登录' },
])
const active = ref(sections.value[0].id)
const scrollEl = ref<HTMLElement>()

function jump(id: string) {
  active.value = id
  const el = document.getElementById(id)
  el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// scroll-spy：用相对容器的位置判断，不用 offsetTop（offsetParent 未必是滚动容器）
function onScroll() {
  const root = scrollEl.value
  if (!root) return
  const rootTop = root.getBoundingClientRect().top
  let current = sections.value[0].id
  for (const s of sections.value) {
    const el = document.getElementById(s.id)
    if (!el) continue
    if (el.getBoundingClientRect().top - rootTop <= 96) current = s.id
  }
  active.value = current
}

// —— 昵称行内编辑 ——
const editing = ref(false)
const nameInput = ref('')
const nameEl = ref<HTMLInputElement>()
const busy = ref(false)
const error = ref('')

async function startEdit() {
  nameInput.value = auth.user?.name || ''
  editing.value = true
  error.value = ''
  await nextTick()
  nameEl.value?.focus()
  nameEl.value?.select()
}
function cancelEdit() {
  editing.value = false
  error.value = ''
}
async function saveName() {
  const value = nameInput.value.trim()
  if (!value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await auth.updateProfile(value)
    editing.value = false
  } catch (e: any) {
    error.value = e?.message || '保存昵称失败'
  } finally {
    busy.value = false
  }
}

// —— 头像 ——
const fileEl = ref<HTMLInputElement>()
async function onAvatar(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  error.value = ''
  try {
    await auth.updateAvatar(file)
  } catch (e: any) {
    error.value = e?.message || '上传头像失败'
  } finally {
    input.value = ''
  }
}
async function removeAvatar() {
  error.value = ''
  try {
    await auth.removeAvatar()
  } catch (e: any) {
    error.value = e?.message || '更换头像失败'
  }
}

// —— 退出 ——
async function onLogout() {
  await auth.logout()
  emit('logout')
  void router.push('/login')
}

const themeOptions: { value: ThemePref; label: string; icon: string }[] = [
  { value: 'light', label: '浅色', icon: 'sun' },
  { value: 'dark', label: '深色', icon: 'moon' },
  { value: 'system', label: '跟随系统', icon: 'monitor' },
]

onMounted(() => {
  void chat.loadConversations()
  void nextTick(onScroll)
})
onBeforeUnmount(() => {
  /* 无全局监听，占位以保持生命周期显式 */
})
</script>

<style scoped>
.profile {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}
.p-scroll {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 0 0 64px;
}

/* —— 页头：色带 + 标题 + 身份标记 —— */
.p-head {
  padding: 0 40px;
}
.head-row {
  max-width: 1040px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border-strong);
}
.head-row h1 {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.015em;
  line-height: 1.25;
}
.head-sub {
  margin-top: 4px;
  font-size: 13px;
  color: var(--text-muted);
}
.head-mark {
  font-size: 11px;
  letter-spacing: 0.1em;
  color: var(--seal);
  border: 1px solid var(--seal);
  border-radius: 4px;
  padding: 3px 8px;
  flex-shrink: 0;
}

/* —— 双栏 —— */
.p-body {
  max-width: 1040px;
  padding: 24px 40px 0;
  display: grid;
  grid-template-columns: 186px minmax(0, 1fr);
  gap: 40px;
  align-items: start;
}
.p-aside {
  position: sticky;
  top: 8px;
}
.id-card {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
/* 头像本身就是「更换头像」的入口：整块可点，悬停出现遮罩提示 */
.id-face {
  position: relative;
  width: 56px;
  height: 56px;
  padding: 0;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid var(--border-strong);
  display: grid;
  place-items: center;
  background: var(--accent-soft);
  cursor: pointer;
}
.id-face img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.id-initial {
  font-size: 20px;
  font-weight: 600;
  color: var(--accent-ink);
}
.id-face-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  background: color-mix(in srgb, var(--text) 62%, transparent);
  color: #fff;
  font-size: 10px;
  letter-spacing: 0.04em;
  opacity: 0;
  transition: opacity var(--fast) var(--ease);
}
.id-face-mask em { font-style: normal; }
.id-face:hover .id-face-mask,
.id-face:focus-visible .id-face-mask { opacity: 1; }
.id-face:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.id-reset {
  margin-top: 8px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--text-faint);
  font-size: 11px;
  cursor: pointer;
}
.id-reset:hover { color: var(--danger); text-decoration: underline; text-underline-offset: 3px; }

/* 行内图标按钮：把「修改」两个字换成可识别的编辑动作 */
.icon-btn {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
  transition: color var(--fast) var(--ease), border-color var(--fast) var(--ease), background var(--fast) var(--ease);
}
.icon-btn:hover {
  color: var(--accent-ink);
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
.icon-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* 编辑态的保存/取消：小实心按钮，比文字链更明确 */
.mini-btn {
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--brand-navy);
  border-radius: 7px;
  background: var(--brand-navy);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.mini-btn:hover { background: var(--brand-navy-hover); border-color: var(--brand-navy-hover); }
.mini-btn:disabled { opacity: 0.5; cursor: default; }
.mini-btn.ghost {
  background: transparent;
  color: var(--text-muted);
  border-color: var(--border);
}
.mini-btn.ghost:hover { background: var(--surface-2); color: var(--text); border-color: var(--border-strong); }
.id-name {
  margin-top: 10px;
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.id-sub {
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
}

/* 栏目条索引 */
.p-index {
  margin-top: 14px;
  display: grid;
  gap: 1px;
}
.index-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 38px;
  padding: 0 8px;
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: left;
}
.index-row:hover {
  background: var(--surface);
  color: var(--text);
}
.index-row.on {
  background: var(--surface);
  color: var(--accent-ink);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(16, 22, 51, 0.07);
}
.index-no {
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
}
.index-row.on .index-no {
  color: var(--accent);
}
.index-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* —— 分区 —— */
.p-main {
  min-width: 0;
}
.p-sec {
  padding: 18px 20px 8px;
  border-radius: 12px;
  scroll-margin-top: 12px;
}
.p-sec + .p-sec {
  margin-top: 8px;
}
/* 分区淡彩：6% 浓度，只铺底色，不抢文字（颜色的第二层） */
.tint-blue {
  background: var(--tint-blue);
}
.sec-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.sec-head h2 {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 16px;
  padding: 13px 0;
  border-bottom: 1px solid var(--border);
  align-items: center;
}
.row-last {
  border-bottom: none;
}
.row-k {
  font-size: 12px;
  color: var(--text-faint);
  letter-spacing: 0.02em;
}
.row-v {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}
.row-text {
  font-size: 14px;
  color: var(--text);
}
.row-count {
  font-size: 24px;
  font-weight: 500;
  line-height: 1;
  color: var(--data);
  letter-spacing: -0.02em;
}
.hint {
  font-size: 11px;
  color: var(--text-faint);
}
.link {
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
  padding: 4px 2px;
}
.link:hover {
  color: var(--accent-hover);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.link.quiet {
  color: var(--text-faint);
}
.link.danger {
  color: var(--danger);
}
.inline-input {
  min-width: 180px;
  height: 32px;
  padding: 0 10px;
  font-size: 14px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: 8px;
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.note {
  padding: 12px 0 10px;
  font-size: 12px;
  line-height: 1.75;
  color: var(--text-muted);
  max-width: 62ch;
}
.p-error {
  padding: 6px 0 10px;
  font-size: 12px;
  color: var(--danger);
}
.p-foot {
  margin-top: 20px;
  padding-left: 4px;
  font-size: 11px;
  color: var(--text-faint);
  letter-spacing: 0.03em;
}

/* 分段控件（主题三档） */
.seg {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface-2);
}
.seg-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 10px;
  border-radius: 7px;
  font-size: 12px;
  color: var(--text-muted);
}
.seg-btn:hover {
  color: var(--text);
}
.seg-btn.on {
  background: var(--surface);
  color: var(--accent-ink);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(16, 22, 51, 0.08);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* 个人资料与对话区共用浅蓝底色和白色内容面。 */
.profile { background: var(--bg); }
.p-scroll { padding-top: 32px; }
.p-head, .p-body { max-width: 1120px; margin-inline: auto; }
.head-row { max-width: none; padding-bottom: 22px; }
.head-row h1 { font-size: 26px; }
.head-mark { color: var(--accent-ink); border-color: var(--accent-border); background: var(--accent-soft); border-radius: 999px; padding: 5px 10px; letter-spacing: 0; }
.p-body { grid-template-columns: 230px minmax(0, 1fr); gap: 24px; }
.id-card { padding: 26px 18px 20px; border: 1px solid var(--border); border-radius: 16px; background: var(--surface); text-align: center; }
.id-face { width: 76px; height: 76px; margin: auto; border-radius: 50%; }
.id-name { margin-top: 14px; font-size: 16px; font-weight: 600; }
.id-sub { margin-top: 5px; font-size: 12px; }
.p-index { padding: 8px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); }
.index-row.on { background: var(--accent-soft); box-shadow: none; }
.p-sec { padding: 20px 24px 8px; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; }
.p-sec + .p-sec { margin-top: 12px; }
.sec-head { align-items: center; }
.row-count { font-size: 18px; color: var(--accent-ink); }
.inline-input { max-width: 100%; }
.profile--embedded .p-scroll { padding-top: 24px; }
.profile--embedded .p-head, .profile--embedded .p-body { padding-left: 20px; padding-right: 20px; }
.profile--embedded .head-row { padding-right: 42px; }
.profile--embedded .head-row h1 { font-size: 22px; }
.profile--embedded .head-mark { display: none; }
.profile--embedded .p-body { grid-template-columns: minmax(0, 1fr); gap: 16px; }
.profile--embedded .p-aside { position: static; }
.profile--embedded .p-index { display: flex; overflow-x: auto; gap: 4px; }
.profile--embedded .index-row { flex-shrink: 0; min-height: 44px; }
.profile--embedded .p-sec { padding: 18px 16px 8px; }
.profile--embedded .row { grid-template-columns: minmax(0, 1fr); gap: 6px; }

@media (max-width: 880px) {
  .p-head,
  .p-body {
    padding-left: 20px;
    padding-right: 20px;
  }
  .p-body {
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
  }
  .p-aside {
    position: static;
  }
  .p-index {
    display: flex;
    overflow-x: auto;
    gap: 6px;
  }
  .p-scroll { padding-top: 20px; }
  .id-card { padding: 20px; }
  .p-sec { padding: 18px 16px 8px; }
  .index-row { min-height: 44px; }
  .index-row {
    flex-shrink: 0;
    border: 1px solid var(--border);
  }
  .row {
    grid-template-columns: minmax(0, 1fr);
    gap: 6px;
    align-items: start;
  }
}
</style>
