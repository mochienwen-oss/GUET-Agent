import api from './index'
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
  return (await api.get<Message[]>(`/conversations/${id}/messages`)).data
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
