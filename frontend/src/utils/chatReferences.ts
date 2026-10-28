import type { RetrievedDoc } from '../types/chat'
import { renderMarkdown } from './markdown'

/** Render only server-known references; code and ordinary Markdown links stay literal. */
export function renderCitedAnswer(text: string, sources: RetrievedDoc[]) {
  const template = document.createElement('template')
  template.innerHTML = renderMarkdown(text)
  const known = new Map(sources.filter(source => source.citation_id).map(source => [source.citation_id!, source]))
  // Citation controls can only be created here, not by raw answer HTML.
  template.content.querySelectorAll('[data-citation-id]').forEach(node => node.removeAttribute('data-citation-id'))
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  let node = walker.nextNode()
  while (node) {
    if (!node.parentElement?.closest('code,pre,a,.katex')) nodes.push(node as Text)
    node = walker.nextNode()
  }
  for (const textNode of nodes) {
    const value = textNode.textContent || ''
    const fragment = document.createDocumentFragment()
    let end = 0
    for (const match of value.matchAll(/(?<!\[)\[(S[1-9]\d*)\](?![\](])/g)) {
      const source = known.get(match[1])
      if (!source) continue
      fragment.append(value.slice(end, match.index!))
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'answer-citation'
      button.dataset.citationId = match[1]
      button.textContent = match[1].slice(1)
      button.setAttribute('aria-label', `查看来源 ${match[1].slice(1)}：${source.title || source.source}`)
      fragment.append(button)
      end = match.index! + match[0].length
    }
    if (end) { fragment.append(value.slice(end)); textNode.replaceWith(fragment) }
  }
  return template.innerHTML
}

/** Keep document structure and table cells; source HTML cannot load resources or execute code. */
export function renderSourceText(text: string, rowNumber?: number | null) {
  const template = document.createElement('template')
  template.innerHTML = renderMarkdown(text)
  template.content.querySelectorAll('script,style,iframe,object,embed,img,svg,math,link,meta,template').forEach(node => node.remove())
  const allowed = new Set(['P', 'BR', 'DIV', 'SPAN', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'UL', 'OL', 'LI',
    'STRONG', 'B', 'EM', 'I', 'S', 'DEL', 'BLOCKQUOTE', 'PRE', 'CODE', 'HR', 'A', 'TABLE', 'THEAD',
    'TBODY', 'TFOOT', 'TR', 'TH', 'TD', 'CAPTION', 'SUP', 'SUB'])
  template.content.querySelectorAll('*').forEach(element => {
    if (!allowed.has(element.tagName)) { element.replaceWith(...Array.from(element.childNodes)); return }
    const href = element.tagName === 'A' ? element.getAttribute('href') : ''
    for (const attr of Array.from(element.attributes)) {
      if (['colspan', 'rowspan', 'start'].includes(attr.name) && /^\d{1,3}$/.test(attr.value)) continue
      element.removeAttribute(attr.name)
    }
    if (href && /^(https?:\/\/|mailto:)/i.test(href.trim())) {
      element.setAttribute('href', href.trim())
      element.setAttribute('target', '_blank')
      element.setAttribute('rel', 'noopener noreferrer')
    }
  })
  if (rowNumber && rowNumber > 0) {
    template.content.querySelectorAll('tr')[rowNumber - 1]?.classList.add('source-row-hit')
  }
  return template.innerHTML
}
