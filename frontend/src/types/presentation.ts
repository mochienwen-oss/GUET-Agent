/** 一条要点。**必须**要么挂 `evidence_ids`，要么写明 `gap`（缺口）——
 *  两者都没有的内容页要点，后端的机械质检会判 `missing_source` 并让整个任务失败。 */
export interface PptClaim {
  text: string
  evidence_ids?: string[]
  gap?: string | null
  /** 对比页用：left / right */
  column?: string | null
  kind?: 'fact' | 'suggestion' | 'analogy'
}

export interface PptSlide {
  slide_id?: string
  purpose?: string
  kind: string
  title: string
  subtitle?: string
  items?: string[]
  left_title?: string
  right_title?: string
  claims?: PptClaim[]
  notes?: PptClaim[]
  speaker_notes?: string
  chart?: { categories: string[]; values: string[]; unit?: string; label?: string }
  table?: { columns: string[]; rows: (string | number | null)[][] }
}

/** 被引用到的来源（后端 `routers/ppt.py::_review` 投影，正文已截断）。 */
export interface PptSource {
  title?: string
  heading_path?: string[]
  line?: number | null
  text?: string
  scope?: string
  date?: string
  url?: string
  published_at?: string | null
  accessed_at?: string | null
  reading_status?: string
  content_scope?: string
  used?: boolean
}

export interface PptChatAttachment {
  material_id: string
  title: string
}

export interface PptChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at?: string
  attachments?: PptChatAttachment[]
  updated_pages?: (PptUpdatedPage | string | number)[]
  outline_available?: boolean
}

export interface PptUpdatedPage {
  slide_id?: string
  page_number?: number
  title?: string
  removed?: boolean
}

export interface PptTask {
  taskId: string
  title?: string
  stage: string
  background_running?: boolean
  pause_requested?: boolean
  can_pause?: boolean
  background_error?: string | null
  can_continue?: boolean
  renderer?: string
  pending_user?: { confirmation_id?: string | null; stage?: string; question?: string; proposal?: string; action_label?: string | null; action_message?: string | null; action_hint?: string | null }
  progress?: { steps?: number; tool_calls: number; open: number; label?: string; model_requests?: number; input_tokens?: number; output_tokens?: number; total_tokens?: number; cached_tokens?: number; reasoning_tokens?: number }
  issues?: { detail?: string }[]
  chat_messages?: PptChatMessage[]
  outline?: string
  live_outline?: string
  live_pages?: { slide_id: string; file: string; title: string; preview_url: string; version: string }[]
  selected_slide_id?: string | null
  question?: string
  pages?: number
  revision?: number
  workflow_version?: number
  materials?: PptMaterial[]
  brief?: PptBrief
  previews?: Record<string, string>
  stale_slide_ids?: string[]
  revision_error?: string
  /** 逐页内容 —— `draft` 与 `ready` 两种态都带回，供人工审阅后再下载。 */
  slides?: PptSlide[]
  sources?: Record<string, PptSource>
  /** **只有确认之后才有**（后端 `POST /ppt/task/{id}/confirm` 的返回）。 */
  ready?: PptDownload
  error?: { stage: string; error: string; recoverable: boolean }
}

export interface PublicCandidate {
  url: string
  question: string
  school_policy: boolean
}

export interface PptMaterial {
  material_id: string
  title: string
  columns?: string[]
  row_count?: number
  source_type?: string
  url?: string
  final_url?: string
  date?: string | null
  accessed_at?: string | null
  published_at?: string | null
  reading_status?: string
  content_scope?: string
}

export interface PptMessageRequest {
  message: string
  expected_revision: number
  selected_slide_id: string | null
  attachments?: PptChatAttachment[]
  confirmation_id?: string
  approved?: boolean
}

export interface PptMaterialInput {
  title: string
  text: string
  source_type: string
}

export interface PptBrief {
  goal: string
  audience: string
  outcome?: string
  page_count?: number | null
  duration_minutes?: number | null
  must_keep?: string[]
  style?: string
  material_policy?: string
  public_candidates?: PublicCandidate[]
}

export interface PptDownload {
  download_url: string
  filename: string
  pages: number
}

export interface PptTaskStatus extends Omit<PptTask, 'taskId' | 'ready'> {
  task_id: string
  revision: number
  materials: PptMaterial[]
  issues?: { detail?: string }[]
}

export interface PptTaskSummary {
  task_id: string
  title: string
  stage: string
  background_running?: boolean
  pages: number
  revision: number
  updated_at: string
  conversation_id: number | null
}

export interface PptAnalysisRequest {
  material_id: string
  operation: string
  field: string | null
  group_by: string | null
  denominator_field: string | null
  denominator_aggregation: string | null
  filters: Record<string, string>
  unit: string
  chart_type: string
}

export interface PptAnalysisResult {
  categories: string[]
  values: string[]
  unit: string
}

export interface PptRevisionRequest {
  expected_revision?: number
  feedback: string
  target_ids: string[] | null
  action: string
}

/** 页面里的单个可编辑文字块；id 由后端按 SVG 内顺序生成。 */
export interface PptPageText {
  id: string
  text: string
  size: string
  weight: string
}

export interface PptPageTexts {
  slide_id: string
  title: string
  texts: PptPageText[]
}

export interface PptTextEdit {
  id: string
  text: string
  original?: string
}
