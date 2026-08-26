import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Ban, ClipboardCopy, Play, Settings2, Square } from 'lucide-react'
import { backendCodeMsg, type CapturedFigureDescriptor, type ScriptInventoryEntry } from '@/lib/api'
import { formatCm } from '@/lib/units'
import { formatMessage, msg, t as translate } from '@/i18n'
import { addRuntimePanel } from '@/store/actions'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import {
  isBusyPhase,
  needsNative,
  useScriptRunStore,
  type ScriptRunState,
} from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { EmptyState } from '../ui/EmptyState'

/**
 * 素材库「脚本」区（Session 5，普通入口）：项目里每个合理 .py 一行，
 * 「运行并发现图」在这里，不再埋在 RegistryDialog（那边继续做冲突裁决 /
 * 手工 stem / 高级诊断）。数据三来源：清单（scriptLibraryStore，
 * `/api/registry` 的 all_scripts）、注册表（同一响应的 scripts 表，
 * 已关联数量）、运行状态机（scriptRunStore）。
 *
 * 文案纪律：不默认暴露 stem / registry / probe 这类内部术语——状态用
 * 人话（「尚未运行」「输出名称只能在运行后确定」），高级详情可折叠。
 */
const sc = (key: string, values?: Record<string, unknown>) =>
  translate(`scripts.${key}`, { ns: 'workspace', ...(values ?? {}) })

const SAFE_NOTE_KEY = 'tavotto.safeProbeNoticeDismissed'

type Group = 'linked' | 'notRun' | 'runtimeNames' | 'needsEnv' | 'infra'
const GROUP_ORDER: Group[] = ['linked', 'notRun', 'runtimeNames', 'needsEnv', 'infra']

function groupOf(entry: ScriptInventoryEntry, run: ScriptRunState | undefined): Group {
  // 本会话 safe 运行失败且形状像环境问题的，收进「可能需要原环境」——
  // 恢复路径文案（总纲 §四）挂在组上，一眼看全
  if (needsNative(run)) return 'needsEnv'
  if (entry.registered) return 'linked'
  if (entry.reason === 'infrastructure') return 'infra'
  if (entry.reason === 'dynamic_stems' || entry.reason === 'unparseable') return 'runtimeNames'
  return 'notRun' // static_candidate / no_static_output
}

export function ScriptLibrary({ query }: { query: string }) {
  useTranslation('workspace')
  const view = useScriptLibraryStore((s) => s.view)
  const loading = useScriptLibraryStore((s) => s.loading)
  const loaded = useScriptLibraryStore((s) => s.loaded)
  const error = useScriptLibraryStore((s) => s.error)
  const runStates = useScriptRunStore((s) => s.byScript)

  useEffect(() => {
    if (!loaded) void useScriptLibraryStore.getState().load()
  }, [loaded])

  const q = query.trim().toLowerCase()
  const scripts = (view?.all_scripts ?? []).filter(
    (s) => !q || s.script.toLowerCase().includes(q),
  )

  const groups = new Map<Group, ScriptInventoryEntry[]>()
  for (const entry of scripts) {
    const g = groupOf(entry, runStates[entry.script])
    const list = groups.get(g)
    if (list) list.push(entry)
    else groups.set(g, [entry])
  }

  if (error && !view) {
    return (
      <p className="px-3 py-1.5 text-xs text-danger">{sc('loadFailed', { error })}</p>
    )
  }
  if (!view) {
    return loading ? (
      <p className="px-3 py-1.5 text-xs text-ink-3">{sc('loading')}</p>
    ) : null
  }
  if (scripts.length === 0) {
    return q ? (
      <p className="px-3 py-1.5 text-xs text-ink-3">{sc('noMatch')}</p>
    ) : (
      <div className="px-3">
        <EmptyState icon={Play} title={sc('emptyTitle')} hint={sc('emptyHint')} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1 px-3 pb-2">
      <SafeModeNote />
      {GROUP_ORDER.filter((g) => groups.has(g)).map((g) =>
        g === 'infra' ? (
          <details key={g} className="mt-0.5">
            <summary className="cursor-pointer select-none rounded-sm px-1 py-0.5 text-xs text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring">
              {sc('groupInfra', { count: groups.get(g)!.length })}
            </summary>
            <ul aria-label={sc('groupInfra', { count: groups.get(g)!.length })}>
              {groups.get(g)!.map((entry) => (
                <ScriptRow key={entry.script} entry={entry} stems={view.scripts[entry.script]?.stems ?? []} />
              ))}
            </ul>
          </details>
        ) : (
          <section key={g} className="mt-0.5">
            <h4 className="mb-0.5 px-1 text-xs text-ink-3">
              {sc(`group_${g}`)}
              <span className="ml-1 font-mono">{groups.get(g)!.length}</span>
            </h4>
            <ul aria-label={sc(`group_${g}`)}>
              {groups.get(g)!.map((entry) => (
                <ScriptRow key={entry.script} entry={entry} stems={view.scripts[entry.script]?.stems ?? []} />
              ))}
            </ul>
          </section>
        ),
      )}
    </div>
  )
}

/**
 * safe 模式首次使用的简洁说明（关掉之后不再出现；不解释术语，只讲两件
 * 用户关心的事：写入被隔离、只有点了才会运行）。
 */
function SafeModeNote() {
  useTranslation('workspace')
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(SAFE_NOTE_KEY) === '1'
    } catch {
      return false
    }
  })
  if (dismissed) return null
  return (
    <div className="rounded-sm border border-border bg-surface-2 p-2">
      <p className="text-xs leading-relaxed text-ink-2">{sc('safeNoteBody')}</p>
      <button
        onClick={() => {
          setDismissed(true)
          try {
            localStorage.setItem(SAFE_NOTE_KEY, '1')
          } catch {
            /* 存不下就只在本次会话里生效 */
          }
        }}
        className="mt-1 rounded-sm text-xs text-ink-3 outline-none hover:text-ink focus-visible:focus-ring"
      >
        {sc('safeNoteDismiss')}
      </button>
    </div>
  )
}

/**
 * 一行脚本：路径 + 状态 + 「运行并发现图」。
 *
 * 运行/取消是**同一个按钮**（busy 态翻转成取消）：取消后焦点自然留在原
 * 脚本行的这个按钮上，不需要任何焦点搬运。状态行 aria-live=polite——只在
 * 相位变化时更新一次，不高频播报。
 */
function ScriptRow({ entry, stems }: { entry: ScriptInventoryEntry; stems: string[] }) {
  useTranslation('workspace')
  const run = useScriptRunStore((s) => s.byScript[entry.script])
  const busy = !!run && isBusyPhase(run.phase)
  const [resultsOpen, setResultsOpen] = useState(false)

  const onRunOrCancel = () => {
    const store = useScriptRunStore.getState()
    if (busy) store.cancel(entry.script)
    else void store.run(entry.script)
  }

  return (
    <li className="flex flex-col gap-0.5 rounded-sm border border-transparent px-1 py-1 hover:border-border">
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className="min-w-0 flex-1 basis-32 truncate font-mono text-xs text-ink"
          title={entry.script}
        >
          {entry.script}
        </span>
        {/* 窄视口下按钮换行而不是被挤出可视区（flex-wrap + basis） */}
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={onRunOrCancel}
          disabled={!!run?.cancelRequested}
          aria-label={
            busy
              ? sc('cancelAria', { script: entry.script })
              : sc(entry.registered ? 'rerunAria' : 'runAria', { script: entry.script })
          }
        >
          {busy ? (
            <>
              <Square size={12} />
              {sc(run?.cancelRequested ? 'cancelling' : 'cancel')}
            </>
          ) : (
            <>
              <Play size={12} />
              {sc(entry.registered ? 'rerun' : 'run')}
            </>
          )}
        </Button>
      </div>

      <StatusLine entry={entry} stems={stems} run={run} onViewResults={() => setResultsOpen(true)} />
      <FailureRecovery script={entry.script} run={run} />
      <AdvancedDetails entry={entry} />

      {run && run.descriptors.length > 0 && (
        <ProbeResultsDialog
          script={entry.script}
          descriptors={run.descriptors}
          dropped={run.droppedFigures}
          open={resultsOpen}
          onOpenChange={setResultsOpen}
        />
      )}
    </li>
  )
}

/** 状态一行话：不暴露内部术语，错误按稳定 code 翻成当前语言 */
function StatusLine({
  entry,
  stems,
  run,
  onViewResults,
}: {
  entry: ScriptInventoryEntry
  stems: string[]
  run: ScriptRunState | undefined
  onViewResults: () => void
}) {
  useTranslation('workspace')
  const phase = run?.phase ?? 'idle'

  let body: React.ReactNode = null
  if (phase === 'starting_runtime' || phase === 'running') {
    body = (
      <span className="flex items-center gap-1.5 text-ink-2">
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-ink-faint" />
        {sc(phase === 'running' ? 'running' : 'starting')}
      </span>
    )
  } else if (phase === 'captured_one' || phase === 'captured_many') {
    body = (
      <span className="flex flex-wrap items-center gap-1.5">
        <span className="text-ink-2">{sc('captured', { count: run!.descriptors.length })}</span>
        {run!.droppedFigures > 0 && (
          <span className="text-ink-3">{sc('dropped', { count: run!.droppedFigures })}</span>
        )}
        <button
          onClick={onViewResults}
          className="rounded-sm text-accent outline-none hover:underline focus-visible:focus-ring"
        >
          {sc('viewResults')}
        </button>
      </span>
    )
  } else if (phase === 'cancelled') {
    body = <span className="text-ink-3">{sc('cancelledNote')}</span>
  } else if (run?.error) {
    body = (
      <span className="text-danger">
        {formatMessage(backendCodeMsg(run.error.code, run.error.params, run.error.message))}
      </span>
    )
  } else if (entry.registered) {
    body = <span className="text-ink-3">{sc('linkedCount', { count: stems.length })}</span>
  } else if (entry.reason === 'dynamic_stems' || entry.reason === 'unparseable') {
    body = <span className="text-ink-3">{sc('runtimeNamesNote')}</span>
  } else {
    body = <span className="text-ink-3">{sc('notRunNote')}</span>
  }

  // aria-live 挂在常驻容器上（内容只随相位变化）：loading / 完成 / 失败
  // 各播报一次，绝不逐帧刷
  return (
    <p aria-live="polite" className="text-xs leading-relaxed">
      {body}
    </p>
  )
}

/**
 * safe 失败的恢复路径（总纲 §四）：解释可能的原因、给「选择渲染环境」的
 * 真实入口与「复制诊断」。**不渲染任何 native 按钮**——PR 2 未落地，
 * 只有文案里的一句「后续版本还将支持」（不许出现可点但无功能的入口）。
 */
function FailureRecovery({ script, run }: { script: string; run: ScriptRunState | undefined }) {
  useTranslation('workspace')
  const [copied, setCopied] = useState(false)
  if (!needsNative(run)) return null
  const error = run!.error

  const copyDiagnostics = async () => {
    const text = [
      `script: ${script}`,
      `code: ${error?.code ?? ''}`,
      error?.message ?? '',
      error?.traceback ?? '',
    ]
      .filter(Boolean)
      .join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* 剪贴板不可用（无权限）：按钮保持原样，用户可从诊断详情手工复制 */
    }
  }

  return (
    <div className="rounded-sm border border-border bg-surface-2 p-1.5">
      <p className="text-xs leading-relaxed text-ink-2">{sc('recoveryBody')}</p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          // 渲染环境卡片住在设置的「关于」段（EngineEnvironmentCard）
          onClick={() => useUiStore.getState().setSettingsOpen(true, 'about')}
        >
          <Settings2 size={12} />
          {sc('openEnvSettings')}
        </Button>
        <Button variant="outline" size="sm" onClick={() => void copyDiagnostics()}>
          <ClipboardCopy size={12} />
          {sc(copied ? 'copied' : 'copyDiagnostics')}
        </Button>
      </div>
      {error?.traceback && (
        <details className="mt-1">
          <summary className="cursor-pointer select-none text-xs text-ink-3">
            {sc('diagnostics')}
          </summary>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-snug text-ink-2">
            {error.traceback}
          </pre>
        </details>
      )}
    </div>
  )
}

/** 高级详情（默认折叠）：entry 候选、静态识别的输出——内部术语只住在这里 */
function AdvancedDetails({ entry }: { entry: ScriptInventoryEntry }) {
  useTranslation('workspace')
  return (
    <details>
      <summary className="cursor-pointer select-none text-xs text-ink-faint outline-none hover:text-ink-3 focus-visible:focus-ring">
        {sc('advanced')}
      </summary>
      <dl className="mt-0.5 flex flex-col gap-0.5 text-xs text-ink-3">
        {entry.entry_candidates.length > 0 && (
          <div className="flex gap-1">
            <dt className="shrink-0">{sc('advEntry')}</dt>
            <dd className="min-w-0 truncate font-mono">{entry.entry_candidates.join(', ')}</dd>
          </div>
        )}
        {entry.static_stems.length > 0 && (
          <div className="flex gap-1">
            <dt className="shrink-0">{sc('advStems')}</dt>
            <dd className="min-w-0 truncate font-mono">{entry.static_stems.join(', ')}</dd>
          </div>
        )}
        <div className="flex gap-1">
          <dt className="shrink-0">{sc('advReason')}</dt>
          <dd className="font-mono">{entry.reason}</dd>
        </div>
      </dl>
    </details>
  )
}

/**
 * 捕获结果弹层：一次运行发现的**每一张**图都在这里（多 Figure 绝不只显示
 * 第一张——负向反证 #4 的看护对象），各自可添加到画布。Dialog 自带
 * focus trap 与 Esc 关闭。
 */
export function ProbeResultsDialog({
  script,
  descriptors,
  dropped,
  open,
  onOpenChange,
}: {
  script: string
  descriptors: CapturedFigureDescriptor[]
  dropped: number
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  useTranslation('workspace')
  const setStatus = useUiStore((s) => s.setStatus)
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={sc('resultsTitle', { script })}
      description={sc('captured', { count: descriptors.length })}
      size="md"
    >
      <ul className="flex flex-col gap-1" aria-label={sc('resultsListAria')}>
        {descriptors.map((d) => (
          <li
            key={d.asset_id}
            className="flex items-center gap-2 rounded-sm border border-border px-2 py-1"
          >
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={d.stem}>
              {d.stem}
            </span>
            <span className="shrink-0 font-mono text-xs text-ink-3">
              {translate('measure.cmSize', {
                w: formatCm(d.size_mm[0]),
                h: formatCm(d.size_mm[1]),
              })}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                addRuntimePanel(d)
                setStatus(msg('registry.addedToCanvas', { stem: d.stem }, 'dialogs'))
              }}
            >
              {sc('addToCanvas')}
            </Button>
          </li>
        ))}
      </ul>
      {dropped > 0 && (
        <p className="mt-1.5 flex items-start gap-1 text-xs leading-relaxed text-ink-3">
          <Ban size={11} className="mt-0.5 shrink-0" />
          {sc('dropped', { count: dropped })}
        </p>
      )}
    </Dialog>
  )
}
