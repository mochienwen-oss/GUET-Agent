<template>
  <div class="overview">
    <!-- ★ 原来是三张"大数字 + 小标签"的等宽卡片 —— 那是 impeccable 点名的
         `hero-metric` 模板（用数据卡片充当页面主体）。改成**一条细线分隔的指标行**：
         同样的信息量，但不再假装是 hero，也不再占掉整屏。 -->
    <div class="stats">
      <div class="stat">
        <span class="k">知识库文档</span>
        <span class="v num">{{ stats.document_count ?? '—' }}<small>篇</small></span>
      </div>
      <div class="stat">
        <span class="k">向量分块</span>
        <span class="v num">{{ stats.vector_count ?? '—' }}</span>
      </div>
      <div class="stat">
        <span class="k">总分块数</span>
        <span class="v num">{{ stats.total_chunks ?? '—' }}</span>
      </div>
      <div class="stat">
        <span class="k">分类</span>
        <span class="v num">{{ categories.length }}<small>个</small></span>
      </div>
    </div>

    <div class="cols">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>最近入库</h2>
            <p>按上传时间倒序</p>
          </div>
          <button class="link-btn" @click="$emit('navigate', 'docs')">
            全部 <Icon name="chevronRight" :size="13" />
          </button>
        </div>
        <ul v-if="recentDocs.length" class="rows">
          <li v-for="d in recentDocs" :key="d.id">
            <Icon name="file" :size="15" class="row-ic" />
            <span class="rt" :title="d.title">{{ d.title }}</span>
            <span class="rm num">{{ d.chunk_count }} 块</span>
          </li>
        </ul>
        <div v-else class="empty">
          <strong>知识库还是空的</strong>
          上传校规、制度或培养方案文件，即可作为问答依据
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>分类分布</h2>
            <p>按当前归属统计</p>
          </div>
          <button class="link-btn" @click="$emit('navigate', 'retrieval')">
            检索调试 <Icon name="chevronRight" :size="13" />
          </button>
        </div>
        <ul v-if="categories.length" class="rows">
          <li v-for="c in categories" :key="c.name">
            <span class="rt">{{ c.name }}</span>
            <span class="rm num">{{ c.count }} 篇</span>
          </li>
        </ul>
        <div v-else class="empty">
          <strong>还没有分类</strong>
          在知识库里把文档移动到分类即可
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import Icon from './Icon.vue'

const emit = defineEmits<{ (e: 'navigate', tab: 'docs' | 'retrieval'): void }>()

interface Doc {
  id: number
  title: string
  chunk_count: number
  status: string
  category?: string
}

const stats = ref<Record<string, number>>({})
const docs = ref<Doc[]>([])

// 分类分布从文档列表现算 —— 不额外请求，也不显示任何"其实不生效"的后端设置值
const categories = computed(() => {
  const m = new Map<string, number>()
  for (const d of docs.value) {
    const c = (d.category || '').trim() || '未分类'
    m.set(c, (m.get(c) || 0) + 1)
  }
  return [...m.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
})

const recentDocs = computed(() => docs.value.slice(0, 6))

async function loadStats() {
  const res = await api.get('/admin/stats')
  stats.value = res.data
}

async function loadDocs() {
  const res = await api.get('/admin/documents')
  docs.value = res.data || []
}

onMounted(() => {
  loadStats()
  loadDocs()
})
</script>

<style scoped>
.overview {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 指标行 */
.stats {
  display: flex;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  flex-wrap: wrap;
}
.stat {
  flex: 1;
  min-width: 150px;
  padding: 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-left: 1px solid var(--border);
}
.stat:first-child {
  border-left: none;
}
.stat .k {
  font-size: 12px;
  color: var(--text-faint);
  letter-spacing: 0.03em;
}
.stat .v {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.25;
}
.stat .v small {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-faint);
  margin-left: 5px;
  letter-spacing: 0;
}

/* 不对称双栏：主栏宽、副栏窄 */
.cols {
  display: grid;
  grid-template-columns: 1.55fr 1fr;
  gap: 20px;
  align-items: start;
}
@media (max-width: 1000px) {
  .cols {
    grid-template-columns: 1fr;
  }
}

.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.panel-head h2 {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.panel-head p {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 2px;
}
.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 13px;
  color: var(--accent-ink);
  padding: 6px 8px;
  border-radius: 6px;
  flex-shrink: 0;
}
.link-btn:hover {
  background: var(--accent-soft);
}

.rows {
  list-style: none;
}
.rows li {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 13px 20px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
}
.rows li:last-child {
  border-bottom: none;
}
.rows li:hover {
  background: var(--surface-2);
}
.row-ic {
  color: var(--text-faint);
}
.rt {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rm {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-faint);
}

/* 空状态：不是一块空白，而是告诉你下一步做什么 */
.empty {
  padding: 34px 20px;
  text-align: center;
  color: var(--text-faint);
  font-size: 13px;
  line-height: 1.7;
}
.empty strong {
  display: block;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 4px;
}
</style>