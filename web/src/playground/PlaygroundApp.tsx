import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check,
  Download,
  FileCode2,
  Loader2,
  RotateCcw,
  RotateCw,
  ShieldAlert,
  TriangleAlert,
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
import { exampleById, type PlaygroundExample } from './examples'
import { GuidedTask } from './components/GuidedTask'
import { PlaygroundFailureActions } from './components/PlaygroundFailureActions'
import { PlaygroundLanding } from './components/PlaygroundLanding'
import { PlaygroundLoading } from './components/PlaygroundLoading'
import { pg } from './pgText'
import {
  openFigure,
  startSession,
  teardownSession,
  verifySourceIntegrity,
  type ActiveSession,
} from './playgroundSession'
import { discardWarmClient, schedulePrewarm } from './prewarm'
import { PlaygroundError, type PlaygroundClient } from './pyodideClient'
import type { FigureChoice, PlaygroundFailure, PlaygroundPhase } from './protocol'
import { MAX_SOURCE_BYTES } from './runtime'
import { shortHash, type SourceIntegrity } from './sourceIntegrity'

/** 摘要算法名是技术标识，不是文案，翻译它只会让人对不上号 */
const HASH_ALGO = 'SHA-256'

/**
 * 会话来源：内置案例还是用户上传。loading / pick / nofigure / edit / failed
 * 全程携带——加载页据此写案例名、编辑器据此给「换一个案例」还是
 * 「换一个脚本」、失败页据此推荐出口、首次引导只对内置案例出现。
 */
export type PlaygroundOrigin =
  | { kind: 'example'; exampleId: string }
  | { kind: 'upload' }

/** 来源的人类可读名：案例用 i18n 过的案例名，上传用文件名原文。 */
const originTitle = (origin: PlaygroundOrigin, filename: string): string => {
  if (origin.kind === 'example') {
    const ex = exampleById(origin.exampleId)
    if (ex) return pg(ex.titleKey)
  }
  return filename
}

/**
 * 回站首页的地址，**跟着当前界面语言走**：中文会话回中文首页。
 * 用相对路径而不是写死 `/`：产物挂在 `/try/` 下（vite 的 `base: './'`），
 * 相对路径让它在任何前缀下、以及独立托管时都指得对。
 */
const homeHref = () => (currentLocale() === 'zh-CN' ? '../zh/' : '../')

/** 顶栏左上角的品牌 = 回站入口。两处 header 共用同一份，别各写一个。 */
function BrandLink() {
  return (
    <a
      href={homeHref()}
      title={pg('backHome')}
      aria-label={pg('backHome')}
      className="shrink-0 rounded-sm text-[13px] font-semibold tracking-tight text-ink transition-colors hover:text-sel"
    >
      {PRODUCT_NAME}
    </a>
  )
}

type Stage =
  | { kind: 'idle' }
  | { kind: 'loading'; phase: PlaygroundPhase | 'start'; filename: string; origin: PlaygroundOrigin }
  | { kind: 'pick'; figures: FigureChoice[]; log: string; truncated: number; origin: PlaygroundOrigin }
  | { kind: 'nofigure'; log: string; origin: PlaygroundOrigin }
  | { kind: 'edit'; panelId: string; fileId: string; stem: string; origin: PlaygroundOrigin }
  | { kind: 'failed'; failure: PlaygroundFailure; filename: string; origin: PlaygroundOrigin }

/**
 * 浏览器 playground：以真实科研图案例为核心的交互式体验空间（V2，
 * docs/ux/PLAYGROUND_V2.md）。案例经真 Pyodide 真实执行——封面只是卡片
 * 展示，启动永远走 `openSource(example.filename, example.source)`。
 *
 * 这个组件不做任何图形编辑逻辑——拖拽、命中、吸附、undo 全部是既有代码。
 * 它管的只有：顶层状态机（含会话来源）、session 生命周期、案例/上传入口、
 * 加载阶段的真话进度、错误分诊、「源文件未被修改」的证明、以及去桌面版的
 * 诚实出口。展示组件都在 `./components/`，不碰 Worker。
 */
export function PlaygroundApp() {
  const [stage, setStage] = useState<Stage>({ kind: 'idle' })
  const sessionRef = useRef<ActiveSession | null>(null)
  // 启动序号：取消/换案例后，旧启动的迟到结果一律作废（不许两个 Worker）
  const launchSeq = useRef(0)
  // 正在加载的那次会话的 client——取消时要能 dispose 它
  const loadingClientRef = useRef<PlaygroundClient | null>(null)
  // 语言切换要触发重渲染（本组件大量用 pg() 而不是 useTranslation）
  const [, setLocaleTick] = useState(0)

  useEffect(
    () => () => {
      teardownSession(sessionRef.current)
      discardWarmClient()
    },
    [],
  )

  /** 这次回到空状态是不是由「取消加载」带来的（见下面的 effect）。 */
  const cancelledRef = useRef(false)

  /**
   * 空闲时预热 Pyodide 核心（`prewarm.ts`）。挂载后与每次回到空状态各一次，
   * **但「取消」带来的那一次要等用户再次表达意图**——否则取消刚杀掉一个
   * Worker，紧接着又起一个在后台继续下载。
   *
   * 三条纪律：① 只发生在 `/try` 这个应用页面上——营销首页是另一个仓库里的
   * 静态页，与本模块毫无连接，一个字节的 Pyodide 都不会加载；② 首帧不等它，
   * 排在 idle 回调里（案例卡片早就渲染完了）；③ **只到核心为止**，科学栈
   * 仍然要等 import 分类说了话才下载。saveData / 慢网下整个跳过。
   */
  useEffect(() => {
    if (stage.kind !== 'idle') return
    const afterCancel = cancelledRef.current
    cancelledRef.current = false
    return schedulePrewarm({ afterCancel })
  }, [stage.kind])

  const fail = useCallback((failure: PlaygroundFailure, filename: string, origin: PlaygroundOrigin) => {
    teardownSession(sessionRef.current)
    sessionRef.current = null
    setStage({ kind: 'failed', failure, filename, origin })
  }, [])

  const openSource = useCallback(
    async (filename: string, source: string, origin: PlaygroundOrigin) => {
      const seq = ++launchSeq.current
      teardownSession(sessionRef.current)
      sessionRef.current = null
      setStage({ kind: 'loading', phase: 'start', filename, origin })
      try {
        const { session, load } = await startSession(
          filename,
          source,
          (phase) => {
            if (seq !== launchSeq.current) return
            setStage((s) => (s.kind === 'loading' ? { ...s, phase } : s))
          },
          (client) => {
            loadingClientRef.current = client
          },
        )
        loadingClientRef.current = null
        if (seq !== launchSeq.current) {
          // 这次启动已被取消/顶掉：收掉刚起来的会话，不许留第二个 Worker
          teardownSession(session)
          return
        }
        sessionRef.current = session
        if (!load.figures.length) {
          setStage({ kind: 'nofigure', log: load.log, origin })
          return
        }
        if (load.figures.length === 1) {
          const { panelId, fileId } = await openFigure(session, load.figures[0].stem)
          setStage({ kind: 'edit', panelId, fileId, stem: load.figures[0].stem, origin })
          return
        }
        setStage({
          kind: 'pick',
          figures: load.figures,
          log: load.log,
          truncated: load.truncated_figures,
          origin,
        })
      } catch (err) {
        loadingClientRef.current = null
        if (seq !== launchSeq.current) return // 被取消的那次，错误也作废
        fail(
          err instanceof PlaygroundError
            ? err.failure
            : { code: 'runtime_failure', message: err instanceof Error ? err.message : String(err) },
          filename,
          origin,
        )
      }
    },
    [fail],
  )

  const openExample = useCallback(
    (ex: PlaygroundExample) =>
      void openSource(ex.filename, ex.source, { kind: 'example', exampleId: ex.id }),
    [openSource],
  )

  const openFile = useCallback(
    async (file: File) => {
      const origin: PlaygroundOrigin = { kind: 'upload' }
      if (!file.name.toLowerCase().endsWith('.py')) {
        setStage({
          kind: 'failed',
          failure: { code: 'wrong_extension', message: file.name },
          filename: file.name,
          origin,
        })
        return
      }
      if (file.size > MAX_SOURCE_BYTES) {
        setStage({
          kind: 'failed',
          failure: { code: 'source_too_large', message: file.name },
          filename: file.name,
          origin,
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
          origin,
        })
        return
      }
      await openSource(file.name, source, origin)
    },
    [openSource],
  )

  const pickFigure = useCallback(
    async (stem: string, origin: PlaygroundOrigin) => {
      const session = sessionRef.current
      if (!session) return
      try {
        const { panelId, fileId } = await openFigure(session, stem)
        setStage({ kind: 'edit', panelId, fileId, stem, origin })
      } catch (err) {
        fail(
          err instanceof PlaygroundError
            ? err.failure
            : { code: 'render_error', message: err instanceof Error ? err.message : String(err) },
          session.filename,
          origin,
        )
      }
    },
    [fail],
  )

  /** 回案例库：teardown 会话、清 Worker、清文档态。预热账本不动，可复用。 */
  const reset = useCallback(() => {
    launchSeq.current++
    teardownSession(sessionRef.current)
    sessionRef.current = null
    setStage({ kind: 'idle' })
  }, [])

  /** 取消加载：真正 dispose 在途 Worker（不是把加载藏起来），回案例库。 */
  const cancelLoading = useCallback(() => {
    launchSeq.current++
    cancelledRef.current = true      // 紧随其后的那次空闲预热要等明确意图
    loadingClientRef.current?.dispose()
    loadingClientRef.current = null
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
        <BrandLink />
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
      return chrome(<PlaygroundLanding onLaunch={openExample} onFile={(f) => void openFile(f)} />)
    case 'loading':
      return chrome(
        <PlaygroundLoading
          phase={stage.phase}
          filename={stage.filename}
          title={originTitle(stage.origin, stage.filename)}
          onCancel={cancelLoading}
        />,
      )
    case 'pick':
      return chrome(
        <PickView
          figures={stage.figures}
          truncated={stage.truncated}
          origin={stage.origin}
          onPick={(s) => void pickFigure(s, stage.origin)}
          onBack={reset}
        />,
      )
    case 'nofigure':
      return chrome(<NoFigureView log={stage.log} origin={stage.origin} onBack={reset} />)
    case 'failed':
      return chrome(
        <FailureView
          failure={stage.failure}
          filename={stage.filename}
          onBack={reset}
          onLaunchExample={openExample}
        />,
      )
    case 'edit':
      return (
        <EditorView
          panelId={stage.panelId}
          session={sessionRef.current!}
          origin={stage.origin}
          onLoadAnother={reset}
          onSwitchLocale={() => void switchLocale()}
        />
      )
  }
}

/** 来源决定返回按钮的自然文案：案例说「换一个案例」，上传说「换一个脚本」。 */
const backLabel = (origin: PlaygroundOrigin) =>
  origin.kind === 'example' ? pg('switchExample') : pg('loadAnother')

// ---------------------------------------------------------------- 图选择

function PickView({
  figures,
  truncated,
  origin,
  onPick,
  onBack,
}: {
  figures: FigureChoice[]
  truncated: number
  origin: PlaygroundOrigin
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
        {backLabel(origin)}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------- 无图 / 失败

function NoFigureView({
  log,
  origin,
  onBack,
}: {
  log: string
  origin: PlaygroundOrigin
  onBack: () => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6">
      <p className="text-[14px] font-medium">{pg('noFigureTitle')}</p>
      <p className="max-w-md text-center text-xs leading-relaxed text-ink-2">{pg('noFigureBody')}</p>
      {log && <LogDisclosure label={pg('showLog')} text={log} open />}
      <button onClick={onBack} className="btn-back mt-2 h-7 rounded-sm border border-border px-3 text-xs text-ink-2 hover:text-ink">
        {backLabel(origin)}
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
  onLaunchExample,
}: {
  failure: PlaygroundFailure
  filename: string
  onBack: () => void
  onLaunchExample: (ex: PlaygroundExample) => void
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
      {/* 三个明确出口：回案例库 / 试内置案例（30 秒成功路径）/ 桌面版 */}
      <PlaygroundFailureActions onBack={onBack} onLaunch={onLaunchExample} />
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
  origin,
  onLoadAnother,
  onSwitchLocale,
}: {
  panelId: string
  session: ActiveSession
  origin: PlaygroundOrigin
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
  // 首次引导（只对内置案例）：跳过/关闭记在会话态里，同一会话不再出现
  const [taskDismissed, setTaskDismissed] = useState(false)
  // 会话起来时那次核对的结论；下面在有意义的时刻重新核对
  const [integrity, setIntegrity] = useState<SourceIntegrity>(session.integrity)
  // 从 1 起：**进编辑态本身就要复核一次**。load 时那次摘要是在 `open` 之前
  // 采的，而 `open` 会再画一遍——脚本注册的 `draw_event` 回调正是在那一刻
  // 才有机会改写自己的源文件。只信 load 那次，等于漏掉了两者之间的窗口。
  const [recheckSeq, setRecheckSeq] = useState(1)

  const example = origin.kind === 'example' ? exampleById(origin.exampleId) : undefined

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

  const overrideCount = panel?.overrides.length ?? 0

  /**
   * 复验源文件完整性：让 Worker 再读一次虚拟 FS 里的脚本重算 sha256。
   *
   * **只在 worker 闲着的时候发**：无阶段请求的硬超时是 30s，排在一次慢渲染
   * 后面就可能到点，而到点等于整个会话被 terminate——为一条状态指示把用户
   * 的编辑现场炸掉，那是本末倒置。所以请求排队，等这一版画完再发。
   */
  const busy = rendering || pending
  const servedRef = useRef(0)
  useEffect(() => {
    if (recheckSeq === servedRef.current || busy) return
    let alive = true
    setIntegrity((cur) => ({ ...cur, verdict: 'checking' }))
    void verifySourceIntegrity(session).then((next) => {
      if (!alive) return
      // **采纳了结果才算服务过**。在 then 之前就推进 servedRef 的话，用户在
      // 核对在途时动一下（busy 翻转 → cleanup 把 alive 置 false）就会把结果
      // 丢掉，而序号已经推过、busy 落下来也不会再发一次——徽章永远停在
      // 「核对中」，一次本该报出来的 mismatch 就此隐身。
      servedRef.current = recheckSeq
      setIntegrity(next)
    })
    return () => {
      alive = false
    }
  }, [recheckSeq, busy, session])

  // 第一次真的改完并画出来之后复验一次——「我改了图，源文件仍然一个字节
  // 没动」正是这句话该被证明的时刻
  const checkedAfterEdit = useRef(false)
  useEffect(() => {
    if (overrideCount === 0 || checkedAfterEdit.current) return
    checkedAfterEdit.current = true
    setRecheckSeq((n) => n + 1)
  }, [overrideCount])

  const openSourceDialog = () => {
    setShowSource(true)
    setRecheckSeq((n) => n + 1)
  }

  const requestRecheck = useCallback(() => setRecheckSeq((n) => n + 1), [])

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
        <BrandLink />
        <span className="hidden text-xs text-ink-3 sm:inline">{pg('title')}</span>

        <span className="mx-1 h-4 w-px bg-border" />
        <button
          onClick={openSourceDialog}
          className="flex h-7 items-center gap-1.5 rounded-sm px-2 font-mono text-[11px] text-ink-2 hover:bg-surface-2"
          title={pg('sourceNote')}
        >
          <FileCode2 size={12} aria-hidden />
          <span className="max-w-[16ch] truncate">{session.scriptName}</span>
          <IntegrityBadge integrity={integrity} />
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
          {backLabel(origin)}
        </button>
        <button
          onClick={onSwitchLocale}
          className="h-7 rounded-sm px-2 text-xs text-ink-3 hover:bg-surface-2"
          lang={currentLocale() === 'zh-CN' ? 'en' : 'zh-Hans'}
        >
          {currentLocale() === 'zh-CN' ? 'EN' : '中文'}
        </button>
      </header>

      {/* 不变式失效：Tavotto 保证碰不到源文件，而工作区里那个文件确实变了。
          这不是一条提示，是「别再信这个会话」——所以常驻、不可关、带技术细节。 */}
      {integrity.verdict === 'changed' && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-danger/40 bg-danger/8 px-3 py-2"
        >
          <ShieldAlert size={14} className="mt-0.5 shrink-0 text-danger" aria-hidden />
          <div className="min-w-0 text-xs leading-relaxed text-ink-2">
            <p className="font-medium text-danger">
              {session.scriptName} · {pg('changed')}
            </p>
            <p className="mt-0.5">{pg('integrityMismatchNote')}</p>
            <p className="mt-1 font-mono text-[11px] text-ink-3">
              {shortHash(integrity.originalSha256)} → {shortHash(integrity.workspaceSha256)}
            </p>
          </div>
        </div>
      )}

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
        {/* CanvasStage 的根是 flex-1：外面必须是 flex 容器（见 McpApp 的注）。
            relative 是给首次引导那张小卡定位用的——它浮在画布左下角，
            不遮树、不遮属性页、无全屏遮罩 */}
        <div className="relative flex min-h-0 min-w-0 flex-1">
          <CanvasStage />
          {example?.guidedTask && !taskDismissed && (
            <GuidedTask
              task={example.guidedTask}
              scriptName={session.scriptName}
              panel={panel}
              integrity={integrity}
              renderBusy={busy}
              renderFailed={renderError != null}
              onRequestIntegrityRecheck={requestRecheck}
              onViewSource={openSourceDialog}
              onDismiss={() => setTaskDismissed(true)}
            />
          )}
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
        <SourceDialog
          filename={session.scriptName}
          source={session.originalSource}
          integrity={integrity}
          onClose={() => setShowSource(false)}
        />
      )}
    </div>
  )
}

/**
 * 源文件完整性的一枚小徽章。四种状态各说各的话：
 *
 *   checking      还在核对——**这时候不许说「未改动」**
 *   unchanged     两个 sha256 相等（一个在主线程算原文，一个在 Worker 里读
 *                 虚拟 FS 算实际执行的那份）
 *   changed       不变式失效，按危险色报
 *   unavailable   这个浏览器算不出哈希（非安全上下文没有 crypto.subtle），
 *                 或者 Worker 那次查询失败——「查不了」不是「没改」
 */
function IntegrityBadge({ integrity }: { integrity: SourceIntegrity }) {
  const { verdict } = integrity
  const label =
    verdict === 'unchanged'
      ? pg('unchanged')
      : verdict === 'changed'
        ? pg('changed')
        : verdict === 'checking'
          ? pg('integrityChecking')
          : pg('integrityUnverified')
  return (
    <span
      className={cn(
        'flex items-center gap-1',
        verdict === 'changed' ? 'text-danger' : verdict === 'unchanged' ? 'text-ink-3' : 'text-ink-faint',
      )}
    >
      {verdict === 'changed' && <ShieldAlert size={11} aria-hidden />}
      · {label}
    </span>
  )
}

/** 完整性明细：默认收起的一行技术事实，不做成安全仪表盘。 */
function IntegrityDetails({ integrity }: { integrity: SourceIntegrity }) {
  const { verdict, originalSha256, workspaceSha256 } = integrity
  const note =
    verdict === 'changed'
      ? pg('integrityMismatchNote')
      : verdict === 'unavailable'
        ? pg('integrityUnavailableNote')
        : pg('integrityNote')
  return (
    <details className="shrink-0 border-t border-border px-4 py-2">
      <summary className="cursor-pointer text-xs text-ink-3">{pg('integrityTitle')}</summary>
      <p className={cn('mt-1.5 text-xs leading-relaxed', verdict === 'changed' ? 'text-danger' : 'text-ink-3')}>
        {note}
      </p>
      {(originalSha256 || workspaceSha256) && (
        <p className="mt-1.5 font-mono text-[11px] text-ink-2">
          {HASH_ALGO}{' '}
          {verdict === 'changed'
            ? `${shortHash(originalSha256)} → ${shortHash(workspaceSha256)}`
            : shortHash(workspaceSha256 || originalSha256)}
        </p>
      )}
    </details>
  )
}

/** 只读源码面板：证明编辑发生在 override 层，源文件一个字节没动。 */
function SourceDialog({
  filename,
  source,
  integrity,
  onClose,
}: {
  filename: string
  source: string
  integrity: SourceIntegrity
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
          <span className="font-mono text-xs">
            <IntegrityBadge integrity={integrity} />
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
        <IntegrityDetails integrity={integrity} />
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
