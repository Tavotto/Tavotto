import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { CopyAction } from './CopyAction'
import { highlightPythonLine, isPythonish, SYNTAX_CLASS, type SyntaxToken } from './codeHighlight'

const ai = (key: string) => translate(key, { ns: 'ai' })

/**
 * 回答里的围栏代码块（2026-10-07 设计审计 §6.3，宪法第十八节「2026-10-07 重做」）：
 *
 * - **无边框** 12px 圆角（lg）、surface-2 底——它坐在回答正文里，不是第二张卡；
 * - 顶部 28px 工具带（ink 3% 的 group 底）：左边语言名（meta），右边复制（复制后 ✓ 1.2s）；
 * - Python 一族（含没写语言的块）按 `codeHighlight` 上 `--color-syntax-*`；其它语言原样等宽；
 * - 每行一个块，hover 那一行浮 surface-hover（读长代码时跟行）；横向滚动只在代码区里。
 *
 * 文字来源是 hast 里 `<code>` 的整段文字（`Markdown` 的 `pre` 从 hast 拍平取得：流式时 `lib/streamMarkdown`
 * 包的逐词 span 在这里不复存在——代码不是逐词读的东西，整块随增量原地长）。
 */
export function CodeBlock({ code, lang }: { code: string; lang: string }) {
  useTranslation('ai')
  const source = code.replace(/\n$/, '')
  const python = isPythonish(lang)
  const lines = useMemo<SyntaxToken[][]>(
    () => source.split('\n').map((l) => (python ? highlightPythonLine(l) : [{ kind: 'plain', text: l }])),
    [source, python],
  )
  return (
    <div data-ai-code={lang || 'plain'} className="overflow-hidden rounded-lg bg-surface-2">
      <div className="flex h-7 items-center gap-2 bg-group pl-3 pr-1">
        <span className="type-meta min-w-0 flex-1 truncate">{lang || ai('code.plain')}</span>
        <CopyAction
          data-ai-code-copy
          text={source}
          label={ai('code.copy')}
          copiedLabel={ai('code.copied')}
        />
      </div>
      <div className="overflow-x-auto py-2">
        <pre className="w-max min-w-full font-mono text-sm leading-[1.6] text-ink">
          {lines.map((tokens, i) => (
            <div key={i} data-code-line className="px-3 transition-colors duration-fast hover:bg-surface-hover">
              {tokens.length === 0 || (tokens.length === 1 && tokens[0].text === '')
                ? ' '
                : tokens.map((tok, j) =>
                    tok.kind === 'plain' ? (
                      tok.text
                    ) : (
                      <span key={j} data-syntax={tok.kind} className={cn(SYNTAX_CLASS[tok.kind])}>
                        {tok.text}
                      </span>
                    ),
                  )}
            </div>
          ))}
        </pre>
      </div>
    </div>
  )
}
