/**
 * 一键排版的**落地**（ADR 0199，高级功能 `quick-layout`）。求解与核对都在后端（`/api/pro/quick-layout/plan`，
 * **只算不写**）；这里只做四件事：收集选区、发请求、回来核对「图还是出发时那样」、**一次 commit**。
 *
 * 纪律照 `issueFixActions`（ADR 0080）：
 *
 * * **一次排版一条历史**：所有面板的覆盖、摆放与标签在同一个 `commit` 里落下，⌘Z 一步撤回。
 * * **先出卡再落地**（ADR 0206 §一，`ai.change_plan.enabled` 默认开；拖分隔线调比例不出卡）：求解回来后不直接 commit，
 *   而是把「排版」做成一张修改方案卡（一个 `atomic` 组）+ 前后两张整版预览，用户在弹窗里「应用」才走**同一个** `commitPlan`。
 *   预览出不来 / 开关关着 = 照旧直接落地（预览失败多一句灰字）。
 * * **等待期间文档被改过就丢弃**：发请求前记下每张相关面板的整个对象、页面与规范，回来对不上就不写，
 *   并把共享 worker 按此刻的列表重放（后端说通过时 worker 停在候选上）。
 * * **失败什么都不改**：不弹窗、不报红，一句话说「没排好」；请求抛了 / 形状不对是「不知道」，同样重放。
 * * **只走 documentStore**：dirty、undo、autosave 与手改一模一样；这里没有任何权益判断（置灰只是画法，
 *   权威在后端，ADR 0199 §九）。
 * * 标签对象与免费的「添加序号标签」同一个构造（`actions.makeSubLabel`）。
 */
import { create } from 'zustand'
import { emitActivity } from '@/lib/activity'
import { currentProjectId } from '@/lib/session'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { releaseOwned } from '@/lib/styleOwned'
import { literal, msg, t as translate, type UiMessage } from '@/i18n'
import { fetchLayoutPreviewPng, quickLayoutPlan, type ChangePlan, type QuickLayoutRequest, type QuickLayoutResponse } from '@/lib/api'
import { changePlanEnabled } from '@/lib/changePlanSwitch'
import { canvasSegmentOf } from '@/lib/exportRequest'
import { ArtifactValidationError, refuseArtifactOperation } from '@/lib/artifactValidation'
import { engineTransport } from '@/lib/engineTransport'
import { takeCheckpoint } from '@/lib/timelineCheckpoint'
import { isSubLabel } from '@/lib/stylePresets'
import { canvasTextDefaults } from '@/lib/typography'
import type { FigureDocument, PanelObject, PanelOverride } from '@/types/document'
import { panelRotation } from '@/types/document'
import { makeSubLabel, movableTargets } from './actions'
import { newRequestId, useAiStore } from './aiStore'
import { useChangePlanStore, type PlanEntry } from './changePlanStore'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { useLicenseStore } from './licenseStore'
import { currentSpec } from './issueFixActions'
import { exactPanelManifest, useRenderStore } from './renderStore'
import { requestRender } from './renderScheduler'
import { alignNewFigures } from './styleBinding'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore, type StatusAction } from './uiStore'
import {
  readQuickLayoutPrefs,
  resolveCanvasMode,
  type QuickLayoutCanvasMode,
  type QuickLayoutTarget,
} from '@/lib/quickLayoutPrefs'
import { quickLayoutValid } from '@/lib/quickLayoutGrid'
import type { QuickLayoutSpecChange } from '@/lib/api'
import type { QuickLayoutMemo } from '@/types/document'

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'workspace')
const note = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`status.${key}`, values, 'workspace')

/**
 * 「撤销」走 `runUndoRedo`（唯一的撤销入口，让位判据在那里）。它的模块（`hooks/useKeyboard`）反过来
 * import 命令面板、命令面板 import 本模块，静态 import 会成环——所以由 `useKeyboard` 在模块加载时把它交进来。
 */
let undoRunner: (() => void) | null = null
export function setQuickLayoutUndoRunner(run: () => void): void {
  undoRunner = run
}

/**
 * 按钮的忙碌态（请求期间）与拖分隔线的预览轮廓（mm，只画淡色框；松手后等方案期间留着，方案回来 / 失败时清掉）。
 * 运行时状态，不进文档；一次只跑一轮
 */
export const useQuickLayoutStore = create<{
  busy: boolean
  preview: Record<string, Box> | null
  /** 拖动中正由图本身（而不是淡色轮廓）跟手的那几张图的 id：这些不再画轮廓 */
  live: readonly string[] | null
  /** 正在拖的那条分隔线（mm）：拖动中把手跟着走、读出两侧的宽（高）；松手后等方案期间留着 */
  drag: GutterDragView | null
}>(() => ({
  busy: false,
  preview: null,
  live: null,
  drag: null,
}))

/** 拖动中的分隔线：位置（已含位移）、沿线范围、把手所在的比例位置、两侧尺寸 */
export interface GutterDragView {
  axis: 'col' | 'row'
  pos: number
  from: number
  to: number
  along: number
  sizes: [number, number]
}

/** 拖分隔线的画面跟手层：方案 commit 之后告诉它最终的框（它负责保持到图真正落位） */
export interface QuickLayoutCommitObserver {
  onCommitted(boxes: Record<string, [number, number, number, number]>): void
}

export type QuickLayoutOutcome =
  | 'applied'
  | 'busy'
  | 'too_few'
  | 'stale'
  | 'failed'
  | 'not_licensed'
  /** 方案已出成一张修改方案卡（ADR 0206），等用户在弹窗里「应用」；这一刻文档一个字没动 */
  | 'planned'

/** 成功胶囊多留一会儿：那一行灰字要读得完（带动作的胶囊本来不自己走） */
const DONE_DISMISS_MS = 10_000

const same = (v: unknown): string => JSON.stringify(v ?? null)

type Box = [number, number, number, number]
const boxOf = (p: PanelObject): Box => [p.x, p.y, p.w, p.h]

/**
 * 求解器按「整张图」摆放与校验（字号 / 轴线对齐 / 间距）。被裁剪的图（`crop` = 相对整图的 0–1 比例框）
 * 页面上的框只是可见部分，显示比例 = 可见框 / crop.w，所以规划请求发整图框，落位再换回可见框，比例尺才与校验的一致。
 */
function fullBoxOf(p: PanelObject): Box {
  return fullFromVisible(p.crop, boxOf(p))
}

/** 页面上的可见框 -> 整图框（`visibleBox` 的逆）；没裁剪原样。拖分隔线的预览 / 网格记忆里都是可见框，发求解器前走这里 */
export function fullFromVisible(crop: PanelObject['crop'], b: Box): Box {
  if (!crop) return b
  const [x, y, vw, vh] = b
  const w = vw / crop.w
  const h = vh / crop.h
  return [x - crop.x * w, y - crop.y * h, w, h]
}

/** 求解器的整图框 -> 页面上的可见框（`fullBoxOf` 的逆）；没裁剪原样 */
function visibleBox(crop: PanelObject['crop'], b: Box): Box {
  if (!crop) return b
  const [x, y, w, h] = b
  return [x + crop.x * w, y + crop.y * h, w * crop.w, h * crop.h]
}

/**
 * 这张图能不能参与求解（改图幅 / 轴位置）：有图内编辑入口的脚本出图，没旋转 / 翻转，不是位图，不是 native，
 * 不是 artifact / 来源驱动的图。其余的只当固定盒子。判据只在这里，后端对 native / 重复图另有一道。
 */
function resizable(p: PanelObject): boolean {
  if (p.fileKind === 'raster') return false
  if (panelRotation(p) !== 0 || p.flipH || p.flipV) return false
  if (!p.fileId) return false
  if (useRuntimeAssetStore.getState().byId[p.fileId]?.profile === 'native') return false
  try {
    refuseArtifactOperation(p)
  } catch (err) {
    if (err instanceof ArtifactValidationError) return false
    throw err
  }
  // 有图内编辑入口（脚本出图，`obj.script`）就能排：渲染与几何由引擎在请求里自己做，
  // 不要求前端此刻已有精确 manifest（没进过图内编辑的图前端本来就没有）
  return !!p.script
}

/**
 * 锁定的图、含锁定成员的分组里的图一律按固定处理（不改位置 / 尺寸 / overrides / 标签）：
 * 判据直接取 `movableTargets`，与拖动 / 方向键同一套。
 */
function unlocked(panels: readonly PanelObject[]): Set<string> {
  return new Set(movableTargets(panels.map((p) => p.id)).objects.map((o) => o.id))
}

/** 选中 ≥ 2 张图就排选中的，否则排当前画布上的全部（ADR 0199 §一） */
function involvedPanels(doc: FigureDocument): PanelObject[] {
  const all = doc.objects.filter((o): o is PanelObject => o.type === 'panel')
  const ids = new Set(useSelectionStore.getState().ids)
  const picked = all.filter((p) => ids.has(p.id))
  return picked.length >= 2 ? picked : all
}

/** 子图标签字母：优先按返回的标签顺序（(a)(b)…），退到面板顺序 */
function letterOf(ref: string, res: Extract<QuickLayoutResponse, { ok: true }>): string {
  let i = res.labels.findIndex((l) => l.key === ref)
  if (i < 0) i = res.panels.findIndex((p) => p.key === ref)
  return String.fromCharCode(97 + Math.max(0, i))
}

const SPEC_ORDER: QuickLayoutSpecChange[] = [
  'font_family',
  'font_size',
  'line_width',
  'tick_direction',
  'legend_frame',
  'text_weight',
  'other',
]

/** 「已按「规范名」改了字体、字号…」：类别按固定顺序，没改动 = 没有这一项 */
function specLine(res: Extract<QuickLayoutResponse, { ok: true }>, specName: string): string | undefined {
  const got = new Set(res.spec_changes)
  const cats = SPEC_ORDER.filter((c) => got.has(c))
  if (!cats.length) return undefined
  const sep = translate('status.quickLayoutSpecSep', { ns: 'workspace' })
  const what = cats.map((c) => translate(`status.quickLayoutSpec.${c}`, { ns: 'workspace' })).join(sep)
  const spec = specName.trim() || translate('status.quickLayoutSpecFallback', { ns: 'workspace' })
  return translate('status.quickLayoutSpecDone', { ns: 'workspace', spec, what })
}

function noteLine(res: Extract<QuickLayoutResponse, { ok: true }>, specName: string, extra?: string): UiMessage | undefined {
  const seen = new Set<string>()
  const lines: string[] = extra ? [extra] : []
  const spec = specLine(res, specName)
  if (spec) lines.push(spec)
  for (const n of res.notes) {
    const id = `${n.code}:${n.ref}`
    if (seen.has(id)) continue
    seen.add(id)
    const key = n.code === 'equal_aspect' ? 'status.quickLayoutNoteEqualAspect' : 'status.quickLayoutNoteSpecNotFixed'
    lines.push(translate(key, { ns: 'workspace', label: letterOf(n.ref, res) }))
  }
  // 多条也只占一行：用「；」连成一句。成文那一刻的语言（胶囊几秒就走，不跟着切语言重翻）
  return lines.length ? literal(lines.join('；')) : undefined
}

function say(message: UiMessage, tone: 'info' | 'done', opts?: { subline?: UiMessage; dismissMs?: number; action?: StatusAction }) {
  useUiStore.getState().setStatus(message, tone, opts)
}

/** 等参与排版、还没出过图的那几张出第一张图（最多 SETTLE_MS），再让画布样式对齐新图（ADR 0081，免费能力）。 */
const SETTLE_MS = 30_000
/** 一次操作出发时所在的文档 / 载入 / 画布：每个 await 回来都对着它核，换了就放弃 */
interface OpContext {
  documentId: string
  loadSeq: number
  canvasId: string
}
const contextNow = (): OpContext => {
  const s = useDocumentStore.getState()
  return { documentId: s.documentId, loadSeq: s.loadSeq, canvasId: s.activeCanvasId }
}
const sameContext = (a: OpContext): boolean => {
  const b = contextNow()
  return a.documentId === b.documentId && a.loadSeq === b.loadSeq && a.canvasId === b.canvasId
}

async function settleNewFigures(ctx: OpContext): Promise<boolean> {
  const ready = (id: string) => {
    const cur = useDocumentStore.getState().doc.objects.find((o) => o.id === id)
    return !cur || cur.type !== 'panel' || !!exactPanelManifest(useRenderStore.getState(), cur)
  }
  const involvedNow = involvedPanels(useDocumentStore.getState().doc)
  const free = unlocked(involvedNow)
  const pending = involvedNow.filter((p) => free.has(p.id) && resizable(p) && !ready(p.id))
  if (pending.length) {
    for (const p of pending) requestRender(p)
    await new Promise<void>((resolve) => {
      const done = () => pending.every((p) => ready(p.id))
      if (done()) return resolve()
      const timer = setTimeout(() => {
        unsubscribe()
        resolve()
      }, SETTLE_MS)
      const unsubscribe = useRenderStore.subscribe(() => {
        if (!done()) return
        clearTimeout(timer)
        unsubscribe()
        resolve()
      })
    })
  }
  // 等待期间切了文档 / 画布 / 项目：不对新活动的那份做任何事
  if (!sameContext(ctx)) return false
  alignNewFigures()
  return true
}

/**
 * 点「一键排版」。`target` / `gapMm` 来自工具条的小下拉（或命令面板读同一份偏好）；排列、画布与「按规范修」读同一份偏好。
 * 回的结局给用例与调用方看；用户看到的只有一枚胶囊。
 */
export async function runQuickLayout(target: QuickLayoutTarget, gapMm: number): Promise<QuickLayoutOutcome> {
  if (useQuickLayoutStore.getState().busy) return 'busy'
  // 离散动作：先把还开着的手势收干净（与 setFigureFrame 同）
  finishActiveGesture()

  // 先让画布样式落到新放上来的图上，再按落好样式之后的字号排（2026-11-20 走查：刚放上的图还没出第一张图，
  // 一键排版抢在样式前面算完，样式随后才套上，轴标题变粗变大，算好的对齐与留白就不作数了）
  const ctx = contextNow() // 第一个 await 之前取基线，整条链路都对着它核
  useQuickLayoutStore.setState({ busy: true })
  let settled = false
  try {
    settled = await settleNewFigures(ctx)
  } finally {
    useQuickLayoutStore.setState({ busy: false })
  }
  if (!settled) return 'stale' // 静默放弃：文档一个字没动，不说话

  const prefs = readQuickLayoutPrefs()
  const docAtStart = useDocumentStore.getState().doc
  return runPlan({
    involved: involvedPanels(docAtStart),
    target,
    gapMm,
    columns: prefs.columns,
    canvas: resolveCanvasMode(prefs.canvas, docAtStart.page.userSized),
    fixSpec: prefs.fixSpec,
    memoFixSpec: prefs.fixSpec,
    label: 'quickLayout',
    checkpoint: true,
    announce: true,
    ctx,
  })
}

/**
 * 拖分隔线松手：用记着的网格（`doc.quickLayout`）与新的行 / 列比例重排一次——**不再按规范修**（`fix_spec: false`，
 * 约 0.4 s），一次 commit、一条历史「调整排版比例」。记忆已失效 / 有别的一轮在跑 = 什么都不做。
 * 失败只是一句话，图一个字没动（调用方负责收掉预览轮廓——这里的 finally 已经清了）。
 */
export async function runQuickLayoutRatios(
  ratios: { cols: number[]; rows: number[]; rows_mm?: number[] },
  observer?: QuickLayoutCommitObserver,
): Promise<QuickLayoutOutcome> {
  if (useQuickLayoutStore.getState().busy) return 'busy'
  const doc = useDocumentStore.getState().doc
  const memo = doc.quickLayout
  if (!quickLayoutValid(memo, doc.objects)) {
    useQuickLayoutStore.setState({ preview: null, drag: null })
    return 'stale'
  }
  const byId = new Map(doc.objects.map((o) => [o.id, o] as const))
  const cellPanels = Object.keys(memo.cells).map((id) => byId.get(id) as PanelObject)
  const fixedPanels = (memo.fixedIds ?? [])
    .map((id) => byId.get(id))
    .filter((o): o is PanelObject => o?.type === 'panel')
  // 记着的网格里的图现在都得还能排；排不了（被旋转 / 换成位图了）= 这份记忆不作数
  const free = unlocked(cellPanels)
  if (!cellPanels.every((p) => resizable(p) && free.has(p.id))) {
    useQuickLayoutStore.setState({ preview: null, drag: null })
    return 'stale'
  }
  return runPlan({
    involved: [...cellPanels, ...fixedPanels],
    target: memo.target,
    gapMm: memo.gapMm,
    columns: memo.cols,
    canvas: memo.canvas,
    fixSpec: false,
    memoFixSpec: memo.fixSpec,
    ratios,
    label: 'quickLayoutRatios',
    checkpoint: false,
    announce: false,
    observer,
  })
}

interface PlanRun {
  involved: PanelObject[]
  target: QuickLayoutTarget
  gapMm: number
  columns: number | null
  canvas: QuickLayoutCanvasMode
  fixSpec: boolean
  /** 写进网格记忆的 `fixSpec`（拖分隔线重排请求里是 false，记忆里仍是那一次排版的选择） */
  memoFixSpec: boolean
  ratios?: { cols: number[]; rows: number[]; rows_mm?: number[] }
  label: 'quickLayout' | 'quickLayoutRatios'
  /** 排版时间线的自动检查点（拖分隔线不打：一次拖动一次撤销就够了） */
  checkpoint: boolean
  /** 成功后说「已排好 · 撤销」（拖分隔线静默） */
  announce: boolean
  /** 拖分隔线的跟手层：commit 之后交最终的框 */
  observer?: QuickLayoutCommitObserver
  /** 出发时的文档 / 画布基线（没给 = 进 runPlan 这一刻取） */
  ctx?: OpContext
}

/** 发请求、回来核对、一次 commit：一键排版与拖分隔线重排共用这一处 */
async function runPlan(run: PlanRun): Promise<QuickLayoutOutcome> {
  const { involved, target, gapMm, columns, canvas: canvasMode, fixSpec } = run
  const start = useDocumentStore.getState()
  const docAtStart = start.doc
  const ctx = run.ctx ?? contextNow()
  if (!sameContext(ctx)) return 'stale'

  const free = unlocked(involved)
  const solvable = involved.filter((p) => free.has(p.id) && resizable(p))
  if (solvable.length < 2) {
    say(note('quickLayoutTooFew'), 'info')
    return 'too_few'
  }
  if (engineTransport()) {
    // 内嵌画布 / playground 里没有后端事务：不发请求，图一个字没动
    say(note('quickLayoutFailed'), 'info')
    return 'failed'
  }
  const solvableIds = new Set(solvable.map((p) => p.id))
  const fixed = involved.filter((p) => !solvableIds.has(p.id))

  // B0：整个对象（含覆盖与摆放）。回来按它比
  const b0 = new Map(involved.map((p) => [p.id, same(p)]))
  const pageAtStart = same(docAtStart.page)
  const specAtStart = same(currentSpec())
  const profile = currentSpec()

  const body: QuickLayoutRequest = {
    panels: solvable.map((p) => ({ key: p.id, id: p.fileId, patches: p.overrides, box: fullBoxOf(p) })),
    fixed: fixed.map((p) => ({ key: p.id, box: boxOf(p) })),
    page: { w: docAtStart.page.w, h: docAtStart.page.h, margin: docAtStart.page.margin ?? 0 },
    target,
    gap_mm: gapMm,
    profile,
    label_pt: canvasTextDefaults().sizePt,
    fix_spec: fixSpec,
    columns,
    canvas: canvasMode,
    ...(run.ratios ? { ratios: run.ratios } : {}),
  }

  // 先出卡再落地（拖分隔线调比例不出卡）：这时检查点留到「应用」那一刻取（还没改任何东西，取早了只会多一个没用的检查点）
  const cardMode = run.announce && layoutCardEnabled()
  // 排版时间线的自动检查点（ADR 0101 / 0199 §七）：快照在任何 await 之前取，失败不打扰
  const checkpoint = () => {
    if (run.checkpoint) void takeCheckpoint({ auto: true }).catch(() => undefined)
  }
  if (!cardMode) checkpoint()

  useQuickLayoutStore.setState({ busy: true })
  let res: QuickLayoutResponse | null = null
  try {
    try {
      res = await quickLayoutPlan(body)
    } catch {
      res = null // 不知道：服务端可能已通过、worker 停在候选上，下面一律重放
    }

    const now = useDocumentStore.getState()
    const sameLoad = now.loadSeq === ctx.loadSeq && now.documentId === ctx.documentId
    /** 把共享 worker 按这几张图**此刻**的列表重放（只在同一份载入的文档里做） */
    const replay = (ids: Iterable<string>) => {
      if (!sameLoad) return
      for (const id of ids) {
        const p = now.doc.objects.find((o) => o.id === id)
        if (p?.type === 'panel') requestRender(p, true)
      }
    }
    const sentKeys = new Set(body.panels.map((p) => p.key))

    if (!res) {
      replay(sentKeys)
      say(note('quickLayoutFailed'), 'info')
      return 'failed'
    }
    if (!res.ok) {
      if (res.replay_required) replay(sentKeys)
      if (res.code === 'not_licensed') {
        // 置灰只是画法；这里只悄悄刷新授权状态，不多说一句
        void useLicenseStore.getState().load()
        return 'not_licensed'
      }
      const key =
        res.code === 'too_few'
          ? 'quickLayoutTooFew'
          : res.code === 'page_too_small'
            ? 'quickLayoutPageTooSmall'
            : 'quickLayoutFailed'
      say(note(key), 'info')
      return res.code === 'too_few' ? 'too_few' : 'failed'
    }

    // 返回里出现没发过的图 = 方案不合规，整份作废（不做部分应用）
    if (res.panels.some((p) => !sentKeys.has(p.key)) || res.labels.some((l) => !sentKeys.has(l.key))) {
      replay(sentKeys)
      say(note('quickLayoutFailed'), 'info')
      return 'failed'
    }

    // ---- 回来之后：这次用到的每一样输入都还是出发时那一份，才写 ----
    const isMoved = (): boolean => {
      const cur = useDocumentStore.getState()
      return (
        cur.loadSeq !== ctx.loadSeq ||
        cur.documentId !== ctx.documentId ||
        cur.activeCanvasId !== ctx.canvasId ||
        // 别的手势的事务开着：commit 会把整个排版并进那条事务（取消拖动就连排版一起丢），这次结果作废
        cur.txn != null ||
        same(cur.doc.page) !== pageAtStart ||
        same(currentSpec()) !== specAtStart ||
        [...b0].some(([id, before]) => {
          const o = cur.doc.objects.find((x) => x.id === id)
          return !o || same(o) !== before
        })
      )
    }
    if (isMoved()) {
      replay(sentKeys)
      say(note('quickLayoutStale'), 'info')
      return 'stale'
    }

    // 页面上落的是可见框（被裁剪的图 = 整图框按 crop 取一块）；网格记忆 / 跟手层也都按页面上的框记
    const cropOf = new Map(involved.map((p) => [p.id, p.crop] as const))
    const shown = (key: string, b: Box): Box => visibleBox(cropOf.get(key), [...b] as Box)
    const memo = memoOf(res, sentKeys, run, canvasMode, fixed, shown)
    const okRes = res
    /** 写文档（一次 commit）+ 重渲染 + 胶囊。直接落地与「应用」共用这一处 */
    const land = (extra?: string): QuickLayoutOutcome => {
      commitPlan(okRes, run.label, memo)
      run.observer?.onCommitted(Object.fromEntries(okRes.panels.map((p) => [p.key, shown(p.key, p.box)])))
      // 重渲染：返回的每张图；后端说热态不干净时，其余发出去的也按全量列表重放一次
      const rendered = new Set(okRes.panels.map((p) => p.key))
      const after = useDocumentStore.getState()
      for (const id of okRes.replay_required ? sentKeys : rendered) {
        const p = after.doc.objects.find((o) => o.id === id)
        if (p?.type === 'panel') requestRender(p, true)
      }
      return finishApplied(okRes, run, profile.label, extra)
    }

    if (cardMode) {
      // 后端说通过时 worker 停在候选上：落地被推迟了，先按此刻的列表把它们放回（用户还没点「应用」）
      replay(sentKeys)
      const shownPanels = new Map(okRes.panels.map((p) => [p.key, shown(p.key, p.box)]))
      const carded = await presentLayoutCard({
        res: okRes,
        memo,
        shownPanels,
        involvedCount: okRes.panels.length,
        ctx,
        apply: () => {
          if (isMoved()) {
            replay(sentKeys)
            say(note('quickLayoutStale'), 'info')
            return 'stale'
          }
          checkpoint()
          land()
          return 'applied'
        },
        regenerate: () => void runQuickLayout(run.target, run.gapMm),
      })
      if (carded) return 'planned'
      // 预览出不来：不卡住，退回直接落地 + 一句灰字（用户要的是排版，不是预览）
      if (isMoved()) {
        replay(sentKeys)
        say(note('quickLayoutStale'), 'info')
        return 'stale'
      }
      checkpoint()
      return land(translate('status.quickLayoutNoPreview', { ns: 'workspace' }))
    }
    return land()
  } finally {
    useQuickLayoutStore.setState({ busy: false, preview: null, live: null, drag: null })
  }
}

/** 落地之后的收尾：活动信号、「已排好 · 撤销」胶囊（拖分隔线静默）。`extra` 是排在备注前面的一句灰字 */
function finishApplied(
  res: Extract<QuickLayoutResponse, { ok: true }>,
  run: PlanRun,
  specName: string,
  extra?: string,
): QuickLayoutOutcome {
  emitActivity({ kind: run.announce ? 'quick_layout.applied' : 'quick_layout.ratios_applied' })
  if (!run.announce) return 'applied'
  // 「撤销」只在同一文档 / 画布、且栈顶还是这一条历史时撤；之后又改了别的，撤的就是别的了，只收胶囊
  const entry = useDocumentStore.getState().past.at(-1)
  const doneCtx = contextNow()
  say(note('quickLayoutDone'), 'done', {
    subline: noteLine(res, specName, extra),
    dismissMs: DONE_DISMISS_MS,
    action: {
      label: note('quickLayoutUndo'),
      run: () => {
        const cur = useDocumentStore.getState()
        if (sameContext(doneCtx) && entry && cur.past.at(-1) === entry) undoRunner?.()
        else useUiStore.getState().setStatus(null)
      },
    },
  })
  return 'applied'
}

/** 「先出修改方案卡」的开关（`ai.change_plan.enabled`，助手面板探测到的能力里带；没探测到 = 默认开） */
function layoutCardEnabled(): boolean {
  return changePlanEnabled(useAiStore.getState().caps)
}

/** 整版预览最多等多久（前后两份并行；到点放弃预览、直接落地） */
export const PREVIEW_TIMEOUT_MS = 45_000

/** 预览出图的页面：前后两份画布放在同一个页框里（取两者的并集），对比的两张图才对得齐 */
function previewDoc(doc: FigureDocument, w: number, h: number): Pick<FigureDocument, 'page' | 'objects'> {
  return { page: { ...doc.page, w, h }, objects: doc.objects }
}

interface LayoutCard {
  res: Extract<QuickLayoutResponse, { ok: true }>
  memo: QuickLayoutMemo | undefined
  /** 每张参与排版的图排完之后在页面上的可见框（mm） */
  shownPanels: Map<string, Box>
  involvedCount: number
  ctx: OpContext
  apply: () => 'applied' | 'stale' | 'failed'
  regenerate: () => void
}

/**
 * 把一次排版方案做成一张修改方案卡并弹出对比（ADR 0206 §一）。前后两份画布各走一次导出管线的整版出图（状态中立，不登记不提交）。
 * → 卡片已经出现 = true；预览出不来（任何原因）= false，由调用方退回直接落地。
 */
async function presentLayoutCard(c: LayoutCard): Promise<boolean> {
  const { res, ctx } = c
  const now = useDocumentStore.getState()
  if (!sameContext(ctx)) return false
  const before = now.doc
  // 「应用之后」的那份文档：与 `commitPlan` 是同一个改写函数、改在一份拷贝上（不碰真文档）
  const after = structuredClone(before)
  applyPlanToDoc(after, res, c.memo)
  // 真提交里面板的 w / h 是「渲染回来按原生图幅再乘一遍之后正好是 box」的预除值（`commitPlan` 的注释）；拷贝上没有那一步渲染同步，
  // 所以预览直接摆最终的框，不然改了图幅的图会按旧大小画在新位置
  for (const [id, [x, y, w, h]] of c.shownPanels) {
    const o = after.objects.find((x2) => x2.id === id)
    if (o) Object.assign(o, { x, y, w, h })
  }
  const pageW = Math.max(before.page.w, after.page.w)
  const pageH = Math.max(before.page.h, after.page.h)
  let beforeSrc = ''
  let afterSrc = ''
  // 出图慢得离谱（导出管线卡住 / 图太重）也不让按钮一直转：到点就放弃预览，调用方直接落地
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), PREVIEW_TIMEOUT_MS)
  try {
    const [b, a] = await Promise.all([
      fetchLayoutPreviewPng(canvasSegmentOf(previewDoc(before, pageW, pageH)), abort.signal),
      fetchLayoutPreviewPng(canvasSegmentOf(previewDoc(after, pageW, pageH)), abort.signal),
    ])
    beforeSrc = URL.createObjectURL(b)
    afterSrc = URL.createObjectURL(a)
  } catch {
    abort.abort() // 另一份还在路上的也一并掐掉
    return false
  } finally {
    clearTimeout(timer)
  }
  // 出图期间文档动过：这次方案作废（调用方按「图改过了」处理，不落地）
  if (!sameContext(ctx)) {
    URL.revokeObjectURL(beforeSrc)
    URL.revokeObjectURL(afterSrc)
    return false
  }
  const frac = (b: Box): [number, number, number, number] => [b[0] / pageW, b[1] / pageH, b[2] / pageW, b[3] / pageH]
  const beforeBoxes = [...c.shownPanels.keys()].flatMap((id) => {
    const o = before.objects.find((x) => x.id === id)
    return o ? [{ gid: id, box: frac([o.x, o.y, o.w, o.h]) }] : []
  })
  const afterBoxes = [...c.shownPanels].map(([id, b]) => ({ gid: id, box: frac(b) }))
  const key = newRequestId()
  const plan: ChangePlan = {
    id: key,
    version: 1,
    source: 'quick_layout',
    base: { target: '', fingerprint: '' },
    base_patches: [],
    candidate_patches: [],
    groups: [
      {
        key: 'layout',
        label: translate('changePlan.layoutGroup', { ns: 'ai' }),
        atomic: true,
        changes: [],
        boxes: { before: beforeBoxes, after: afterBoxes },
      },
    ],
    unhandled: [],
    preview: { before: beforeSrc, after: afterSrc },
  }
  const entry: PlanEntry = {
    key,
    from: 'quick_layout',
    kind: 'layout',
    pj: currentProjectId(),
    panelId: '',
    fileId: '',
    source: { fileId: '', script: null },
    documentId: ctx.documentId,
    loadSeq: ctx.loadSeq,
    canvasId: ctx.canvasId,
    createdAt: Date.now(),
    status: 'ready',
    plan,
    beforeSrc,
    afterSrc,
    unchecked: {},
    request: {},
    comments: [],
    commentCount: 0,
    drafts: {},
    layout: { apply: c.apply, regenerate: c.regenerate, count: c.involvedCount },
  }
  const store = useChangePlanStore.getState()
  store.addLayoutEntry(entry)
  // 弹窗住在助手页签里：切过去，方案卡也留在那里（关掉弹窗 = 方案留着，卡片仍待确认）
  useUiStore.getState().setRightTab('assistant')
  store.openDialog(key)
  return true
}

/** 网格记忆：后端回了网格、且格子里的图都是发出去的才记；否则这一轮不留记忆（旧的也清掉） */
function memoOf(
  res: Extract<QuickLayoutResponse, { ok: true }>,
  sentKeys: Set<string>,
  run: PlanRun,
  canvas: QuickLayoutCanvasMode,
  fixed: PanelObject[],
  shown: (key: string, b: Box) => Box,
): QuickLayoutMemo | undefined {
  const g = res.grid
  if (!g) return undefined
  const ids = Object.keys(g.cells)
  if (!ids.length || ids.some((id) => !sentKeys.has(id))) return undefined
  const boxes: QuickLayoutMemo['boxes'] = {}
  for (const p of res.panels) if (g.cells[p.key]) boxes[p.key] = shown(p.key, p.box)
  if (ids.some((id) => !boxes[id])) return undefined
  return {
    rows: g.rows,
    cols: g.cols,
    cells: Object.fromEntries(ids.map((id) => [id, [g.cells[id][0], g.cells[id][1]] as [number, number]])),
    colRatios: [...g.col_ratios],
    rowRatios: [...g.row_ratios],
    boxes,
    target: run.target,
    gapMm: run.gapMm,
    canvas,
    fixSpec: run.memoFixSpec,
    ...(fixed.length ? { fixedIds: fixed.map((p) => p.id) } : {}),
  }
}

/**
 * 一次 commit：每张返回的图换上最终的全量覆盖并落到新摆放，再追加标签（顺序 = 返回的 labels 顺序，(a)(b)…）。
 *
 * 图幅变了时，渲染回来 `useEngineSync` 会按「新图幅 / 旧原生图幅」把页面尺寸再乘一遍（缩放比不变，
 * `syncPanelNativeSize`）。所以这里先把 w / h 摆成「乘完正好是 box」的值——与 `setFigureFrameScaled` 同一个办法；
 * 求解器给的 box 与图幅等大（缩放 = 1），渲染一回来页面上的框就是 box。
 */
function commitPlan(
  res: Extract<QuickLayoutResponse, { ok: true }>,
  label: 'quickLayout' | 'quickLayoutRatios',
  memo: QuickLayoutMemo | undefined,
): void {
  useDocumentStore.getState().commit(hist(label), (d) => applyPlanToDoc(d, res, memo))
}

/** 把排版方案写进一份文档草稿（真提交与「应用之后」的预览拷贝共用这一个改写，不会各算各的） */
function applyPlanToDoc(
  d: FigureDocument,
  res: Extract<QuickLayoutResponse, { ok: true }>,
  memo: QuickLayoutMemo | undefined,
): void {
  // 旧的子图标签（「(a)」这类，与样式系统同一判据 `isSubLabel`）落在**这次真拿到新标签的那几张图**
  // 原来的范围里（被裁剪的图按整图框，求解器的标签相对整图摆；外扩 5 mm）的，换成这次算好位置的新标签：重复点不叠标签，手加的 (a)(b) 也跟着落到对的地方。
  // 固定的图（位图 / 旋转…）后端不回标签，它的旧标签原样留着
  const labelled = new Set(res.labels.map((l) => l.key))
  const before = d.objects
    .filter((o): o is PanelObject => o.type === 'panel' && labelled.has(o.id))
    .map(fullBoxOf)
  const near = (x: number, y: number) =>
    before.some(([bx, by, bw, bh]) => x >= bx - 5 && x <= bx + bw + 5 && y >= by - 5 && y <= by + bh + 5)
  d.objects = d.objects.filter((o) => !(o.type === 'text' && isSubLabel(o) && near(o.x, o.y)))
  // 参与排版的图离开它们原来的布局组：否则尺寸一变，自动重排（`startLayoutAutoReflow`）120 ms 后
  // 会按旧组的排布把方案盖掉。与「取消成组」同写法：成员少于 2 个的布局组连同约束一起移除；同一次 commit，⌘Z 一并回去
  const moving = new Set(res.panels.map((p) => p.key))
  const gids = new Set<string>()
  for (const o of d.objects) {
    if (moving.has(o.id) && o.groupId && d.layoutGroups?.some((g) => g.id === o.groupId)) {
      gids.add(o.groupId)
      o.groupId = undefined
    }
  }
  if (gids.size && d.layoutGroups) {
    const dead = new Set(
      d.layoutGroups.filter((g) => gids.has(g.id) && d.objects.filter((o) => o.groupId === g.id).length < 2).map((g) => g.id),
    )
    for (const o of d.objects) if (o.groupId && dead.has(o.groupId)) o.groupId = undefined
    d.layoutGroups = d.layoutGroups.filter((g) => !dead.has(g.id))
    if (!d.layoutGroups.length) delete d.layoutGroups
  }
  for (const p of res.panels) {
    const obj = d.objects.find((o) => o.id === p.key)
    if (obj?.type !== 'panel') continue
    const next: PanelOverride[] = p.patches.map((x) => ({ ...x }))
    // 这次改过 / 删掉的那几条从「样式写的」登记里注销（ADR 0081 §十三）：与手改一致，脚本重跑不让位
    const eff = (list: PanelOverride[], x: PanelOverride) => effectiveOverride(list, x.gid, x.prop)
    const written = [...obj.overrides, ...next].filter((x) => {
      const a = eff(obj.overrides, x)
      const b = eff(next, x)
      return !a || !b || JSON.stringify(a.value) !== JSON.stringify(b.value)
    })
    obj.overrides = next
    releaseOwned(d, obj, written)
    const [x, y, w, h] = visibleBox(obj.crop, [...p.box] as Box)
    const fullW = obj.crop ? w / obj.crop.w : w
    const fullH = obj.crop ? h / obj.crop.h : h
    const size = effectiveOverride(next, 'figure', 'size_mm')?.value
    const [sw, sh] = Array.isArray(size) ? (size as unknown[]) : []
    obj.x = x
    obj.y = y
    if (obj.nativeW > 0 && obj.nativeH > 0 && typeof sw === 'number' && typeof sh === 'number' && sw > 0 && sh > 0) {
      // 整图在页面上按 nativeW / sw 缩放，可见框 = 它乘 crop 比例（crop 不动，渲染回来的 native 同步不改比例）
      obj.w = ((fullW * obj.nativeW) / sw) * (obj.crop?.w ?? 1)
      obj.h = ((fullH * obj.nativeH) / sh) * (obj.crop?.h ?? 1)
    } else {
      obj.w = w
      obj.h = h
    }
  }
  res.labels.forEach((l, i) => {
    d.objects.push(makeSubLabel(i, l.x, l.y))
  })
  // 页面跟着内容收紧（同一条历史，⌘Z 一次撤回）
  // 注意：这里**不**置 `page.userSized`——那是「用户自己定过画布大小」的记号，排版自己写的不算
  // 没参与排版的对象（只排选中一部分时的别的图 / 标注 / 形状，固定的图）原地不动：页面至少要包得住它们，
  // 否则导出按页面裁掉原来看得见的内容（页边口径与请求里发的 margin 一致）
  const margin = d.page.margin ?? 0
  let needW = res.page.w
  let needH = res.page.h
  for (const o of d.objects) {
    if (moving.has(o.id) || o.hidden) continue
    needW = Math.max(needW, o.x + o.w + margin)
    needH = Math.max(needH, o.y + o.h + margin)
  }
  d.page.w = needW
  d.page.h = needH
  // 网格记忆与排版同一次提交（⌘Z 一起回去）；这一轮没有网格 = 旧的也不再作数
  if (memo) d.quickLayout = memo
  else delete d.quickLayout
}
