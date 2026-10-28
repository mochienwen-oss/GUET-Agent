<template><div class="ppt-markdown" v-html="html" /></template>

<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps<{ content: string }>()
function escapeHtmlOutsideLiterals(text: string) {
  // 代码与公式由已有渲染器转义；保留其中的 <、>，避免改变公式含义。
  const literals = /(\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g
  let result = '', end = 0
  for (const match of text.matchAll(literals)) {
    const index = match.index || 0
    result += text.slice(end, index).replace(/</g, '&lt;') + match[0]
    end = index + match[0].length
  }
  return result + text.slice(end).replace(/</g, '&lt;')
}
function safeLink(value: string, image = false) {
  try {
    const url = new URL(value, window.location.href)
    return (image ? ['http:', 'https:'] : ['http:', 'https:', 'mailto:']).includes(url.protocol) ? url.href : ''
  } catch { return '' }
}
const html = computed(() => {
  // 原始 HTML 仅作为文字；模板内容保持惰性，外部图片不会在净化期间加载。
  const template = document.createElement('template')
  template.innerHTML = renderMarkdown(escapeHtmlOutsideLiterals(props.content))
  template.content.querySelectorAll('script,style,iframe,object,embed,form,input,button,link,meta').forEach(node => node.remove())
  template.content.querySelectorAll('img').forEach(image => {
    const link = document.createElement('a'), url = safeLink(image.getAttribute('src') || '', true)
    link.textContent = image.getAttribute('alt') ? `图片：${image.getAttribute('alt')}` : '查看图片'
    if (url) link.setAttribute('href', url)
    image.replaceWith(link)
  })
  template.content.querySelectorAll('*').forEach(element => {
    for (const attribute of [...element.attributes]) {
      if (/^on/i.test(attribute.name) || ['src', 'srcset', 'srcdoc', 'formaction', 'xlink:href'].includes(attribute.name)) element.removeAttribute(attribute.name)
    }
  })
  template.content.querySelectorAll('a').forEach(link => {
    const href = link.getAttribute('href'), url = href ? safeLink(href) : ''
    if (url) { link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer' }
    else link.removeAttribute('href')
  })
  template.content.querySelectorAll('table').forEach(table => {
    const wrapper = document.createElement('div'); wrapper.className = 'markdown-table'
    table.replaceWith(wrapper); wrapper.append(table)
  })
  return template.innerHTML
})
</script>

<style scoped>
.ppt-markdown { overflow-wrap: anywhere; font-size: 14px; line-height: 1.8; }
.ppt-markdown :deep(> :first-child) { margin-top: 0; }.ppt-markdown :deep(> :last-child) { margin-bottom: 0; }
.ppt-markdown :deep(p), .ppt-markdown :deep(ul), .ppt-markdown :deep(ol), .ppt-markdown :deep(blockquote), .ppt-markdown :deep(pre), .ppt-markdown :deep(.markdown-table) { margin: 12px 0; }
.ppt-markdown :deep(h1), .ppt-markdown :deep(h2), .ppt-markdown :deep(h3), .ppt-markdown :deep(h4) { line-height: 1.5; font-weight: 600; margin: 24px 0 10px; }
.ppt-markdown :deep(h1) { font-size: 22px; }.ppt-markdown :deep(h2) { font-size: 19px; }.ppt-markdown :deep(h3) { font-size: 16px; }.ppt-markdown :deep(h4) { font-size: 14px; }
.ppt-markdown :deep(ul), .ppt-markdown :deep(ol) { padding-left: 24px; }.ppt-markdown :deep(li + li) { margin-top: 5px; }.ppt-markdown :deep(li > p) { margin: 5px 0; }
.ppt-markdown :deep(a[href]) { color: var(--accent-ink); text-decoration: underline; text-underline-offset: 3px; }.ppt-markdown :deep(a:focus-visible) { outline: 2px solid var(--accent); outline-offset: 3px; }
.ppt-markdown :deep(blockquote) { padding: 8px 14px; border-left: 1px solid var(--border-strong); color: var(--text-muted); background: var(--surface-2); }
.ppt-markdown :deep(pre) { padding: 12px 14px; overflow-x: auto; background: var(--surface-2); border-radius: 6px; white-space: pre; }.ppt-markdown :deep(code) { font-family: Consolas, monospace; font-size: .9em; }.ppt-markdown :deep(:not(pre) > code) { background: var(--surface-2); padding: 2px 4px; border-radius: 3px; }
.ppt-markdown :deep(.markdown-table) { overflow-x: auto; }.ppt-markdown :deep(table) { min-width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }.ppt-markdown :deep(th), .ppt-markdown :deep(td) { border: 1px solid var(--border); padding: 8px 10px; min-width: 90px; }.ppt-markdown :deep(th) { background: var(--surface-2); font-weight: 600; }
.ppt-markdown :deep(hr) { border: 0; border-top: 1px solid var(--border); margin: 20px 0; }.ppt-markdown :deep(.katex-display) { overflow-x: auto; overflow-y: hidden; padding: 4px 0; }
</style>
