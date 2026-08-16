import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * AI 回答的 Markdown 渲染。
 *
 * 全部元素显式给样式：markdown 的浏览器默认样式（大标题、粗边框表格、
 * 蓝链接）会把 240px 的聊天侧栏变成文档页，与低对比纪律冲突。
 * 不开 rehype-raw —— 模型输出里的裸 HTML 一律当纯文本，省掉一类注入面。
 */
const components: Components = {
  p: ({ children }) => (
    <p className="text-sm leading-[1.6] break-words text-ink-2">{children}</p>
  ),

  // 标题只比正文大一档，靠字重和留白拉层级，不靠字号
  h1: ({ children }) => <h4 className="mt-1 text-sm font-medium text-ink">{children}</h4>,
  h2: ({ children }) => <h4 className="mt-1 text-sm font-medium text-ink">{children}</h4>,
  h3: ({ children }) => <h5 className="mt-1 text-sm font-medium text-ink">{children}</h5>,
  h4: ({ children }) => <h5 className="mt-1 text-sm font-medium text-ink">{children}</h5>,
  h5: ({ children }) => <h5 className="mt-1 text-sm font-medium text-ink">{children}</h5>,
  h6: ({ children }) => <h5 className="mt-1 text-sm font-medium text-ink">{children}</h5>,

  ul: ({ children }) => (
    <ul className="ml-3.5 list-disc text-sm leading-[1.6] text-ink-2 marker:text-ink-3">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="ml-3.5 list-decimal text-sm leading-[1.6] text-ink-2 marker:text-ink-3">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="my-0.5 break-words">{children}</li>,

  strong: ({ children }) => <strong className="font-medium text-ink">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => <del className="text-ink-3 line-through">{children}</del>,

  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-ink-2 underline decoration-ink-3 underline-offset-2 hover:text-ink"
    >
      {children}
    </a>
  ),

  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-border pl-2 text-sm leading-[1.6] text-ink-3">
      {children}
    </blockquote>
  ),

  hr: () => <hr className="my-1 border-border" />,

  code: ({ className, children }) => {
    // react-markdown 用 language-* class 区分围栏代码块与行内代码
    const fenced = /language-/.test(className ?? '')
    if (!fenced) {
      return (
        <code className="rounded-[3px] bg-surface-2 px-1 py-px font-mono text-xs text-ink [overflow-wrap:anywhere]">
          {children}
        </code>
      )
    }
    return <code className="font-mono text-xs leading-[1.5] text-ink">{children}</code>
  },
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-sm border border-border bg-surface-2 p-2">
      {children}
    </pre>
  ),

  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-border bg-surface-2 px-1.5 py-1 text-left font-medium break-words text-ink">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-border px-1.5 py-1 align-top break-words text-ink-2">{children}</td>
  ),

  img: ({ alt }) => <span className="text-xs text-ink-3">[图片：{alt || '未命名'}]</span>,
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
