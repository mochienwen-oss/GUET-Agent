// api/files.ts —— 会话文件上传、受权限保护的下载、回答导出
//
// 下载/导出都走 axios 取 blob：后端用 Bearer 头鉴权，
// 直接给 <a href> 不会带令牌，会 401。
import api from '.'
import type { ChatFileRef } from '../types/chat'

export async function uploadChatFile(conversationId: number, file: File): Promise<ChatFileRef> {
  const form = new FormData()
  form.append('file', file)
  // ★ 不要手写 Content-Type: multipart/form-data —— 那样会丢掉 boundary 参数，
  //   服务端无法切分表单，上传会静默失败或 422。交给浏览器/axios 自动带 boundary。
  const { data } = await api.post(`/conversations/${conversationId}/files`, form, {
    timeout: 60000,
  })
  return data.file as ChatFileRef
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Safari 需要下一轮再释放
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function filenameFrom(disposition: string | undefined, fallback: string) {
  if (!disposition) return fallback
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (utf8) {
    try { return decodeURIComponent(utf8[1]) } catch { return fallback }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  return plain ? plain[1] : fallback
}

export async function downloadChatFile(file: ChatFileRef) {
  const response = await api.get(`/files/${file.file_id}/download`, {
    responseType: 'blob', timeout: 60000,
  })
  saveBlob(response.data as Blob, filenameFrom(response.headers['content-disposition'], file.filename))
}

