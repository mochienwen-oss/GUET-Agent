import api from '.'
import type { Conversation, Message, SourcePreview } from '../types/chat'

export async function getMessageSource(conversationId: number, messageId: number, citationId: string, full = false) {
  return (await api.get<SourcePreview>(
    `/conversations/${conversationId}/messages/${messageId}/sources/${encodeURIComponent(citationId)}`,
    { params: { full } }
  )).data
}

export async function listConversations() {
  return (await api.get<Conversation[]>('/conversations')).data
}

export async function getMessages(id: number) {
  const data = (await api.get<any[]>(`/conversations/${id}/messages`)).data
  // 后端把校园查询结果存进 answer_json，这里映射成 Message.campus_result 供渲染。
  return data.map((m: any) => {
    const campus = m.answer && m.answer.kind === 'campus_result'
      ? { date: m.answer.date, units: m.answer.units || [], query: m.answer.query, summary: m.answer.summary }
      : undefined
    return { ...m, campus_result: campus }
  })
}

export async function createConversation(title: string) {
  return (await api.post<Conversation>('/conversations', { title })).data
}

export async function renameConversation(id: number, title: string) {
  await api.patch(`/conversations/${id}`, { title })
}

export async function deleteConversation(id: number) {
  await api.delete(`/conversations/${id}`)
}
