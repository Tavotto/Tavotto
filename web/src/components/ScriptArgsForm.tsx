import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import {
  applyEdit,
  groupProblems,
  missingRequired,
  readTokens,
  type EditError,
  type FormEdit,
  type ScriptArgsSchema,
  type ScriptArgument,
} from '@/lib/scriptArgsForm'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { Checkbox } from './ui/Checkbox'
import { IconButton } from './ui/Button'
import { ICON_SIZE } from './ui/Icon'
import { X } from './ui/icons'
import { TextInput } from './ui/Input'
import { Segmented } from './ui/Segmented'
import { Select } from './ui/Select'

const rf = (key: string, values?: Record<string, unknown>) =>
  translate(`readiness.argv.form.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 识别不全的理由 → 一句话（闭集，`engine/scriptargs.REASONS`）。没收录的码只说"部分识别"。 */
const REASON_TEXT: Record<string, () => string> = {
  dynamic_add_argument: () => rf('reason_dynamic_add_argument'),
  conditional_argument: () => rf('reason_conditional_argument'),
  custom_action: () => rf('reason_custom_action'),
  dynamic_value: () => rf('reason_dynamic_value'),
  subcommands: () => rf('reason_subcommands'),
  remainder: () => rf('reason_remainder'),
  explicit_parse_args: () => rf('reason_explicit_parse_args'),
  parse_known_args: () => rf('reason_parse_known_args'),
  reads_sys_argv: () => rf('reason_reads_sys_argv'),
  other_cli: () => rf('reason_other_cli'),
}

const ERROR_TEXT: Record<EditError, () => string> = {
  form_disabled: () => rf('error_form_disabled'),
  not_editable: () => rf('error_not_editable'),
  positionals_uncertain: () => rf('error_positionals_uncertain'),
  earlier_positional_missing: () => rf('error_earlier_positional_missing'),
  later_positional_present: () => rf('error_later_positional_present'),
  positional_looks_like_option: () => rf('error_positional_looks_like_option'),
  value_looks_like_option: () => rf('error_value_looks_like_option'),
  wrong_value_count: () => rf('error_wrong_value_count'),
}

const UNSET = '\u0000unset'

const label = (arg: ScriptArgument) => (arg.positional ? (arg.metavar ?? arg.dest) : arg.flags.join(', '))

/** 类型提示：只提醒，不拦（真 parser 才是裁判）。 */
const typeWarning = (arg: ScriptArgument, value: string): string | null => {
  if (value === '') return null
  if (arg.type === 'int' && !/^\s*[+-]?\d[\d_]*\s*$/.test(value)) return rf('warn_int')
  if (arg.type === 'float' && Number.isNaN(Number(value.replace(/_/g, '')))) return rf('warn_float')
  if (arg.choices && !arg.choices.includes(value)) return rf('warn_choice')
  return null
}

/**
 * 参数表单（T07）：从后端的静态 schema 渲染字段，**每次编辑都只是改 token 列表**（`applyEdit`）。
 *
 * 默认值只当占位符展示；"清空"= 不出现这个参数（用脚本默认），输入框里删光 = 空字符串（一个值）；
 * 开关的"关"与"用脚本默认"按 token 形状分开（BooleanOptional 才有三态）。编辑失败时输入框保留你打的字与焦点，
 * 下面说为什么——token 列表不变。表单认不出的 token 原样留着，列在最后。
 */
export function ScriptArgsForm({
  script,
  schema,
  disabled,
}: {
  script: string
  schema: ScriptArgsSchema
  disabled?: boolean
}) {
  useTranslation('dialogs')
  const draft = useScriptArgvStore((s) => s.drafts[script])
  const tokens = draft?.tokens ?? []
  const sensitive = draft?.sensitive ?? false
  const [pending, setPending] = useState<Record<string, string[]>>({})
  const [errors, setErrors] = useState<Record<string, EditError>>({})
  const view = readTokens(schema, tokens)
  const missing = new Set(missingRequired(schema, view))
  const problems = groupProblems(schema, view)
  const byId = new Map(schema.arguments.map((a) => [a.id, a]))

  const edit = (arg: ScriptArgument, e: FormEdit, typed?: string[]) => {
    const r = applyEdit(schema, tokens, e)
    if (r.ok) {
      useScriptArgvStore.getState().setTokens(script, r.tokens)
      setPending(({ [arg.id]: _a, ...rest }) => rest)
      setErrors(({ [arg.id]: _b, ...rest }) => rest)
    } else {
      if (typed) setPending((p) => ({ ...p, [arg.id]: typed }))
      setErrors((x) => ({ ...x, [arg.id]: r.error }))
    }
  }

  const reasons = schema.reasons.map((r) => REASON_TEXT[r]?.()).filter(Boolean)
  const shown = schema.arguments.filter((a) => !a.hidden)

  return (
    <div className="flex flex-col gap-1.5" data-testid={`argv-form-${script}`}>
      <p className="type-meta" data-testid="argv-form-status">
        {schema.status === 'complete'
          ? rf('statusComplete', { n: shown.length })
          : rf('statusPartial', { n: shown.length })}
        {reasons.length > 0 ? ` ${reasons.join(' ')}` : ''}
      </p>
      {!schema.form_enabled ? (
        <p className="type-meta" data-testid="argv-form-readonly">
          {rf('readonly')}
        </p>
      ) : null}
      {missing.size > 0 ? (
        <p className="type-meta text-ink" data-testid="argv-form-missing">
          {rf('missing', {
            names: [...missing].map((id) => label(byId.get(id)!)).join(', '),
          })}
        </p>
      ) : null}
      {problems.map((g) => (
        <p key={g.id} className="type-meta text-ink" data-testid={`argv-form-group-${g.problem}`}>
          {g.problem === 'conflict'
            ? rf('groupConflict', { names: g.members.map((m) => label(byId.get(m)!)).join(', ') })
            : rf('groupMissing', { names: g.members.map((m) => label(byId.get(m)!)).join(', ') })}
        </p>
      ))}
      <ul className="flex flex-col gap-1.5">
        {shown.map((arg) => {
          const field = view.fields[arg.id]
          const editable = schema.form_enabled && arg.editable && !field?.uncertain
          const values = pending[arg.id] ?? field?.values ?? []
          const err = errors[arg.id]
          const warn = arg.arity === 1 && field?.state === 'value' ? typeWarning(arg, values[0] ?? '') : null
          return (
            <li key={arg.id} className="flex flex-col gap-0.5" data-testid={`argv-field-${arg.dest}`}>
              <div className="flex items-center gap-1.5">
                <span className="type-meta min-w-0 truncate font-mono text-ink-2">{label(arg)}</span>
                {arg.required ? <span className="type-meta">{rf('required')}</span> : null}
              </div>
              {!editable ? (
                <p className="type-meta" data-testid="argv-field-readonly">
                  {field?.uncertain ? rf('uncertain') : rf('notEditable')}
                  {field && field.occurrences > 0 ? ` ${rf('present', { n: field.occurrences })}` : ''}
                </p>
              ) : arg.action === 'boolean_optional' ? (
                <Segmented
                  ariaLabel={label(arg)}
                  value={field?.state === 'on' ? 'on' : field?.state === 'off' ? 'off' : 'default'}
                  onChange={(v) => edit(arg, { op: 'flag', arg: arg.id, value: v })}
                  items={[
                    { value: 'default', label: rf('useDefault') },
                    { value: 'on', label: rf('on') },
                    { value: 'off', label: rf('off') },
                  ]}
                />
              ) : arg.arity === 0 ? (
                <label className="type-meta flex items-center gap-1.5">
                  <Checkbox
                    checked={field?.state === 'on'}
                    disabled={disabled}
                    onChange={(e) =>
                      edit(arg, { op: 'flag', arg: arg.id, value: e.target.checked ? 'on' : 'off' })
                    }
                  />
                  {rf('flagOn', { flag: arg.flags[0] })}
                </label>
              ) : arg.choices && arg.arity === 1 ? (
                <Select
                  ariaLabel={label(arg)}
                  disabled={disabled}
                  value={field?.state === 'value' ? (values[0] ?? '') : UNSET}
                  onChange={(v) =>
                    v === UNSET
                      ? edit(arg, { op: 'clear', arg: arg.id })
                      : edit(arg, { op: 'set', arg: arg.id, values: [v] })
                  }
                  options={[
                    {
                      value: UNSET,
                      label: arg.required
                        ? rf('notProvided')
                        : arg.default?.kind === 'literal'
                          ? rf('scriptDefault', { value: arg.default.display })
                          : rf('notProvided'),
                    },
                    ...arg.choices.map((c) => ({ value: c, label: c })),
                    ...(field?.state === 'value' && !arg.choices.includes(values[0] ?? '')
                      ? [{ value: values[0] ?? '', label: rf('outsideChoices', { value: values[0] ?? '' }) }]
                      : []),
                  ]}
                />
              ) : (
                <div className="flex items-center gap-1">
                  {Array.from({ length: arg.arity as number }, (_, k) => (
                    <TextInput
                      key={k}
                      value={values[k] ?? ''}
                      disabled={disabled}
                      type={sensitive ? 'password' : 'text'}
                      spellCheck={false}
                      autoComplete="off"
                      aria-label={rf('valueAria', { name: label(arg), n: k + 1 })}
                      placeholder={
                        field?.state === 'value' || pending[arg.id]
                          ? ''
                          : arg.default?.kind === 'literal' && !arg.required
                            ? rf('scriptDefault', { value: arg.default.display })
                            : rf('notProvided')
                      }
                      className="h-7 min-w-0 flex-1 font-mono"
                      onChange={(e) => {
                        const next = Array.from({ length: arg.arity as number }, (_, j) =>
                          j === k ? e.target.value : (values[j] ?? ''),
                        )
                        edit(arg, { op: 'set', arg: arg.id, values: next }, next)
                      }}
                    />
                  ))}
                  <IconButton
                    iconSize="sm"
                    variant="ghost"
                    label={rf('clearAria', { name: label(arg) })}
                    disabled={disabled || (field?.state === 'unset' && !pending[arg.id])}
                    onClick={() => edit(arg, { op: 'clear', arg: arg.id })}
                  >
                    <X size={ICON_SIZE.sm} />
                  </IconButton>
                </div>
              )}
              {field?.state === 'value' && values.length === 1 && values[0] === '' ? (
                <p className="type-meta">{rf('emptyString')}</p>
              ) : null}
              {field && field.occurrences > 1 && arg.arity !== 0 ? (
                <p className="type-meta">{rf('repeated', { n: field.occurrences })}</p>
              ) : null}
              {arg.role === 'output_file' ? <p className="type-meta">{rf('outputFile')}</p> : null}
              {arg.role === 'input_file' ? <p className="type-meta">{rf('inputFile')}</p> : null}
              {arg.help ? <p className="type-meta">{arg.help}</p> : null}
              {err ? (
                <p className="type-meta text-ink" role="alert" data-testid="argv-field-error">
                  {ERROR_TEXT[err]()}
                </p>
              ) : warn ? (
                <p className="type-meta" data-testid="argv-field-warning">
                  {warn}
                </p>
              ) : null}
            </li>
          )
        })}
      </ul>
      {view.unattributed.length > 0 ? (
        <p className="type-meta" data-testid="argv-form-other">
          {rf('other', {
            tokens: view.unattributed.map((j) => (sensitive ? '••••' : JSON.stringify(tokens[j]))).join(' '),
          })}
        </p>
      ) : null}
    </div>
  )
}
