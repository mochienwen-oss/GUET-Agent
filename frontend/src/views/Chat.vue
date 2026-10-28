<template>
  <div class="chat-main">
      <div class="chat-messages" ref="messagesContainer" @scroll="trackMessagesScroll">
        <!-- 欢迎页 / 空状态 -->
        <div v-if="chat.messages.length === 0" class="welcome">
          <div class="welcome-in">
            <div class="hero">
              <BrandLogo :size="56" />
              <span class="hero-title">SAI</span>
            </div>
            <p class="hero-sub">桂林电子科技大学 · 智能问答，随时为你解答校园疑问</p>

            <RouterLink v-if="chat.pptTask" class="ppt-workspace-link empty-task-link" :to="`/presentations/${chat.pptTask.taskId}`">
              <Icon name="presentation" :size="20" /><span><strong>{{ chat.pptTask.brief?.goal || '演示文稿制作任务' }}</strong><small>进入工作区继续制作与审阅</small></span><Icon name="chevronRight" :size="18" />
            </RouterLink>

            <div class="composer">
              <textarea
                v-model="input"
                rows="1"
                placeholder="问我任何关于学校的问题…"
                @keydown.enter.exact.prevent="send()"
              ></textarea>
              <button
                class="send"
                :disabled="!input.trim()"
                aria-label="发送"
                @click="send()"
              >
                <Icon name="send" :size="17" />
              </button>
            </div>

            <!-- 建议项：两列细线清单，不做卡片网格（卡片是"偷懒的容器"） -->
            <div class="suggestions">
              <button v-for="s in samples" :key="s" class="suggestion" @click="send(s)">
                <span>{{ s }}</span>
                <Icon name="chevronRight" :size="14" />
              </button>
            </div>
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
              @stop="stopStreaming"
              @regenerate="regenerate"
              @open-source="openSource(msg, $event)"
            />

            <RouterLink v-if="chat.pptTask" class="ppt-workspace-link" :to="`/presentations/${chat.pptTask.taskId}`">
              <Icon name="presentation" :size="20" />
              <span><strong>{{ chat.pptTask.brief?.goal || '演示文稿制作任务' }}</strong><small>在独立工作区查看方案、审阅页面与下载</small></span>
              <Icon name="chevronRight" :size="18" />
            </RouterLink>

            <div v-if="showFollowUps" class="follow-ups">
              <div class="follow-ups-label">你可能还想问</div>
              <div class="follow-up-chips">
                <button v-for="q in followUps" :key="q" class="follow-up-chip" @click="send(q)">
                  {{ q }}
                </button>
              </div>
            </div>
          </div>
        </template>
      </div>

      <div v-if="chat.messages.length > 0" class="dock">
        <div class="dock-in">
          <div class="composer">
            <textarea
              v-model="input"
              rows="1"
              placeholder="输入你的问题，回车发送，Shift+回车换行…"
              :disabled="chat.streaming || chat.pptRunning"
              @keydown.enter.exact.prevent="send()"
            ></textarea>
            <button
              class="send"
              :disabled="chat.streaming || chat.pptRunning || !input.trim()"
              :aria-label="chat.streaming ? '生成中' : '发送'"
              @click="send()"
            >
              <Icon :name="chat.streaming ? 'stop' : 'send'" :size="17" />
            </button>
          </div>
          <div v-if="chat.pptTask?.stage === 'clarify' && !chat.pptTask.error" class="ppt-clarify">
            <span>回答上方问题后，选择继续制作；普通发送会作为新问题处理。</span>
            <button class="ppt-action" type="button" :disabled="chat.streaming || !input.trim()" @click="continuePpt">
              继续制作 PPT
            </button>
          </div>
          <div class="dock-hint">回答依据校内制度文件生成，重要事项请以主管部门答复为准</div>
        </div>
      </div>
    <ChatSourceReader v-if="sourceSelection" :key="`${chat.currentId}-${sourceSelection.messageId}-${sourceSelection.source.citation_id || sourceSelection.source.source}`" :conversation-id="chat.currentId" :message-id="sourceSelection.messageId" :source="sourceSelection.source" :sources="sourceSelection.sources" @close="sourceSelection = null" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useChatStore } from '../store/chat'
import MessageBubble from '../components/MessageBubble.vue'
import BrandLogo from '../components/BrandLogo.vue'
import Icon from '../components/Icon.vue'
import ChatSourceReader from '../components/ChatSourceReader.vue'
import type { Message, RetrievedDoc } from '../types/chat'
import { useChatStream } from '../composables/useChatStream'

const chat = useChatStore()
const input = ref('')
const sourceSelection = ref<{ messageId?: number; source: RetrievedDoc; sources: RetrievedDoc[] } | null>(null)
function openSource(message: Message, source: RetrievedDoc) {
  sourceSelection.value = { messageId: message.id, source, sources: message.retrieved_docs || [source] }
}
watch(() => chat.currentId, () => { sourceSelection.value = null })
const messagesContainer = ref<HTMLElement>()
const followLatest = ref(true)
const { send, stopStreaming, regenerate } = useChatStream(input, scrollToBottom)
const samples = [
  '学校的图书馆开放时间是几点？',
  '奖学金的申请条件有哪些？',
  '转专业需要什么流程和条件？',
  '宿舍水电或设施损坏怎么报修？',
  '毕业论文答辩有哪些注意事项？',
  '课程退选和补选截止到什么时候？',
]

const lastAssistantIndex = computed(() => {
  for (let i = chat.messages.length - 1; i >= 0; i--) {
    if (chat.messages[i].role === 'assistant') return i
  }
  return -1
})

const showFollowUps = computed(
  () => !chat.streaming && chat.messages.length > 0 && lastAssistantIndex.value === chat.messages.length - 1
)
const followUps = computed(() => {
  let q = ''
  for (let i = chat.messages.length - 1; i >= 0; i--) {
    if (chat.messages[i].role === 'user') {
      q = chat.messages[i].content
      break
    }
  }
  return recommendFollowUps(q)
})

function recommendFollowUps(question: string): string[] {
  const rules: [RegExp, string[]][] = [
    [/图书馆|借书|自习|座位/, ['图书馆怎么预约座位？', '图书超期了怎么续借？', '图书馆节假日开放时间？']],
    [/奖学金|助学金|奖助/, ['奖学金多久评一次？', '助学金需要什么材料？', '国家励志奖学金有哪些条件？']],
    [/转专业|换专业/, ['转专业后学分怎么认定？', '转专业申请截止时间？', '转专业需要面试吗？']],
    [/宿舍|报修|水电|后勤|食堂/, ['食堂的营业时间是？', '校园卡丢失怎么补办？', '宿舍网络怎么开通？']],
    [/答辩|论文|毕业|查重/, ['论文查重率要求是多少？', '答辩不通过怎么办？', '毕业论文格式要求？']],
    [/选课|退选|补选|课程/, ['选课系统的网址是什么？', '课程冲突怎么办？', '重修怎么申请？']],
  ]
  for (const [re, follows] of rules) {
    if (re.test(question)) return follows
  }
  return ['学校的作息时间是怎样的？', '校历在哪里查看？', '如何联系教务处？']
}

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
.chat-main { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; }
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
.hero {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 8px;
}
.hero-title {
  font-size: 32px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.1;
}
.hero-sub {
  font-size: 15px;
  color: var(--text-muted);
  margin-bottom: 32px;
}

/* —— 输入岛：页面里唯一常驻浮起的东西 —— */
.composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 10px 10px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  box-shadow: var(--shadow-md);
  transition: border-color var(--base) var(--ease), box-shadow var(--base) var(--ease);
}
.composer:focus-within {
  border-color: var(--accent-border);
  box-shadow: var(--shadow-md), 0 0 0 3px var(--accent-soft);
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

/* —— 建议项：细线分行的两列清单 —— */
.suggestions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  border-top: 1px solid var(--border);
  margin-top: 28px;
}
.suggestion {
  display: flex;
  align-items: center;
  gap: 10px;
  text-align: left;
  padding: 15px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
  color: var(--text-muted);
  background: transparent;
}
.suggestion:nth-child(odd) {
  border-right: 1px solid var(--border);
}
.suggestion:hover {
  background: var(--accent-soft);
  color: var(--accent-ink);
}
.suggestion span {
  flex: 1;
}
.suggestion .ic {
  opacity: 0;
  transition: opacity var(--fast) var(--ease), transform var(--fast) var(--ease);
}
.suggestion:hover .ic {
  opacity: 1;
  transform: translateX(2px);
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

/* —— 推荐追问 —— */
.follow-ups {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
  margin-left: 44px; /* 头像 32 + gap 12，与消息正文对齐 */
}
.follow-ups-label {
  font-size: 12px;
  color: var(--text-faint);
}
.follow-up-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.follow-up-chip {
  padding: 7px 13px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-muted);
  font-size: 13px;
}
.follow-up-chip:hover {
  border-color: var(--accent-border);
  color: var(--accent-ink);
  background: var(--accent-soft);
}

@media (max-width: 820px) {
  .suggestions {
    grid-template-columns: 1fr;
  }
  .suggestion:nth-child(odd) {
    border-right: none;
  }
}
.ppt-workspace-link { display: flex; align-items: center; gap: 16px; margin: 16px 0 24px 44px; padding: 18px 0; border-block: 1px solid var(--border); color: var(--text); text-decoration: none; }
.ppt-workspace-link span { flex: 1; min-width: 0; }
.ppt-workspace-link strong { font-size: 14px; font-weight: 600; }
.ppt-workspace-link small { display: block; color: var(--text-muted); font-size: 12px; }
.ppt-workspace-link:hover { color: var(--accent-ink); }
.empty-task-link { margin-left: 0; }
@media(max-width: 600px) { .chat-messages { padding: 16px 12px 8px; } .welcome { padding: 16px 4px; } .dock { padding: 12px; } .ppt-workspace-link { margin-left: 0; } }
</style>
