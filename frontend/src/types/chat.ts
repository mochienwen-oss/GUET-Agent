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
  /** 本条消息关联的会话文件：用户上传的表格，或工具生成的表格结果。 */
  files?: ChatFileRef[]
  /** 校园空教室查询结果（桌面端调接口回传后，作为一条消息展示）。 */
  campus_result?: CampusResult
}

/** 校园空教室查询结果（结构化，前端按楼栋分组展示）。 */
export interface CampusResult {
  date: string
  units: string[]
  query?: { end_date?: string; weekdays?: string[]; building?: string; room?: string; room_type?: string; seats?: number }
  summary: {
    total: number
    buildings: Array<{
      name: string
      rooms: Array<{ code: string; name: string; type: string; seats: number; remark: string }>
    }>
  }
}

/** 会话内文件（不透明 id，下载必须带令牌走后端校验）。 */
export interface ChatFileRef {
  file_id: string
  filename: string
  size?: number
  role?: 'upload' | 'result'
}

/** 校园服务任务：只表示"该打开学校页面了"，不代表已查到结果。 */
export interface CampusAction {
  task_id: string
  service: string
  query?: {
    start_date?: string
    end_date?: string
    weekdays?: string[]
    units?: string[]
    building?: string
    room?: string
    room_type?: string
    date?: string
    period?: 'morning' | 'noon' | 'afternoon' | 'evening'
    campus_id?: number
    building_id?: string
    seats?: number
  }
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
  /** 表格工具产出的结果文件；与答案正文分开处理。 */
  file?: ChatFileRef
  /** 校园服务任务；桌面端据此打开学校页面。 */
  campus_action?: CampusAction
  ppt_task?: {
    renderer?: string
    task_id: string; stage: string; progress?: PptTask['progress']; question?: string
  }
  ppt_draft?: { task_id: string }
  ppt_error?: NonNullable<PptTask['error']> & { task_id?: string }
}
