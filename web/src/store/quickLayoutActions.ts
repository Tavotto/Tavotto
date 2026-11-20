/**
 * 一键排版的**落地**（ADR 0199，高级功能 `quick-layout`）。求解与核对都在后端（`/api/pro/quick-layout/plan`，
 * **只算不写**）；这里只做四件事：收集选区、发请求、回来核对「图还是出发时那样」、**一次 commit**。
 *
 * 纪律照 `issueFixActions`（ADR 0080）：
 *
 * * **一次排版一条历史**：所有面板的覆盖、摆放与标签在同一个 `commit` 里落下，⌘Z 一步撤回。
 * * **等待期间文档被改过就丢弃**：发请求前记下每张相关面板的整个对象、页面与规范，回来对不上就不写，
 *   并把共享 worker 按此刻的列表重放（后端说通过时 worker 停在候选上）。
 * * **失败什么都不改**：不弹窗、不报红，一句话说「没排好」；请求抛了 / 形状不对是「不知道」，同样重放。
 * * **只走 documentStore**：dirty、undo、autosave 与手改一模一样；这里没有任何权益判断（置灰只是画法，
 *   权威在后端，ADR 0199 §九）。
 * * 标签对象与免费的「添加序号标签」同一个构造（`actions.makeSubLabel`）。
 */
import { create } from 'zustand'
import { emitActivity } from '@/lib/activity'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { releaseOwned } from '@/lib/styleOwned'
import { literal, msg, t as translate, type UiMessage } from '@/i18n'
import { quickLayoutPlan, type QuickLayoutRequest, type QuickLayoutResponse } from '@/lib/api'
import { ArtifactValidationError, refuseArtifactOperation } from '@/lib/artifactValidation'
import { engineTransport } from '@/lib/engineTransport'
import { takeCheckpoint } from '@/lib/timelineCheckpoint'
import { isSubLabel } from '@/lib/stylePresets'
import { canvasTextDefaults } from '@/lib/typography'
import type { FigureDocument, PanelObject, PanelOverride } from '@/types/document'
import { panelRotation } from '@/types/document'
import { makeSubLabel } from './actions'
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
}>(() => ({
  busy: false,
  preview: null,
  live: null,
}))

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

/** 成功胶囊多留一会儿：那一行灰字要读得完（带动作的胶囊本来不自己走） */
const DONE_DISMISS_MS = 10_000

const same = (v: unknown): string => JSON.stringify(v ?? null)

type Box = [number, number, number, number]
const boxOf = (p: PanelObject): Box => [p.x, p.y, p.w, p.h]

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

function noteLine(res: Extract<QuickLayoutResponse, { ok: true }>, specName: string): UiMessage | undefined {
  const seen = new Set<string>()
  const lines: string[] = []
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
async function settleNewFigures(): Promise<void> {
  const ready = (id: string) => {
    const cur = useDocumentStore.getState().doc.objects.find((o) => o.id === id)
    return !cur || cur.type !== 'panel' || !!exactPanelManifest(useRenderStore.getState(), cur)
  }
  const pending = involvedPanels(useDocumentStore.getState().doc).filter((p) => resizable(p) && !ready(p.id))
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
  alignNewFigures()
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
  useQuickLayoutStore.setState({ busy: true })
  try {
    await settleNewFigures()
  } finally {
    useQuickLayoutStore.setState({ busy: false })
  }

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
    useQuickLayoutStore.setState({ preview: null })
    return 'stale'
  }
  const byId = new Map(doc.objects.map((o) => [o.id, o] as const))
  const cellPanels = Object.keys(memo.cells).map((id) => byId.get(id) as PanelObject)
  const fixedPanels = (memo.fixedIds ?? [])
    .map((id) => byId.get(id))
    .filter((o): o is PanelObject => o?.type === 'panel')
  // 记着的网格里的图现在都得还能排；排不了（被旋转 / 换成位图了）= 这份记忆不作数
  if (!cellPanels.every(resizable)) {
    useQuickLayoutStore.setState({ preview: null })
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
}

/** 发请求、回来核对、一次 commit：一键排版与拖分隔线重排共用这一处 */
async function runPlan(run: PlanRun): Promise<QuickLayoutOutcome> {
  const { involved, target, gapMm, columns, canvas: canvasMode, fixSpec } = run
  const start = useDocumentStore.getState()
  const docAtStart = start.doc
  const loadSeq = start.loadSeq
  const canvasId = start.activeCanvasId

  const solvable = involved.filter((p) => resizable(p))
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
    panels: solvable.map((p) => ({ key: p.id, id: p.fileId, patches: p.overrides, box: boxOf(p) })),
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

  // 排版时间线的自动检查点（ADR 0101 / 0199 §七）：快照在任何 await 之前取，失败不打扰
  if (run.checkpoint) void takeCheckpoint({ auto: true }).catch(() => undefined)

  useQuickLayoutStore.setState({ busy: true })
  let res: QuickLayoutResponse | null = null
  try {
    try {
      res = await quickLayoutPlan(body)
    } catch {
      res = null // 不知道：服务端可能已通过、worker 停在候选上，下面一律重放
    }

    const now = useDocumentStore.getState()
    const sameLoad = now.loadSeq === loadSeq
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
    const moved =
      !sameLoad ||
      now.activeCanvasId !== canvasId ||
      same(now.doc.page) !== pageAtStart ||
      same(currentSpec()) !== specAtStart ||
      [...b0].some(([id, before]) => {
        const o = now.doc.objects.find((x) => x.id === id)
        return !o || same(o) !== before
      })
    if (moved) {
      replay(sentKeys)
      say(note('quickLayoutStale'), 'info')
      return 'stale'
    }

    const memo = memoOf(res, sentKeys, run, canvasMode, fixed)
    commitPlan(res, involved.map(boxOf), run.label, memo)
    run.observer?.onCommitted(Object.fromEntries(res.panels.map((p) => [p.key, [...p.box] as Box])))
    // 重渲染：返回的每张图；后端说热态不干净时，其余发出去的也按全量列表重放一次
    const rendered = new Set(res.panels.map((p) => p.key))
    const after = useDocumentStore.getState()
    for (const id of res.replay_required ? sentKeys : rendered) {
      const p = after.doc.objects.find((o) => o.id === id)
      if (p?.type === 'panel') requestRender(p, true)
    }

    emitActivity({ kind: run.announce ? 'quick_layout.applied' : 'quick_layout.ratios_applied' })
    if (!run.announce) return 'applied'
    // 「撤销」只在撤销栈还停在这一步时撤；之后又改了别的，撤的就是别的了，只收胶囊
    const depth = after.past.length
    say(note('quickLayoutDone'), 'done', {
      subline: noteLine(res, profile.label),
      dismissMs: DONE_DISMISS_MS,
      action: {
        label: note('quickLayoutUndo'),
        run: () => {
          if (useDocumentStore.getState().past.length === depth) undoRunner?.()
          else useUiStore.getState().setStatus(null)
        },
      },
    })
    return 'applied'
  } finally {
    useQuickLayoutStore.setState({ busy: false, preview: null, live: null })
  }
}

/** 网格记忆：后端回了网格、且格子里的图都是发出去的才记；否则这一轮不留记忆（旧的也清掉） */
function memoOf(
  res: Extract<QuickLayoutResponse, { ok: true }>,
  sentKeys: Set<string>,
  run: PlanRun,
  canvas: QuickLayoutCanvasMode,
  fixed: PanelObject[],
): QuickLayoutMemo | undefined {
  const g = res.grid
  if (!g) return undefined
  const ids = Object.keys(g.cells)
  if (!ids.length || ids.some((id) => !sentKeys.has(id))) return undefined
  const boxes: QuickLayoutMemo['boxes'] = {}
  for (const p of res.panels) if (g.cells[p.key]) boxes[p.key] = [...p.box]
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
  before: Box[],
  label: 'quickLayout' | 'quickLayoutRatios',
  memo: QuickLayoutMemo | undefined,
): void {
  useDocumentStore.getState().commit(hist(label), (d) => {
    // 旧的子图标签（「(a)」这类，与样式系统同一判据 `isSubLabel`）落在参与排版的图原来的范围里（外扩 5 mm）的，
    // 换成这次算好位置的新标签：重复点不叠标签，手加的 (a)(b) 也跟着落到对的地方
    const near = (x: number, y: number) =>
      before.some(([bx, by, bw, bh]) => x >= bx - 5 && x <= bx + bw + 5 && y >= by - 5 && y <= by + bh + 5)
    d.objects = d.objects.filter((o) => !(o.type === 'text' && isSubLabel(o) && near(o.x, o.y)))
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
      const [x, y, w, h] = p.box
      const size = effectiveOverride(next, 'figure', 'size_mm')?.value
      const [sw, sh] = Array.isArray(size) ? (size as unknown[]) : []
      obj.x = x
      obj.y = y
      if (obj.nativeW > 0 && obj.nativeH > 0 && typeof sw === 'number' && typeof sh === 'number' && sw > 0 && sh > 0) {
        obj.w = (w * obj.nativeW) / sw
        obj.h = (h * obj.nativeH) / sh
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
    d.page.w = res.page.w
    d.page.h = res.page.h
    // 网格记忆与排版同一次提交（⌘Z 一起回去）；这一轮没有网格 = 旧的也不再作数
    if (memo) d.quickLayout = memo
    else delete d.quickLayout
  })
}
