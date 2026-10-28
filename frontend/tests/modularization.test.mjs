import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { beforeEach, test } from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createRenderer, defineComponent, nextTick, ref } from 'vue'

// Node 的原生 TypeScript 支持需要补全项目中由 Vite 解析的相对导入。
registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context)
    } catch (error) {
      if (!specifier.startsWith('.') || !['ERR_MODULE_NOT_FOUND', 'ERR_UNSUPPORTED_DIR_IMPORT'].includes(error.code)) throw error
      try { return nextResolve(`${specifier}.ts`, context) }
      catch { return nextResolve(`${specifier}/index.ts`, context) }
    }
  },
})

const storage = new Map()
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
} })

const { default: api } = await import('../src/api/index.ts')
const { streamChat } = await import('../src/api/stream.ts')
const { useChatStore } = await import('../src/store/chat.ts')
const { useChatStream } = await import('../src/composables/useChatStream.ts')
const { usePresentationTask } = await import('../src/composables/usePresentationTask.ts')
const { usePptPreview } = await import('../src/composables/usePptPreview.ts')
const { generatePresentation, revisePresentation, uploadPresentationMaterials } = await import('../src/api/presentation.ts')

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

function streamResponse(text, chunkSize = 7) {
  const bytes = new TextEncoder().encode(text)
  return new Response(new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += chunkSize) controller.enqueue(bytes.slice(i, i + chunkSize))
      controller.close()
    },
  }))
}

function adapter(handler) {
  api.defaults.adapter = async config => ({
    data: await handler(config), config, status: 200, statusText: 'OK', headers: {},
  })
}

beforeEach(() => {
  storage.clear()
  setActivePinia(createPinia())
})

test('SSE handles split UTF-8, ignores malformed frames and requires DONE', async t => {
  const events = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/chat')
    assert.equal(options.headers.Authorization, 'Bearer local-token')
    assert.deepEqual(JSON.parse(options.body), { message: '问题', conversation_id: 8, ppt_task_id: 'p1' })
    return streamResponse('data: {"content":"中文增量"}\n\ndata: invalid\n\ndata: {"replace":"最终正文"}\n\ndata: [DONE]\n\n')
  })
  const request = { conversationId: 8, message: '问题', token: 'local-token',
    signal: new AbortController().signal, pptTaskId: 'p1' }
  await streamChat(request, async event => { events.push(event) })
  assert.deepEqual(events, [{ content: '中文增量' }, { replace: '最终正文' }])
  globalThis.fetch = async () => streamResponse('data: {"content":"未完成"}\n\n')
  await assert.rejects(streamChat(request, async () => {}), /连接已中断/)
})

test('SSE resume keeps the endpoint, empty body and HTTP errors', async t => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/ppt/task/p1/resume')
    assert.equal(options.body, undefined)
    return new Response('{"error":"当前版本不可续跑"}', { status: 409 })
  })
  await assert.rejects(streamChat({ conversationId: 8, message: '', token: '',
    signal: new AbortController().signal, resumeUrl: '/api/ppt/task/p1/resume' }, async () => {}), /当前版本不可续跑/)
})

test('chat applies authoritative replacement and stays busy until DONE', async t => {
  adapter(() => [])
  const chat = useChatStore()
  chat.currentId = 8
  chat.conversations = [{ id: 8, title: '原题目' }]
  chat.messages = [{ role: 'assistant', content: '' }]
  const replaced = deferred(), started = deferred()
  let controller
  const response = new Response(new ReadableStream({ start(value) { controller = value } }))
  t.mock.method(globalThis, 'fetch', async () => { started.resolve(); return response })
  const operations = useChatStream(ref(''), async () => {
    if (chat.messages[0].content === '权威正文') replaced.resolve()
  })
  const running = operations.runStream(8, '问题', 0)
  await started.promise
  controller.enqueue(new TextEncoder().encode('data: {"content":"草稿","replace":"权威正文","title":"新题目"}\n\n'))
  await replaced.promise
  assert.equal(chat.messages[0].content, '权威正文')
  assert.equal(chat.conversations[0].title, '新题目')
  assert.equal(chat.streaming, true)
  controller.enqueue(new TextEncoder().encode('data: [DONE]\n\n'))
  controller.close()
  await running
  assert.equal(chat.streaming, false)
})

test('stopping a stream aborts it and pauses a running PPT', async t => {
  const chat = useChatStore()
  chat.currentId = 8
  chat.messages = [{ role: 'assistant', content: '' }]
  chat.pptTask = { taskId: 'p1', stage: 'generating' }
  chat.pptRunning = true
  let signal
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    signal = options.signal
    return new Response(new ReadableStream({
      start(controller) {
        signal.addEventListener('abort', () => controller.error(new DOMException('Stopped', 'AbortError')))
      },
    }))
  })
  const operations = useChatStream(ref(''), async () => {})
  const running = operations.runStream(8, '问题', 0)
  operations.stopStreaming()
  await running
  assert.equal(signal.aborted, true)
  assert.equal(chat.messages[0].content, '（已停止生成）')
  assert.equal(chat.pptTask.stage, 'paused')
  assert.equal(chat.pptRunning, false)
  assert.equal(chat.streaming, false)
})

test('PPT confirmation sends revision; failure retains the reviewed draft', async () => {
  const chat = useChatStore()
  const slides = [{ kind: 'cover', title: '本地草稿' }]
  chat.pptTask = { taskId: 'p1', stage: 'draft', revision: 3, slides }
  const operations = usePresentationTask(async () => {}, async () => {})
  adapter(config => {
    assert.equal(config.url, '/ppt/task/p1/confirm')
    assert.deepEqual(JSON.parse(config.data), { expected_revision: 3 })
    throw Object.assign(new Error('草稿版本已变化'), {
      response: { status: 409, data: { error: '草稿版本已变化' } },
    })
  })
  await operations.confirmPpt()
  assert.equal(chat.pptTask.stage, 'draft')
  assert.equal(chat.pptTask.slides[0].title, '本地草稿')
  assert.match(chat.pptTask.error.error, /草稿版本已变化/)
  assert.equal(operations.pptBusy.value, false)
  adapter(() => ({ download_url: '/api/ppt/download/token', filename: 'p1.pptx', pages: 3 }))
  await operations.confirmPpt()
  assert.equal(chat.pptTask.stage, 'ready')
  assert.equal(chat.pptTask.ready.filename, 'p1.pptx')
})

test('PPT restore requests a download token only for a confirmed task', async () => {
  const requests = []
  let stage = 'draft'
  adapter(config => {
    requests.push(config.url)
    if (config.url.endsWith('download-token')) return { download_url: '/api/ppt/download/token', filename: 'p1.pptx', pages: 3 }
    return { task_id: 'p1', stage, revision: 2, materials: [], slides: [] }
  })
  const chat = useChatStore()
  await chat.syncPpt('p1')
  assert.deepEqual(requests, ['/ppt/task/p1'])
  assert.equal(chat.pptTask.ready, undefined)
  stage = 'ready'
  await chat.syncPpt('p1')
  assert.equal(requests.at(-1), '/ppt/task/p1/download-token')
  assert.equal(chat.pptRunning, false)
})

test('PPT API keeps multipart uploads and the long generation/revision timeout', async () => {
  const requests = []
  adapter(config => { requests.push(config); return {} })
  await uploadPresentationMaterials('p1', [new File(['材料'], '材料.txt')])
  assert.ok(requests[0].data instanceof FormData)
  assert.equal(requests[0].data.get('files').name, '材料.txt')
  await generatePresentation('p1', 4)
  await revisePresentation('p1', { expected_revision: 4, feedback: '精简', target_ids: ['s2'], action: 'replace' })
  assert.equal(requests[1].timeout, 190000)
  assert.equal(requests[2].timeout, 190000)
  assert.deepEqual(JSON.parse(requests[2].data).target_ids, ['s2'])
})

test('PPT download uses the confirmed filename and releases its object URL', async t => {
  const chat = useChatStore()
  chat.pptTask = { taskId: 'p1', stage: 'ready',
    ready: { download_url: '/api/ppt/download/token', filename: '校园介绍.pptx', pages: 3 } }
  adapter(config => {
    assert.equal(config.url, '/ppt/download/token')
    assert.equal(config.responseType, 'blob')
    return new Blob(['local-ppt'])
  })
  const link = { click() { this.clicked = true } }, revoked = []
  t.mock.method(URL, 'createObjectURL', () => 'blob:download')
  t.mock.method(URL, 'revokeObjectURL', url => { revoked.push(url) })
  const documentDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'document')
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { createElement: () => link } })
  t.after(() => {
    if (documentDescriptor) Object.defineProperty(globalThis, 'document', documentDescriptor)
    else delete globalThis.document
  })
  await usePresentationTask(async () => {}, async () => {}).downloadPpt()
  assert.equal(link.href, 'blob:download')
  assert.equal(link.download, '校园介绍.pptx')
  assert.equal(link.clicked, true)
  assert.deepEqual(revoked, ['blob:download'])
})

test('PPT resume refreshes readable drafts and streams only unfinished tasks', async () => {
  const chat = useChatStore()
  chat.currentId = 8
  chat.messages = [{ role: 'assistant', content: '' }]
  chat.pptTask = { taskId: 'p1', stage: 'draft' }
  let stage = 'draft'
  adapter(() => ({ task_id: 'p1', stage, revision: 2, materials: [] }))
  const resumed = []
  const operations = usePresentationTask(async (...args) => { resumed.push(args) }, async () => {})
  await operations.resumePpt()
  assert.equal(resumed.length, 0)
  stage = 'paused'
  await operations.resumePpt()
  assert.deepEqual(resumed, [[8, '', 0, undefined, '/api/ppt/task/p1/resume']])
})

test('preview revision switches discard stale images and release URLs on unmount', async t => {
  const requests = [], revoked = []
  adapter(config => {
    const result = deferred()
    requests.push(result)
    return result.promise
  })
  let sequence = 0
  t.mock.method(URL, 'createObjectURL', () => `blob:${++sequence}`)
  t.mock.method(URL, 'revokeObjectURL', url => { revoked.push(url) })
  const task = ref({ taskId: 'p1', revision: 1, previews: { s1: '/api/ppt/first' } })
  let preview
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null,
  })
  const app = renderer.createApp(defineComponent({
    setup() { preview = usePptPreview(() => task.value); return () => null },
  }))
  app.mount({})
  task.value = { taskId: 'p1', revision: 2, previews: { s2: '/api/ppt/second' } }
  await nextTick()
  requests[0].resolve(new Blob(['old']))
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(preview.previewImages.value, {})
  assert.deepEqual(revoked, ['blob:1'])
  requests[1].resolve(new Blob(['new']))
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(preview.previewImages.value, { s2: 'blob:2' })
  app.unmount()
  assert.deepEqual(revoked, ['blob:1', 'blob:2'])
})
