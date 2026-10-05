import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type { ScriptInputRequest } from '@/lib/api'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'

const si = (key: string, values?: Record<string, unknown>) =>
  translate(`scriptInput.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 脚本 `input()` 作答的内容，**与展示面无关**（ADR 0099 §六 / §九）：原对话框与准备面板（T09）用同一份。
 *
 * - 答题框的值只在组件状态里：换了一问清空；口令（getpass）一交出去就清空——不进 store、不进历史。
 * - 口令用密码框（`type=password`、不自动补全）；建议（上次的回答）只作为一句话给人看，**不预填、不自动交**：
 *   菜单可能换了序，按 Enter 就交出旧编号正是要防的事。
 * - 脚本的文字（提示、输出片段、建议）一律 React 文本节点，不走 innerHTML。
 */
export interface ScriptInputAnswer {
  head: ScriptInputRequest | null
  value: string
  setValue: (v: string) => void
  busy: boolean
  error: string | null
  submit: () => void
  eof: () => void
  stop: () => void
}

export function useScriptInputAnswer(): ScriptInputAnswer {
  const head = useScriptInputStore((s) => s.queue[0] ?? null)
  const busy = useScriptInputStore((s) => s.busy)
  const error = useScriptInputStore((s) => s.error)
  const [value, setValue] = useState('')
  // 换了一问就清空输入框：上一问的答案与这一问无关
  useEffect(() => setValue(''), [head?.id])
  const store = useScriptInputStore.getState()
  return {
    head,
    value,
    setValue,
    busy,
    error,
    submit: () => {
      if (!head || busy) return
      const answer = value
      // 口令不在界面里多留一刻：交出去就清掉（失败了重新输入）
      if (isSecret(head)) setValue('')
      void store.submit(answer)
    },
    eof: () => void store.submit(null),
    stop: () => void store.stop(),
  }
}

export const isSecret = (req: ScriptInputRequest) => req.secret === true || req.input_kind === 'getpass'

export function ScriptInputFields({ answer }: { answer: ScriptInputAnswer }) {
  useTranslation('dialogs')
  const { head, value, setValue, busy, error } = answer
  const outRef = useRef<HTMLPreElement>(null)
  // 输出片段滚到底：编号清单通常就在最后几行
  useEffect(() => {
    const el = outRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [head?.id])
  if (!head) return null
  const secret = isSecret(head)
  const suggestion = !secret && head.suggestion ? head.suggestion : null

  return (
    <form
      className="flex flex-col gap-3 text-xs"
      onSubmit={(e) => {
        e.preventDefault()
        answer.submit()
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
          type={secret ? 'password' : 'text'}
          autoComplete={secret ? 'off' : undefined}
          spellCheck={secret ? false : undefined}
          value={value}
          disabled={busy}
          data-script-input-answer=""
          onChange={(e) => setValue(e.target.value)}
        />
      </label>
      {suggestion !== null && (
        <p data-script-input-suggestion="" className="whitespace-pre-wrap break-words text-ink-2">
          {si('suggestion', { answer: suggestion })}
        </p>
      )}
      <p className="text-ink-3">{secret ? si('getpassNote') : si('rememberNote')}</p>
      {error && (
        <p role="alert" className="text-danger">
          {si('failed', { error })}
        </p>
      )}
    </form>
  )
}

export function ScriptInputActions({ answer }: { answer: ScriptInputAnswer }) {
  useTranslation('dialogs')
  const { busy } = answer
  return (
    <>
      <Button variant="danger" size="md" disabled={busy} onClick={answer.stop}>
        {si('stop')}
      </Button>
      <span className="flex-1" />
      <Button variant="secondary" size="md" disabled={busy} title={si('eofTip')} onClick={answer.eof}>
        {si('eof')}
      </Button>
      <Button
        variant="primary"
        size="md"
        loading={busy}
        data-script-input-submit=""
        onClick={answer.submit}
      >
        {si('submit')}
      </Button>
    </>
  )
}
