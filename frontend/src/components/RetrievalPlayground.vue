<template>
  <div class="playground">
    <div class="panel">
      <div class="panel-head">
        <div>
          <h2>检索调试台</h2>
          <p>
            输入问题查看知识库实际召回情况。检索参数（top_k / 精排阈值 / 评估器）由后端标定固定，
            此处只做效果查看。
          </p>
        </div>
      </div>

      <div class="pg-body">
        <div class="pg-query">
          <input
            v-model="query"
            placeholder="例如：转专业需要哪些条件？"
            @keydown.enter.prevent="run"
          />
          <button class="btn-accent" @click="run" :disabled="loading || !query.trim()">
            {{ loading ? '检索中…' : '检索' }}
          </button>
        </div>

        <p v-if="error" class="pg-error">
          <Icon name="alert" :size="14" /> {{ error }}
        </p>

        <div v-if="ran" class="pg-results">
          <div class="pg-summary" :class="tierClass">
            <Icon :name="tierIcon" :size="15" />
            <span>{{ tierLabel }} · {{ reasonLabel }} · 召回 <b class="num">{{ contexts.length }}</b> 个上下文</span>
          </div>

          <div v-if="contexts.length === 0" class="pg-empty">
            <strong>未命中</strong>
            向量库可能为空，或相似度低于阈值（低相关时不生成答案）
          </div>

          <div
            v-for="(c, i) in contexts"
            :key="i"
            class="hit"
            :class="{ weak: isWeak(c) }"
          >
            <div class="hit-head">
              <span class="hit-idx num">#{{ i + 1 }}</span>
              <span class="hit-source" :title="c.source">{{ c.source }}</span>
              <span v-if="c.rerank_score != null" class="hit-score rerank" title="rerank 精排分">
                精排 <b class="num">{{ c.rerank_score }}</b>
              </span>
              <span v-if="c.score != null" class="hit-score" title="dense 余弦分">
                余弦 <b class="num">{{ c.score }}</b>
              </span>
            </div>
            <div v-if="c.heading_path?.length" class="hit-path">{{ c.heading_path.join(' / ') }}</div>
            <div class="hit-text">{{ c.text }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import api from '../api'
import Icon from './Icon.vue'

interface Ctx {
  source: string
  snippet: string
  text: string
  heading_path?: string[]
  score: number | null
  rerank_score?: number | null
}

const query = ref('')
const contexts = ref<Ctx[]>([])
const tier = ref('')
const reason = ref('')
const ran = ref(false)
const loading = ref(false)
const error = ref('')

const tierLabel = computed(() =>
  tier.value === 'high' ? '高相关' : tier.value === 'medium' ? '中相关' : '低相关'
)
const tierIcon = computed(() =>
  tier.value === 'high' ? 'check' : tier.value === 'medium' ? 'search' : 'alert'
)
const tierClass = computed(() =>
  tier.value === 'high' ? 'good' : tier.value === 'medium' ? 'warn' : 'bad'
)

const REASON_MAP: Record<string, string> = {
  empty: '无召回结果',
  score_high: 'embedding 最佳分达到高阈值',
  score_low: 'embedding 最佳分低于低阈值',
  rerank_high: 'rerank 最佳分达到高阈值',
  rerank_low: 'rerank 最佳分低于低阈值',
  medium: '中档上下文返回，模型自行判断是否足够',
  grader_yes: '评估器判定相关',
  grader_no: '评估器判定不相关',
  grader_error: '评估器失败，返回中档上下文',
  no_dense_signal: '无 dense 信号，保守返回中档',
}
const reasonLabel = computed(() => {
  const r = reason.value || ''
  if (r.startsWith('absent:')) return '覆盖面判空：语料中没有该文档'
  return REASON_MAP[r] || r || '—'
})

function isWeak(c: Ctx): boolean {
  const s = c.rerank_score ?? c.score
  if (s == null) return false
  return s < 0.54 // 与后端 rerank_low 标定值一致，仅用于视觉高亮
}

async function run() {
  if (!query.value.trim() || loading.value) return
  loading.value = true
  error.value = ''
  try {
    // ★ 不传任何检索参数：一律用后端标定值（见 backend 冻结设置）
    const res = await api.post('/admin/test-retrieval', { query: query.value.trim() })
    contexts.value = res.data.contexts || []
    tier.value = res.data.tier || ''
    reason.value = res.data.reason || ''
    ran.value = true
  } catch (e: any) {
    error.value = e.message || '检索失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.playground {
  max-width: 940px;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
}
.panel-head {
  padding: 18px 22px;
  border-bottom: 1px solid var(--border);
}
.panel-head h2 {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin-bottom: 3px;
}
.panel-head p {
  font-size: 13px;
  color: var(--text-muted);
  line-height: 1.7;
}
.pg-body {
  padding: 20px 22px 24px;
}
.pg-query {
  display: flex;
  gap: 10px;
}
.pg-query input {
  flex: 1;
  height: 44px;
  padding: 0 14px;
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 15px;
}
.pg-query input::placeholder {
  color: var(--text-faint);
}
.pg-query input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.btn-accent {
  height: 44px;
  padding: 0 22px;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}
.btn-accent:hover:not(:disabled) {
  background: var(--accent-hover);
}
.btn-accent:disabled {
  background: var(--surface-2);
  color: var(--text-faint);
}
.pg-error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--danger);
  font-size: 13px;
  margin-top: 12px;
}
.pg-results {
  margin-top: 20px;
}
.pg-summary {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 14px;
}
.pg-summary.good {
  color: var(--success);
}
.pg-summary.warn {
  color: var(--warn);
}
.pg-summary.bad {
  color: var(--danger);
}
.pg-summary b {
  font-weight: 600;
}

.pg-empty {
  padding: 34px 20px;
  text-align: center;
  color: var(--text-faint);
  font-size: 13px;
  line-height: 1.7;
}
.pg-empty strong {
  display: block;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 4px;
}

/* ★ 命中块：**没有左侧彩色竖条**。
   改版前这里是「左边一条 3px 的强调色描边」，而"卡片一侧的彩色边"
   正是 impeccable 点名的头号 AI 痕迹 `side-tab`。改用名次序号 + 细线分隔。 */
.hit {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 13px 15px;
  margin-bottom: 10px;
}
.hit.weak {
  opacity: 0.72;
}
.hit-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 6px;
}
.hit-idx {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-ink);
  flex-shrink: 0;
}
.hit.weak .hit-idx {
  color: var(--text-faint);
}
.hit-source {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
  font-size: 13px;
}
.hit-score {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-faint);
}
.hit-score b {
  font-weight: 600;
  color: var(--text-muted);
}
.hit-score.rerank b {
  color: var(--warn);
}
.hit.weak .hit-score.rerank b {
  color: var(--danger);
}
.hit-path {
  font-size: 12px;
  color: var(--text-faint);
  margin-bottom: 6px;
}
.hit-text {
  font-size: 14px;
  line-height: 1.75;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>