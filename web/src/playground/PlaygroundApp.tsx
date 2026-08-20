import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check,
  Download,
  FileCode2,
  Loader2,
  RotateCcw,
  RotateCw,
  TriangleAlert,
  Upload,
  X,
} from 'lucide-react'
import { CanvasStage } from '@/canvas/CanvasStage'
import { ElementInspector } from '@/components/inspector/ElementInspector'
import { ElementTree } from '@/components/left/ElementTree'
import { useEngineSync } from '@/hooks/useEngineSync'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { currentLocale, formatMessage, msg, setLocale, t as translate, type UiMessage } from '@/i18n'
import { PRODUCT_NAME, RELEASES_LATEST_URL } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelRender } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'
import { EXAMPLES } from './examples'
import {
  openFigure,
  startSession,
  teardownSession,
  type ActiveSession,
} from './playgroundSession'
import { PlaygroundError } from './pyodideClient'
import type { FigureChoice, PlaygroundFailure, PlaygroundPhase } from './protocol'
import { MAX_SOURCE_BYTES, PYODIDE_VERSION, RUNTIME_PACKAGES } from './runtime'

/** playground 这一屏的文案都在 `dialogs:playground.*` 下 */
const pg = (key: string, values?: Record<string, unknown>) =>
  translate(`playground.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 拖放区里的文件名示意——语言中立的字面量，不进翻译 */
const SAMPLE_FILENAME = 'figure.py'

type Stage =
  | { kind: 'idle' }
  | { kind: 'loading'; phase: PlaygroundPhase | 'start'; filename: string }
  | { kind: 'pick'; figures: FigureChoice[]; log: string; truncated: number }
  | { kind: 'nofigure'; log: string }
  | { kind: 'edit'; panelId: string; fileId: string; stem: string }
  | { kind: 'failed'; failure: PlaygroundFailure; filename: string }

/**
 * 浏览器 playground：把一个普通的 Matplotlib `.py` 在本机 Pyodide 里跑起来，
 * 然后用 Tavotto **同一份**画布 / 属性页 / stores 做语义编辑（ADR 0007）。
 *
 * 这个组件不做任何图形编辑逻辑——拖拽、命中、吸附、undo 全部是既有代码。
 * 它管的只有：上传与示例入口、加载阶段的真话进度、错误分诊、
 * 「源文件未被修改」的证明、以及去桌面版的诚实出口。
 */
export function PlaygroundApp() {
  const [stage, setStage] = useState<Stage>({ kind: 'idle' })
  const sessionRef = useRef<ActiveSession | null>(null)
  // 语言切换要触发重渲染（本组件大量用 pg() 而不是 useTranslation）
  const [, setLocaleTick] = useState(0)

  useEffect(() => () => teardownSession(sessionRef.current), [])

  const fail = useCallback((failure: PlaygroundFailure, filename: string) => {
    teardownSession(sessionRef.current)
    sessionRef.current = null
    setStage({ kind: 'failed', failure, filename })
  }, [])

  const openSource = useCallback(
    async (filename: string, source: string) => {
      teardownSession(sessionRef.current)
      sessionRef.current = null
      setStage({ kind: 'loading', phase: 'start', filename })
      try {
        const { session, load } = await startSession(filename, source, (phase) =>
          setStage((s) => (s.kind === 'loading' ? { ...s, phase } : s)),
        )
        sessionRef.current = session
        if (!load.figures.length) {
          setStage({ kind: 'nofigure', log: load.log })
          return
        }
        if (load.figures.length === 1) {
          const { panelId, fileId } = await openFigure(session, load.figures[0].stem)
          setStage({ kind: 'edit', panelId, fileId, stem: load.figures[0].stem })
          return
        }
        setStage({
          kind: 'pick',
          figures: load.figures,
          log: load.log,
          truncated: load.truncated_figures,
        })
      } catch (err) {
        fail(
          err instanceof PlaygroundError
            ? err.failure
            : { code: 'runtime_failure', message: err instanceof Error ? err.message : String(err) },
          filename,
        )
      }
    },
    [fail],
  )

  const openFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith('.py')) {
        setStage({
          kind: 'failed',
          failure: { code: 'wrong_extension', message: file.name },
          filename: file.name,
        })
        return
      }
      if (file.size > MAX_SOURCE_BYTES) {
        setStage({
          kind: 'failed',
          failure: { code: 'source_too_large', message: file.name },
          filename: file.name,
        })
        return
      }
      let source: string
      try {
        source = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
      } catch {
        setStage({
          kind: 'failed',
          failure: { code: 'decode_error', message: file.name },
          filename: file.name,
        })
        return
      }
      await openSource(file.name, source)
    },
    [openSource],
  )

  const pickFigure = useCallback(
    async (stem: string) => {
      const session = sessionRef.current
      if (!session) return
      try {
        const { panelId, fileId } = await openFigure(session, stem)
        setStage({ kind: 'edit', panelId, fileId, stem })
      } catch (err) {
        fail(
          err instanceof PlaygroundError
            ? err.failure
            : { code: 'render_error', message: err instanceof Error ? err.message : String(err) },
          session.filename,
        )
      }
    },
    [fail],
  )

  const reset = useCallback(() => {
    teardownSession(sessionRef.current)
    sessionRef.current = null
    setStage({ kind: 'idle' })
  }, [])

  const switchLocale = useCallback(async () => {
    await setLocale(currentLocale() === 'zh-CN' ? 'en-US' : 'zh-CN')
    setLocaleTick((n) => n + 1)
  }, [])

  const chrome = (body: React.ReactNode) => (
    <div className="flex h-full w-full flex-col bg-bg text-ink">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-surface px-4">
        <span className="text-[13px] font-semibold tracking-tight">{PRODUCT_NAME}</span>
        <span className="text-xs text-ink-3">{pg('title')}</span>
        <span className="flex-1" />
        <button
          onClick={() => void switchLocale()}
          className="h-7 rounded-sm px-2 text-xs text-ink-2 hover:bg-surface-2"
          lang={currentLocale() === 'zh-CN' ? 'en' : 'zh-Hans'}
        >
          {currentLocale() === 'zh-CN' ? 'English' : '简体中文'}
        </button>
        <a
          href={RELEASES_LATEST_URL}
          className="flex h-7 items-center gap-1.5 rounded-sm bg-ink px-2.5 text-xs text-white"
        >
          <Download size={13} />
          {pg('downloadDesktop')}
        </a>
      </header>
      {body}
    </div>
  )

  switch (stage.kind) {
    case 'idle':
      return chrome(<IdleView onFile={(f) => void openFile(f)} onExample={(f, s) => void openSource(f, s)} />)
    case 'loading':
      return chrome(<LoadingView phase={stage.phase} filename={stage.filename} />)
    case 'pick':
      return chrome(
        <PickView figures={stage.figures} truncated={stage.truncated} onPick={(s) => void pickFigure(s)} onBack={reset} />,
      )
    case 'nofigure':
      return chrome(<NoFigureView log={stage.log} onBack={reset} />)
    case 'failed':
      return chrome(<FailureView failure={stage.failure} filename={stage.filename} onBack={reset} />)
    case 'edit':
      return (
        <EditorView
          panelId={stage.panelId}
          session={sessionRef.current!}
          onLoadAnother={reset}
          onSwitchLocale={() => void switchLocale()}
        />
      )
  }
}

// ---------------------------------------------------------------- 空状态

function IdleView({
  onFile,
  onExample,
}: {
  onFile: (f: File) => void
  onExample: (filename: string, source: string) => void
}) {
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div className="flex min-h-0 flex-1 items-start justify-center overflow-y-auto p-6">
      <div className="w-full max-w-[560px] pt-[7vh]">
        <div
          role="button"
          tabIndex={0}
          aria-label={pg('chooseFile')}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              inputRef.current?.click()
            }
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setOver(true)
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setOver(false)
            const f = e.dataTransfer.files?.[0]
            if (f) onFile(f)
          }}
          className={cn(
            'flex cursor-pointer flex-col items-center gap-3 rounded-[10px] border border-dashed bg-surface px-8 py-12 text-center transition-colors',
            over ? 'border-sel bg-sel/5' : 'border-border hover:border-ink-faint',
          )}
        >
          <FileCode2 size={26} className="text-ink-3" aria-hidden />
          <div>
            <p className="text-[15px] font-medium">{pg('dropTitle')}</p>
            <p className="mt-1 font-mono text-xs text-ink-3">{SAMPLE_FILENAME}</p>
          </div>
          <span className="flex h-7 items-center gap-1.5 rounded-sm border border-border bg-bg px-2.5 text-xs text-ink-2">
            <Upload size={12} aria-hidden />
            {pg('chooseFile')}
          </span>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".py"
          className="sr-only"
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (f) onFile(f)
          }}
        />

        <p className="mt-5 text-center text-xs text-ink-3">{pg('orExample')}</p>
        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex.id}
              onClick={() => onExample(ex.filename, ex.source)}
              className="flex h-7 items-center gap-1.5 rounded-sm border border-border bg-surface px-2.5 text-xs text-ink-2 hover:text-ink"
            >
              {pg(ex.labelKey)}
              <span className="font-mono text-[11px] text-ink-faint">{ex.filename}</span>
            </button>
          ))}
        </div>

        <div className="mt-8 border-t border-border pt-4 text-center">
          <p className="text-xs leading-relaxed text-ink-2">{pg('privacyNote')}</p>
          <p className="mt-2 text-xs leading-relaxed text-ink-3">{pg('scopeNote')}</p>
          <p className="mt-3 font-mono text-[11px] text-ink-3">
            {Object.entries(RUNTIME_PACKAGES)
              .map(([n, v]) => `${n} ${v}`)
              .join(' · ')}
          </p>
          <p className="mt-1 font-mono text-[11px] text-ink-faint">
            {pg('cdnNote', { version: PYODIDE_VERSION })}
          </p>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- 加载

const PHASE_ORDER: (PlaygroundPhase | 'start')[] = [
  'start',
  'runtime',
  'engine',
  'packages',
  'script',
  'figures',
]

function LoadingView({ phase, filename }: { phase: PlaygroundPhase | 'start'; filename: string }) {
  const at = PHASE_ORDER.indexOf(phase)
  const steps: { key: string; values?: Record<string, unknown> }[] = [
    { key: 'phaseRuntime' },
    { key: 'phaseEngine' },
    { key: 'phasePackages' },
    { key: 'phaseScript', values: { filename } },
    { key: 'phaseFigures' },
  ]
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-6">
      {/* 真话进度：确切阶段逐个点亮，没有假造的百分比 */}
      <ol className="flex flex-col gap-2.5" aria-live="polite">
        {steps.map((s, i) => {
          const stepAt = i + 1 // PHASE_ORDER 里 'start' 占 0 位
          const state = at > stepAt ? 'done' : at === stepAt ? 'active' : 'todo'
          return (
            <li key={s.key} className="flex items-center gap-2.5 text-[13px]">
              {state === 'done' ? (
                <Check size={14} className="shrink-0 text-ink-3" aria-hidden />
              ) : state === 'active' ? (
                <Loader2 size={14} className="shrink-0 animate-spin text-sel" aria-hidden />
              ) : (
                <span className="h-3.5 w-3.5 shrink-0 rounded-full border border-border" aria-hidden />
              )}
              <span className={state === 'todo' ? 'text-ink-faint' : 'text-ink-2'}>
                {pg(s.key, s.values)}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

// ---------------------------------------------------------------- 图选择

function PickView({
  figures,
  truncated,
  onPick,
  onBack,
}: {
  figures: FigureChoice[]
  truncated: number
  onPick: (stem: string) => void
  onBack: () => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto p-6">
      <p className="text-[14px] font-medium">{pg('pickTitle')}</p>
      {truncated > 0 && (
        <p className="mt-1 text-xs text-ink-3">{pg('pickTruncated', { count: truncated })}</p>
      )}
      <div className="mt-5 flex flex-wrap items-start justify-center gap-4">
        {figures.map((f) => (
          <button
            key={f.stem}
            onClick={() => onPick(f.stem)}
            className="flex w-[220px] flex-col gap-2 rounded-[6px] border border-border bg-surface p-3 text-left hover:border-sel"
          >
            {f.preview ? (
              <img
                src={`data:image/png;base64,${f.preview}`}
                alt=""
                className="w-full rounded-[4px] border border-border bg-white"
              />
            ) : (
              <span className="flex h-24 items-center justify-center rounded-[4px] border border-border text-xs text-ink-faint">
                {f.stem}
              </span>
            )}
            <span className="truncate text-xs font-medium">{f.stem}</span>
            <span className="font-mono text-[11px] text-ink-3">
              {translate('measure.mmSizeSpaced', {
                w: f.size_mm[0].toFixed(1),
                h: f.size_mm[1].toFixed(1),
              })}
            </span>
          </button>
        ))}
      </div>
      <button onClick={onBack} className="mt-6 text-xs text-ink-3 underline-offset-2 hover:underline">
        {pg('loadAnother')}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------- 无图 / 失败

function NoFigureView({ log, onBack }: { log: string; onBack: () => void }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6">
      <p className="text-[14px] font-medium">{pg('noFigureTitle')}</p>
      <p className="max-w-md text-center text-xs leading-relaxed text-ink-2">{pg('noFigureBody')}</p>
      {log && <LogDisclosure label={pg('showLog')} text={log} open />}
      <button onClick={onBack} className="btn-back mt-2 h-7 rounded-sm border border-border px-3 text-xs text-ink-2 hover:text-ink">
        {pg('loadAnother')}
      </button>
    </div>
  )
}

/** 错误分诊：code → 一句人话 + 出口。traceback/log 收在折叠里，绝不当主文案。 */
function failureText(f: PlaygroundFailure, filename: string): { title: string; body: string } {
  switch (f.code) {
    case 'unsupported_import':
      return {
        title: pg('errUnsupportedImport', { modules: (f.modules ?? []).join(', ') }),
        body: pg('errUnsupportedImportBody'),
      }
    case 'missing_file':
      return {
        title: pg('errMissingFile', { filename: f.filename || '?' }),
        body: pg('errMissingFileBody'),
      }
    case 'timeout':
      return { title: pg('errTimeout'), body: pg('errTimeoutBody') }
    case 'syntax_error':
      return {
        title: f.line ? pg('errSyntaxLine', { line: f.line }) : pg('errSyntax'),
        body: pg('errSyntaxBody'),
      }
    case 'script_error':
      return { title: pg('errScript', { filename }), body: pg('errScriptBody') }
    case 'source_too_large':
      return { title: pg('errSourceTooLarge', { kib: MAX_SOURCE_BYTES / 1024 }), body: '' }
    case 'wrong_extension':
      return { title: pg('errWrongExtension'), body: '' }
    case 'decode_error':
      return { title: pg('errDecode'), body: '' }
    case 'out_of_memory':
      return { title: pg('errOom'), body: pg('errOomBody') }
    case 'worker_crashed':
      return { title: pg('errWorkerCrashed'), body: pg('errWorkerCrashedBody') }
    case 'runtime_failure':
      return { title: pg('errRuntime'), body: f.message }
    default:
      return { title: pg('errUnknown'), body: f.message }
  }
}

function FailureView({
  failure,
  filename,
  onBack,
}: {
  failure: PlaygroundFailure
  filename: string
  onBack: () => void
}) {
  const { title, body } = failureText(failure, filename)
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 overflow-y-auto p-6">
      <TriangleAlert size={18} className="text-danger" aria-hidden />
      <p className="max-w-lg text-center text-[14px] font-medium" role="alert">
        {title}
      </p>
      {body && <p className="max-w-lg text-center text-xs leading-relaxed text-ink-2">{body}</p>}
      {failure.log && <LogDisclosure label={pg('showLog')} text={failure.log} />}
      {failure.traceback && <LogDisclosure label={pg('showTraceback')} text={failure.traceback} open />}
      <div className="mt-2 flex items-center gap-2">
        <button
          onClick={onBack}
          className="h-7 rounded-sm border border-border px-3 text-xs text-ink-2 hover:text-ink"
        >
          {pg('loadAnother')}
        </button>
        <a
          href={RELEASES_LATEST_URL}
          className="flex h-7 items-center gap-1.5 rounded-sm bg-ink px-3 text-xs text-white"
        >
          <Download size={12} aria-hidden />
          {pg('downloadDesktop')}
        </a>
      </div>
    </div>
  )
}

function LogDisclosure({ label, text, open }: { label: string; text: string; open?: boolean }) {
  return (
    <details className="w-full max-w-lg" open={open}>
      <summary className="cursor-pointer text-xs text-ink-3">{label}</summary>
      <pre className="mt-1 max-h-48 overflow-auto rounded-[6px] border border-border bg-surface p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-ink-2">
        {text}
      </pre>
    </details>
  )
}

// ---------------------------------------------------------------- 编辑器

function EditorView({
  panelId,
  session,
  onLoadAnother,
  onSwitchLocale,
}: {
  panelId: string
  session: ActiveSession
  onLoadAnother: () => void
  onSwitchLocale: () => void
}) {
  // 既有的引擎同步器：文档一变就按策略重渲染，传输层已经换成 Pyodide
  useEngineSync()

  const objects = useDocumentStore((s) => s.doc.objects)
  const panel = objects.find((o): o is PanelObject => o.id === panelId && o.type === 'panel')
  const canUndo = useDocumentStore((s) => s.past.length > 0)
  const canRedo = useDocumentStore((s) => s.future.length > 0)

  const render = usePanelRender(panel ?? null)
  const rendering = render?.status === 'rendering'
  const renderError = render?.status === 'error' ? render.error : null
  const pending = !!panel && JSON.stringify(panel.overrides) !== (render?.lastPatches ?? '[]')

  const [showSource, setShowSource] = useState(false)
  const [showPatches, setShowPatches] = useState(false)
  const [cueDismissed, setCueDismissed] = useState(false)

  // ⌘Z / ⌘⇧Z：与工作台同一条 runUndoRedo 通道（带 undoRedoBlocked 守卫）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'z') return
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      e.preventDefault()
      runUndoRedo(e.shiftKey)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 「源文件未被修改」不是口号，是逐字节比对的结论
  const unchanged = session.loadedSource === session.originalSource
  const overrideCount = panel?.overrides.length ?? 0

  const resetEdits = () => {
    if (!panel || panel.overrides.length === 0) return
    useDocumentStore.getState().commit(msg('history.playgroundResetEdits', undefined, 'workspace'), (d) => {
      const p = d.objects.find((o) => o.id === panelId)
      if (p?.type === 'panel') p.overrides = []
    })
  }

  if (!panel) {
    return <div className="p-4 text-sm text-ink-2">{pg('panelGone')}</div>
  }

  return (
    <div className="flex h-full w-full flex-col bg-bg text-ink">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-surface px-3">
        <span className="text-[13px] font-semibold tracking-tight">{PRODUCT_NAME}</span>
        <span className="hidden text-xs text-ink-3 sm:inline">{pg('title')}</span>

        <span className="mx-1 h-4 w-px bg-border" />
        <button
          onClick={() => setShowSource(true)}
          className="flex h-7 items-center gap-1.5 rounded-sm px-2 font-mono text-[11px] text-ink-2 hover:bg-surface-2"
          title={pg('sourceNote')}
        >
          <FileCode2 size={12} aria-hidden />
          <span className="max-w-[16ch] truncate">{session.filename}</span>
          <span className={cn('flex items-center gap-1', unchanged ? 'text-ink-3' : 'text-danger')}>
            · {unchanged ? pg('unchanged') : pg('changed')}
          </span>
        </button>
        <button
          onClick={() => setShowPatches((v) => !v)}
          className="h-7 rounded-sm px-2 font-mono text-[11px] text-ink-3 hover:bg-surface-2"
        >
          {pg('overrides', { count: overrideCount })}
        </button>

        <span className="mx-1 h-4 w-px bg-border" />
        <IconButton label={translate('topbar.undo', { ns: 'workspace' })} disabled={!canUndo} onClick={() => runUndoRedo(false)}>
          <RotateCcw size={13} />
        </IconButton>
        <IconButton label={translate('topbar.redo', { ns: 'workspace' })} disabled={!canRedo} onClick={() => runUndoRedo(true)}>
          <RotateCw size={13} />
        </IconButton>
        <button
          onClick={resetEdits}
          disabled={overrideCount === 0}
          className="h-7 rounded-sm px-2 text-xs text-ink-2 hover:bg-surface-2 disabled:opacity-30"
        >
          {pg('resetEdits')}
        </button>

        <span className="flex-1" />
        <RenderState rendering={rendering} pending={pending} error={renderError} />
        <button onClick={onLoadAnother} className="h-7 rounded-sm px-2 text-xs text-ink-2 hover:bg-surface-2">
          {pg('loadAnother')}
        </button>
        <button
          onClick={onSwitchLocale}
          className="h-7 rounded-sm px-2 text-xs text-ink-3 hover:bg-surface-2"
          lang={currentLocale() === 'zh-CN' ? 'en' : 'zh-Hans'}
        >
          {currentLocale() === 'zh-CN' ? 'EN' : '中文'}
        </button>
      </header>

      {showPatches && (
        <div className="shrink-0 border-b border-border bg-surface px-3 py-2">
          {/* 真实的 Tavotto patch 表示，不造一个更友好的假格式 */}
          <pre className="max-h-40 overflow-auto font-mono text-[11px] leading-relaxed text-ink-2">
            {JSON.stringify(panel.overrides, null, 2)}
          </pre>
        </div>
      )}

      {/* 窄屏是刻意的受限形态（ADR 0007）：画布可看可拖，树与属性页收起，
          不硬塞一个 375px 上没法精确操作的三栏 */}
      <p className="shrink-0 border-b border-border bg-surface px-3 py-1.5 text-xs text-ink-3 md:hidden">
        {pg('mobileNote')}
      </p>
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-[224px] shrink-0 flex-col overflow-y-auto border-r border-border bg-surface lg:flex">
          <ElementTree />
        </aside>
        {/* CanvasStage 的根是 flex-1：外面必须是 flex 容器（见 McpApp 的注） */}
        <div className="flex min-h-0 min-w-0 flex-1">
          <CanvasStage />
        </div>
        <aside className="hidden w-[304px] shrink-0 flex-col overflow-y-auto border-l border-border bg-surface md:flex">
          <ElementInspector panel={panel} />
        </aside>
      </div>

      {overrideCount > 0 && !cueDismissed && (
        <footer className="flex shrink-0 items-center gap-3 border-t border-border bg-surface px-3 py-1.5">
          <p className="min-w-0 flex-1 truncate text-xs text-ink-3">{pg('desktopNote')}</p>
          <a
            href={RELEASES_LATEST_URL}
            className="flex h-6 shrink-0 items-center gap-1 rounded-sm border border-border px-2 text-[11px] text-ink-2 hover:text-ink"
          >
            <Download size={11} aria-hidden />
            {pg('downloadDesktop')}
          </a>
          <button
            onClick={() => setCueDismissed(true)}
            aria-label={translate('actions.close')}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm text-ink-3 hover:bg-surface-2"
          >
            <X size={12} />
          </button>
        </footer>
      )}

      {showSource && (
        <SourceDialog filename={session.filename} source={session.originalSource} unchanged={unchanged} onClose={() => setShowSource(false)} />
      )}
    </div>
  )
}

/** 只读源码面板：证明编辑发生在 override 层，源文件一个字节没动。 */
function SourceDialog({
  filename,
  source,
  unchanged,
  onClose,
}: {
  filename: string
  source: string
  unchanged: boolean
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/20 p-6"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={pg('sourceTitle')}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-[10px] border border-border bg-surface shadow-pop"
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
          <span className="font-mono text-xs">{filename}</span>
          <span className={cn('text-xs', unchanged ? 'text-ink-3' : 'text-danger')}>
            · {unchanged ? pg('unchanged') : pg('changed')}
          </span>
          <span className="flex-1" />
          <button
            onClick={onClose}
            aria-label={translate('actions.close')}
            className="flex h-6 w-6 items-center justify-center rounded-sm text-ink-3 hover:bg-surface-2"
          >
            <X size={13} />
          </button>
        </div>
        <p className="shrink-0 border-b border-border px-4 py-2 text-xs leading-relaxed text-ink-3">
          {pg('sourceNote')}
        </p>
        <pre className="min-h-0 flex-1 overflow-auto p-4 font-mono text-[12px] leading-relaxed text-ink-2">
          {source}
        </pre>
      </div>
    </div>
  )
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-ink-2 hover:bg-surface-2 disabled:opacity-30"
    >
      {children}
    </button>
  )
}

/** 渲染态：正在画 / 有改动没画上 / 画失败了。三者都必须看得见。 */
function RenderState({
  rendering,
  pending,
  error,
}: {
  rendering: boolean
  pending: boolean
  error: UiMessage | null
}) {
  if (error) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-xs text-danger" title={formatMessage(error)}>
        <TriangleAlert size={12} />
        {pg('renderFailed')}
      </span>
    )
  }
  if (rendering || pending) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-xs text-ink-3">
        <Loader2 size={12} className={rendering ? 'animate-spin' : ''} />
        {rendering ? pg('rendering') : pg('pendingEdits')}
      </span>
    )
  }
  return (
    <span className="flex shrink-0 items-center gap-1 text-xs text-ink-3">
      <Check size={12} />
      {pg('synced')}
    </span>
  )
}
