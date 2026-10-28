import type { PptTask } from './presentation'

export interface RetrievedDoc {
  source: string
  /** 剥掉公文外壳与文号的**简短真名**（后端 `citations.short_title` 生成）。
   *  ★ 与答案末尾注入的「资料来源：《…》」**同一口径** —— 原先标签显示带文号的
   *  原始文件名、注入行显示剥壳后的真名，同一份文档在一屏里出现两种写法。
   *  显示优先用它，`source` 仍保留（它是索引里的身份，不要改）。 */
  title?: string
  score?: number
  text?: string
  citation_id?: string
  cited?: boolean
  doc_id?: string
  chunk_id?: string
  heading_path?: string[]
  source_span?: number[]
  block_type?: string
  row_number?: number | null
  metadata?: SourceMetadata
}

export interface SourceMetadata {
  items?: SourceMetadataItem[]
  published_year?: number | null
  applicable_cohort?: string | null
  audience?: string
  coverage?: string
  basis?: string
  maintained?: boolean
}

export interface SourceMetadataEvidence {
  source: 'content' | 'title' | 'filename' | 'catalog' | 'manual'
  text: string
  start?: number
  end?: number
  line?: number
}

export interface SourceMetadataItem {
  key: string
  label: string
  value: string
  state: 'confirmed' | 'conflict'
  detail?: boolean
  evidence: SourceMetadataEvidence[]
}

export interface SourcePreview {
  citation_id: string
  title: string
  text: string
  excerpt: string
  metadata: SourceMetadata
  heading_path: string[]
  row_number?: number | null
  block_type?: string
  state: 'located' | 'unlocated' | 'unavailable'
  message: string
  view: 'full' | 'excerpt'
  location?: { start: number; end: number; line: number; row_number?: number | null } | null
}

export interface Message {
  id?: number
  role: 'user' | 'assistant'
  content: string
  retrieved_docs?: RetrievedDoc[]
}

export interface Conversation {
  id: number
  title: string
  updated_at?: string
}

export interface ChatStreamEvent {
  message_id?: number
  retrieved_docs?: RetrievedDoc[]
  content?: string
  replace?: string
  title?: string
  error?: string
  ppt_task?: {
    renderer?: string
    task_id: string; stage: string; progress?: PptTask['progress']; question?: string
  }
  ppt_draft?: { task_id: string }
  ppt_error?: NonNullable<PptTask['error']> & { task_id?: string }
}
