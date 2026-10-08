import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowDown,
  ArrowUp,
  CaseSensitive,
  ChartLine,
  ChevronRight,
  FileCodeCorner,
  Image as ImageIcon,
  LayoutGrid,
  LayoutList,
  Minus,
  MoveHorizontal,
  RotateCcwClock,
  Sparkles,
  Square,
  Type,
  type IconComponent,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  agentById,
  agentDisplayName,
  backendErrorText,
  effectiveAgent,
  usableAgents,
  type AiCapabilities,
  type ManifestElement,
} from '@/lib/api'
import { cn, modKey } from '@/lib/utils'
import { t as translate } from '@/i18n'
import { engineLabel } from '@/components/inspector/roles/registry'
import { isSessionOf, useAiStore, type AiScope, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Reveal } from '../ui/Field'
import { fitTextAreaHeight } from '../ui/Input'
import { Notice } from '../ui/Notice'
import { Popover } from '../ui/Popover'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { StepSlider } from '../ui/StepSlider'
import { Tip } from '../ui/Tooltip'
import { TaskHistory } from './TaskHistory'
import { Turn } from './Transcript'

export { TaskHistory }

/** 右栏标签名与图标：tab bar 引用这里，改名只改这一处 */
export const assistantTabLabel = () => translate('tabLabel', { ns: 'ai' })
export const ASSISTANT_TAB_ICON = FileCodeCorner

/** 本面板的文案都在 ai 命名空间下 */
const ai = (key: string, values?: Record<string, unknown>) =>
  translate(key, { ns: 'ai', ...(values ?? {}) })

/**
 * 贴底跟随的松弛量：离底部这么近仍算「看着最新内容」。滚轮一格通常 ≥ 40px，
 * 用户真往上翻时一步就超过它；小于它的偏差只是子像素 / 滚动条尾巴。
 */
const STICK_SLACK = 24

/** 一次发送钉住的目标：作用范围 + 元素上下文（整张图时 gid / label 为空） */
type SendTarget = { scope: AiScope; gid: string | null; label: string | null; target: string }

/** 一轮会话发起时的目标：会话里存着 scope / gid / target，元素名就是 target（整张图时没有元素） */
const targetOf = (s: AiSession): SendTarget => ({
  scope: s.scope,
  gid: s.gid,
  label: s.gid ? s.target : null,
  target: s.target,
})
/** 输入框最多长到几行，再多在框内滚动 */
const COMPOSER_MAX_ROWS = 8
/** 空态里最多摆几条可点的示例提示（2026-10-07 设计审计 §6.7） */
const EXAMPLE_COUNT = 3

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

/** 起手式的前置小图标（§6.5）：只是「这条改哪类东西」的视觉提示，按改动对象分 */
const CHIP_ICON: Record<string, IconComponent> = {
  unifyFont: Type,
  unifyAxisFont: Type,
  adjustFontSize: Type,
  switchToTimes: Type,
  shrinkLegendFont: Type,
  checkMinFontSize: CaseSensitive,
  unifyTickFormat: CaseSensitive,
  unifyLineWidth: Minus,
  thickenLines: Minus,
  improveSpacing: MoveHorizontal,
  adjustPadding: MoveHorizontal,
  avoidOverlap: MoveHorizontal,
  fixLegendOverlap: LayoutList,
  moveLegend: LayoutList,
  legendTwoColumns: LayoutList,
  changeColormap: ImageIcon,
  increaseContrast: ImageIcon,
  unifyColorScale: ImageIcon,
  distinguishablePalette: ChartLine,
  adjustMarkerSize: ChartLine,
}

interface Chip {
  id: string
  label: string
  Icon: IconComponent
}

const chips = (kind: string): Chip[] =>
  (CHIP_IDS[kind] ?? []).map((id) => ({
    id,
    label: translate(`chip.${id}`, { ns: 'ai' }),
    Icon: CHIP_ICON[id] ?? LayoutGrid,
  }))

function chipsFor(scope: AiScope, element: ManifestElement | null, hasAxes: boolean): Chip[] {
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

/**
 * 「这一刻真的会派给谁、用哪个模型、哪档推理强度」——弹层（详情）与输入框工具行的两颗胶囊（摘要）
 * 读同一份，不各算一遍。首选那个暂时不可用时用第一个可用的，**但不改用户存着的首选值**；
 * 模型 / 强度选项完全由该 Agent 自己声明的能力决定，不在前端列第二份名单。
 */
function useAgentChoice() {
  const agent = useAiStore((s) => s.agent)
  const caps = useAiStore((s) => s.caps)
  const models = useAiStore((s) => s.models)
  const efforts = useAiStore((s) => s.efforts)
  const usable = usableAgents(caps)
  const active = effectiveAgent(agent, caps)
  const cur = agentById(caps, active)
  const model = (active && models[active]) ?? cur?.default_model ?? ''
  const effort = (active && efforts[active]) ?? cur?.default_effort ?? ''
  // 档位下标由**真实能力数组**算出来；记忆里那个已经不在清单里时回落到 0，
  // 绝不凭字符串造一个数组里没有的档位
  const effortList: string[] = cur?.efforts ?? []
  const effortIndex = Math.max(0, effortList.indexOf(effort))
  return { caps, usable, active, cur, model, effortList, effortIndex }
}

/**
 * 以 aiStore 的项目代际为 key 重挂：切项目时草稿、错误提示、打开着的历史视图这些组件内状态
 * 跟着换代，不把 A 的东西留在 B 的面板里（#589）。
 */
export function AssistantPanel() {
  const generation = useAiStore((s) => s.generation)
  return <AssistantPanelBody key={generation} />
}

function AssistantPanelBody() {
  useTranslation('ai')
  const sessions = useAiStore((s) => s.sessions)
  const storedScope = useAiStore((s) => s.scope)
  const { panel, element, axes } = useAssistantTarget()
  const caps = useAiStore((s) => s.caps)
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<{ text: string; prompt: string; target: SendTarget; panelId: string } | null>(null)
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
  // 输入框浮在对话流上（玻璃，参考 Codex）：它的高度会变（起手式收起、输入框长高 / 两态切换、报错一行），
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
  const canSend = !!panel && !noAgent && !sending && !runningHere

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

  /**
   * 发一条：默认发输入框里的；「重新发送」传入那一轮的原话（不动输入框里正在写的草稿）。
   * 「重试」再带上**那一次的目标**（`pinned`）：失败之后用户可能已经选了别的元素 / 换了范围，
   * 重试必须改原来那处，不能悄悄改到此刻选中的东西上（Codex #827 P1）
   */
  const send = async (override?: string, pinned?: SendTarget) => {
    const text = (override ?? prompt).trim()
    if (!text || !canSend || !panel) return
    setSending(true)
    setError(null)
    // 自己发的消息一定要看见：不管刚才翻到哪，发出去就回到底部
    jumpToBottom()
    // 作用范围直接决定发给后端的元素上下文：整张图不带 gid，
    // 后端 _build_prompt 就不会写「用户选中的元素」那一行
    const ctx: SendTarget =
      pinned ??
      (scope === 'element' && element
        ? { scope, gid: element.gid, label: element.label, target: element.label }
        : scope === 'axes' && axes
          ? { scope, gid: axes.gid, label: axes.label, target: axes.label }
          : { scope, gid: null, label: null, target: ai('scope.figure') })
    try {
      await useAiStore.getState().start({
        prompt: text,
        fileId: panel.fileId,
        panelId: panel.id,
        gid: ctx.gid,
        label: ctx.label,
        scope: ctx.scope,
        target: ctx.target,
        overrides: panel.overrides,
        canvas: useDocumentStore.getState().activeCanvasId,
      })
      if (override == null) setPrompt('')
    } catch (e) {
      setError({ text: backendErrorText(e), prompt: text, target: ctx, panelId: panel.id })
    } finally {
      setSending(false)
    }
  }

  // 发送 ↔ 中止同一颗按钮、同一个位置（ChatGPT / Claude 的约定）：正在跑的时候它就是「中止」
  const stopRunning = () => {
    const running = mine.find((s) => s.status === 'running')
    if (running) void useAiStore.getState().cancel(running.id)
  }

  const suggestions = panel ? chipsFor(scope, element, !!axes) : []
  // 两态（§6.5）：空着时是 42px 的单行胶囊；一有内容（或正在跑）就展开成「内容 / 工具行」两行
  const expanded = prompt !== '' || runningHere

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 面板顶部只剩历史：作用目标挪到了输入框上方的上下文带（§6.5） */}
      <div className="flex shrink-0 items-center justify-end px-3 pb-1">
        <Popover
          width={320}
          align="end"
          open={historyOpen}
          onOpenChange={setHistoryOpen}
          trigger={
            <IconButton
              data-ai-history-trigger
              label={ai('panel.taskHistory')}
              tip={!historyOpen}
              iconSize="sm"
              className="text-ink-2"
            >
              <RotateCcwClock size={ICON_SIZE.sm} />
            </IconButton>
          }
        >
          <TaskHistory onClose={() => setHistoryOpen(false)} />
        </Popover>
      </div>

      <div ref={stageRef} className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          data-ai-scroller
          onScroll={syncStick}
          className="h-full overflow-y-auto px-3 pt-2"
          style={{ paddingBottom: 'calc(var(--composer-h, 0px) + 8px)' }}
        >
          {!panel ? (
            /* 「这里没有可干的活」是真正的空状态，留在中间 */
            <EmptyState icon={FileCodeCorner} title={ai('panel.noPanelTitle')} />
          ) : mine.length > 0 ? (
            // 节奏由列表容器统一管（§6.1）：轮与轮 16、轮内各块 8；子组件不带外边距
            <div
              ref={contentRef}
              data-ai-transcript
              className="flex flex-col gap-(--turn-gap) [--item-gap:8px] [--turn-gap:16px]"
            >
              {mine.map((s) => (
                <Turn
                  key={s.id}
                  session={s}
                  onResend={(text) => void send(text)}
                  onRetry={(t) => void send(t.prompt, targetOf(t))}
                  canResend={canSend}
                  canRetry={canSend && s.panelId === panel?.id}
                />
              ))}
            </div>
          ) : (
            /* 还没发过任务：中间是空态 + 至多三条可点的示例提示（§6.7）——点一下填进输入框，改完再发 */
            <div data-ai-empty className="flex min-h-full flex-col items-center justify-center pb-4">
              <div className="w-full">
                <EmptyState icon={Sparkles} title={ai('panel.emptyTitle')} hint={ai('panel.emptyHint')} />
              </div>
              <div className="-mt-4 flex w-full max-w-[17rem] flex-col gap-1.5">
                {suggestions.slice(0, EXAMPLE_COUNT).map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    data-ai-example={c.id}
                    onClick={() => fillPrompt(c.label)}
                    className={cn(
                      'flex min-h-8 items-center gap-2 rounded-md bg-surface px-3 py-1.5 text-left text-base text-ink-2',
                      'inset-ring inset-ring-border outline-none transition-colors duration-fast',
                      'hover:bg-surface-hover hover:text-ink focus-visible:focus-ring',
                    )}
                  >
                    <c.Icon size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-ink-3" />
                    <span className="min-w-0 break-words">{c.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        {/* 往上翻着看、而新内容还在来：给一颗回到底部的钮（34px 毛玻璃圆）；到底了它自己消失 */}
        {detached && runningHere && (
          <div
            className="pointer-events-none absolute inset-x-0 flex justify-center"
            style={{ bottom: 'calc(var(--composer-h, 0px) + 8px)' }}
          >
            <IconButton
              data-ai-scroll-bottom
              label={ai('panel.scrollToBottom')}
              iconSize="sm"
              side="top"
              className="pointer-events-auto h-[34px] w-[34px] animate-pop-in bg-glass text-ink-2 shadow-pop backdrop-blur-lg hover:bg-glass hover:text-ink"
              onClick={jumpToBottom}
            >
              <ArrowDown size={ICON_SIZE.md} />
            </IconButton>
          </div>
        )}

        {/* 输入区浮在对话流的底部（absolute），内容从它底下滚过；玻璃在下面那个框上 */}
        <div ref={composerRef} data-ai-composer-dock className="absolute inset-x-0 bottom-0 z-drawer px-3 pb-3 pt-1">
          {/* 起手式（有对话之后的快捷追问）：一开始打字 / 正在跑就收起——收起是跟着内容合上（Reveal）。
              空态时它们以「示例提示」的形态在正中，这里不重复摆 */}
          <Reveal open={!!panel && mine.length > 0 && !prompt.trim() && !runningHere}>
            <div
              data-ai-chips
              className="flex gap-1.5 overflow-x-auto pb-2 scrollbar-none [mask-image:linear-gradient(to_right,black_calc(100%-24px),transparent)]"
            >
              {suggestions.map((c) => (
                <Button
                  key={c.id}
                  data-ai-chip={c.id}
                  size="sm"
                  className="h-[26px] shrink-0 bg-surface px-2.5 text-ink-2 inset-ring inset-ring-border hover:bg-surface-hover hover:text-ink"
                  onClick={() => fillPrompt(c.label)}
                >
                  <c.Icon size={ICON_SIZE.xs} aria-hidden className="text-ink-3" />
                  {c.label}
                </Button>
              ))}
            </div>
          </Reveal>
          {/* 发送失败只属于那一张图：换到别的面板就不再摆着它的「重试」（Codex #827 P1） */}
          {error && error.panelId === panel?.id && (
            <Notice
              tone="danger"
              data-ai-error="send"
              className="mb-2"
              title={ai('panel.sendFailed')}
              action={
                <Button data-ai-retry variant="secondary" size="sm" disabled={!canSend} onClick={() => send(error.prompt, error.target)}>
                  {ai('panel.retry')}
                </Button>
              }
            >
              {error.text}
            </Notice>
          )}
          {noAgent && (
            // 「没装 CLI」不是错误，用中性语气 + 一个可执行的下一步
            <Notice
              tone="neutral"
              data-ai-no-agent
              className="mb-2"
              action={
                <Button variant="secondary" size="sm" onClick={() => useUiStore.getState().setSettingsOpen(true, 'ai')}>
                  {ai('panel.openAiSettings')}
                </Button>
              }
            >
              {ai('panel.noCli')}
            </Notice>
          )}
          {panel && (
            <ContextBand panel={panel} element={element} axes={axes} scope={scope} scopes={scopes} />
          )}
          {/* 玻璃（2026-09-15 参考 Codex，用户拍板）：field 90% 的底 + 16px 背景模糊 + --shadow-composer。
              聚焦只把边框加深到 border-strong（§6.5：只变色无环，更安静；插入点是框里唯一的一点 accent）。
              两态由 data-layout 说：compact = 42px 胶囊 [内容 | 发送]；expanded = 16px 圆角
              [内容 / 工具行 + 发送]。同一个 textarea 节点换格子，焦点不丢。禁用只有 opacity-40 一档 */}
          <div
            data-ai-composer
            data-layout={expanded ? 'expanded' : 'compact'}
            className={cn(
              'relative grid grid-cols-[minmax(0,1fr)_auto] border border-transparent bg-glass text-ink shadow-composer backdrop-blur-lg',
              'transition-[border-color] duration-fast focus-within:border-border-strong',
              expanded
                ? "min-h-24 gap-y-2 rounded-panel pb-2 pl-3.5 pr-2 pt-2.5 [grid-template-areas:'content_content'_'start_end']"
                : "h-[42px] items-center gap-x-2 rounded-full pl-4 pr-2 [grid-template-areas:'content_end']",
              (!panel || noAgent) && 'opacity-40',
            )}
          >
            <textarea
              ref={inputRef}
              data-ai-input
              value={prompt}
              rows={1}
              disabled={!panel || noAgent}
              placeholder={panel ? ai('panel.placeholder') : undefined}
              aria-label={ai('panel.inputAria')}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault()
                  void send()
                }
              }}
              className={cn(
                'block w-full resize-none bg-transparent text-base leading-5 [grid-area:content]',
                // 占位是要读的字：ink-3，与其它输入框同一档（faint 只给装饰 / 禁用）
                'text-ink outline-none placeholder:text-ink-3',
                expanded ? 'min-h-10 self-start' : 'h-5 overflow-hidden',
              )}
            />
            {expanded && (
              <div className="flex min-w-0 items-center gap-1 [grid-area:start]">
                <AgentPills panel={panel} element={element} axes={axes} scope={scope} scopes={scopes} />
              </div>
            )}
            <div className="flex items-center self-end [grid-area:end]">
              {/* 快捷键只说一次：发送钮的气泡里已经有「⌘↵」，输入框上不再常驻一枚键帽（打磨 A5） */}
              <Tip label={runningHere ? ai('panel.abort') : ai('panel.send', { key: modKey('↵') })}>
                <Button
                  variant="primary"
                  size="icon-sm"
                  data-ai-send={runningHere ? 'stop' : 'send'}
                  className="relative h-[26px] w-[26px]"
                  disabled={!panel || noAgent || sending || (!runningHere && !prompt.trim())}
                  aria-busy={sending || runningHere || undefined}
                  onClick={runningHere ? stopRunning : () => send()}
                  aria-label={runningHere ? ai('panel.abort') : ai('panel.sendAria')}
                >
                  <SendStopGlyph stop={runningHere} busy={sending || runningHere} />
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
 * 发送 ↔ 中止的图标（§6.5）：26px 黑圆里，↑ 与 9px 圆角方块（图标集的 Square 填实）叠在同一格交叉淡化——**不缩放**
 * （此前 scale-50 换形，小钮上像一跳）。进行中多一圈 1px 的弧线绕着转：它是「中止」这个状态的一部分
 * （说明此刻点下去是停），不是第二个「还活着」信号——那仍只有转录里的状态行（宪法第十八节）。
 */
function SendStopGlyph({ stop, busy }: { stop: boolean; busy: boolean }) {
  const cls = (shown: boolean) =>
    cn('col-start-1 row-start-1 transition-opacity duration-base', shown ? 'opacity-100' : 'opacity-0')
  return (
    <>
      <span className="grid place-items-center" aria-hidden>
        <ArrowUp size={ICON_SIZE.sm} className={cls(!stop)} />
        {/* 图标集里的 Square 填实：12px 的格里画出来约 9px 见方、圆角约 2px */}
        <Square size={ICON_SIZE.xs} className={cn('fill-current', cls(stop))} />
      </span>
      {busy && (
        <span
          aria-hidden
          data-ai-orbit
          className="pointer-events-none absolute inset-[3px] animate-spin rounded-full border border-surface/25 border-t-surface"
        />
      )}
    </>
  )
}

type TargetProps = {
  panel: PanelObject
  element: ManifestElement | null
  axes: ManifestElement | null
  scope: AiScope
  scopes: AiScope[]
}

/**
 * 上下文带（§6.5）：输入框**上方**一条 30px 的玻璃带，里面一枚「● 目标 · 作用范围」chip——发送前这一行
 * 独立回答「按下去会改什么」。点开是作用范围与执行器的详情弹层。作用范围只在这里说一次（打磨 L6）。
 * 带的下沿压在输入框后面 10px，看上去像从输入框里长出来的一截。
 */
function ContextBand(props: TargetProps) {
  useTranslation('ai')
  const { panel, element, axes, scope } = props
  // 元素名是引擎发来的散文，过 engineLabel 换成当前语言；面板名是用户内容
  const targetText =
    scope === 'element' && element
      ? engineLabel(element.label)
      : scope === 'axes' && axes
        ? engineLabel(axes.label)
        : (panel.name ?? panel.fileId)
  return (
    <div
      data-ai-context
      className="mx-3 -mb-2.5 flex h-10 items-start rounded-t-lg bg-glass px-1.5 pt-[3px] backdrop-blur-lg"
    >
      <Popover
        width={288}
        align="start"
        side="top"
        trigger={
          <button
            type="button"
            data-ai-target
            aria-label={ai('panel.targetAria', { target: targetText })}
            className={cn(
              'flex h-6 min-w-0 max-w-full items-center gap-1.5 rounded-full px-2 text-left text-sm',
              'outline-none transition-colors duration-fast hover:bg-surface-hover focus-visible:focus-ring data-[state=open]:bg-surface-active',
            )}
          >
            <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-accent" />
            <span className="min-w-0 truncate text-ink">{targetText}</span>
            <span className="shrink-0 text-ink-3">· {scopeLabel(scope)}</span>
          </button>
        }
      >
        <ScopeAgentContent {...props} />
      </Popover>
    </div>
  )
}

/**
 * 工具行的两颗胶囊（§6.5）：「执行器 · 模型」与「推理 强度」直接可见，不再埋进弹层；点开是同一份详情弹层。
 * 写的是**这一刻真的会派给谁**，不是用户存着的首选值。探测中 / 没有可用 Agent 时不摆（上下文带与说明条说）。
 */
function AgentPills({ panel, ...rest }: Omit<TargetProps, 'panel'> & { panel: PanelObject | null }) {
  useTranslation('ai')
  const { caps, active, cur, model, effortList, effortIndex } = useAgentChoice()
  if (!panel || !active || !cur) return null
  const pill = (key: string, text: string, aria: string) => (
    <Popover
      width={288}
      align="start"
      side="top"
      trigger={
        <Button
          data-ai-pill={key}
          variant="secondary"
          size="sm"
          aria-label={aria}
          className="h-6 min-w-0 px-2 text-ink-2 hover:text-ink"
        >
          <span className="min-w-0 truncate">{text}</span>
        </Button>
      }
    >
      <ScopeAgentContent panel={panel} {...rest} />
    </Popover>
  )
  return (
    <>
      {pill('model', modelText(caps, active, model), ai('panel.agentModel'))}
      {effortList.length > 0 &&
        pill('effort', ai('panel.effortPill', { effort: effortLabel(effortList[effortIndex]) }), ai('panel.effort'))}
    </>
  )
}

const modelText = (caps: AiCapabilities | null, active: string, model: string) =>
  model ? `${agentDisplayName(caps, active)} · ${model}` : agentDisplayName(caps, active)

export function ScopeAgentContent({ panel, element, axes, scope, scopes }: TargetProps) {
  useTranslation('ai')
  const { caps, usable, active, cur, model, effortList, effortIndex } = useAgentChoice()
  const [effortOpen, setEffortOpen] = useState(false)

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
              不出现——执行器是谁，工具行的胶囊上已经写着了 */}
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
