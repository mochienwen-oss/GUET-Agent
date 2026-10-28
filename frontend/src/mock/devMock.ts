// mock/devMock.ts —— 开发环境专用的前端 Mock（无后端时预览界面与交互）
// 只在 `import.meta.env.DEV`（vite dev）下生效，生产构建下为 no-op。
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import api from '../api'

const MOCK_FLAG = 'guet_mock_preview'

let enabled = false
let originalAdapter: typeof api.defaults.adapter | undefined
let originalFetch: typeof window.fetch | undefined

// 内存中的假数据（预览模式期间有效，刷新即重置）
interface MockConversation {
  id: number
  title: string
  updated_at: string
}
interface MockMessage {
  role: 'user' | 'assistant'
  content: string
}
let mockConversations: MockConversation[] = []
const mockMessages = new Map<number, MockMessage[]>()

function makeResponse<T>(config: InternalAxiosRequestConfig, data: T): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  } as AxiosResponse<T>
}

function buildAnswer(question: string): string {
  const q = question || '……'
  return [
    '### 演示回复（前端 Mock）',
    '',
    `你问的是：**${q}**`,
    '',
    '这是「无后端」下的模拟回答，用来预览 Markdown 渲染、表格美化与消息操作条。',
    '',
    '一张示例表格：',
    '',
    '| 服务 | 时间 | 地点 |',
    '| --- | --- | --- |',
    '| 图书馆 | 8:00-22:00 | 主校区北门 |',
    '| 食堂 | 6:30-20:30 | 一/二/三食堂 |',
    '',
    '> 接入后端后，这里会是 RAG 检索生成的正式回答。',
  ].join('\n')
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms))
}

function mockChatStream(answer: string): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const tokens = answer.match(/[\s\S]{1,4}/g) || []
      for (const token of tokens) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ content: token })}\n\n`))
        await sleep(18)
      }
      controller.enqueue(encoder.encode('data: [DONE]\n\n'))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

async function mockAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method || 'get').toLowerCase()
  const url = String(config.url || '')

  if (url === '/conversations' && method === 'get') {
    return makeResponse(config, mockConversations)
  }
  if (url === '/conversations' && method === 'post') {
    let title = ''
    try {
      title = JSON.parse(String(config.data)).title || ''
    } catch {
      /* ignore */
    }
    const conv: MockConversation = {
      id: Date.now(),
      title: title.slice(0, 30) || '新对话',
      updated_at: new Date().toISOString(),
    }
    mockConversations.unshift(conv)
    return makeResponse(config, conv)
  }

  const messagesMatch = url.match(/^\/conversations\/(\d+)\/messages$/)
  if (messagesMatch && method === 'get') {
    return makeResponse(config, mockMessages.get(Number(messagesMatch[1])) || [])
  }

  const convMatch = url.match(/^\/conversations\/(\d+)$/)
  if (convMatch && method === 'patch') {
    const id = Number(convMatch[1])
    let title = ''
    try {
      title = JSON.parse(String(config.data)).title || ''
    } catch {
      /* ignore */
    }
    const c = mockConversations.find((x) => x.id === id)
    if (c) {
      c.title = title
      c.updated_at = new Date().toISOString()
    }
    return makeResponse(config, c || { id, title })
  }
  if (convMatch && method === 'delete') {
    const id = Number(convMatch[1])
    mockConversations = mockConversations.filter((x) => x.id !== id)
    mockMessages.delete(id)
    return makeResponse(config, { ok: true })
  }

  if (url === '/admin/stats') {
    return makeResponse(config, {
      total_conversations: mockConversations.length,
      total_messages: Array.from(mockMessages.values()).reduce((n, arr) => n + arr.length, 0),
      document_count: 0,
      vector_count: 0,
    })
  }
  if (url === '/admin/documents' && method === 'get') {
    return makeResponse(config, [])
  }

  return Promise.reject(new Error(`预览模式（Mock）未模拟接口：${method.toUpperCase()} ${url}`))
}

function mockFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === 'string' ? input : (input as { url: string }).url
  if (url === '/api/chat') {
    let body: { message?: string; conversation_id?: number } = {}
    try {
      body = JSON.parse(String(init?.body ?? ''))
    } catch {
      /* ignore */
    }
    const question = body.message || ''
    const answer = buildAnswer(question)
    if (body.conversation_id != null) {
      const arr = mockMessages.get(body.conversation_id) || []
      arr.push({ role: 'user', content: question })
      arr.push({ role: 'assistant', content: answer })
      mockMessages.set(body.conversation_id, arr)
    }
    return Promise.resolve(mockChatStream(answer))
  }
  return (originalFetch as typeof fetch)(input, init)
}

export function enableDevMock(): void {
  if (!import.meta.env.DEV) return
  if (!enabled) {
    originalAdapter = api.defaults.adapter
    originalFetch = window.fetch
    api.defaults.adapter = mockAdapter
    window.fetch = mockFetch as typeof fetch
    enabled = true
  }
  localStorage.setItem(MOCK_FLAG, '1')
}

export function disableDevMock(): void {
  if (!enabled) return
  if (originalAdapter !== undefined) api.defaults.adapter = originalAdapter
  if (originalFetch !== undefined) window.fetch = originalFetch
  enabled = false
  localStorage.removeItem(MOCK_FLAG)
}

export function initDevMock(): void {
  if (import.meta.env.DEV && localStorage.getItem(MOCK_FLAG) === '1') {
    enableDevMock()
  }
}