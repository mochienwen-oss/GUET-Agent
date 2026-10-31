import api from '.'
import type {
  PptAnalysisRequest, PptAnalysisResult, PptBrief, PptDownload, PptMaterialInput,
  PptPageTexts, PptRevisionRequest, PptTaskStatus, PptTaskSummary, PptTextEdit, PublicCandidate, PptMessageRequest,
} from '../types/presentation'

const taskPath = (taskId: string) => `/ppt/task/${encodeURIComponent(taskId)}`


export async function renamePresentation(taskId: string, title: string) {
  return (await api.patch<PptTaskStatus>(`${taskPath(taskId)}/title`, { title })).data
}

export async function deletePresentation(taskId: string) {
  return (await api.delete<{ task_id: string; deleted: boolean }>(taskPath(taskId))).data
}

/** Creating an empty workspace does not start model execution. */
export async function createPresentationChat(message = '') {
  return (await api.post<PptTaskStatus>('/ppt/chat', { message })).data
}

export async function sendPresentationMessage(taskId: string, body: PptMessageRequest) {
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/messages`, body, { timeout: 190000 })).data
}

export async function selectPresentationSlide(taskId: string, slideId: string | null) {
  return (await api.patch<PptTaskStatus>(`${taskPath(taskId)}/selection`, { slide_id: slideId })).data
}

export async function listPresentations() {
  return (await api.get<PptTaskSummary[]>('/ppt/tasks')).data
}

export async function getPresentation(taskId: string) {
  return (await api.get<PptTaskStatus>(taskPath(taskId))).data
}

export async function createPresentation(body: {
  conversation_id: number | null; brief: PptBrief; materials: PptMaterialInput[]
}) {
  return (await api.post<PptTaskStatus>('/ppt/tasks', body)).data
}

export async function updatePresentationBrief(taskId: string, brief: PptBrief, revision: number) {
  return (await api.patch<PptTaskStatus>(`${taskPath(taskId)}/brief`, {
    brief, expected_revision: revision,
  })).data
}

export async function addPresentationText(taskId: string, material: PptMaterialInput) {
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/materials/text`, material)).data
}

export async function uploadPresentationMaterials(taskId: string, files: File[]) {
  const form = new FormData()
  files.forEach(file => form.append('files', file))
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/materials`, form)).data
}

export async function analysePresentation(taskId: string, body: PptAnalysisRequest) {
  return (await api.post<PptAnalysisResult>(`${taskPath(taskId)}/analysis`, body)).data
}

export async function addPresentationCandidate(taskId: string, candidate: PublicCandidate) {
  await api.post(`${taskPath(taskId)}/public-candidates`, candidate)
}

export async function generatePresentation(taskId: string, revision: number) {
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/generate`, {
    expected_revision: revision,
  }, { timeout: 190000 })).data
}

export async function continuePresentation(taskId: string, revision: number) {
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/continue`, {
    expected_revision: revision,
  }, { timeout: 190000 })).data
}


export async function pausePresentation(taskId: string, revision: number) {
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/pause`, {
    expected_revision: revision,
  })).data
}

export async function answerPresentationDesign(taskId: string, revision: number,
  confirmationId: string, approved: boolean, feedback: string) {
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/design-answer`, {
    expected_revision: revision, confirmation_id: confirmationId, approved, feedback,
  }, { timeout: 190000 })).data
}

export async function revisePresentation(taskId: string, body: PptRevisionRequest) {
  await api.post(`${taskPath(taskId)}/revise`, body, { timeout: 190000 })
}

export async function confirmPresentation(taskId: string, revision: number) {
  return (await api.post<PptDownload>(`${taskPath(taskId)}/confirm`, {
    expected_revision: revision,
  })).data
}

export async function getPresentationDownload(taskId: string) {
  return (await api.post<PptDownload>(`${taskPath(taskId)}/download-token`)).data
}

export async function getPresentationBlob(path: string) {
  return (await api.get<Blob>(path.replace(/^\/api/, ''), { responseType: 'blob' })).data
}

export function presentationResumeUrl(taskId: string) {
  return `/api${taskPath(taskId)}/resume`
}


/** 直接读取某页 SVG 里的文字，用于工作区就地改字（不调用模型）。 */
export async function getPageTexts(taskId: string, slideId: string) {
  return (await api.get<PptPageTexts>(`${taskPath(taskId)}/page/${encodeURIComponent(slideId)}/texts`)).data
}

/** 保存改字并重新导出；失败时后端会还原页面。 */
export async function savePageTexts(taskId: string, slideId: string, revision: number, edits: PptTextEdit[]) {
  return (await api.post<PptTaskStatus>(
    `${taskPath(taskId)}/page/${encodeURIComponent(slideId)}/texts`,
    { expected_revision: revision, edits }, { timeout: 190000 })).data
}

/** 按新的页面顺序重排并重新导出；slide_id 保持不变。 */
export async function reorderPresentationPages(taskId: string, revision: number, order: string[]) {
  return (await api.post<PptTaskStatus>(`${taskPath(taskId)}/order`,
    { expected_revision: revision, order }, { timeout: 190000 })).data
}
