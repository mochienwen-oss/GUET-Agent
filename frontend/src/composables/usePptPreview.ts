import { onBeforeUnmount, ref, watch } from 'vue'
import { getPresentationBlob } from '../api/presentation'
import type { PptTask } from '../types/presentation'

/** 正式页面与制作中 SVG 独立缓存；单页更新就绪后才替换旧图。 */
export function usePptPreview(task: () => PptTask | null | undefined) {
  const previewImages = ref<Record<string, string>>({}), livePreviewImages = ref<Record<string, string>>({}), previewError = ref('')
  type Entry = { id: string; live: boolean; source: string; loaded: string; url: string; pending: boolean; error: boolean }
  const entries = new Map<string, Entry>()
  let taskId = '', generation = 0
  function publish() {
    const saved: Record<string, string> = {}, live: Record<string, string> = {}
    entries.forEach(entry => { if (entry.url) (entry.live ? live : saved)[entry.id] = entry.url })
    previewImages.value = saved; livePreviewImages.value = live
    previewError.value = [...entries.values()].some(entry => entry.error) ? '部分页面预览读取未完成，已保留可用预览。可稍后刷新制作进度。' : ''
  }
  function clear() { generation++; entries.forEach(entry => { if (entry.url) URL.revokeObjectURL(entry.url) }); entries.clear(); publish() }
  async function load(entry: Entry, path: string) {
    const current = generation, source = entry.source
    entry.pending = true
    try {
      const blob = await getPresentationBlob(path)
      if (current !== generation || entries.get(`${entry.live ? 'live' : 'saved'}:${entry.id}`) !== entry || entry.source !== source) return
      const url = URL.createObjectURL(blob)
      if (entry.url) URL.revokeObjectURL(entry.url)
      entry.url = url; entry.loaded = source; entry.error = false
    } catch { if (current === generation && entry.source === source) entry.error = true }
    finally { if (current === generation && entry.source === source) { entry.pending = false; publish() } }
  }
  watch(() => [task(), task()?.revision, JSON.stringify(task()?.previews), JSON.stringify(task()?.live_pages)] as const, ([value]) => {
    if ((value?.taskId || '') !== taskId) { clear(); taskId = value?.taskId || '' }
    const wanted = new Set<string>()
    const pages = [
      ...Object.entries(value?.previews || {}).map(([id, path]) => ({ id, path, version: String(value?.revision || 0), live: false })),
      ...(value?.live_pages || []).map(page => ({ id: page.slide_id, path: page.preview_url, version: page.version, live: true })),
    ]
    for (const page of pages) {
      const key = `${page.live ? 'live' : 'saved'}:${page.id}`, source = `${page.path}|${page.version}`
      wanted.add(key)
      let entry = entries.get(key)
      if (!entry) { entry = { id: page.id, live: page.live, source, loaded: '', url: '', pending: false, error: false }; entries.set(key, entry) }
      if (entry.source !== source) { entry.source = source; entry.pending = false; entry.error = false }
      if (entry.loaded !== source && !entry.pending) void load(entry, page.path)
    }
    entries.forEach((entry, key) => { if (!wanted.has(key)) { if (entry.url) URL.revokeObjectURL(entry.url); entries.delete(key) } })
    publish()
  }, { immediate: true })
  onBeforeUnmount(clear)
  return { previewImages, livePreviewImages, previewError }
}
