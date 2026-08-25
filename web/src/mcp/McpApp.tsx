import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Check,
  Download,
  Lightbulb,
  Loader2,
  RotateCcw,
  RotateCw,
  ShieldCheck,
  ShieldQuestion,
  TriangleAlert,
} from 'lucide-react'
import { CanvasStage } from '@/canvas/CanvasStage'
import { ElementInspector } from '@/components/inspector/ElementInspector'
import { useEngineSync } from '@/hooks/useEngineSync'
import { formatMessage, t as translate, type UiMessage } from '@/i18n'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelRender } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import type { AppsBridge } from './appsBridge'
import {
  sessionIdFor,
  unwrap,
  type OpenFigureResult,
  type PreflightIssuePayload,
  type PreflightPayload,
} from './session'

/**
 * Codex 内嵌的 Tavotto 画布。
 *
 * 这个组件本身**不做任何图形编辑逻辑**——拖拽、命中测试、shift 锁向、吸附、
 * 属性表单、撤销重做全部由 `CanvasStage` / `ElementInspector` / 既有 stores
 * 承担，与 Tavotto 桌面版跑的是同一份代码。这里只负责三件事：
 *
 *   1. 顶部把「按哪套规范、多大、预检怎么样、有没有还没画上的改动、渲染错了没」
 *      摆出来（用户在别人的界面里，看不到 Tavotto 的状态栏）；
 *   2. 预检 / 导出这两个动作转成 `tools/call`；
 *   3. 拿不出结构化控件的属性，如实说「这条得回代码改」，而不是造一个点了没用的开关。
 */
export function McpApp({
  bridge,
  open,
  panelId,
}: {
  bridge: AppsBridge
  open: OpenFigureResult
  panelId: string
}) {
  // 既有的引擎同步器：文档一变就按策略重渲染。传输层已经换成 MCP 了，
  // 这里一行都不用改
  useEngineSync()

  const objects = useDocumentStore((s) => s.doc.objects)
  const panel = objects.find((o): o is PanelObject => o.id === panelId && o.type === 'panel')
  const canUndo = useDocumentStore((s) => s.past.length > 0)
  const canRedo = useDocumentStore((s) => s.future.length > 0)
  const undo = useDocumentStore((s) => s.undo)
  const redo = useDocumentStore((s) => s.redo)

  // usePanelRender 接受 null（面板还没到位时不该造一个假对象骗它）
  const render = usePanelRender(panel)
  const rendering = render?.status === 'rendering'
  const renderError = render?.status === 'error' ? render.error : null
  // 「有改动还没画上」：文档里的 overrides 与最近画成功的那一版不一致
  const pending =
    !!panel && JSON.stringify(panel.overrides) !== (render?.lastPatches ?? '[]')

  const [preflight, setPreflight] = useState<PreflightPayload | null>(open.preflight ?? null)
  const [preflightStale, setPreflightStale] = useState(false)
  const [busy, setBusy] = useState<'preflight' | 'export' | null>(null)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null)
  const [confirmForced, setConfirmForced] = useState(false)
  const sessionId = sessionIdFor(open.stem + '.pdf') ?? open.session_id

  // 改过图之后旧的预检结论就不作数了——**标成过期而不是留着**，
  // 留着的话用户会拿一份属于上一版的「通过」去导出
  const lastPatches = render?.lastPatches
  const firstRun = useRef(true)
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false
      return
    }
    setPreflightStale(true)
    setConfirmForced(false)
  }, [lastPatches])

  const runPreflight = useCallback(async () => {
    setBusy('preflight')
    setNotice(null)
    try {
      const body = unwrap(await bridge.callTool('tavotto_preflight', { session_id: sessionId }))
      setPreflight(body as unknown as PreflightPayload)
      setPreflightStale(false)
    } catch (err) {
      setNotice({ tone: 'bad', text: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(null)
    }
  }, [bridge, sessionId])

  const runExport = useCallback(
    async (formats: string[]) => {
      setBusy('export')
      setNotice(null)
      try {
        const body = unwrap(
          await bridge.callTool('tavotto_export', {
            session_id: sessionId,
            formats,
            explicit_confirm: confirmForced,
          }),
        )
        const files = (body.files as { path: string }[]) ?? []
        setPreflight((body.preflight as PreflightPayload) ?? preflight)
        setPreflightStale(false)
        setNotice({ tone: 'ok', text: mc('exported', { files: files.map((f) => f.path).join('、') }) })
        // 这句是**发给 Codex 的对话内容**，不是界面文案：它进的是聊天记录，
        // 语言该跟着那边的对话走，不该被这个 webview 的界面语言改写
        bridge.sendMessage(`我在画布里改完并导出了 ${open.stem}：${files.map((f) => f.path).join('、')}`)
      } catch (err) {
        setNotice({ tone: 'bad', text: err instanceof Error ? err.message : String(err) })
      } finally {
        setBusy(null)
      }
    },
    [bridge, sessionId, confirmForced, open.stem, preflight],
  )

  // 手势结束后画布尺寸可能变了：告诉 host 一声（inline 模式下它据此调高度）
  useEffect(() => {
    bridge.notifySize()
  }, [bridge, panel?.w, panel?.h])

  const counts = preflight?.counts ?? {}
  const issues = useMemo(
    () =>
      preflight
        ? [
            ...preflight.errors,
            ...preflight.warnings,
            ...preflight.not_verifiable,
            ...preflight.suggestions,
          ]
        : [],
    [preflight],
  )
  const needsConfirm =
    !!preflight && (preflight.errors.length > 0 || preflight.not_verifiable.length > 0)

  if (!panel) {
    return <div className="p-4 text-sm text-ink-2">{mc('panelGone')}</div>
  }

  return (
    <div className="flex h-full w-full flex-col bg-bg text-ink">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-surface px-3">
        <span className="truncate text-[13px] font-medium">{open.stem}</span>
        <span className="shrink-0 rounded-sm bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-ink-3">
          {open.profile.profile_id} v{open.profile.profile_version}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-ink-3">
          {translate('measure.mmSizeSpaced', { w: panel.w.toFixed(1), h: panel.h.toFixed(1) })}
        </span>

        <span className="mx-1 h-4 w-px bg-border" />
        <IconButton label={translate('topbar.undo', { ns: 'workspace' })} disabled={!canUndo} onClick={() => undo()}>
          <RotateCcw size={13} />
        </IconButton>
        <IconButton label={translate('topbar.redo', { ns: 'workspace' })} disabled={!canRedo} onClick={() => redo()}>
          <RotateCw size={13} />
        </IconButton>

        <span className="flex-1" />

        <RenderState rendering={rendering} pending={pending} error={renderError} />
        <PreflightPill
          counts={counts}
          stale={preflightStale}
          loading={busy === 'preflight'}
          onClick={() => void runPreflight()}
        />
        <button
          className="flex h-7 shrink-0 items-center gap-1.5 rounded-sm bg-ink px-2.5 text-xs text-white disabled:opacity-40"
          disabled={busy != null || pending || (needsConfirm && !confirmForced)}
          title={
            pending
              ? mc('exportPendingTitle')
              : needsConfirm && !confirmForced
                ? mc('exportBlockedTitle')
                : undefined
          }
          onClick={() => void runExport(['pdf', 'png'])}
        >
          {busy === 'export' ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          {mc('exportBoth')}
        </button>
      </header>

      {needsConfirm && (
        <label className="flex shrink-0 items-start gap-1.5 border-b border-border bg-danger-subtle px-3 py-1.5 text-xs text-ink-2">
          <input
            type="checkbox"
            checked={confirmForced}
            onChange={(e) => setConfirmForced(e.target.checked)}
            className="mt-0.5 shrink-0"
          />
          {/* 两种情况各是一句完整的话，不拼字符串（英文从句位置与中文不同） */}
          <span>
            {preflight!.not_verifiable.length > 0
              ? mc('confirmBoth', {
                  errors: preflight!.errors.length,
                  notVerifiable: preflight!.not_verifiable.length,
                })
              : mc('confirmErrors', { errors: preflight!.errors.length })}
          </span>
        </label>
      )}

      {notice && (
        <p
          className={cn(
            'shrink-0 truncate border-b border-border px-3 py-1.5 text-xs',
            notice.tone === 'ok' ? 'text-ink-2' : 'text-danger',
          )}
        >
          {notice.text}
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        {/* CanvasStage 的根是 `flex-1`：**外面必须是 flex 容器**，否则它在普通
            block 父级里高度塌成 0，画布连同面板被 overflow-hidden 整块裁掉
            ——DOM 还在、getBoundingClientRect 还有值，只是既画不出来也点不中
            （e2e/mcp-canvas.spec.ts 的第一版就撞在这上面） */}
        <div className="flex min-h-0 min-w-0 flex-1">
          <CanvasStage />
        </div>
        <aside className="flex w-[304px] shrink-0 flex-col overflow-y-auto border-l border-border bg-surface">
          <ElementInspector panel={panel} />
          <IssueList issues={issues} stale={preflightStale} panel={panel} />
        </aside>
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
      <span className="flex shrink-0 items-center gap-1 text-xs text-danger"
            title={formatMessage(error)}>
        <TriangleAlert size={12} />
        {mc('renderFailed')}
      </span>
    )
  }
  if (rendering) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-xs text-ink-3">
        <Loader2 size={12} className="animate-spin" />
        {mc('rendering')}
      </span>
    )
  }
  if (pending) {
    return (
      <span className="flex shrink-0 items-center gap-1 text-xs text-ink-3">
        <Loader2 size={12} />
        {mc('pending')}
      </span>
    )
  }
  return (
    <span className="flex shrink-0 items-center gap-1 text-xs text-ink-3">
      <Check size={12} />
      {mc('synced')}
    </span>
  )
}

function PreflightPill({
  counts,
  stale,
  loading,
  onClick,
}: {
  counts: Record<string, number>
  stale: boolean
  loading: boolean
  onClick: () => void
}) {
  const err = counts.error ?? 0
  const warn = counts.warn ?? 0
  const nv = counts.not_verifiable ?? 0
  const clean = err + warn + nv === 0
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex h-7 shrink-0 items-center gap-1.5 rounded-sm border px-2 text-xs',
        stale
          ? 'border-border bg-surface-2 text-ink-3'
          : err
            ? 'border-danger/40 text-danger'
            : 'border-border text-ink-2',
      )}
      title={stale ? mc('pillStaleTitle') : mc('pillTitle')}
    >
      {loading ? (
        <Loader2 size={12} className="animate-spin" />
      ) : err ? (
        <TriangleAlert size={12} />
      ) : nv ? (
        <ShieldQuestion size={12} />
      ) : (
        <ShieldCheck size={12} />
      )}
      {stale
        ? mc('pillStale')
        : clean
          ? mc('pillClean')
          : mc('pillCounts', { errors: err, warnings: warn, notVerifiable: nv })}
    </button>
  )
}

/** MCP 画布这一屏的文案都在 `dialogs:mcp.*` 下 */
const mc = (key: string, values?: Record<string, unknown>) =>
  translate(`mcp.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const SEVERITY_ICON = {
  error: TriangleAlert,
  warn: TriangleAlert,
  not_verifiable: ShieldQuestion,
  suggestion: Lightbulb,
} as const

/** 预检条目的显示文案：有描述符按本地 locale 渲染，否则回退 Python 成文 */
const issueDisplayText = (it: PreflightIssuePayload): string =>
  it.message?.key
    ? translate(`preflight.${it.message.key}`, {
        ns: 'errors',
        defaultValue: it.text,
        ...(it.message.params ?? {}),
      })
    : it.text

function IssueList({
  issues,
  stale,
  panel,
}: {
  issues: PreflightIssuePayload[]
  stale: boolean
  panel: PanelObject
}) {
  // 订阅语言变化：宿主中途切 locale（host-context-changed）时，
  // 预检条目要跟着重译，不能停在挂载那一刻的语言上
  useTranslation('errors')
  const setSelectedGids = useUiStore((s) => s.setSelectedGids)
  const manifest = usePanelRender(panel)?.manifest
  if (!issues.length) return null
  return (
    <section className="border-t border-border p-2">
      <h3 className="mb-1.5 text-[11px] text-ink-3">
        {stale ? mc('issuesTitleStale') : mc('issuesTitle')}
      </h3>
      <ul className="flex flex-col gap-1.5">
        {issues.map((it) => {
          const Icon = SEVERITY_ICON[it.severity]
          // 只有当 gid 真的在当前 manifest 里才给「定位」——图改过之后
          // 旧结论里的 gid 可能已经不存在，点了什么都不会发生比点了会选错更好
          const gids = it.gids.filter((g) => manifest?.elements.some((e) => e.gid === g))
          return (
            <li key={it.id}>
              <button
                disabled={!gids.length}
                onClick={() => setSelectedGids(gids)}
                className="flex w-full items-start gap-1.5 text-left text-xs leading-relaxed text-ink-2 disabled:cursor-default"
              >
                <Icon
                  size={12}
                  className={cn(
                    'mt-px shrink-0',
                    it.severity === 'error' ? 'text-danger' : 'text-ink-3',
                  )}
                />
                {/* issue #30：Python 求值器随 issue 发可翻译描述符（message =
                    key + params，golden vectors 与前端求值器逐字对齐），这里按
                    webview 自己的 locale 渲染；老引擎没有 message、或 key 尚未
                    登记（引擎比界面新）时回退 Python 的成文 text。 */}
                <span className="min-w-0 flex-1">{issueDisplayText(it)}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
