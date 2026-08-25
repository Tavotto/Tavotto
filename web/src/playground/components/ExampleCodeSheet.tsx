/**
 * Code Sheet：卡片的「背面」——点「查看代码」后展开成一张大代码页。
 *
 * 完整源码 + 行号 + 复制 + 「用这个案例开始」。**只读**：本轮不做在线代码
 * 编辑器——产品主叙事是直接改 Figure、代码仍是源头；第一屏同时鼓励改代码
 * 会模糊产品差异（PLAYGROUND_V2.md §三）。
 *
 * 可访问性走既有 Dialog（Radix）：焦点圈定、Esc 关闭、关闭后焦点回到打开它
 * 的按钮。行号放在 aria-hidden + select-none 的独立列里——屏幕阅读器读到的
 * 与复制到剪贴板的都只有代码本身。打开它**不触发任何 Pyodide 加载**。
 */
import { useEffect, useState } from 'react'
import { Check, Copy, Play } from 'lucide-react'
import { Dialog } from '@/components/ui/Dialog'
import { cn } from '@/lib/utils'
import type { PlaygroundExample } from '../examples'
import { pg } from '../pgText'
import { tokenizePython, type TokenKind } from '../pythonHighlight'

//: 技术名词，语言中立，不进翻译（与 runtime 包名同一口径）
const CODE_DEPS = ['NumPy', 'Matplotlib'].join(' · ')

const TOKEN_CLASS: Record<TokenKind, string> = {
  comment: 'text-ink-3 italic',
  string: 'text-[#7a5a2b]',
  number: 'text-[#2868b7]',
  keyword: 'text-[#8a3350] font-medium',
  plain: '',
}

export function ExampleCodeSheet({
  example,
  onClose,
  onStart,
}: {
  example: PlaygroundExample | null
  onClose: () => void
  onStart: (example: PlaygroundExample) => void
}) {
  const [copied, setCopied] = useState(false)
  useEffect(() => setCopied(false), [example])

  if (!example) return null
  const lines = tokenizePython(example.source)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(example.source)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // 剪贴板被策略挡住：不装成功
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="lg"
      width={640}
      title={<span className="font-mono text-[15px]">{example.filename}</span>}
      description={
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span>{pg('codePlain')}</span>
          <span aria-hidden>·</span>
          <span className="font-mono">{CODE_DEPS}</span>
          <span aria-hidden>·</span>
          <span>{pg('codeSelfContained')}</span>
        </span>
      }
      footer={
        <>
          <button
            onClick={() => void copy()}
            className="flex h-7 items-center gap-1.5 rounded-[6px] border border-border px-2.5 text-xs text-ink-2 transition-colors hover:border-ink-faint hover:text-ink"
          >
            {copied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
            {copied ? pg('copied') : pg('copyCode')}
          </button>
          <button
            onClick={() => onStart(example)}
            className="flex h-7 items-center gap-1.5 rounded-[6px] bg-ink px-3 text-xs font-medium text-white transition-opacity hover:opacity-90"
          >
            <Play size={12} aria-hidden />
            {pg('codeStart')}
          </button>
        </>
      }
    >
      <div className="overflow-x-auto rounded-[6px] border border-border bg-bg">
        <pre className="flex min-w-max p-3 font-mono text-[12px] leading-[1.7] text-ink-2">
          {/* 行号列：aria-hidden + select-none——复制与朗读都只有代码本身 */}
          <span
            aria-hidden
            className="mr-3 select-none border-r border-border pr-3 text-right text-ink-faint"
          >
            {lines.map((_, i) => (
              <span key={i} className="block">
                {i + 1}
              </span>
            ))}
          </span>
          <code>
            {lines.map((tokens, i) => (
              <span key={i} className="block">
                {tokens.length === 0
                  ? ' '
                  : tokens.map((tk, j) => (
                      <span key={j} className={cn(TOKEN_CLASS[tk.kind])}>
                        {tk.text}
                      </span>
                    ))}
              </span>
            ))}
          </code>
        </pre>
      </div>
    </Dialog>
  )
}
