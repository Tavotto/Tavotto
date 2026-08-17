import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowUp,
  ChevronRight,
  FileCode2,
  History,
  Pin,
  RotateCcw,
  Settings2,
  Square,
  Trash2,
  X,
} from 'lucide-react'
import {
  aiRevert,
  deleteAiHistory,
  fetchAiHistory,
  pinAiHistory,
  type AiHistoryEntry,
  type ManifestElement,
} from '@/lib/api'
import { cn, MOD, modKey } from '@/lib/utils'
import {
  isSessionOf,
  scriptName,
  useAiStore,
  type AiEntry,
  type AiScope,
  type AiSession,
} from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Popover } from '../ui/Popover'
import { Segmented } from '../ui/Segmented'
import { Tip } from '../ui/Tooltip'
import { InlineLoader, TextLoader } from 'generative-loaders'
import { DiffView } from './DiffView'
import { Markdown } from './Markdown'

/** 右栏标签名与图标：tab bar 引用这里，改名只改这一处 */
export const ASSISTANT_TAB_LABEL = '改图助手'
export const ASSISTANT_TAB_ICON = FileCode2

/** loader 一律走灰阶，别在聊天区制造高对比 */
const LOADER_COLOR = 'var(--color-ink-3)'

const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  done: '完成',
  failed: '失败',
  timeout: '超时',
  cancelled: '已取消',
  reverted: '已回滚',
  interrupted: '已中断',
}

const SCOPE_ITEMS: { value: AiScope; label: string }[] = [
  { value: 'element', label: '当前元素' },
  { value: 'axes', label: '当前子图' },
  { value: 'figure', label: '整张图' },
]

/** 按目标类型给的起手式：点一下填进输入框，改完再发 */
const CHIPS: Record<string, string[]> = {
  figure: ['统一字体', '统一线宽', '检查最小字号', '优化子图间距'],
  axes: ['统一坐标轴字体', '调整留白', '修复图例遮挡', '统一刻度格式'],
  image: ['调整色图', '增强对比度', '统一色条范围'],
  text: ['调整字号', '统一为 Times 字体', '避免与相邻元素重叠'],
  legend: ['调整图例位置', '缩小图例字号', '图例改为两列'],
  series: ['加粗线条', '换成更易区分的配色', '调整标记大小'],
}

function chipsFor(scope: AiScope, element: ManifestElement | null, hasAxes: boolean): string[] {
  if (scope === 'figure') return CHIPS.figure
  if (scope === 'axes') return CHIPS.axes
  switch (element?.role) {
    case 'image':
    case 'colorbar':
      return CHIPS.image
    case 'text':
    case 'title':
    case 'axis_label':
    case 'ticklabel':
      return CHIPS.text
    case 'legend':
      return CHIPS.legend
    case 'line':
    case 'scatter':
    case 'bar':
    case 'bar_series':
    case 'errorbar':
    case 'fill':
      return CHIPS.series
    default:
      return hasAxes ? CHIPS.axes : CHIPS.figure
  }
}

/** 当前作用的目标面板：优先图内编辑中的，其次单选的 script 面板 */
function useTargetPanel(): PanelObject | null {
  const objects = useDocumentStore((s) => s.doc.objects)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const ids = useSelectionStore((s) => s.ids)
  const byId = (id: string | null) =>
    objects.find((o) => o.id === id && o.type === 'panel') as PanelObject | undefined
  const target = byId(elementPanelId) ?? (ids.length === 1 ? byId(ids[0]) : undefined)
  return target?.script ? target : null
}

/**
 * 目标三段式：面板 / 子图 / 元素。
 * gid 约定见 engine/manifest.py：`axes_<i>` 是子图本体，`axes_<i>.xxx` 是它的
 * 子元素，`fig.xxx` 与 `figure` 属于整图。
 */
function useAssistantTarget() {
  const panel = useTargetPanel()
  const selectedGid = useUiStore((s) => s.selectedGids.at(-1) ?? null)
  const elements = useRenderStore((s) =>
    panel ? (s.byFile[panel.fileId]?.manifest?.elements ?? null) : null,
  )

  return useMemo(() => {
    const find = (gid: string | null) =>
      gid ? (elements?.find((e) => e.gid === gid) ?? null) : null
    const picked = find(selectedGid)
    const axesGid = selectedGid?.startsWith('axes_') ? selectedGid.split('.')[0] : null
    const axes = find(axesGid)
    // 选中的就是子图本体（或整图）时，不算「当前元素」
    const element = picked && picked.gid !== 'figure' && picked.gid !== axesGid ? picked : null
    return { panel, element, axes }
  }, [panel, selectedGid, elements])
}

export function AssistantPanel() {
  const sessions = useAiStore((s) => s.sessions)
  const storedScope = useAiStore((s) => s.scope)
  const { panel, element, axes } = useAssistantTarget()
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // 目标不支持某个范围时只是降级显示，不去改用户存下的偏好
  const scopes: AiScope[] = [
    ...(element ? (['element'] as const) : []),
    ...(axes ? (['axes'] as const) : []),
    'figure',
  ]
  const scope = scopes.includes(storedScope) ? storedScope : scopes[0]

  const mine = panel ? sessions.filter((s) => isSessionOf(s, panel)) : []
  // 只有「同一个脚本正在被改」才该挡住发送——别的面板在跑与这里无关
  const runningHere = mine.some((s) => s.status === 'running')

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [sessions, panel?.id])

  const fillPrompt = (text: string) => {
    setPrompt((p) => (p.trim() ? `${p.replace(/[；;，,\s]+$/, '')}；${text}` : text))
    inputRef.current?.focus()
  }

  const send = async () => {
    const text = prompt.trim()
    if (!text || !panel || sending || runningHere) return
    setSending(true)
    setError(null)
    // 作用范围直接决定发给后端的元素上下文：整张图不带 gid，
    // 后端 _build_prompt 就不会写「用户选中的元素」那一行
    const ctx =
      scope === 'element' && element
        ? { gid: element.gid, label: element.label, target: element.label }
        : scope === 'axes' && axes
          ? { gid: axes.gid, label: axes.label, target: axes.label }
          : { gid: null, label: null, target: '整张图' }
    try {
      await useAiStore.getState().start({
        prompt: text,
        fileId: panel.fileId,
        panelId: panel.id,
        gid: ctx.gid,
        label: ctx.label,
        scope,
        target: ctx.target,
        overrides: panel.overrides,
        canvas: useDocumentStore.getState().activeCanvasId,
      })
      setPrompt('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 px-3 pb-2">
        {panel ? (
          <TargetChip panel={panel} element={element} axes={axes} scope={scope} scopes={scopes} />
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        <Tip label="任务历史">
          <Button
            size="icon-sm"
            className="shrink-0"
            onClick={() => setHistoryOpen((v) => !v)}
            aria-label="任务历史"
            aria-expanded={historyOpen}
          >
            <History size={12} className="text-ink-2" />
          </Button>
        </Tip>
      </div>

      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} className="h-full overflow-y-auto px-2.5 py-2">
          {!panel ? (
            <EmptyState
              icon={FileCode2}
              title="选中一个可参数化面板"
              hint="带 { } 标记的面板由脚本生成，助手可以直接修改脚本。"
            />
          ) : mine.length === 0 ? (
            <EmptyState
              icon={FileCode2}
              title="描述想要的改动"
              hint="助手会修改这张图的脚本；改完可查看差异、一键回滚。"
            />
          ) : (
            <div className="flex flex-col gap-3">
              {mine.map((s) => (
                <SessionBlock key={s.id} session={s} />
              ))}
            </div>
          )}
        </div>
        {historyOpen && <TaskHistory onClose={() => setHistoryOpen(false)} />}
      </div>

      <div className="shrink-0 px-3 pb-3 pt-1">
        {panel && mine.length === 0 && !prompt.trim() && (
          <div className="mb-1.5 flex flex-wrap gap-1">
            {chipsFor(scope, element, !!axes).map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => fillPrompt(c)}
                className={cn(
                  'h-6 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink-2',
                  'transition-colors hover:border-border-strong hover:text-ink',
                )}
              >
                {c}
              </button>
            ))}
          </div>
        )}
        {error && <p className="mb-1.5 text-xs text-danger">{error}</p>}
        <div
          className={cn(
            'rounded-md border border-border bg-surface transition-colors focus-within:border-accent',
            !panel && 'opacity-60',
          )}
        >
          <textarea
            ref={inputRef}
            value={prompt}
            rows={2}
            disabled={!panel}
            placeholder={panel ? '例如：把图例移到左上角' : '先选中一个可参数化面板'}
            onChange={(e) => {
              setPrompt(e.target.value)
              // 两行起步，随内容自动增长（封顶约 8 行）
              const el = e.currentTarget
              el.style.height = 'auto'
              el.style.height = `${Math.min(el.scrollHeight, 160)}px`
            }}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                void send()
              }
            }}
            className={cn(
              'block w-full resize-none bg-transparent px-2 pt-2 text-xs leading-relaxed',
              'text-ink outline-none placeholder:text-ink-faint',
            )}
          />
          <div className="flex items-center gap-1 px-1.5 pb-1.5">
            <ScopeAgentButton
              panel={panel}
              element={element}
              axes={axes}
              scope={scope}
              scopes={scopes}
            />
            <span className="ml-auto font-mono text-xs text-ink-faint">{MOD}↵</span>
            <Tip label={runningHere ? '该脚本的任务正在运行' : `发送（${modKey('↵')}）`}>
              <Button
                variant="primary"
                size="icon-sm"
                disabled={!panel || !prompt.trim() || runningHere}
                loading={sending || runningHere}
                onClick={send}
                aria-label="发送"
              >
                {!(sending || runningHere) && <ArrowUp size={13} />}
              </Button>
            </Tip>
          </div>
        </div>
      </div>
    </div>
  )
}

/** 顶部目标标签：一眼可见作用对象，点开可换作用范围 */
function TargetChip({
  panel,
  element,
  axes,
  scope,
  scopes,
}: {
  panel: PanelObject
  element: ManifestElement | null
  axes: ManifestElement | null
  scope: AiScope
  scopes: AiScope[]
}) {
  const targetText =
    scope === 'element' && element
      ? element.label
      : scope === 'axes' && axes
        ? axes.label
        : (panel.name ?? panel.fileId)
  return (
    <Popover
      width={232}
      align="start"
      trigger={
        <button
          aria-label={`作用目标：${targetText}`}
          className={cn(
            'flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-sm bg-surface-2 px-2 text-left',
            'outline-none transition-colors hover:bg-ink/[.06] focus-visible:focus-ring',
          )}
        >
          <FileCode2 size={12} className="shrink-0 text-ink-3" />
          <span className="min-w-0 truncate text-xs text-ink">{targetText}</span>
          <span className="ml-auto shrink-0 text-xs text-ink-3">
            {SCOPE_ITEMS.find((i) => i.value === scope)?.label}
          </span>
        </button>
      }
    >
      <ScopeAgentContent panel={panel} element={element} axes={axes} scope={scope} scopes={scopes} />
    </Popover>
  )
}

/** 输入器左侧模式按钮：作用范围 + 执行器 */
function ScopeAgentButton({
  panel,
  element,
  axes,
  scope,
  scopes,
}: {
  panel: PanelObject | null
  element: ManifestElement | null
  axes: ManifestElement | null
  scope: AiScope
  scopes: AiScope[]
}) {
  const agent = useAiStore((s) => s.agent)
  return (
    <Popover
      width={232}
      align="start"
      trigger={
        <Button size="sm" className="text-ink-2" disabled={!panel} aria-label="作用范围与执行器">
          <Settings2 size={12} />
          <span className="text-xs">
            {SCOPE_ITEMS.find((i) => i.value === scope)?.label} · {agent === 'codex' ? 'Codex' : 'Claude'}
          </span>
        </Button>
      }
    >
      {panel && (
        <ScopeAgentContent panel={panel} element={element} axes={axes} scope={scope} scopes={scopes} />
      )}
    </Popover>
  )
}

function ScopeAgentContent({
  panel,
  element,
  axes,
  scope,
  scopes,
}: {
  panel: PanelObject
  element: ManifestElement | null
  axes: ManifestElement | null
  scope: AiScope
  scopes: AiScope[]
}) {
  const agent = useAiStore((s) => s.agent)
  const caps = useAiStore((s) => s.caps)
  const models = useAiStore((s) => s.models)
  const efforts = useAiStore((s) => s.efforts)
  const [detailsOpen, setDetailsOpen] = useState(false)

  // 只展示实际安装的 provider；模型 / 强度选项完全由 capability 探测结果决定
  const installed = (['codex', 'claude'] as const).filter(
    (a) => caps?.providers[a]?.installed,
  )
  const cur = caps?.providers[agent]
  const model = models[agent] ?? cur?.default_model ?? ''
  const effort = efforts[agent] ?? cur?.default_effort ?? ''

  return (
    <div className="flex flex-col gap-2">
      <div>
        <p className="mb-1 text-xs text-ink-2">作用范围</p>
        <Segmented
          tone="quiet"
          className="w-full"
          value={scope}
          onChange={(v) => useAiStore.getState().setScope(v)}
          items={SCOPE_ITEMS.filter((i) => scopes.includes(i.value))}
        />
        <div className="mt-1">
          <Breadcrumb panel={panel} element={element} axes={axes} scope={scope} />
        </div>
      </div>
      <div className="h-px bg-border" />
      <div>
        <p className="mb-1 text-xs text-ink-2">执行改动的命令行工具</p>
        {caps == null ? (
          <p className="text-xs text-ink-3">正在探测本机 CLI…</p>
        ) : installed.length === 0 ? (
          <div className="flex flex-col gap-1">
            <p className="text-xs leading-relaxed text-ink-3">
              未检测到 Codex 或 Claude CLI。可在设置里一键用 npm 安装，或指定其路径。
            </p>
            <div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => useUiStore.getState().setSettingsOpen(true, 'ai')}
              >
                打开 AI 工具设置
              </Button>
            </div>
          </div>
        ) : (
          <>
            <Segmented
              tone="quiet"
              className="w-full"
              value={agent}
              onChange={(v) => useAiStore.getState().setAgent(v)}
              items={installed.map((a) => ({
                value: a,
                label: a === 'codex' ? 'Codex' : 'Claude',
              }))}
            />
            {cur && cur.models.length > 0 && (
              <label className="mt-1.5 flex items-center gap-2 text-xs text-ink-2">
                模型
                <select
                  value={model}
                  onChange={(e) => useAiStore.getState().setModel(agent, e.target.value)}
                  aria-label="模型"
                  className="h-6 flex-1 rounded-sm border border-border bg-surface px-1 text-xs text-ink outline-none focus-visible:focus-ring"
                >
                  {cur.models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {cur && cur.efforts.length > 0 && (
              <div className="mt-1.5">
                <p className="mb-1 text-xs text-ink-2">推理强度</p>
                <Segmented
                  tone="quiet"
                  className="w-full"
                  value={effort}
                  onChange={(v) => useAiStore.getState().setEffort(agent, v)}
                  items={cur.efforts.map((e) => ({ value: e, label: e }))}
                />
              </div>
            )}
          </>
        )}
        <p className="mt-1 text-xs leading-relaxed text-ink-3">
          直接改脚本文件；每次运行前自动快照，可回滚。
        </p>
      </div>
      {/* 文件名 / 路径等技术信息默认不展示 */}
      <button
        onClick={() => setDetailsOpen((v) => !v)}
        aria-expanded={detailsOpen}
        className="flex items-center gap-1 text-left text-xs text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        <ChevronRight
          size={11}
          className={cn('shrink-0 transition-transform', detailsOpen && 'rotate-90')}
        />
        技术详情
      </button>
      {detailsOpen && (
        <div className="flex flex-col gap-0.5 border-l border-border pl-2">
          <p className="truncate font-mono text-xs text-ink-3" title={panel.script ?? ''}>
            脚本：{panel.script ? scriptName(panel.script) : '—'}
          </p>
          {cur?.version && (
            <p className="truncate font-mono text-xs text-ink-3" title={cur.path ?? ''}>
              CLI：{cur.version}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** 旧名保留：右栏 tab 仍按 AiPanel 引用这个面板 */
export const AiPanel = AssistantPanel

/** 面板 / 子图 / 元素——当前作用范围那一段加重，其余留灰 */
function Breadcrumb({
  panel,
  element,
  axes,
  scope,
}: {
  panel: PanelObject
  element: ManifestElement | null
  axes: ManifestElement | null
  scope: AiScope
}) {
  const crumbs: { level: AiScope; text: string }[] = [
    { level: 'figure', text: panel.name ?? panel.fileId },
    ...(axes ? [{ level: 'axes' as const, text: axes.label }] : []),
    ...(element ? [{ level: 'element' as const, text: element.label }] : []),
  ]
  return (
    <p className="truncate text-xs">
      {crumbs.map((c, i) => (
        <span key={c.level}>
          {i > 0 && <span className="mx-1 text-ink-3">/</span>}
          <span className={c.level === scope ? 'text-ink' : 'text-ink-3'}>{c.text}</span>
        </span>
      ))}
    </p>
  )
}

const HISTORY_STATUS_LABEL: Record<string, string> = {
  ...STATUS_LABEL,
  interrupted: '已中断',
}

const PAGE = 20

/**
 * 任务历史：项目级持久化记录（SQLite），刷新与后端重启后仍在。
 * 默认只显示人类可读目标；脚本名等技术信息在条目的「技术详情」里。
 */
function TaskHistory({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [entries, setEntries] = useState<AiHistoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const load = async (q: string, st: string, off: number) => {
    try {
      const res = await fetchAiHistory({ q, status: st, limit: PAGE, offset: off })
      setEntries(res.sessions)
      setTotal(res.total)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    const t = window.setTimeout(() => void load(query, status, offset), query ? 250 : 0)
    return () => window.clearTimeout(t)
  }, [query, status, offset])

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-surface">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-border px-2.5">
        <h3 className="text-xs text-ink">任务历史</h3>
        <span className="text-xs text-ink-3">{total} 条</span>
        <Button size="icon-sm" className="-mr-1 ml-auto" onClick={onClose} aria-label="关闭任务历史">
          <X size={12} />
        </Button>
      </div>
      <div className="flex shrink-0 items-center gap-1.5 px-2.5 py-1.5">
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setOffset(0)
          }}
          placeholder="搜索任务…"
          aria-label="搜索任务历史"
          className="h-6 min-w-0 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none placeholder:text-ink-faint focus:border-accent"
        />
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            setOffset(0)
          }}
          aria-label="按状态筛选"
          className="h-6 rounded-sm border border-border bg-surface px-1 text-xs text-ink outline-none focus-visible:focus-ring"
        >
          <option value="">全部状态</option>
          {Object.entries(HISTORY_STATUS_LABEL).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-2">
        {error ? (
          <p className="py-2 text-xs text-danger">{error}</p>
        ) : entries.length === 0 ? (
          <EmptyState
            icon={History}
            title={query || status ? '没有匹配的任务' : '还没有改图任务'}
            hint={query || status ? undefined : '在下方输入需求，助手会修改脚本并留下记录。'}
          />
        ) : (
          <div className="flex flex-col gap-2 pt-1">
            {entries.map((s) => (
              <HistoryRow
                key={s.id}
                entry={s}
                onChanged={() => void load(query, status, offset)}
              />
            ))}
          </div>
        )}
      </div>
      {total > PAGE && (
        <div className="flex shrink-0 items-center justify-between border-t border-border px-2.5 py-1.5">
          <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            上一页
          </Button>
          <span className="text-xs text-ink-3">
            {Math.floor(offset / PAGE) + 1} / {Math.ceil(total / PAGE)}
          </span>
          <Button
            size="sm"
            disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  )
}

function HistoryRow({ entry, onChanged }: { entry: AiHistoryEntry; onChanged: () => void }) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const failed = entry.status === 'failed' || entry.status === 'timeout' || entry.status === 'interrupted'

  return (
    <div className="border-b border-border pb-2 last:border-b-0">
      <p className="line-clamp-2 text-xs leading-relaxed text-ink-2">{entry.prompt}</p>
      <p className="mt-0.5 truncate text-xs text-ink-3">
        {entry.target || '整张图'} · {entry.provider === 'codex' ? 'Codex' : 'Claude'}
        {entry.model ? ` · ${entry.model}` : ''} · {timeOf(entry.started_ms)}
      </p>
      <div className="mt-1 flex items-center gap-1.5">
        <span className={cn('text-xs', failed ? 'text-danger' : 'text-ink-3')}>
          {HISTORY_STATUS_LABEL[entry.status] ?? entry.status}
          {entry.changed ? ' · 有改动' : ''}
        </span>
        <span className="flex-1" />
        <Tip label={entry.pinned ? '取消固定（会随保留期清理）' : '固定：不随保留期清理'}>
          <Button
            size="icon-sm"
            active={entry.pinned}
            aria-pressed={entry.pinned}
            aria-label={entry.pinned ? '取消固定' : '固定此记录'}
            onClick={() => void pinAiHistory(entry.id, !entry.pinned).then(onChanged)}
          >
            <Pin size={11} className={entry.pinned ? undefined : 'text-ink-3'} />
          </Button>
        </Tip>
        {entry.changed && entry.revert_available && (
          <Tip label="回滚此次修改（恢复脚本快照）">
            <Button
              size="icon-sm"
              className="text-danger"
              aria-label="回滚此次修改"
              onClick={() =>
                void aiRevert(entry.id).then(() => {
                  useUiStore.getState().setStatus('已回滚该次修改，脚本恢复到修改前')
                  onChanged()
                })
              }
            >
              <RotateCcw size={11} />
            </Button>
          </Tip>
        )}
        <Tip label="删除此记录">
          <Button
            size="icon-sm"
            aria-label="删除此记录"
            onClick={() => void deleteAiHistory(entry.id).then(onChanged)}
          >
            <Trash2 size={11} className="text-ink-3" />
          </Button>
        </Tip>
      </div>
      {entry.error && <p className="mt-0.5 text-xs text-danger">{entry.error}</p>}
      <button
        onClick={() => setDetailsOpen((v) => !v)}
        aria-expanded={detailsOpen}
        className="mt-0.5 flex items-center gap-1 text-left text-xs text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        <ChevronRight
          size={11}
          className={cn('shrink-0 transition-transform', detailsOpen && 'rotate-90')}
        />
        技术详情
      </button>
      {detailsOpen && (
        <div className="mt-0.5 flex flex-col gap-0.5 border-l border-border pl-2">
          <p className="truncate font-mono text-xs text-ink-3">
            脚本：{entry.script ? scriptName(entry.script) : '—'}
          </p>
          {entry.effort && <p className="font-mono text-xs text-ink-3">推理强度：{entry.effort}</p>}
          <p className="font-mono text-xs text-ink-3">
            快照{entry.revert_available ? '可用' : '已清理'} · id {entry.id}
          </p>
        </div>
      )}
    </div>
  )
}

const timeOf = (ts: number) =>
  new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

/** 改动是直接落盘的，措辞不能像「待应用的预览」 */
function statusText(s: AiSession): string {
  if (s.status === 'running') return '正在改写脚本…'
  if (s.status === 'done') return s.changed ? '修改已写入脚本，可查看 diff 或回滚' : '已完成，脚本没有变化'
  if (s.status === 'reverted') return '已回滚，脚本恢复到本次修改前'
  return STATUS_LABEL[s.status] ?? s.status
}

const toneOf = (s: AiSession) =>
  s.status === 'failed' || s.status === 'timeout' ? 'text-danger' : 'text-ink-3'

/** 把连续的 thinking/action 折成一组「过程」，正文单独成条 */
type Group =
  | { type: 'message'; text: string; streaming?: boolean }
  | { type: 'process'; items: { kind: string; text: string }[] }

function groupEntries(entries: AiEntry[]): Group[] {
  const groups: Group[] = []
  for (const e of entries) {
    if (e.kind === 'message' || e.kind === 'delta') {
      groups.push({ type: 'message', text: e.text, streaming: e.streaming })
      continue
    }
    const last = groups.at(-1)
    if (last?.type === 'process') last.items.push(e)
    else groups.push({ type: 'process', items: [e] })
  }
  return groups
}

function SessionBlock({ session }: { session: AiSession }) {
  const running = session.status === 'running'
  const groups = groupEntries(session.entries)
  // 有文字正在流入就撤掉 loader——同时出现会互相抢注意力
  const streamingNow = session.entries.some((e) => e.streaming)
  // 已经说过话、又还没开始流下一段 → 用骨架占位，别让面板空着
  const awaitingParagraph = running && !streamingNow && session.entries.length > 0

  return (
    <div className="flex flex-col gap-1.5">
      <div className="rounded-sm border border-border bg-surface-2 px-2 py-1.5">
        <p className="text-sm leading-[1.6] break-words text-ink-2">{session.prompt}</p>
        <p className="mt-0.5 truncate font-mono text-xs text-ink-3">
          {session.agent} · {session.target} · {timeOf(session.startedAt)}
        </p>
      </div>

      {groups.map((g, i) =>
        g.type === 'message' ? (
          <MessageBody key={i} text={g.text} streaming={g.streaming} />
        ) : (
          <ProcessGroup key={i} items={g.items} />
        ),
      )}

      {awaitingParagraph && (
        <div className="text-sm leading-[1.6]">
          <TextLoader
            variant="skeleton"
            text="正在生成回答"
            color={LOADER_COLOR}
            aria-label="正在生成回答"
          />
        </div>
      )}

      <div className="flex items-center gap-1.5">
        {running && !streamingNow && session.entries.length === 0 && (
          <InlineLoader variant="matrix" size={24} color={LOADER_COLOR} />
        )}
        <span className={cn('text-xs', toneOf(session))}>{statusText(session)}</span>
        {running && (
          <Button
            size="icon-sm"
            className="ml-auto text-danger"
            onClick={() => void useAiStore.getState().cancel(session.id)}
            aria-label="中止"
          >
            <Square size={10} />
          </Button>
        )}
      </div>

      {session.error && <p className="text-xs text-danger">{session.error}</p>}

      {session.changed && session.diff && (
        <>
          <DiffView diff={session.diff} script={session.script} />
          <Button
            variant="outline"
            size="sm"
            className="w-full text-danger"
            onClick={() => void revertSession(session)}
          >
            <RotateCcw size={12} />
            回滚此次修改
          </Button>
        </>
      )}
    </div>
  )
}

/** 思考/动作默认只占一行，点开才看时间线——不刷屏 */
function ProcessGroup({ items }: { items: { kind: string; text: string }[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 text-left text-xs text-ink-3 hover:text-ink-2"
      >
        <ChevronRight size={11} className={cn('shrink-0 transition-transform', open && 'rotate-90')} />
        <span className="truncate">
          过程 {items.length} 步
          {!open && items.at(-1) ? ` · ${items.at(-1)!.text.replace(/\s+/g, ' ').slice(0, 16)}` : ''}
        </span>
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-1 border-l border-border pl-2">
          {items.map((it, i) => (
            <li
              key={i}
              className={cn(
                'whitespace-pre-wrap break-words text-xs leading-relaxed',
                it.kind === 'action' ? 'font-mono text-ink-2' : 'text-ink-3',
              )}
            >
              {it.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

async function revertSession(session: AiSession) {
  await useAiStore.getState().revert(session.id)
  // 回滚后 worker 会话同样失效，重建让画布自动回到改动前的样子
  if (session.fileId) useRenderStore.getState().markStale([session.fileId])
  useUiStore.getState().setStatus('已回滚改图助手的修改，正在重建图表…')
}

/** 正文：按空行分段，行高放松；流式中在末段尾部挂闪烁光标 */
function MessageBody({ text, streaming }: { text: string; streaming?: boolean }) {
  // 流式阶段交给 redact 做逐字显影：它只吃纯字符串，半截 markdown 会不停闪
  // 未闭合的语法；等 message 终稿到达再换成 Markdown 排版，两个阶段各司其职。
  if (streaming) {
    return (
      <div className="whitespace-pre-wrap break-words text-sm leading-[1.6] text-ink-2">
        <TextLoader variant="redact" text={text} color={LOADER_COLOR} aria-label="正在输出" />
      </div>
    )
  }
  return <Markdown text={text} />
}
