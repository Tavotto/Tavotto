import ReactMarkdown, { type Components } from 'react-markdown'
import { t } from '@/i18n'
import { rehypeStreamWords } from '@/lib/streamMarkdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from './CodeBlock'

/**
 * AI 回答的 Markdown 渲染（2026-10-07 设计审计 §6.3，宪法第十八节「2026-10-07 重做」）。
 *
 * 回答是**阅读面**：正文 `type-reading`（13 / 1.6、ink——最重要的内容不该是灰的），不装卡。
 * 全部元素显式给样式：markdown 的浏览器默认样式（大标题、粗边框表格）会把 300px 的侧栏变成文档页。
 * 层级：h1 15 / h2 14 / h3 13，一律 600 + 上方 12px 留白；h4–h6 与 h3 同档。
 * 链接是 accent 色 + 1px 下划线（offset 2）——**助手转录是「链接不用蓝」的唯一例外**（宪法第十八节）：
 * 回答里的链接是要点开的出处，不是界面导航。
 * 不开 rehype-raw —— 模型输出里的裸 HTML 一律当纯文本，省掉一类注入面。
 */

/** hast 的最小结构：`pre` 里那个 `<code>` 的语言与整段文字从这里取（`@types/hast` 不是直接依赖） */
interface HastLike {
  type: string
  tagName?: string
  value?: string
  properties?: { className?: unknown }
  children?: HastLike[]
}

const textOf = (n: HastLike | undefined): string =>
  !n ? '' : n.type === 'text' ? (n.value ?? '') : (n.children ?? []).map(textOf).join('')

function codeOf(pre: HastLike | undefined): { code: string; lang: string } {
  const code = pre?.children?.find((c) => c.type === 'element' && c.tagName === 'code')
  const cls = code?.properties?.className
  const list = Array.isArray(cls) ? cls.map(String) : typeof cls === 'string' ? cls.split(/\s+/) : []
  const lang = list.find((c) => c.startsWith('language-'))?.slice('language-'.length) ?? ''
  return { code: textOf(code), lang: lang.toLowerCase() }
}

const HEADING = 'mt-3 break-words text-ink'

const components: Components = {
  p: ({ children }) => <p className="type-reading break-words">{children}</p>,

  h1: ({ children }) => <h4 className={`${HEADING} type-title`}>{children}</h4>,
  h2: ({ children }) => <h4 className={`${HEADING} text-lg font-semibold`}>{children}</h4>,
  h3: ({ children }) => <h5 className={`${HEADING} type-reading font-semibold`}>{children}</h5>,
  h4: ({ children }) => <h5 className={`${HEADING} type-reading font-medium`}>{children}</h5>,
  h5: ({ children }) => <h5 className={`${HEADING} type-reading font-medium`}>{children}</h5>,
  h6: ({ children }) => <h5 className={`${HEADING} type-reading font-medium`}>{children}</h5>,

  ul: ({ children }) => <ul className="type-reading ml-4 list-disc marker:text-ink-3">{children}</ul>,
  ol: ({ children }) => <ol className="type-reading ml-4 list-decimal marker:text-ink-3">{children}</ol>,
  li: ({ children }) => <li className="my-0.5 break-words pl-0.5">{children}</li>,

  strong: ({ children }) => <strong className="font-medium text-ink">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => <del className="text-ink-3 line-through">{children}</del>,

  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      data-ai-link
      className="text-accent underline decoration-1 underline-offset-2 hover:decoration-2"
    >
      {children}
    </a>
  ),

  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-border pl-3 text-ink-2 [&_p]:text-ink-2">{children}</blockquote>
  ),

  hr: () => <hr className="my-1 border-border" />,

  // 只剩行内代码走这里：围栏块在 pre 里整块交给 CodeBlock（pre 的 children 被丢弃，不会渲染两份）。
  // box-decoration-clone：换行时两段都有圆角与内边距，不是一头齐切
  code: ({ children }) => (
    <code className="box-decoration-clone rounded-xs bg-surface-hover px-1 py-px font-mono text-sm text-ink [overflow-wrap:anywhere]">
      {children}
    </code>
  ),
  pre: ({ node }) => {
    const { code, lang } = codeOf(node as HastLike | undefined)
    return <CodeBlock code={code} lang={lang} />
  },

  // 表格：无外框，12px 圆角包住，表头 surface-2，行间 hairline，行 hover
  table: ({ children }) => (
    <div data-ai-table className="overflow-x-auto rounded-lg">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-surface-2">{children}</thead>,
  tr: ({ children }) => (
    <tr className="border-b border-border transition-colors duration-fast last:border-b-0 hover:bg-surface-hover">
      {children}
    </tr>
  ),
  th: ({ children }) => (
    <th className="px-2 py-1.5 text-left font-medium break-words text-ink">{children}</th>
  ),
  td: ({ children }) => <td className="px-2 py-1.5 align-top break-words text-ink">{children}</td>,

  img: ({ alt }) => (
    <span className="text-sm text-ink-3">
      {t('markdown.image', { ns: 'ai', alt: alt || t('markdown.untitled', { ns: 'ai' }) })}
    </span>
  ),
}

/** 引用稳定：react-markdown 每次渲染都按 options 建处理器，数组别在渲染里现造 */
const REMARK = [remarkGfm]
const REHYPE_STREAM = [rehypeStreamWords]

/**
 * `streaming`：正文还在逐字流入。照样按 markdown 渲染（与 ChatGPT / Claude 一致，
 * 不等终稿），只是每个新到的词多包一层一次性淡入（`lib/streamMarkdown`）。
 * 块与块之间 8px；第一块不带上边距、最后一块不带下边距（标题的 mt-3 在最上面时清零）。
 */
export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <div data-ai-markdown className="flex flex-col gap-2 text-ink [&>:first-child]:mt-0 [&>:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={REMARK}
        rehypePlugins={streaming ? REHYPE_STREAM : undefined}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
