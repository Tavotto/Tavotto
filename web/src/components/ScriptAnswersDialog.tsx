import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import type { RememberedAnswer } from '@/lib/api'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

/** 选择器的空值必须是同一个引用：每次新建 `[]` 会让 zustand 判成「变了」而无限重渲染 */
const NONE: RememberedAnswer[] = []

const si = (key: string, values?: Record<string, unknown>) =>
  translate(`scriptInput.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 一个脚本记住的输入（ADR 0099 §七）：看、改、删。改 / 删之后后端作废热会话并通知重渲染用到它的面板，
 * 这里再对这个脚本发起一次运行——产出的图名可能随选择变化，要重新登记。
 * 提示文字是用户脚本的文字，纯文本渲染。
 */
export function ScriptAnswersDialog() {
  useTranslation('dialogs')
  const script = useScriptInputStore((s) => s.managing)
  const answers = useScriptInputStore((s) => (script ? (s.answers?.[script] ?? NONE) : NONE))
  const location = useScriptInputStore((s) => s.location)

  if (!script) return null
  const close = () => useScriptInputStore.getState().closeManager()
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && close()}
      anchor="script-answers"
      size="lg"
      title={si('manageTitle', { script })}
      description={si('manageIntro')}
      footer={
        <Button variant="secondary" size="md" onClick={close}>
          {translate('actions.close')}
        </Button>
      }
    >
      <div className="flex flex-col gap-3 text-xs">
        {answers.length === 0 ? (
          <p className="text-ink-3">{si('manageEmpty')}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {answers.map((a) => (
              <AnswerRow key={`${a.index}:${a.prompt}`} script={script} entry={a} />
            ))}
          </ul>
        )}
        {location && <p className="text-ink-3">{si('manageWhere', { path: location })}</p>}
      </div>
    </Dialog>
  )
}

function AnswerRow({ script, entry }: { script: string; entry: RememberedAnswer }) {
  const [value, setValue] = useState(entry.answer)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setValue(entry.answer), [entry.answer])
  const dirty = value !== entry.answer
  const rerun = () => {
    useUiStore.getState().setStatus(msg('scriptInput.manageSaved', { script }, 'dialogs'))
    void useScriptRunStore.getState().run(script)
  }

  return (
    <li className="flex flex-col gap-1" data-script-answer={entry.index}>
      <span className="whitespace-pre-wrap break-words font-mono text-ink-2">
        {entry.prompt
          ? si('managePrompt', { index: entry.index, prompt: entry.prompt })
          : si('manageNoPrompt', { index: entry.index })}
      </span>
      <div className="flex items-center gap-1.5">
        <TextInput
          align="left"
          value={value}
          aria-label={si('answerLabel')}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button
          variant="primary"
          size="md"
          disabled={!dirty}
          onClick={async () => {
            const res = await useScriptInputStore.getState().saveAnswer(script, entry.index, value)
            // 请求在飞时换了项目：什么都不做——尤其不在新项目里重跑同名脚本
            if (res.status === 'stale') return
            setError(res.status === 'error' ? res.error : null)
            if (res.status === 'ok') rerun()
          }}
        >
          {si('manageSave')}
        </Button>
        <Button
          variant="ghost"
          size="md"
          aria-label={si('manageForgetAria', { index: entry.index })}
          onClick={async () => {
            const res = await useScriptInputStore.getState().forgetAnswer(script, entry.index)
            // 请求在飞时换了项目：什么都不做——尤其不在新项目里重跑同名脚本
            if (res.status === 'stale') return
            setError(res.status === 'error' ? res.error : null)
            if (res.status === 'ok') rerun()
          }}
        >
          {si('manageForget')}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
    </li>
  )
}
