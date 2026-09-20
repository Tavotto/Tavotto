import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  FileCodeCorner,
  RotateCcwClock,
  Pencil,
  Pin,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
  Wrench,
  X,
} from '@/components/ui/icons'
import { Badge } from '../ui/Badge'
import { ICON_SIZE } from '@/components/ui/Icon'
import { SwapText } from '@/components/ui/SwapText'
import {
  agentById,
  agentDisplayName,
  backendErrorText,
  aiRevert,
  deleteAiHistory,
  effectiveAgent,
  fetchAiHistory,
  pinAiHistory,
  usableAgents,
  type AiHistoryEntry,
  type ManifestElement,
} from '@/lib/api'
import { cn, modKey } from '@/lib/utils'
import { msg, t as translate } from '@/i18n'
import { formatTime } from '@/i18n/format'
import { engineLabel } from '@/components/inspector/roles/registry'
import {
  isSessionOf,
  scriptName,
  sessionAgentLabel,
  useAiStore,
  type AiEntry,
  type AiScope,
  type AiSession,
} from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelDisplayManifest, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Reveal } from '../ui/Field'
import { fitTextAreaHeight } from '../ui/Input'
import { SearchInput } from '../ui/SearchInput'
import { Popover } from '../ui/Popover'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { StepSlider } from '../ui/StepSlider'
import { Tip } from '../ui/Tooltip'
import { DiffView } from './DiffView'
import { Markdown } from './Markdown'

/** 右栏标签名与图标：tab bar 引用这里，改名只改这一处 */
export const assistantTabLabel = () => translate('tabLabel', { ns: 'ai' })
export const ASSISTANT_TAB_ICON = FileCodeCorner

/** 本面板的文案都在 ai 命名空间下 */
const ai = (key: string, values?: Record<string, unknown>) =>
  translate(key, { ns: 'ai', ...(values ?? {}) })

/** 会话状态名；未知状态原样透出（后端加了新状态也不会变成空白） */
const statusLabel = (status: string) =>
  translate(`status.${status}`, { ns: 'ai', defaultValue: status })

/**
 * 贴底跟随的松弛量：离底部这么近仍算「看着最新内容」。滚轮一格通常 ≥ 40px，
 * 用户真往上翻时一步就超过它；小于它的偏差只是子像素 / 滚动条尾巴。
 */
const STICK_SLACK = 24
/** 输入框最多长到几行，再多在框内滚动 */
const COMPOSER_MAX_ROWS = 8

const SCOPE_VALUES: AiScope[] = ['element', 'axes', 'figure']

/**
 * 「执行器 · 模型」合成选择器的值编码（审计 T37）。
 *
 * 呈现上是一个控件，存下去仍是 aiStore 的两个字段。Agent id 是后端注册表里的
 * 短标识（`codex` / `claude`），不含 `/`；模型名整段留给右边，所以按**第一个**
 * `/` 切开，模型名里真出现斜杠也不会被截断。
 */
const PAIR_SEP = '/'
const pairValue = (agentId: string, model: string) => `${agentId}${PAIR_SEP}${model}`
const splitPair = (v: string): [string, string] => {
  const i = v.indexOf(PAIR_SEP)
  return i < 0 ? [v, ''] : [v.slice(0, i), v.slice(i + 1)]
}

const scopeItems = () =>
  SCOPE_VALUES.map((value) => ({ value, label: ai(`scope.${value}`) }))

const scopeLabel = (scope: AiScope) => ai(`scope.${scope}`)

/**
 * 按目标类型给的起手式：点一下填进输入框，改完再发。
 *
 * **分组留在代码里（那是逻辑），文案在 `ai:chip.<id>`（那是文案）**。
 * 以前整组存成 JSON 数组，提取器每次都要把数组原样重写一遍，`--ci` 永远红；
 * 拆成一条一个 key 之后，漏翻某一条也能被 key 集合对比抓到。
 */
const CHIP_IDS: Record<string, string[]> = {
  figure: ['unifyFont', 'unifyLineWidth', 'checkMinFontSize', 'improveSpacing'],
  axes: ['unifyAxisFont', 'adjustPadding', 'fixLegendOverlap', 'unifyTickFormat'],
  image: ['changeColormap', 'increaseContrast', 'unifyColorScale'],
  text: ['adjustFontSize', 'switchToTimes', 'avoidOverlap'],
  legend: ['moveLegend', 'shrinkLegendFont', 'legendTwoColumns'],
  series: ['thickenLines', 'distinguishablePalette', 'adjustMarkerSize'],
}

const chips = (kind: string): string[] =>
  (CHIP_IDS[kind] ?? []).map((id) => translate(`chip.${id}`, { ns: 'ai' }))

function chipsFor(scope: AiScope, element: ManifestElement | null, hasAxes: boolean): string[] {
  if (scope === 'figure') return chips('figure')
  if (scope === 'axes') return chips('axes')
  switch (element?.role) {
    case 'image':
    case 'colorbar':
      return chips('image')
    case 'text':
    case 'title':
    case 'axis_label':
    case 'ticklabel':
      return chips('text')
    case 'legend':
      return chips('legend')
    case 'line':
    case 'scatter':
    case 'bar':
    case 'bar_series':
    case 'errorbar':
    case 'fill':
      return chips('series')
    default:
      return hasAxes ? chips('axes') : chips('figure')
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
  const elements = usePanelDisplayManifest(panel)?.elements ?? null

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
  useTranslation('ai')
  const sessions = useAiStore((s) => s.sessions)
  const storedScope = useAiStore((s) => s.scope)
  const { panel, element, axes } = useAssistantTarget()
  const caps = useAiStore((s) => s.caps)
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // 贴底跟随（ChatGPT / Claude 的约定）：只在用户本来就看着底部时才跟着新内容滚（判据在下面的
  // syncStick）。pin 是唯一的「滚到底」出口——**盯的是内容尺寸，不只是 store**（2026-09-16，学 beUI
  // MessageScroller）：底边会长的来源有三个——新 delta（store）、过程 Reveal 展开（内容长高 180 ms）、
  // 玻璃输入框长高（写 --composer-h → 底边距长）。此前只在 store 变化时重滚，后两个来源发生时
  // scrollHeight 长了而 scrollTop 没动：上一条回答的末几行滑到玻璃底下，而 syncStick 只在 scroll
  // 事件里算，「回到底部」那颗钮也不出现。
  const stick = useRef(true)
  const pin = () => {
    const el = scrollRef.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }
  // 对话流的内容容器：尺寸一变就 pin（jsdom 没有 ResizeObserver，那里只剩 store 那条路）
  const contentRef = useCallback((node: HTMLDivElement | null) => {
    if (!node || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(pin)
    ro.observe(node)
    return () => ro.disconnect()
    // pin 只读 ref，身份无所谓
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // 输入框浮在对话流上（玻璃，参考 Codex）：它的高度会变（起手式收起、输入框长高、报错一行），
  // 量出来写成 --composer-h，滚动区用它做底部内边距，最后一条回答不会被压在玻璃底下——
  // 底边距长了要紧跟着 pin，不然贴着底的末几行正好被长高的那截玻璃盖住
  const stageRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const el = composerRef.current
    const stage = stageRef.current
    if (!el || !stage) return
    const write = () => {
      stage.style.setProperty('--composer-h', `${el.offsetHeight}px`)
      pin()
    }
    write()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(write)
    ro.observe(el)
    return () => ro.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
  // 一个可用的编码 Agent 都没有：**探测出结果之后**才这么说
  // （caps 还是 null 时是「正在检测」，那时不该把输入区锁上）
  const noAgent = caps !== null && usableAgents(caps).length === 0

  // 原先每个 delta 都把视口拽回底部——往上翻看旧回答时等于不让人看。
  // 换了目标面板视作重新贴底。jsdom 里 scrollHeight 恒 0，gap 恒 0，一律贴底。
  const [detached, setDetached] = useState(false)
  const syncStick = () => {
    const el = scrollRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLACK
    stick.current = near
    setDetached(!near)
  }
  const jumpToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    stick.current = true
    setDetached(false)
  }
  useEffect(() => {
    stick.current = true
    setDetached(false)
  }, [panel?.id])
  useEffect(() => {
    pin()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions, panel?.id])

  // 输入框高度跟着内容走（宪法第五节：调用点不自己算 rows）；jsdom 量不到行高时什么都不动
  useLayoutEffect(() => {
    if (inputRef.current) fitTextAreaHeight(inputRef.current, COMPOSER_MAX_ROWS)
  }, [prompt])

  const fillPrompt = (text: string) => {
    setPrompt((p) => (p.trim() ? `${p.replace(/[；;，,\s]+$/, '')}；${text}` : text))
    inputRef.current?.focus()
  }

  const send = async () => {
    const text = prompt.trim()
    if (!text || !panel || noAgent || sending || runningHere) return
    setSending(true)
    setError(null)
    // 自己发的消息一定要看见：不管刚才翻到哪，发出去就回到底部
    jumpToBottom()
    // 作用范围直接决定发给后端的元素上下文：整张图不带 gid，
    // 后端 _build_prompt 就不会写「用户选中的元素」那一行
    const ctx =
      scope === 'element' && element
        ? { gid: element.gid, label: element.label, target: element.label }
        : scope === 'axes' && axes
          ? { gid: axes.gid, label: axes.label, target: axes.label }
          : { gid: null, label: null, target: ai('scope.figure') }
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
      setError(backendErrorText(e))
    } finally {
      setSending(false)
    }
  }

  const bakeIntoSource = async () => {
    if (!panel || panel.overrides.length === 0 || noAgent || sending || runningHere) return
    setSending(true)
    setError(null)
    jumpToBottom()
    try {
      await useAiStore.getState().start({
        prompt: ai('panel.bakePrompt'),
        fileId: panel.fileId,
        panelId: panel.id,
        gid: null,
        label: null,
        scope: 'figure',
        target: ai('scope.figure'),
        overrides: panel.overrides,
        canvas: useDocumentStore.getState().activeCanvasId,
        bakeOverrides: true,
      })
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setSending(false)
    }
  }

  // 发送 ↔ 中止同一颗按钮、同一个位置（ChatGPT / Claude 的约定）：正在跑的时候它就是「中止」
  const stopRunning = () => {
    const running = mine.find((s) => s.status === 'running')
    if (running) void useAiStore.getState().cancel(running.id)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 px-3 pb-2">
        {panel ? (
          <TargetChip panel={panel} element={element} axes={axes} scope={scope} scopes={scopes} />
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        <Tip label={ai('panel.taskHistory')}>
          <Button
            size="icon-sm"
            className="shrink-0"
            onClick={() => setHistoryOpen((v) => !v)}
            aria-label={ai('panel.taskHistory')}
            aria-expanded={historyOpen}
          >
            <RotateCcwClock size={ICON_SIZE.sm} className="text-ink-2" />
          </Button>
        </Tip>
      </div>

      <div ref={stageRef} className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={syncStick}
          className="h-full overflow-y-auto px-2.5 pt-2"
          style={{ paddingBottom: 'calc(var(--composer-h, 0px) + 8px)' }}
        >
          {!panel ? (
            /* 「这里没有可干的活」是真正的空状态，留在中间 */
            <EmptyState icon={FileCodeCorner} title={ai('panel.noPanelTitle')} />
          ) : (
            /* 还没发过任务：中间是空态——「助手会做什么」那一句就是空态的说明（二审 D2；
               审计 T37 曾把它压到输入框上方，底部叠成四层而中间全空）。起手式仍留在输入框旁：
               它们是输入的快捷方式，跟着输入框走。会话一来，空态让位给它 */
            mine.length > 0 ? (
              <div ref={contentRef} className="flex flex-col gap-3">
                {mine.map((s) => (
                  <SessionBlock key={s.id} session={s} />
                ))}
              </div>
            ) : (
              <EmptyState icon={Sparkles} title={ai('panel.emptyHint')} />
            )
          )}
        </div>
        {/* 往上翻着看、而新内容还在来：给一颗回到底部的钮；到底了它自己消失 */}
        {detached && runningHere && (
          <div
            className="pointer-events-none absolute inset-x-0 flex justify-center"
            style={{ bottom: 'calc(var(--composer-h, 0px) + 8px)' }}
          >
            <IconButton
              label={ai('panel.scrollToBottom')}
              iconSize="sm"
              variant="secondary"
              side="top"
              className="pointer-events-auto animate-pop-in rounded-full shadow-pop"
              onClick={jumpToBottom}
            >
              <ArrowDown size={ICON_SIZE.sm} />
            </IconButton>
          </div>
        )}
        {historyOpen && <TaskHistory onClose={() => setHistoryOpen(false)} />}

        {/* 输入区浮在对话流的底部（absolute），内容从它底下滚过；玻璃在下面那个框上 */}
        <div ref={composerRef} className="absolute inset-x-0 bottom-0 z-30 px-3 pb-3 pt-1">
        {/* 起手式：一开始打字就收起——收起是跟着内容合上（Reveal），不是原地消失让输入框跳一下 */}
        <Reveal open={!!panel && mine.length === 0 && !prompt.trim()}>
          <div className="mb-1.5 flex flex-wrap gap-1">
            {panel &&
              chipsFor(scope, element, !!axes).map((c) => (
                <Button key={c} variant="secondary" size="sm" className="text-ink-2 hover:text-ink" onClick={() => fillPrompt(c)}>
                  {c}
                </Button>
              ))}
          </div>
        </Reveal>
        {error && <p className="mb-1.5 text-xs text-danger">{error}</p>}
        {noAgent && (
          // 「没装 CLI」不是错误，用中性语气 + 一个可执行的下一步。
          // 以前这句话只藏在「作用范围与执行器」弹层里，不点开根本看不到
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
            <p className="text-xs leading-relaxed text-ink-3">{ai('panel.noCli')}</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => useUiStore.getState().setSettingsOpen(true, 'ai')}
            >
              {ai('panel.openAiSettings')}
            </Button>
          </div>
        )}
        {/* 玻璃（2026-09-15 参考 Codex 的 _ComposerLayoutBody，用户拍板）：field 90% 的底 + 16px 背景模糊 +
            环 4% 与两层投影（--shadow-composer），无边线；它浮在对话流上，内容从底下滚过时被糊掉——玻璃
            只在浮着的时候成立，所以整个输入区是 absolute 的，滚动区按 --composer-h 留底边。圆角 lg：
            它是一块浮在流上的多行输入区（Codex 多行是 3xl），比控件（6）与卡（10）都大一档。
            聚焦仍是不透明 accent 边（3:1 由它承担）；禁用只有 opacity-40 一档 */}
        <div
          className={cn(
            'rounded-lg border border-transparent bg-glass text-sm text-ink shadow-composer backdrop-blur-lg',
            'transition-colors duration-fast focus-within:border-accent',
            (!panel || noAgent) && 'opacity-40',
          )}
          data-ai-composer
        >
          <textarea
            ref={inputRef}
            value={prompt}
            rows={2}
            disabled={!panel || noAgent}
            placeholder={panel ? ai('panel.placeholder') : undefined}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                void send()
              }
            }}
            className={cn(
              'block w-full resize-none bg-transparent px-2 pt-2 text-xs leading-relaxed',
              // 占位是要读的字：ink-3，与其它输入框同一档（faint 只给装饰 / 禁用）
              'text-ink outline-none placeholder:text-ink-3',
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
            {panel && panel.overrides.length > 0 && (
              <Tip label={ai('panel.bakeTip')}>
                <Button
                  variant="secondary"
                  size="sm"
                  data-ai-bake-overrides
                  disabled={noAgent || sending || runningHere}
                  onClick={() => void bakeIntoSource()}
                >
                  {ai('panel.bake')}
                </Button>
              </Tip>
            )}
            {/* 快捷键只说一次：发送钮的气泡里已经有「⌘↵」，输入框上不再常驻一枚键帽
                （打磨 A5——已表达过的不重复） */}
            <span className="ml-auto" />
            <Tip label={runningHere ? ai('panel.abort') : ai('panel.send', { key: modKey('↵') })}>
              <Button
                variant="primary"
                size="icon-sm"
                data-ai-send={runningHere ? 'stop' : 'send'}
                disabled={!panel || noAgent || (!runningHere && !prompt.trim())}
                loading={sending}
                onClick={runningHere ? stopRunning : send}
                aria-label={runningHere ? ai('panel.abort') : ai('panel.sendAria')}
              >
                {!sending && <SendStopGlyph stop={runningHere} />}
              </Button>
            </Tip>
          </div>
        </div>
        </div>
      </div>
    </div>
  )
}

/**
 * 发送 ↔ 中止的图标：两个叠在同一格里，换的时候旧的缩小淡出、新的放大浮现，
 * 弹簧曲线收尾——按钮本身不动，位置与尺寸一像素都不变。
 */
function SendStopGlyph({ stop }: { stop: boolean }) {
  const cls = (shown: boolean) =>
    cn(
      'col-start-1 row-start-1 transition-[opacity,transform] duration-base ease-spring',
      shown ? 'scale-100 opacity-100' : 'scale-50 opacity-0',
    )
  return (
    <span className="grid place-items-center" aria-hidden>
      <ArrowUp size={ICON_SIZE.sm} className={cls(!stop)} />
      <Square size={ICON_SIZE.xs} className={cn('fill-current', cls(stop))} />
    </span>
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
  useTranslation('ai')
  // 元素名是引擎发来的散文，过 engineLabel 换成当前语言；面板名是用户内容
  const targetText =
    scope === 'element' && element
      ? engineLabel(element.label)
      : scope === 'axes' && axes
        ? engineLabel(axes.label)
        : (panel.name ?? panel.fileId)
  return (
    <Popover
      width={288}
      align="start"
      /*
        它是个触发器，不是只读值：**hover 才浮底**（surface-2 常驻底是「只读值」
        的语义，第一节；打磨 A7）。右端的作用范围也去掉了——输入框那颗
        「作用于：X · Agent」已经把范围与执行器说全了，顶部片只回答「改哪张图 /
        哪个元素」，它就是面包屑（打磨 L6）。
      */
      trigger={
        <button
          aria-label={ai('panel.targetAria', { target: targetText })}
          className={cn(
            'flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-sm px-2 text-left',
            'outline-none transition-colors hover:bg-surface-hover focus-visible:focus-ring',
          )}
        >
          <FileCodeCorner size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
          <span className="min-w-0 truncate text-xs text-ink">{targetText}</span>
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
  useTranslation('ai')
  const caps = useAiStore((s) => s.caps)
  const preferred = useAiStore((s) => s.agent)
  // 按钮上写的是**这一刻真的会派给谁**，不是用户存着的首选值——
  // 首选那个被关掉时，按钮说 Codex、任务却交给 Claude Code 是最难查的一类错。
  const active = effectiveAgent(preferred, caps)
  return (
    <Popover
      width={288}
      align="start"
      trigger={
        <Button
          size="sm"
          className="text-ink-2"
          disabled={!panel}
          aria-label={ai('panel.scopeAndAgent')}
        >
          <SlidersHorizontal size={ICON_SIZE.sm} />
          {/* 「作用于：当前元素」而不是光一个「当前元素」（审计 T37）：
              发送前这一行要能独立回答「按下去会改什么」 */}
          <span className="text-xs">
            {ai('panel.actsOn', { scope: scopeLabel(scope) })}
            {active ? ` · ${agentDisplayName(caps, active)}` : ''}
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

export function ScopeAgentContent({
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
  useTranslation('ai')
  const agent = useAiStore((s) => s.agent)
  const caps = useAiStore((s) => s.caps)
  const models = useAiStore((s) => s.models)
  const efforts = useAiStore((s) => s.efforts)
  const [effortOpen, setEffortOpen] = useState(false)

  // 只展示**可用**的 Agent（装了、没被关掉、也没在等登录）；顺序沿用后端注册表。
  // 模型 / 强度选项完全由该 Agent 自己声明的能力决定，不在前端列第二份名单。
  const usable = usableAgents(caps)
  // 首选那个暂时不可用时用第一个可用的，**但不改用户存着的首选值**
  const active = effectiveAgent(agent, caps)
  const cur = agentById(caps, active)
  const model = (active && models[active]) ?? cur?.default_model ?? ''
  const effort = (active && efforts[active]) ?? cur?.default_effort ?? ''
  // 档位下标由**真实能力数组**算出来；记忆里那个已经不在清单里时回落到 0，
  // 绝不凭字符串造一个数组里没有的档位
  const effortList: string[] = cur?.efforts ?? []
  const effortIndex = Math.max(0, effortList.indexOf(effort))
  // 「执行器 · 模型」的候选。装了两个 Agent 时每一项都带执行器名，只装一个时
  // 不重复它（触发按钮上已经写着）。**模型清单为空 = 跟随 CLI 默认**，给一条
  // 只有执行器名的项，绝不伪造一个模型名。
  const pairs = usable.flatMap((a) =>
    a.models.length
      ? a.models.map((m: string) => ({
          value: pairValue(a.id, m),
          label: usable.length > 1 ? `${a.display_name} · ${m}` : m,
        }))
      : [{ value: pairValue(a.id, ''), label: a.display_name }],
  )
  const currentPair = active ? pairValue(active, model) : ''
  // 记忆里（或 CLI 默认里）那个模型已经不在清单里时**照实把它显示出来**，
  // 不静默换成清单里的另一项：控件上写着 A、任务却交给 B 是最难查的一类错。
  if (currentPair && cur && !pairs.some((p) => p.value === currentPair)) {
    pairs.unshift({
      value: currentPair,
      label: usable.length > 1 ? `${cur.display_name} · ${model}` : model,
    })
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        {/* 标题与路径同一行：左边说「这是什么」，右边说「现在指向谁」，
            视线不用在分段控件上下来回跳 */}
        <div className="mb-1 flex min-w-0 items-center gap-2">
          <p className="shrink-0 text-xs font-medium text-ink-2">{ai('panel.scopeTitle')}</p>
          <div className="min-w-0 flex-1 text-right">
            <Breadcrumb panel={panel} element={element} axes={axes} scope={scope} />
          </div>
        </div>
        <Segmented
          className="w-full"
          ariaLabel={ai('panel.scopeTitle')}
          value={scope}
          onChange={(v) => useAiStore.getState().setScope(v)}
          items={scopeItems().filter((i) => scopes.includes(i.value))}
        />
      </div>
      {caps == null ? (
        <p className="text-xs text-ink-3">{ai('panel.probing')}</p>
      ) : usable.length === 0 ? (
        /* 缺件是**错误恢复路径**，不能因为「减负」被折叠掉 */
        <div className="flex flex-col gap-1">
          <p className="text-xs leading-relaxed text-ink-3">{ai('panel.noCli')}</p>
          <div>
            <Button
              data-ai-open-settings
              variant="secondary"
              size="sm"
              onClick={() => useUiStore.getState().setSettingsOpen(true, 'ai')}
            >
              {ai('panel.openAiSettings')}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {/* 执行器与模型合成一个紧凑选择器（审计 T37）。**只是呈现合并**：
              底下仍是 aiStore 的两个字段（agent / models[agent]），选中一项时
              各写各的，切回另一个 Agent 时它自己的模型记忆还在。
              只有一项可选时不摆一个选不动的选择器：**但那一项写的是什么仍要
              看得见**，退成一行静态文字。连模型名都没有（跟随 CLI 默认）时整块
              不出现——执行器是谁，弹层触发按钮上已经写着了 */}
          {pairs.length > 1 ? (
            <div data-ai-agent-model="select" className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 text-xs text-ink-2">{ai('panel.agentModel')}</span>
              <Select
                className="min-w-0 flex-1"
                ariaLabel={ai('panel.agentModel')}
                value={currentPair}
                onChange={(v) => {
                  const [id, m] = splitPair(v)
                  useAiStore.getState().setAgent(id)
                  if (m) useAiStore.getState().setModel(id, m)
                }}
                options={pairs}
              />
            </div>
          ) : (
            cur &&
            model && (
              <div data-ai-agent-model="static" className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 text-xs text-ink-2">{ai('panel.agentModel')}</span>
                <span className="min-w-0 flex-1 truncate text-xs text-ink" title={model}>
                  {`${cur.display_name} · ${model}`}
                </span>
              </div>
            )
          )}
          {/* 推理强度：档位来自 caps 的真实数组，一格一个值。
              **控件按需展示，当前值不藏**（审计 T37）——收起时那一行就写着
              「推理强度 · 高」，要动它才展开滑杆。只有一档时滑杆不可调
              （不是一个假装能拖的滑杆）；一档都没有时整块不出现 */}
          {effortList.length > 0 && (
            <div className="flex min-w-0 flex-col gap-0.5">
              <button
                data-ai-effort="disclosure"
                onClick={() => setEffortOpen((v) => !v)}
                aria-expanded={effortOpen}
                className="flex min-w-0 items-center gap-1 text-left outline-none focus-visible:focus-ring"
              >
                <ChevronRight
                  size={ICON_SIZE.xs}
                  aria-hidden
                  className={cn('shrink-0 text-ink-3 transition-transform', effortOpen && 'rotate-90')}
                />
                <span className="shrink-0 text-xs text-ink-2">{ai('panel.effort')}</span>
                <span
                  className="ml-auto min-w-0 truncate text-xs font-medium text-ink"
                  title={effortLabel(effortList[effortIndex])}
                >
                  {effortLabel(effortList[effortIndex])}
                </span>
              </button>
              {effortOpen && (
                <StepSlider
                  value={effortIndex}
                  count={effortList.length}
                  disabled={effortList.length === 1}
                  ariaLabel={ai('panel.effort')}
                  valueText={effortLabel(effortList[effortIndex])}
                  onChange={(i) => active && useAiStore.getState().setEffort(active, effortList[i])}
                />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * 推理强度的显示名。**这是开集**——档位由本机 CLI 声明，后端会把用户配置里
 * 的自定义档位原样带上来（实测 codex 的 xhigh 就是这么来的）。查不到就回退
 * 原文，绝不因为「表里没有」而把一个真实存在的档位显示成空白。
 */
function effortLabel(value: string | undefined): string {
  if (!value) return ''
  return translate(`effortLabel.${value}`, { ns: 'ai', defaultValue: value })
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
    ...(axes ? [{ level: 'axes' as const, text: engineLabel(axes.label) }] : []),
    ...(element ? [{ level: 'element' as const, text: engineLabel(element.label) }] : []),
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

/** 历史筛选下拉里的状态集合（会话状态 + 只在历史里出现的 interrupted） */
/** 「全部状态」的哨兵：Radix Select 不接受空串作为 Item 的值 */
const ALL_STATUSES = '__all__'

const HISTORY_STATUSES = [
  'running',
  'done',
  'failed',
  'timeout',
  'cancelled',
  'reverted',
  'interrupted',
]

const PAGE = 20

/**
 * 任务历史：项目级持久化记录（SQLite），刷新与后端重启后仍在。
 * 默认只显示人类可读目标；脚本名等技术信息在条目的「技术详情」里。
 */
export function TaskHistory({ onClose }: { onClose: () => void }) {
  useTranslation('ai')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [entries, setEntries] = useState<AiHistoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  // 第一次查回来之前什么都不判断：还不知道有没有记录时既不该摆筛选，
  // 也不该先闪一句「还没有改图任务」
  const [loaded, setLoaded] = useState(false)
  const filtering = !!query || !!status
  // 一条记录都没有时不渲染搜索与筛选（审计 T37）——搜一个空库、按状态筛
  // 一个空库，两个动作都不会有任何结果。**筛出零条时它们必须留着**，
  // 否则用户没有办法把筛选条件取消掉。
  const showFilters = loaded && (filtering || total > 0)

  const load = async (q: string, st: string, off: number) => {
    try {
      const res = await fetchAiHistory({ q, status: st, limit: PAGE, offset: off })
      setEntries(res.sessions)
      setTotal(res.total)
      setError(null)
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setLoaded(true)
    }
  }

  useEffect(() => {
    const t = window.setTimeout(() => void load(query, status, offset), query ? 250 : 0)
    return () => window.clearTimeout(t)
  }, [query, status, offset])

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-surface">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-2.5">
        <h3 className="text-xs font-medium text-ink">{ai('history.title')}</h3>
        <Button
          size="icon-sm"
          className="-mr-1 ml-auto"
          onClick={onClose}
          aria-label={ai('history.close')}
        >
          <X size={ICON_SIZE.sm} />
        </Button>
      </div>
      {showFilters && (
      <div className="flex shrink-0 items-center gap-1.5 px-2.5 py-1.5">
        <SearchInput
          value={query}
          onValueChange={(v) => {
            setQuery(v)
            setOffset(0)
          }}
          placeholder={ai('history.searchPlaceholder')}
          aria-label={ai('history.searchAria')}
        />
        <Select
          value={status || ALL_STATUSES}
          onChange={(v) => {
            // Radix 的 Item 不许用空串当值（那是「未选中」的保留态），所以
            // 「全部状态」走一个显式哨兵，只在这一层翻译成后端认的空筛选
            setStatus(v === ALL_STATUSES ? '' : v)
            setOffset(0)
          }}
          options={[
            { value: ALL_STATUSES, label: ai('history.allStatuses') },
            ...HISTORY_STATUSES.map((v) => ({ value: v, label: statusLabel(v) })),
          ]}
          ariaLabel={ai('history.filterAria')}
          className="w-auto shrink-0"
        />
      </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-2">
        {error ? (
          <p className="py-2 text-xs text-danger">{error}</p>
        ) : !loaded ? null : entries.length === 0 ? (
          /* 空状态只给一句（审计 T37）：怎么开始，输入框自己说 */
          <EmptyState icon={RotateCcwClock} title={ai(filtering ? 'history.noMatch' : 'history.empty')} />
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
            {ai('history.prev')}
          </Button>
          <span className="text-xs text-ink-3">
            {Math.floor(offset / PAGE) + 1} / {Math.ceil(total / PAGE)}
          </span>
          <Button
            size="sm"
            disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}
          >
            {ai('history.next')}
          </Button>
        </div>
      )}
    </div>
  )
}

function HistoryRow({ entry, onChanged }: { entry: AiHistoryEntry; onChanged: () => void }) {
  useTranslation('ai')
  const caps = useAiStore((s) => s.caps)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const failed = entry.status === 'failed' || entry.status === 'timeout' || entry.status === 'interrupted'

  return (
    // 一条任务一张卡（shadow-card，2026-09-15 学 Beautiful UI 的 Task Rows，用户拍板）：
    // 此前是 hairline 隔开的段落；状态改成徽章（语义色 + 淡底一对），失败 danger、改过 ok、其余中性
    <div className="rounded-md bg-surface p-2 shadow-card">
      <p className="line-clamp-2 text-xs leading-relaxed text-ink">{entry.prompt}</p>
      <p className="type-meta mt-0.5 truncate">
        {/* 历史里的 provider 是**当时**用的那个 Agent id：显示名从当前
            capabilities 查，查不到就原样显示 id（不写死两个名字，也不留空） */}
        {entry.target || ai('scope.figure')} · {agentDisplayName(caps, entry.provider)}
        {entry.model ? ` · ${entry.model}` : ''} · {timeOf(entry.started_ms)}
      </p>
      <div className="mt-1.5 flex items-center gap-1.5">
        <Badge tone={failed ? 'danger' : entry.changed ? 'ok' : 'neutral'}>
          {statusLabel(entry.status)}
          {entry.changed ? ai('history.changedSuffix') : ''}
        </Badge>
        <span className="flex-1" />
        <Tip label={ai(entry.pinned ? 'history.unpinTip' : 'history.pinTip')}>
          <Button
            size="icon-sm"
            active={entry.pinned}
            aria-pressed={entry.pinned}
            aria-label={ai(entry.pinned ? 'history.unpin' : 'history.pin')}
            onClick={() => void pinAiHistory(entry.id, !entry.pinned).then(onChanged)}
          >
            <Pin size={ICON_SIZE.xs} filled={entry.pinned} className={entry.pinned ? undefined : 'text-ink-3'} />
          </Button>
        </Tip>
        {entry.changed && entry.revert_available && (
          <Tip label={ai('history.revertTip')}>
            <Button
              size="icon-sm"
              className="text-danger"
              aria-label={ai('history.revert')}
              onClick={() =>
                void aiRevert(entry.id).then(() => {
                  useUiStore.getState().setStatus(msg('history.reverted', undefined, 'ai'))
                  onChanged()
                })
              }
            >
              <RotateCcw size={ICON_SIZE.xs} />
            </Button>
          </Tip>
        )}
        <Tip label={ai('history.delete')}>
          <Button
            size="icon-sm"
            aria-label={ai('history.delete')}
            onClick={() => void deleteAiHistory(entry.id).then(onChanged)}
          >
            <Trash2 size={ICON_SIZE.xs} className="text-ink-3" />
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
          size={ICON_SIZE.xs}
          className={cn('shrink-0 transition-transform', detailsOpen && 'rotate-90')}
        />
        {ai('panel.techDetails')}
      </button>
      {detailsOpen && (
        <div className="mt-0.5 flex flex-col gap-0.5 border-l border-border pl-2">
          {/* 脚本名是路径 → 等宽片；句子本身不是代码 */}
          <p className="type-meta flex min-w-0 items-center gap-1">
            <span className="shrink-0">{ai('panel.scriptLabel')}</span>
            <code className="min-w-0 truncate rounded-xs bg-surface-2 px-1 font-mono text-ink-2">
              {entry.script ? scriptName(entry.script) : ai('panel.none')}
            </code>
          </p>
          {entry.effort && <p className="type-meta">{ai('history.effort', { effort: entry.effort })}</p>}
          <p className="type-meta">
            {ai(entry.revert_available ? 'history.snapshotAvailable' : 'history.snapshotCleared', {
              id: entry.id,
            })}
          </p>
        </div>
      )}
    </div>
  )
}

/** 时间按**当前界面语言**格式化（以前钉死 zh-CN，英文界面里会露馅） */
const timeOf = (ts: number) => formatTime(ts)

/** 改动是直接落盘的，措辞不能像「待应用的预览」 */
function statusText(s: AiSession): string {
  if (s.status === 'running') return ai('session.running')
  if (s.status === 'done') return ai(s.changed ? 'session.doneChanged' : 'session.doneNoChange')
  if (s.status === 'reverted') return ai('session.reverted')
  return statusLabel(s.status)
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
  useTranslation('ai')
  const caps = useAiStore((s) => s.caps)
  const running = session.status === 'running'
  const groups = groupEntries(session.entries)

  return (
    // 新的一轮对话落位：淡入 + 4px 上浮，弹簧收尾（settle-in）。
    // 一轮对话是一张卡（shadow-card，2026-09-15 学 Beautiful UI 的 Chat / Task Rows，用户拍板）：
    // 提示 → 过程 → 回答 → 状态 → diff 是一件事的五段，卡把它们收在一起；卡与卡之间只靠间距。
    // 卡里再分层用 surface-2 的凹块（提示），不再套第二张卡。
    <div
      className="flex animate-settle-in flex-col gap-1.5 rounded-md bg-surface p-2 shadow-card"
      data-ai-session={session.status}
    >
      {/* 提示是卡里的凹块：surface-2 底、无边（Beautiful UI 的 inset 那一级）。
          meta 那行是执行器 / 目标 / 时刻，不是代码或路径——等宽字体只留给代码 */}
      <div className="rounded-sm bg-surface-2 px-2 py-1.5">
        <p className="text-sm leading-[1.6] break-words text-ink">{session.prompt}</p>
        <p className="type-meta mt-0.5 truncate">
          {sessionAgentLabel(caps, session)} · {session.target} · {timeOf(session.startedAt)}
        </p>
      </div>

      {groups.map((g, i) =>
        g.type === 'message' ? (
          <MessageBody key={i} text={g.text} streaming={g.streaming} />
        ) : (
          <ProcessGroup key={i} items={g.items} />
        ),
      )}

      {/* 状态行是唯一的「还活着」信号：进行中一道亮带扫过文字，完成即停——不另摆
          loader / 骨架（三样东西同时在动是在互相抢注意力）。中止在输入框旁那颗按钮上
          （发送 ↔ 中止同一位置），这里不再复制一颗。 */}
      {/* 换字原位换（宪法第二十三节）：running → done 那一下不硬切。`text-shimmer` 落在会动的那句字上；
          外层 ink-3 是退场那句幽灵的颜色。`data-ai-status` 跟着字走——用例与诊断认它 */}
      <p className="text-xs text-ink-3">
        <SwapText
          text={statusText(session)}
          textClassName={running ? 'text-shimmer' : toneOf(session)}
          data-ai-status={session.status}
        />
      </p>

      {session.error && <p className="text-xs text-danger">{session.error}</p>}

      {session.verification && (
        <p
          className={cn(
            'text-xs leading-relaxed',
            session.verification.status === 'verified' ? 'text-ink-2' : 'text-danger',
          )}
          data-ai-source-bake={session.verification.status}
        >
          {ai(
            session.verification.status === 'verified'
              ? 'session.bakeVerified'
              : session.verification.status === 'mismatch'
                ? 'session.bakeMismatch'
                : 'session.bakeVerifyFailed',
          )}
        </p>
      )}

      {session.changed && session.diff && (
        <div className="flex animate-settle-in flex-col gap-1.5">
          <DiffView diff={session.diff} script={session.script} />
          {/* destructive 只有红字 ghost（第五节）：此前是 secondary + 撑满 + 红字，
              整个对话区最宽的一颗钮（打磨 A3） */}
          <Button
            variant="danger"
            size="sm"
            className="self-end"
            onClick={() => void revertSession(session)}
          >
            <RotateCcw size={ICON_SIZE.sm} />
            {ai('panel.revert')}
          </Button>
        </div>
      )}
    </div>
  )
}

/** 思考/动作默认只占一行，点开才看时间线——不刷屏 */
function ProcessGroup({ items }: { items: { kind: string; text: string }[] }) {
  useTranslation('ai')
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 text-left text-xs text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        <ChevronRight size={ICON_SIZE.xs} className={cn('shrink-0 transition-transform', open && 'rotate-90')} />
        <span className="truncate">
          {ai('panel.processSteps', { count: items.length })}
          {!open && items.at(-1)
            ? ai('panel.processTail', {
                text: items.at(-1)!.text.replace(/\s+/g, ' ').slice(0, 16),
              })
            : ''}
        </span>
      </button>
      {/* 展开是跟着内容长高（Reveal），不是一整块瞬间跳出来 */}
      <Reveal open={open}>
        <ul className="mt-1 flex flex-col gap-1 pl-1">
          {items.map((it, i) => (
            <ProcessRow key={i} kind={it.kind} text={it.text} />
          ))}
        </ul>
      </Reveal>
    </div>
  )
}

/**
 * 过程里的一行（2026-09-15 学 Beautiful UI 的 Tool Chips）：种类图标（ink-3）· 动词 · 参数片。
 * 后端（`engine/ai_agents.py`）给 action 文本开头放一个**标记符 + 空格**：`$` = 跑了一条命令，
 * 其它符号（一支笔）= 改了文件 / 调了工具。这里只拆标记、不画它——图标是 Wrench / Pencil；
 * 「一个非字母数字的符号 + 空格」统一当标记解析，不把那个字形写死在前端。拆成「动词 + 参数」后
 * 参数放进 surface-2 底的等宽片里——路径 / 命令是代码，正文的动词不是。没有标记的 action 原样
 * 当参数片；thinking 是一句话，ink-3、无片。
 */
const ACTION_MARK = /^([^\p{L}\p{N}\s])\s+(.*)$/su

function ProcessRow({ kind, text }: { kind: string; text: string }) {
  useTranslation('ai')
  if (kind !== 'action') {
    return (
      <li className="flex items-start gap-1.5 text-xs leading-relaxed text-ink-3">
        <Sparkles size={ICON_SIZE.xs} className="mt-[3px] shrink-0" aria-hidden />
        <span className="min-w-0 whitespace-pre-wrap break-words">{text}</span>
      </li>
    )
  }
  const m = ACTION_MARK.exec(text)
  const shell = m?.[1] === '$'
  const Icon = shell ? Wrench : Pencil
  let verb = ''
  let arg = text
  if (m && shell) {
    verb = ai('panel.stepRan')
    arg = m[2]
  } else if (m) {
    // 「<标记> 名字 目标」：名字是动词位，目标是参数；只有名字时参数为空
    const rest = m[2].trim()
    const sp = rest.indexOf(' ')
    verb = sp === -1 ? rest : rest.slice(0, sp)
    arg = sp === -1 ? '' : rest.slice(sp + 1)
  }
  return (
    <li className="flex min-w-0 items-start gap-1.5 text-xs leading-relaxed text-ink-2">
      <Icon size={ICON_SIZE.xs} className="mt-[3px] shrink-0 text-ink-3" aria-hidden />
      {verb && <span className="shrink-0 text-ink">{verb}</span>}
      {arg && (
        <code className="min-w-0 truncate rounded-xs bg-surface-2 px-1 font-mono text-ink-2" title={arg}>
          {arg}
        </code>
      )}
    </li>
  )
}

async function revertSession(session: AiSession) {
  await useAiStore.getState().revert(session.id)
  // 回滚后 worker 会话同样失效，重建让画布自动回到改动前的样子
  if (session.fileId) useRenderStore.getState().markStale([session.fileId])
  useUiStore.getState().setStatus(msg('session.revertedStatus', undefined, 'ai'))
}

/**
 * 正文。流式阶段**照样按 markdown 渲染**（与 ChatGPT / Claude 一致，不等终稿），
 * 新到的词由 `lib/streamMarkdown` 包成一次淡入；终稿到达后去掉插件，视觉零变化。
 * 此前流式阶段走第三方的「涂黑显影」效果、终稿才换排版——两段观感不同，切换那一下会跳。
 */
function MessageBody({ text, streaming }: { text: string; streaming?: boolean }) {
  return <Markdown text={text} streaming={!!streaming} />
}
