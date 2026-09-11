import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Ban, Copy, Play, Settings, Square } from 'lucide-react'
import { listRowClass } from '@/components/ui/listRow'
import { Notice } from '@/components/ui/Notice'
import { cn } from '@/lib/utils'
import { Details, Summary } from '@/components/ui/Details'
import { ICON_SIZE } from '@/components/ui/Icon'
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
import { Button, IconButton } from '../ui/Button'
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
        <EmptyState icon={Play} title={sc('emptyTitle')} />
      </div>
    )
  }

  return (
    <div className="flex flex-col px-2 pb-2">
      <SafeModeNote />
      {GROUP_ORDER.filter((g) => groups.has(g)).map((g) =>
        g === 'infra' ? (
          <Details key={g} className="mt-1">
            <Summary className="mx-1 h-6 rounded-xs px-1 type-meta hover:text-ink-2">
              {sc('groupInfra', { count: groups.get(g)!.length })}
            </Summary>
            <ul aria-label={sc('groupInfra', { count: groups.get(g)!.length })}>
              {groups.get(g)!.map((entry) => (
                <ScriptRow key={entry.script} entry={entry} stems={view.scripts[entry.script]?.stems ?? []} />
              ))}
            </ul>
          </Details>
        ) : (
          <section key={g} className="mt-1">
            {/* 分组名 + 计数是一行元数据，不是又一级标题 */}
            <h4 className="flex h-6 items-center gap-1.5 px-2 type-meta">
              {sc(`group_${g}`)}
              <span className="tabular-nums">{groups.get(g)!.length}</span>
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
    <Notice
      className="mx-1"
      action={
        <Button
          size="sm"
          className="text-ink-3"
          onClick={() => {
            setDismissed(true)
            try {
              localStorage.setItem(SAFE_NOTE_KEY, '1')
            } catch {
              /* 存不下就只在本次会话里生效 */
            }
          }}
        >
          {sc('safeNoteDismiss')}
        </Button>
      }
    >
      {sc('safeNoteBody')}
    </Notice>
  )
}

/**
 * 一行脚本（Tavotto File Row）：状态点 | 文件名 | 状态一句话 | 运行图标钮。
 *
 *   ● plot.py            已关联 2 张图     ▶
 *   ○ analyze.py         这个脚本尚未运行   ▶
 *
 * 28px 一行，与树行、列表行同一种外观（`listRowClass`）。运行 / 取消是**同一个
 * 按钮**（busy 态翻转成取消）：取消后焦点自然留在原脚本行的这个按钮上，不需要
 * 任何焦点搬运。它常驻但常态是 ink-3，行 hover 时才与文字同色——它是这一行唯一
 * 的操作，不该比文件名更响。状态那一段 aria-live=polite——只在相位变化时更新
 * 一次，不高频播报。
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
    <li className="flex flex-col">
      <div className={cn(listRowClass(), 'gap-1.5 pl-1.5 pr-0.5')}>
        <StatusDot entry={entry} run={run} />
        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-ink"
          title={entry.script}
        >
          {entry.script}
        </span>
        <StatusLine entry={entry} stems={stems} run={run} onViewResults={() => setResultsOpen(true)} />
        <IconButton
          iconSize="sm"
          label={
            busy
              ? sc('cancelAria', { script: entry.script })
              : sc(entry.registered ? 'rerunAria' : 'runAria', { script: entry.script })
          }
          tip={busy ? sc(run?.cancelRequested ? 'cancelling' : 'cancel') : sc(entry.registered ? 'rerun' : 'run')}
          disabled={!!run?.cancelRequested}
          onClick={onRunOrCancel}
          className={cn(!busy && 'text-ink-3 group-hover:text-ink focus-visible:text-ink')}
        >
          {busy ? <Square size={ICON_SIZE.sm} /> : <Play size={ICON_SIZE.sm} />}
        </IconButton>
      </div>

      <FailureRecovery script={entry.script} run={run} />

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

/**
 * 行首的状态点（6px，坐在 16px 列里）：实心 = 已关联；空心 = 还没跑过；
 * 呼吸 = 正在跑；红 = 这次失败。纯装饰——状态本身由旁边那句话与可达名说出。
 */
function StatusDot({ entry, run }: { entry: ScriptInventoryEntry; run: ScriptRunState | undefined }) {
  const phase = run?.phase ?? 'idle'
  const running = phase === 'starting_runtime' || phase === 'running'
  const failed = !running && !!run?.error
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden>
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          running
            ? 'animate-pulse bg-ink-2'
            : failed
              ? 'bg-danger'
              : entry.registered
                ? 'bg-ink-2'
                : 'border border-ink-faint',
        )}
      />
    </span>
  )
}

/**
 * 状态一句话（元数据档，靠右、单行截断）：不暴露内部术语，错误按稳定 code
 * 翻成当前语言。发现了图时那句话本身就是「查看捕获结果」的入口。
 */
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
  let title: string | undefined
  if (phase === 'starting_runtime' || phase === 'running') {
    body = sc(phase === 'running' ? 'running' : 'starting')
  } else if (phase === 'captured_one' || phase === 'captured_many') {
    body = (
      // 可见的是结果本身（「已发现 3 张图」），动作名「查看捕获结果」给读屏与气泡
      <button
        onClick={onViewResults}
        className="max-w-full truncate rounded-xs text-ink-2 underline-offset-2 outline-none hover:text-ink hover:underline focus-visible:focus-ring"
        title={sc('viewResults')}
      >
        {sc('captured', { count: run!.descriptors.length })}
        <span className="sr-only">，{sc('viewResults')}</span>
      </button>
    )
  } else if (phase === 'cancelled') {
    body = sc('cancelledNote')
  } else if (run?.error) {
    const text = formatMessage(backendCodeMsg(run.error.code, run.error.params, run.error.message))
    title = text
    body = <span className="text-danger">{text}</span>
  } else if (entry.registered) {
    body = sc('linkedCount', { count: stems.length })
  } else if (entry.reason === 'dynamic_stems' || entry.reason === 'unparseable') {
    body = sc('runtimeNamesNote')
  } else {
    body = sc('notRunNote')
  }

  // aria-live 挂在常驻容器上（内容只随相位变化）：loading / 完成 / 失败
  // 各播报一次，绝不逐帧刷
  return (
    <span
      aria-live="polite"
      title={title}
      className="flex min-w-0 max-w-[55%] shrink items-center truncate type-meta"
    >
      {body}
    </span>
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
    // 缩进到文件名那一列（状态点列 + 间距），不套框：它是这一行的第二行，不是另一张卡
    <div className="mb-1.5 mt-0.5 flex flex-col gap-1.5 pl-8 pr-2">
      <p className="type-caption">{sc('recoveryBody')}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          variant="secondary"
          size="sm"
          // 渲染环境卡片住在设置的「关于」段（EngineEnvironmentCard）
          onClick={() => useUiStore.getState().setSettingsOpen(true, 'about')}
        >
          <Settings size={ICON_SIZE.sm} />
          {sc('openEnvSettings')}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => void copyDiagnostics()}>
          <Copy size={ICON_SIZE.sm} />
          {sc(copied ? 'copied' : 'copyDiagnostics')}
        </Button>
      </div>
      {error?.traceback && (
        <Details>
          <Summary className="type-meta">{sc('diagnostics')}</Summary>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs leading-snug text-ink-2">
            {error.traceback}
          </pre>
        </Details>
      )}
    </div>
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
      <ul className="flex flex-col divide-y divide-border" aria-label={sc('resultsListAria')}>
        {descriptors.map((d) => (
          <li key={d.asset_id} className="flex items-center gap-2 py-1">
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={d.stem}>
              {d.stem}
            </span>
            <span className="shrink-0 type-meta tabular-nums">
              {translate('measure.cmSize', {
                w: formatCm(d.size_mm[0]),
                h: formatCm(d.size_mm[1]),
              })}
            </span>
            <Button
              variant="secondary"
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
        <p className="mt-1.5 flex items-start gap-1 type-meta">
          <Ban size={ICON_SIZE.xs} className="mt-0.5 shrink-0" />
          {sc('dropped', { count: dropped })}
        </p>
      )}
    </Dialog>
  )
}
