import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2 } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatDate } from '@/i18n/format'
import {
  deleteFixMemory,
  fetchFixMemory,
  setFixMemoryEnabled,
  type FixMemoryEntry,
  type FixMemoryState,
} from '@/lib/api'
import { Button, IconButton } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Details, Summary } from '../ui/Details'
import { EmptyLine } from '../ui/EmptyLine'
import { FieldGroup } from '../ui/FormSection'
import { Toggle } from '../ui/Toggle'
import { rowMetaClass } from '../ui/listRow'
import { ag } from './agentState'
import { SettingRow, settingRowLabelId } from './SettingRow'

const fm = (key: string, values?: Record<string, unknown>) => ag(`fixMemory.${key}`, values)

/** 一条记住的做法 → 一句话（「缺环境变量」之后的那半句）。字段由后端按种类给，这里只翻译 */
export function fixMemoryAction(e: FixMemoryEntry): string {
  const f = e.fields ?? {}
  if (e.kind === 'run_args') return fm('kind.run_args', { args: (f.args ?? []).join(' ') })
  if (e.kind === 'launcher') {
    const env = (f.env ?? []).map((v) => (v.value === undefined ? v.name : `${v.name}=${v.value}`))
    return env.length ? fm('kind.launcherEnv', { env: env.join(' · ') }) : fm('kind.launcher')
  }
  if (e.kind === 'data_link')
    return fm('kind.data_link', {
      asked: f.asked ?? '',
      linked: f.linked ?? '',
    })
  return fm('kind.execution', {
    mode: fm(`mode.${f.mode === 'native' ? 'native' : 'safe'}`),
  })
}

/** 句子里的路径 / 参数 / 变量用等宽（index.css：字面量一律 mono）：按占位符把译文切开，字面量那几段单独包 */
function ActionText({ e }: { e: FixMemoryEntry }) {
  const f = e.fields ?? {}
  const literals: string[] = []
  if (e.kind === 'run_args') literals.push((f.args ?? []).join(' '))
  else if (e.kind === 'launcher')
    literals.push(...(f.env ?? []).map((v) => (v.value === undefined ? v.name : `${v.name}=${v.value}`)))
  else if (e.kind === 'data_link') literals.push(f.asked ?? '', f.linked ?? '')
  const text = fixMemoryAction(e)
  const parts: (string | { code: string })[] = []
  let rest = text
  for (const lit of literals.filter(Boolean)) {
    const i = rest.indexOf(lit)
    if (i < 0) continue
    parts.push(rest.slice(0, i), { code: lit })
    rest = rest.slice(i + lit.length)
  }
  parts.push(rest)
  return (
    <span className="break-words text-ink-2">
      {parts.map((p, i) =>
        typeof p === 'string' ? (
          p
        ) : (
          <code key={i} className="break-all font-mono text-xs text-ink">
            {p.code}
          </code>
        ),
      )}
    </span>
  )
}

export function fixMemoryShape(shape: string): string {
  const key = `fixMemory.shape.${shape}`
  const text = ag(key)
  return text === `settings.agents.${key}` || !text ? fm('shape.other') : text
}

/**
 * 设置 → 改图助手 →「记住修好的做法」（ADR 0197 第 3 层·在线，维护者 11-21「连具体做法也记，要做界面」）。
 *
 * 一行：开关 + 现状一句（「已记住 3 条 · 查看」）。点「查看」开一个列表弹窗：每条一句「哪类问题 → 怎么修好的」、
 * 灰色一行「脚本 · 日期 · 用过几次」、行尾删除；启动脚本正文默认折叠。页脚「全部删除」。
 * 老后端没有 `/api/ai/fix-memory` → 整行不渲染（不画一个按不动的开关）。
 */
export function FixMemoryRow() {
  useTranslation('dialogs')
  const [state, setState] = useState<FixMemoryState | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  // 删掉一行时被点的那颗按钮跟着消失，焦点会掉到弹窗外、弹窗当成「点了外面」自己关掉：先把焦点交给列表本身
  const listRef = useRef<HTMLDivElement>(null)

  const load = useCallback(() => {
    fetchFixMemory()
      // 老后端 / 别的回应形状：不渲染这一行（不画一个按不动的开关）
      .then((r) => setState(r && typeof r.enabled === 'boolean' && Array.isArray(r.entries) ? r : null))
      .catch(() => setState(null))
  }, [])
  useEffect(load, [load])
  if (!state) return null

  const count = state.entries.length
  const change = async (next: boolean) => {
    const prev = state
    setState({ ...state, enabled: next }) // 先动：开关不等本机往返
    setBusy(true)
    try {
      setState(await setFixMemoryEnabled(next))
    } catch {
      setState(prev) // 没存上就退回，不让界面说谎
    }
    setBusy(false)
  }
  const remove = async (id?: string) => {
    listRef.current?.focus()
    setBusy(true)
    try {
      setState(await deleteFixMemory(id))
    } catch {
      load()
    }
    setBusy(false)
  }

  return (
    <FieldGroup data-fix-memory-group>
      <SettingRow
        label={fm('label')}
        help={fm('help')}
        controlId="setting-fix-memory"
        data-settings-anchor="ai.fixMemory"
        data-fix-memory
        status={count > 0 ? fm('count', { count }) : undefined}
      >
        <div className="flex items-center justify-end gap-3">
          {count > 0 && (
            <Button variant="secondary" size="sm" data-fix-memory-view onClick={() => setOpen(true)}>
              {fm('view')}
            </Button>
          )}
          <Toggle
            aria-labelledby={settingRowLabelId('setting-fix-memory')}
            id="setting-fix-memory"
            checked={state.enabled}
            disabled={busy}
            onChange={(v) => void change(v)}
          />
        </div>
      </SettingRow>
      <Dialog
        open={open}
        onOpenChange={(v) => {
          setOpen(v)
          setConfirming(false)
        }}
        title={fm('title')}
        size="lg"
        dimBehind
        footer={
          count === 0
            ? undefined
            : confirming
              ? {
                  // 原地确认（不弹全局确认框：那一层会把这个列表弹窗当成「点了外面」关掉）
                  start: (
                    <span className="text-sm text-ink-2" data-fix-memory-confirm>
                      {fm('clearBody', { count })}
                    </span>
                  ),
                  secondary: (
                    <Button variant="secondary" size="lg" data-confirm-cancel onClick={() => setConfirming(false)}>
                      {fm('cancel')}
                    </Button>
                  ),
                  primary: (
                    <Button
                      variant="danger"
                      size="lg"
                      disabled={busy}
                      data-confirm-ok
                      onClick={() => {
                        setConfirming(false)
                        void remove()
                      }}
                    >
                      {fm('clearAll')}
                    </Button>
                  ),
                }
              : {
                  start: (
                    <Button
                      variant="secondary"
                      size="lg"
                      disabled={busy}
                      data-fix-memory-clear
                      onClick={() => setConfirming(true)}
                    >
                      {fm('clearAll')}
                    </Button>
                  ),
                }
        }
      >
        <div ref={listRef} tabIndex={-1} className="outline-none">
          {count === 0 ? (
            <EmptyLine data-fix-memory-empty>{fm('none')}</EmptyLine>
          ) : (
            <ul className="flex flex-col divide-y divide-border" data-fix-memory-list>
              {state.entries.map((e) => (
                <li key={e.id} className="flex items-start gap-3 py-2.5" data-fix-memory-entry={e.id}>
                  <div className="min-w-0 flex-1">
                    <p className="text-base">
                      <span className="text-ink">{fixMemoryShape(e.shape)}</span>
                      <span className="mx-1.5 text-ink-3" aria-hidden>
                        →
                      </span>
                      <ActionText e={e} />
                    </p>
                    <p className={rowMetaClass(false)}>
                      <span className="font-mono">{e.script || '—'}</span>
                      {' · '}
                      {fm('meta', {
                        date: formatDate(e.last_used * 1000),
                        count: e.times,
                      })}
                    </p>
                    {e.kind === 'launcher' && e.fields.text && (
                      <Details className="mt-1" data-fix-memory-launcher>
                        <Summary className="w-fit text-xs text-ink-2">{fm('launcherText')}</Summary>
                        <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-field px-2.5 py-2 font-mono text-xs text-ink-2">
                          {e.fields.text}
                        </pre>
                      </Details>
                    )}
                  </div>
                  <IconButton
                    label={fm('delete')}
                    iconSize="sm"
                    className="text-ink-3 hover:text-ink"
                    disabled={busy}
                    data-fix-memory-delete={e.id}
                    onClick={() => void remove(e.id)}
                  >
                    <Trash2 size={ICON_SIZE.sm} aria-hidden />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Dialog>
    </FieldGroup>
  )
}
