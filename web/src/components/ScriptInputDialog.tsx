import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

const si = (key: string, values?: Record<string, unknown>) =>
  translate(`scriptInput.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 脚本里的 `input()` 在等作答（ADR 0099 §六）。
 *
 * - **一次只问一个**：显示队首；答完这一问就关，下一问来了再打开（同一个框换了内容）。
 * - **不许随手关**（`blockDismiss`）：关掉而不答，脚本会白等 10 分钟。出口只有三个——提交 / 结束输入（EOF）/
 *   停止脚本（硬杀会话）。
 * - 脚本的输出片段与提示是**用户脚本的文字**：一律 React 文本节点，不走 innerHTML。输出片段让用户看得到
 *   脚本刚列出的编号清单（「1. xxx  2. yyy」），不然没法选。
 */
export function ScriptInputDialog() {
  useTranslation('dialogs')
  const head = useScriptInputStore((s) => s.queue[0] ?? null)
  const busy = useScriptInputStore((s) => s.busy)
  const error = useScriptInputStore((s) => s.error)
  const [value, setValue] = useState('')
  const outRef = useRef<HTMLPreElement>(null)

  // 换了一问就清空输入框：上一问的答案与这一问无关
  useEffect(() => setValue(''), [head?.id])
  // 输出片段滚到底：编号清单通常就在最后几行
  useEffect(() => {
    const el = outRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [head?.id])

  if (!head) return null
  const store = useScriptInputStore.getState()
  const secret = head.input_kind === 'getpass'

  return (
    <Dialog
      open
      onOpenChange={() => {}}
      blockDismiss
      busy={busy}
      anchor="script-input"
      size="lg"
      title={si('title')}
      description={si('question', { index: head.index, script: head.script })}
      footer={
        <>
          <Button variant="danger" size="md" disabled={busy} onClick={() => store.stop()}>
            {si('stop')}
          </Button>
          <span className="flex-1" />
          <Button
            variant="secondary"
            size="md"
            disabled={busy}
            title={si('eofTip')}
            onClick={() => store.submit(null)}
          >
            {si('eof')}
          </Button>
          <Button
            variant="primary"
            size="md"
            loading={busy}
            data-script-input-submit=""
            onClick={() => store.submit(value)}
          >
            {si('submit')}
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-3 text-xs"
        onSubmit={(e) => {
          e.preventDefault()
          if (!busy) void store.submit(value)
        }}
      >
        {head.stdout_tail && (
          <section className="flex flex-col gap-1">
            <h3 className="type-meta">{si('outputLabel')}</h3>
            <pre
              ref={outRef}
              data-script-input-output=""
              className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 px-2 py-1.5 font-mono text-xs text-ink"
            >
              {head.stdout_tail}
            </pre>
          </section>
        )}
        <section className="flex flex-col gap-1">
          <h3 className="type-meta">{si('promptLabel')}</h3>
          {head.prompt ? (
            <p data-script-input-prompt="" className="whitespace-pre-wrap break-words font-mono text-ink">
              {head.prompt}
            </p>
          ) : (
            <p className="text-ink-3">{si('noPrompt')}</p>
          )}
        </section>
        <label className="flex flex-col gap-1">
          <span className="type-meta">{si('answerLabel')}</span>
          <TextInput
            autoFocus
            align="left"
            value={value}
            disabled={busy}
            data-script-input-answer=""
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <p className="text-ink-3">{secret ? si('getpassNote') : si('rememberNote')}</p>
        {error && (
          <p role="alert" className="text-danger">
            {si('failed', { error })}
          </p>
        )}
      </form>
    </Dialog>
  )
}
