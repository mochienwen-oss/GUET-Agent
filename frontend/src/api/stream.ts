import type { ChatStreamEvent } from '../types/chat'

export interface ChatStreamRequest {
  conversationId: number
  message: string
  token: string
  signal: AbortSignal
  pptTaskId?: string
  resumeUrl?: string
}

/** 读取既有 SSE 协议；界面状态由调用者按事件更新。 */
export async function streamChat(
  request: ChatStreamRequest,
  onEvent: (event: ChatStreamEvent) => Promise<void>
) {
  const { conversationId, message, token, signal, pptTaskId, resumeUrl } = request
  const resp = await fetch(resumeUrl || '/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: resumeUrl ? undefined : JSON.stringify({ message, conversation_id: conversationId,
      ...(pptTaskId ? { ppt_task_id: pptTaskId } : {}) }),
    signal,
  })
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({ error: '请求失败' }))
    throw new Error(data.error || '请求失败')
  }
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const part of parts) {
      if (!part.startsWith('data:')) continue
      const data = part.slice(5).trim()
      if (data === '[DONE]') {
        completed = true
        continue
      }
      try {
        await onEvent(JSON.parse(data))
      } catch {
        /* 沿用既有行为：忽略无法解析或应用的片段。 */
      }
    }
  }
  if (!completed) throw new Error('连接已中断，本轮未确认完成，请重试')
}
