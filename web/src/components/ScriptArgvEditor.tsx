import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { fetchScriptArguments } from '@/lib/api'
import { parsePastedCommand, type PasteError } from '@/lib/argvPaste'
import type { ScriptArgsSchema } from '@/lib/scriptArgsForm'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { ScriptArgsForm } from './ScriptArgsForm'
import { Button, IconButton } from './ui/Button'
import { Checkbox } from './ui/Checkbox'
import { Details, Summary } from './ui/Details'
import { ICON_SIZE } from './ui/Icon'
import { ArrowUp, Plus, X } from './ui/icons'
import { TextInput } from './ui/Input'

const ra = (key: string, values?: Record<string, unknown>) =>
  translate(`readiness.argv.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const PASTE_ERROR: Record<PasteError, () => string> = {
  empty: () => ra('paste.error_empty'),
  unsupported_syntax: () => ra('paste.error_unsupported_syntax'),
  unbalanced_quote: () => ra('paste.error_unbalanced_quote'),
  multiline: () => ra('paste.error_multiline'),
  env_assignment: () => ra('paste.error_env_assignment'),
  interpreter_options: () => ra('paste.error_interpreter_options'),
  different_script: () => ra('paste.error_different_script'),
  unrecognized_launcher: () => ra('paste.error_unrecognized_launcher'),
}

/** 展开时取一次静态 schema（后端只读源码）；取不到就只有参数列表——表单是便利，不是门槛。 */
function useScriptSchema(script: string) {
  const [schema, setSchema] = useState<ScriptArgsSchema | null>(null)
  const seq = useRef(0)
  const load = () => {
    const mine = ++seq.current
    fetchScriptArguments(script).then(
      (r) => {
        if (mine === seq.current) setSchema(r.arguments)
      },
      () => {
        if (mine === seq.current) setSchema(null)
      },
    )
  }
  return { schema, load }
}

function PasteCommand({ script, disabled }: { script: string; disabled?: boolean }) {
  const [text, setText] = useState('')
  const [error, setError] = useState<PasteError | null>(null)
  const apply = () => {
    const r = parsePastedCommand(text, script)
    if (!r.ok) {
      setError(r.error) // 原文留在框里，不猜 token 边界
      return
    }
    useScriptArgvStore.getState().setTokens(script, r.argv)
    setError(null)
    setText('')
  }
  return (
    <div className="flex flex-col gap-0.5" data-testid={`argv-paste-${script}`}>
      <div className="flex items-center gap-1">
        <TextInput
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') apply()
          }}
          aria-label={ra('paste.aria', { script })}
          placeholder={ra('paste.placeholder')}
          className="h-7 min-w-0 flex-1 font-mono"
          spellCheck={false}
          autoComplete="off"
        />
        <Button variant="ghost" size="sm" disabled={disabled || text.trim() === ''} onClick={apply}>
          {ra('paste.apply')}
        </Button>
      </div>
      {error ? (
        <p className="type-meta text-ink" role="alert" data-testid="argv-paste-error">
          {PASTE_ERROR[error]()}
        </p>
      ) : null}
    </div>
  )
}

/**
 * 脚本的运行参数（T03）：**一项一个 token**，不是一个要被拆开的字符串。
 *
 * 默认收起（一行摘要）；展开后每个参数一个输入框，可增、删、上移，允许空项（空串是合法 token）。
 * 保存提示只说一件事：含密码 / 令牌的参数请勾"敏感"——它只留在内存里。
 * 草稿住在 `scriptArgvStore`；真正发出去的是"运行开始那一刻"的拷贝。
 */
export function ScriptArgvEditor({ script, disabled }: { script: string; disabled?: boolean }) {
  useTranslation('dialogs')
  const draft = useScriptArgvStore((s) => s.drafts[script])
  const tokens = draft?.tokens ?? []
  const sensitive = draft?.sensitive ?? false
  const st = useScriptArgvStore.getState
  const { schema, load } = useScriptSchema(script)
  const hasForm = schema !== null && schema.arguments.some((a) => !a.hidden)
  return (
    <Details
      className="pl-1"
      data-testid={`argv-${script}`}
      onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open) load()
      }}
    >
      <Summary className="type-meta h-6 gap-1 rounded-sm hover:text-ink-2">
        {ra('title')}
        <span className="tabular-nums">
          {tokens.length > 0 ? ra('count', { n: tokens.length }) : ra('none')}
        </span>
      </Summary>
      <div className="flex flex-col gap-1 py-1">
        {hasForm ? <ScriptArgsForm script={script} schema={schema} disabled={disabled} /> : null}
        <p className="type-meta">{hasForm ? ra('listHint') : ra('hint')}</p>
        <ol className="flex flex-col gap-1">
          {tokens.map((token, i) => (
            // 顺序就是语义，重复的 token 也各占一项：用位置当 key
            <li key={i} className="flex items-center gap-1">
              <TextInput
                value={token}
                disabled={disabled}
                onChange={(e) => st().setToken(script, i, e.target.value)}
                aria-label={ra('tokenAria', { script, n: i + 1 })}
                className="h-7 min-w-0 flex-1 font-mono"
                type={sensitive ? 'password' : 'text'}
                spellCheck={false}
                autoComplete="off"
              />
              <IconButton
                iconSize="sm"
                variant="ghost"
                label={ra('upAria', { script, n: i + 1 })}
                disabled={disabled || i === 0}
                onClick={() => st().moveToken(script, i, -1)}
              >
                <ArrowUp size={ICON_SIZE.sm} />
              </IconButton>
              <IconButton
                iconSize="sm"
                variant="ghost"
                label={ra('removeAria', { script, n: i + 1 })}
                disabled={disabled}
                onClick={() => st().removeToken(script, i)}
              >
                <X size={ICON_SIZE.sm} />
              </IconButton>
            </li>
          ))}
        </ol>
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => st().addToken(script)}
          >
            <Plus size={ICON_SIZE.sm} />
            {ra('add')}
          </Button>
          <label className="type-meta flex items-center gap-1.5">
            <Checkbox
              checked={sensitive}
              disabled={disabled || tokens.length === 0}
              onChange={(e) => st().setSensitive(script, e.target.checked)}
            />
            {ra('sensitive')}
          </label>
        </div>
        <PasteCommand script={script} disabled={disabled} />
      </div>
    </Details>
  )
}
