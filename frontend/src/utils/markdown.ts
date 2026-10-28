// utils/markdown.ts —— 统一的 Markdown 渲染
//
// ★ 2026-10-09 加 KaTeX：模型会把算式写成 LaTeX（`\[ … \]`、`\( … \)`、`$$ … $$`），
//   而 marked 默认把 `\[` 当**转义符**处理 —— 输出成 `[`，反斜杠就没了，
//   所以「先 marked 再用 auto-render 扫 DOM」这条路走不通（反斜杠已被吃掉）。
//   必须用**扩展**在转义之前截获，直接交给 KaTeX。实测原文：
//     \[ 学生综合测评成绩(H) = D \times 10\% + X1 \times 60\% + … \]
//   前端此前只挂 marked（无数学渲染），这段会显示成一堆 `\times` 乱码。
//
//   只认 `\( \)`、`\[ \]`、`$$ $$` 三种定界符；**故意不认单个 `$`** ——
//   中文规章里 `$` 和金额容易撞车，误判比不渲染更糟。
import { marked } from 'marked'
import katex from 'katex'
import 'katex/dist/katex.min.css'

type Delim = { re: RegExp; display: boolean }

const DELIMS: Delim[] = [
  { re: /^\\\[([\s\S]+?)\\\]/, display: true }, // \[ … \]
  { re: /^\$\$([\s\S]+?)\$\$/, display: true }, // $$ … $$
  { re: /^\\\(([\s\S]+?)\\\)/, display: false }, // \( … \)
]

const katexExt = {
  name: 'katexMath',
  level: 'inline',
  start(src: string): number | undefined {
    const i = src.search(/\\\[|\\\(|\$\$/)
    return i < 0 ? undefined : i
  },
  tokenizer(src: string) {
    for (const d of DELIMS) {
      const m = d.re.exec(src)
      if (m) {
        return { type: 'katexMath', raw: m[0], text: m[1], display: d.display }
      }
    }
    return undefined
  },
  renderer(token: { text: string; display: boolean; raw: string }): string {
    try {
      return katex.renderToString(token.text, {
        displayMode: token.display,
        throwOnError: false,
        output: 'html',
        // 中文规章里的公式常把中文写在数学模式里（如 `学生综合测评成绩(H) = …`）。
        // KaTeX 会把连续中文归成一个 cjk_fallback 原子、照常渲染，只是 strict 模式
        // 会刷一屏 `unicodeTextInMathMode` 警告 —— 关掉告警，渲染结果不变。
        strict: false,
      })
    } catch {
      // 渲染失败就退回原文，绝不把用户的回答吞掉
      return token.raw
    }
  },
}

// marked v12 的扩展类型定义对自定义 token 收得很紧，这里显式放开
marked.use({ extensions: [katexExt] } as unknown as Parameters<typeof marked.use>[0])

export function renderMarkdown(text: string): string {
  return marked.parse(text || '', { breaks: true }) as string
}