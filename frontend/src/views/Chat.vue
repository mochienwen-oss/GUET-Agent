<template>
  <div class="chat-main">
      <div class="chat-messages" ref="messagesContainer" @scroll="trackMessagesScroll">
        <!-- 欢迎页 / 空状态 -->
        <div v-if="chat.messages.length === 0" class="welcome">
          <div class="welcome-in">
            <header class="hero">
              <span class="hero-ring" aria-hidden="true"></span>
              <span class="hero-ring hero-ring--inner" aria-hidden="true"></span>
              <div class="hero-row">
                <BrandLogo :size="52" />
                <div class="hero-text">
                  <span class="hero-title">SAI</span>
                  <span class="hero-kicker">校园智能助手</span>
                </div>
              </div>
              <p class="hero-sub">桂林电子科技大学 · 有问题，或者有想完成的任务，都可以从这里开始。</p>
            </header>

            <RouterLink v-if="chat.pptTask" class="ppt-workspace-link empty-task-link" :to="`/presentations/${chat.pptTask.taskId}`">
              <Icon name="presentation" :size="20" /><span><strong>{{ chat.pptTask.brief?.goal || '演示文稿制作任务' }}</strong><small>进入工作区继续制作与审阅</small></span><Icon name="chevronRight" :size="18" />
            </RouterLink>

            <div v-if="pendingFiles.length || uploadError" class="attach-list">
              <span v-for="(file, i) in pendingFiles" :key="i" class="attach-chip">
                <span class="attach-ic"><Icon name="file" :size="14" /></span>
                <span class="attach-text">
                  <span class="attach-name">{{ file.name }}</span>
                  <span class="attach-meta">{{ fileSize(file.size) }} · 待发送</span>
                </span>
                <button class="attach-x" type="button" title="移除" @click="pendingFiles.splice(i, 1)">
                  <Icon name="close" :size="12" />
                </button>
              </span>
              <span v-if="uploadError" class="attach-error">{{ uploadError }}</span>
            </div>
            <div class="composer-box">
            <div class="composer-tools" aria-label="快捷功能">
              <RouterLink class="composer-tool" to="/presentations"><span class="tool-icon tool-icon--ppt"><Icon name="presentation" :size="16" /></span><span>PPT 制作</span></RouterLink>
              <button class="composer-tool" type="button" :disabled="busy || uploading" @click="pickTable"><span class="tool-icon tool-icon--data"><Icon name="table" :size="16" /></span><span>数据处理</span></button>
            </div>
            <div class="composer">
              <button class="attach" type="button" title="上传 Excel / CSV"
                :disabled="chat.streaming || chat.pptRunning || uploading"
                @click="filePicker?.click()">
                <Icon :name="uploading ? 'refresh' : 'upload'" :size="17" />
              </button>
              <textarea
                v-model="input"
                rows="1"
                placeholder="输入问题或描述你的任务…"
                @keydown.enter.exact.prevent="sendWithFiles()"
              ></textarea>
              <!-- 生成中同一个键切换为「停止」：不再需要在气泡下方找暂停按钮 -->
              <button
                class="send"
                :class="{ stopping: busy }"
                :disabled="uploading || (!busy && !input.trim())"
                :aria-label="busy ? '停止生成' : '发送'"
                :title="busy ? '停止生成' : '发送'"
                @click="busy ? stopStreaming() : sendWithFiles()"
              >
                <Icon :name="busy ? 'stop' : 'send'" :size="17" />
              </button>
            </div>
            </div>
            <p v-if="capHint" class="cap-hint">{{ capHint }}</p>

          </div>
        </div>

        <!-- 消息列表 -->
        <template v-else>
          <div class="thread">
            <MessageBubble
              v-for="(msg, i) in chat.messages"
              :key="i"
              :msg="msg"
              :index="i"
              :streaming="chat.streaming"
              :streaming-index="chat.streaming ? chat.messages.length - 1 : -1"
              :last-assistant-index="lastAssistantIndex"
              @regenerate="regenerate"
              @open-source="openSource(msg, $event)"
            />

            <!-- 校园服务进度：只说真实页面状态，不承诺"已经查到" -->
            <div v-if="campus.active" class="campus-step" :class="campus.status">
              <span class="step-avatar"><Icon name="bot" :size="15" /></span>
              <div class="step-body">
                <p class="step-text">{{ campus.statusHint }}</p>
                <p class="step-status">
                  <span class="dots" :class="{ running: campus.awaiting }"><i /><i /><i /></span>
                  <span>{{ campus.statusText }}</span>
                  <span v-if="campus.detail" class="step-detail">· {{ campus.detail }}</span>
                  <span v-if="campus.service" class="step-service">{{ campus.serviceName }}</span>
                  <button v-if="campus.available && !campus.visible && campus.taskId" type="button"
                    class="step-link" @click="campus.open(campus.taskId, campus.service)">重新打开学校页面</button>
                </p>
              </div>
            </div>

            <RouterLink v-if="chat.pptTask" class="ppt-workspace-link" :to="`/presentations/${chat.pptTask.taskId}`">
              <Icon name="presentation" :size="20" />
              <span><strong>{{ chat.pptTask.brief?.goal || '演示文稿制作任务' }}</strong><small>在独立工作区查看方案、审阅页面与下载</small></span>
              <Icon name="chevronRight" :size="18" />
            </RouterLink>

          </div>
        </template>
      </div>

      <div v-if="chat.messages.length > 0" class="dock">
        <div class="dock-in">
          <div v-if="pendingFiles.length || uploadError" class="attach-list">
            <span v-for="(file, i) in pendingFiles" :key="i" class="attach-chip">
              <span class="attach-ic"><Icon name="file" :size="14" /></span>
              <span class="attach-text">
                <span class="attach-name">{{ file.name }}</span>
                <span class="attach-meta">{{ fileSize(file.size) }} · 待发送</span>
              </span>
              <button class="attach-x" type="button" title="移除" @click="pendingFiles.splice(i, 1)">
                <Icon name="close" :size="12" />
              </button>
            </span>
            <span v-if="uploadError" class="attach-error">{{ uploadError }}</span>
          </div>
          <div class="composer-box">
          <div class="composer-tools" aria-label="快捷功能">
            <RouterLink class="composer-tool" to="/presentations"><span class="tool-icon tool-icon--ppt"><Icon name="presentation" :size="16" /></span><span>PPT 制作</span></RouterLink>
            <button class="composer-tool" type="button" :disabled="busy || uploading" @click="pickTable"><span class="tool-icon tool-icon--data"><Icon name="table" :size="16" /></span><span>数据处理</span></button>
          </div>
          <div class="composer">
            <button class="attach" type="button" title="上传 Excel / CSV"
              :disabled="chat.streaming || chat.pptRunning || uploading"
              @click="filePicker?.click()">
              <Icon :name="uploading ? 'refresh' : 'upload'" :size="17" />
            </button>
            <textarea
              v-model="input"
              rows="1"
              placeholder="输入你的问题，回车发送，Shift+回车换行…"
              :disabled="chat.streaming || chat.pptRunning"
              @keydown.enter.exact.prevent="sendWithFiles()"
            ></textarea>
            <button
              class="send"
              :class="{ stopping: busy }"
              :disabled="uploading || (!busy && !input.trim())"
              :aria-label="busy ? '停止生成' : '发送'"
              :title="busy ? '停止生成' : '发送'"
              @click="busy ? stopStreaming() : sendWithFiles()"
            >
              <Icon :name="busy ? 'stop' : 'send'" :size="17" />
            </button>
          </div>
          </div>
          <p v-if="capHint" class="cap-hint">{{ capHint }}</p>
          <div v-if="chat.pptTask?.stage === 'clarify' && !chat.pptTask.error" class="ppt-clarify">
            <span>回答上方问题后，选择继续制作；普通发送会作为新问题处理。</span>
            <button class="ppt-action" type="button" :disabled="chat.streaming || !input.trim()" @click="continuePpt">
              继续制作 PPT
            </button>
          </div>
          <div class="dock-hint">重要信息请核对相关来源</div>
        </div>
      </div>
    <ChatSourceReader v-if="sourceSelection" :key="`${chat.currentId}-${sourceSelection.messageId}-${sourceSelection.source.citation_id || sourceSelection.source.source}`" :conversation-id="chat.currentId" :message-id="sourceSelection.messageId" :source="sourceSelection.source" :sources="sourceSelection.sources" @close="sourceSelection = null" />
    <!-- 上传入口：欢迎页与对话页共用这一个 input，保证任何时候都能选文件 -->
    <input ref="filePicker" class="attach-input" type="file" accept=".xlsx,.csv" multiple
      @change="onPickFiles" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useChatStore } from '../store/chat'
import MessageBubble from '../components/chat/MessageBubble.vue'
import BrandLogo from '../components/common/BrandLogo.vue'
import Icon from '../components/common/Icon.vue'
import ChatSourceReader from '../components/chat/ChatSourceReader.vue'
import { createConversation } from '../api/chat'
import { uploadChatFile } from '../api/files'
import type { ChatFileRef, Message, RetrievedDoc } from '../types/chat'
import { useCampusStore } from '../store/campus'
import { useChatStream } from '../composables/useChatStream'

const chat = useChatStore()
const campus = useCampusStore()
const input = ref('')
const sourceSelection = ref<{ messageId?: number; source: RetrievedDoc; sources: RetrievedDoc[] } | null>(null)
function openSource(message: Message, source: RetrievedDoc) {
  sourceSelection.value = { messageId: message.id, source, sources: message.retrieved_docs || [source] }
}
watch(() => chat.currentId, () => { sourceSelection.value = null })
const messagesContainer = ref<HTMLElement>()
const followLatest = ref(true)
const { send, stopStreaming, regenerate } = useChatStream(input, scrollToBottom)

/** 正在产出：发送键统一切换为停止键（回答流式 / PPT 任务都算）。 */
const busy = computed(() => chat.streaming || chat.pptRunning)

// —— 表格附件：先在本地收集，发送时再上传，避免产生空会话 ——
const filePicker = ref<HTMLInputElement>()
const pendingFiles = ref<File[]>([])
const uploading = ref(false)
const uploadError = ref('')
const capHint = ref('')

/** 待发文件的大小：B / KB / MB，只保留有意义的小数位。 */
function fileSize(bytes: number) {
  const size = Number(bytes || 0)
  if (size < 1024) return `${size} B`
  if (size < 1048576) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`
  return `${(size / 1048576).toFixed(1)} MB`
}

function pickTable() {
  if (chat.streaming || chat.pptRunning || uploading.value) return
  capHint.value = '选择 Excel/CSV 后，用一句话说明要算什么（如：按班统计平均分和缺分）。'
  filePicker.value?.click()
}

function onPickFiles(event: Event) {
  const target = event.target as HTMLInputElement
  const picked = Array.from(target.files || [])
  target.value = ''
  uploadError.value = ''
  for (const file of picked) {
    if (!/\.(xlsx|csv)$/i.test(file.name)) { uploadError.value = '只支持 .xlsx 与 .csv 文件'; continue }
    if (file.size > 20 * 1024 * 1024) { uploadError.value = `${file.name} 超过 20 MiB`; continue }
    if (pendingFiles.value.length >= 3) { uploadError.value = '一次最多 3 个文件'; break }
    pendingFiles.value.push(file)
  }
}

async function sendWithFiles() {
  if (chat.streaming || chat.pptRunning || uploading.value) return
  capHint.value = ''
  if (!pendingFiles.value.length) { await send(); return }
  const question = input.value.trim()
  if (!question) { uploadError.value = '请先描述要处理的表格需求'; return }
  uploading.value = true
  uploadError.value = ''
  try {
    let conversationId = chat.currentId
    if (conversationId === null) {
      const conversation = await createConversation(question)
      conversationId = Number(conversation.id)
      chat.currentId = conversationId
    }
    const refs: ChatFileRef[] = []
    for (const file of pendingFiles.value) {
      refs.push(await uploadChatFile(conversationId, file))
    }
    pendingFiles.value = []
    await send(question, undefined, refs)
  } catch (error: any) {
    uploadError.value = error?.message || '上传失败'
  } finally {
    uploading.value = false
  }
}

const lastAssistantIndex = computed(() => {
  for (let i = chat.messages.length - 1; i >= 0; i--) {
    if (chat.messages[i].role === 'assistant') return i
  }
  return -1
})

function trackMessagesScroll() {
  const element = messagesContainer.value
  if (element) followLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
}

async function scrollToBottom(force = false) {
  if (force) followLatest.value = true
  if (!followLatest.value) return
  await nextTick()
  if (followLatest.value && messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

watch(() => chat.currentId, () => { void scrollToBottom(true) })

function continuePpt() {
  const task = chat.pptTask
  if (task?.stage === 'clarify') void send(input.value, task.taskId)
}

</script>

<style scoped>
.chat-main {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  /* 中性底色承托白色对话与输入区域。 */
  background: var(--bg);
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 32px 24px 8px;
}
/* 消息列固定 760 居中，**不随窗口拉伸** —— 4K 屏上一行 40 个字是读不下去的 */
.thread {
  max-width: 760px;
  margin: 0 auto;
}

/* —— 欢迎页 —— */
.welcome {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 24px;
}
.welcome-in {
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
}
/* 欢迎横幅：与登录页同源的深海蓝。它是登录后的第二印象，
   用同一套语言（深蓝底 + 白字 + 环 + 橙点）把"品牌"钉进脑子里。 */
/* 欢迎横幅：浅淡紫底融入页面（--accent-soft），不再用大色块硬压。
   院徽随之回到「浅底深线」的官方用法 —— 官网浅色场景就是深色院徽，
   细线比白线在深底上锐利得多；深色主题下 accent-soft 自动变深、
   院徽自动反白，两套主题都不需要特殊处理。 */
.hero {
  position: relative;
  overflow: hidden;
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  color: var(--text);
  border-radius: 18px;
  padding: 28px 30px 24px;
  margin-bottom: 18px;
}
/* 环从右缘外溢，被裁一半 —— 与登录页同一手法，两页之间形成视觉接力 */
.hero-ring {
  position: absolute;
  border-radius: 50%;
  border: 1px solid var(--accent-border);
  width: 380px;
  height: 380px;
  right: -160px;
  top: -120px;
  pointer-events: none;
}
.hero-ring--inner {
  width: 240px;
  height: 240px;
  right: -90px;
  top: -60px;
  border-color: var(--border);
}
/* 环上一点：橙色 + 微光环，与登录页环上一点、结果块数据色是同一个语言 */
.hero-ring::after {
  content: '';
  position: absolute;
  left: 16%;
  bottom: 10%;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--data-fill, #f5a623);
  box-shadow: 0 0 0 4px rgba(245, 166, 35, 0.2);
}
.hero-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
}
.hero-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hero-title {
  font-size: 30px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.05;
  color: var(--text);
}
.hero-kicker {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: var(--accent-ink);
}
.hero-sub {
  position: relative;
  font-size: 15px;
  color: var(--text-muted);
  margin: 16px 0 0;
}
.cap-hint {
  margin: 10px 4px 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-muted);
}
/* —— 校园服务进度：跟在对话里，像助手的一步操作 —— */
.campus-step {
  display: flex;
  gap: 12px;
  margin: -16px 0 28px;
}
.step-avatar {
  width: 32px;
  height: 32px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--text-muted);
}
.step-body {
  min-width: 0;
  padding-top: 2px;
}
.step-text {
  margin: 0 0 8px;
  font-size: 15px;
  line-height: 1.7;
  color: var(--text);
}
.step-status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}
.dots {
  display: inline-flex;
  gap: 3px;
}
.dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.4;
}
.dots.running i {
  animation: step-bounce 1.2s ease-in-out infinite;
}
.dots.running i:nth-child(2) {
  animation-delay: 0.15s;
}
.dots.running i:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes step-bounce {
  0%, 60%, 100% { opacity: 0.35; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
.campus-step.at_query_page .dots i,
.campus-step.filled .dots i {
  opacity: 1;
}
.campus-step.failed .dots i {
  background: var(--danger, var(--danger));
}
.step-service {
  margin-left: 4px;
  padding: 1px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
}
.step-link {
  margin-left: 4px;
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--accent-ink);
  font-size: 12px;
  cursor: pointer;
}
.step-link:hover {
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
.step-detail {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 320px;
}
/* —— 空教室查询结果 —— */
.campus-result {
  margin-top: 12px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-2);
}
.result-head {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--text);
}
.result-head strong {
  font-size: 15px;
  color: var(--accent-ink);
}
.result-date {
  margin-left: 8px;
  color: var(--text-muted);
  font-size: 12px;
}
.result-groups {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.result-group .group-name {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
}
.result-group .group-name span {
  margin-left: 4px;
  font-weight: 400;
  color: var(--text-faint);
}
.room-chip {
  display: inline-block;
  margin: 0 4px 4px 0;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
}
.room-chip:hover {
  border-color: var(--accent-border);
  color: var(--accent-ink);
}
/* —— 输入岛：页面里唯一常驻浮起的东西 —— */
.composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 10px 10px 16px;
  background: transparent;
}
.composer-box {
  padding: 10px 10px 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow-md);
  transition: border-color var(--base) var(--ease), box-shadow var(--base) var(--ease);
}
.composer-box:focus-within {
  border-color: var(--accent-border);
  box-shadow: var(--shadow-md), 0 0 0 3px var(--accent-soft);
}
.composer-tools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.composer-tool { min-height: 38px; display: inline-flex; align-items: center; gap: 8px; padding: 4px 11px 4px 5px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface); color: var(--text); font-size: 13px; text-decoration: none; }
.composer-tool:hover:not(:disabled) { border-color: var(--accent-border); background: var(--surface-2); }
.composer-tool:disabled { opacity: .55; cursor: default; }
.composer-tool:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tool-icon { width: 28px; height: 28px; display: grid; place-items: center; border-radius: 7px; }
.tool-icon--ppt { background: var(--data-soft); color: var(--data); }
.tool-icon--data { background: var(--accent-soft); color: var(--accent-ink); }
.attach {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  margin-bottom: 2px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text-muted, var(--text));
  cursor: pointer;
  flex: none;
}
.attach:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent-border);
}
.attach:disabled {
  opacity: 0.5;
  cursor: default;
}
.attach-input {
  display: none;
}
/* 待发文件：贴在输入框**上方**一行，图标 + 文件名 + 体积 + 移除。
   放在上方而不是下方，是因为它属于「接下来要发的东西」，
   和输入框是一个整体，发送后这些卡片的对应物会落到消息下面。 */
.attach-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 12px;
}
.attach-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 260px;
  padding: 6px 8px 6px 6px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-2);
}
.attach-ic {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--accent-ink);
}
.attach-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.35;
}
.attach-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.attach-meta {
  font-size: 11px;
  color: var(--text-faint);
}
.attach-chip .attach-x {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-faint);
  cursor: pointer;
}
.attach-x:hover {
  background: var(--surface);
  color: var(--danger);
}
.attach-error {
  color: var(--danger);
}
.composer textarea {
  flex: 1;
  min-width: 0;
  resize: none;
  font-size: 16px;
  line-height: 1.6;
  min-height: 38px;
  max-height: 168px;
  padding: 8px 0;
  color: var(--text);
  field-sizing: content;
}
.composer textarea:disabled {
  opacity: 0.6;
}
.send {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border-radius: 12px;
  background: var(--accent);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.send:hover:not(:disabled) {
  background: var(--accent-hover);
}
.send:disabled {
  background: var(--surface-2);
  color: var(--text-faint);
}
/* 停止态：从实心主色变为描边 + 危险色图标，与「发送」明确区分 */
.send.stopping {
  background: var(--surface);
  color: var(--danger);
  border: 1px solid var(--border-strong);
}
.send.stopping:hover {
  background: var(--seal-soft);
  border-color: var(--danger);
}

/* —— 底部输入区 —— */
.dock {
  flex-shrink: 0;
  padding: 12px 24px 24px;
}
.dock-in {
  max-width: 760px;
  margin: 0 auto;
}
/* 演示文稿的状态与审阅面板整体移入 PptReview.vue —— 那里是它唯一的使用处。
   这里只剩输入岛里的「继续制作」一行与共用的 .ppt-action（定义在 style.css）。 */
.ppt-clarify {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  color: var(--text-muted);
  font-size: 13px;
}
.dock-hint {
  text-align: center;
  font-size: 12px;
  color: var(--text-faint);
  margin-top: 9px;
}

.ppt-workspace-link { display: flex; align-items: center; gap: 16px; margin: 16px 0 24px 44px; padding: 18px 0; border-block: 1px solid var(--border); color: var(--text); text-decoration: none; }
.ppt-workspace-link span { flex: 1; min-width: 0; }
.ppt-workspace-link strong { font-size: 14px; font-weight: 600; }
.ppt-workspace-link small { display: block; color: var(--text-muted); font-size: 12px; }
.ppt-workspace-link:hover { color: var(--accent-ink); }
.empty-task-link { margin-left: 0; }
@media(max-width: 600px) { .chat-messages { padding: 16px 12px 8px; } .welcome { padding: 16px 4px; } .hero { padding: 22px 20px; } .hero-sub { line-height: 1.6; } .dock { padding: 8px 12px 10px; } .composer-box { border-radius: 14px; } .composer { padding: 8px 2px 4px; } .composer-tool { min-height: 44px; } .attach, .send { width: 44px; height: 44px; } .dock-hint { font-size: 11px; } .ppt-workspace-link { margin-left: 0; } .campus-step { margin-left: 0; } }
</style>
