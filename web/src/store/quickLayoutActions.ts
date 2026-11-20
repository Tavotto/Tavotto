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
import { effectiveOverride } from '@/lib/effectiveOverride'
import { releaseOwned } from '@/lib/styleOwned'
import { literal, msg, t as translate, type UiMessage } from '@/i18n'
import { quickLayoutPlan, type QuickLayoutRequest, type QuickLayoutResponse } from '@/lib/api'
import { ArtifactValidationError, refuseArtifactOperation } from '@/lib/artifactValidation'
import { engineTransport } from '@/lib/engineTransport'
import { takeCheckpoint } from '@/lib/timelineCheckpoint'
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
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore, type StatusAction } from './uiStore'
import type { QuickLayoutTarget } from '@/lib/quickLayoutPrefs'

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

/** 按钮的忙碌态（请求期间）。运行时状态，不进文档；一次只跑一轮 */
export const useQuickLayoutStore = create<{ busy: boolean }>(() => ({ busy: false }))

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
 * 这张图能不能参与求解（改图幅 / 轴位置）：有精确 manifest 的引擎出图，没旋转 / 翻转，不是位图，不是 native，
 * 不是 artifact / 来源驱动的图。其余的只当固定盒子。判据只在这里，后端对 native / 重复图另有一道。
 */
function resizable(p: PanelObject, state: ReturnType<typeof useRenderStore.getState>): boolean {
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
  return !!exactPanelManifest(state, p)
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

function noteLine(res: Extract<QuickLayoutResponse, { ok: true }>): UiMessage | undefined {
  const seen = new Set<string>()
  const lines: string[] = []
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

/**
 * 点「一键排版」。`target` / `gapMm` 来自工具条的小下拉（或命令面板读同一份偏好）。
 * 回的结局给用例与调用方看；用户看到的只有一枚胶囊。
 */
export async function runQuickLayout(target: QuickLayoutTarget, gapMm: number): Promise<QuickLayoutOutcome> {
  if (useQuickLayoutStore.getState().busy) return 'busy'
  // 离散动作：先把还开着的手势收干净（与 setFigureFrame 同）
  finishActiveGesture()

  const start = useDocumentStore.getState()
  const docAtStart = start.doc
  const loadSeq = start.loadSeq
  const canvasId = start.activeCanvasId
  const renderState = useRenderStore.getState()

  const involved = involvedPanels(docAtStart)
  const solvable = involved.filter((p) => resizable(p, renderState))
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
  }

  // 排版时间线的自动检查点（ADR 0101 / 0199 §七）：快照在任何 await 之前取，失败不打扰
  void takeCheckpoint({ auto: true }).catch(() => undefined)

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
      say(note(res.code === 'too_few' ? 'quickLayoutTooFew' : 'quickLayoutFailed'), 'info')
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

    commitPlan(res)
    // 重渲染：返回的每张图；后端说热态不干净时，其余发出去的也按全量列表重放一次
    const rendered = new Set(res.panels.map((p) => p.key))
    const after = useDocumentStore.getState()
    for (const id of res.replay_required ? sentKeys : rendered) {
      const p = after.doc.objects.find((o) => o.id === id)
      if (p?.type === 'panel') requestRender(p, true)
    }

    // 「撤销」只在撤销栈还停在这一步时撤；之后又改了别的，撤的就是别的了，只收胶囊
    const depth = after.past.length
    say(note('quickLayoutDone'), 'done', {
      subline: noteLine(res),
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
    useQuickLayoutStore.setState({ busy: false })
  }
}

/**
 * 一次 commit：每张返回的图换上最终的全量覆盖并落到新摆放，再追加标签（顺序 = 返回的 labels 顺序，(a)(b)…）。
 *
 * 图幅变了时，渲染回来 `useEngineSync` 会按「新图幅 / 旧原生图幅」把页面尺寸再乘一遍（缩放比不变，
 * `syncPanelNativeSize`）。所以这里先把 w / h 摆成「乘完正好是 box」的值——与 `setFigureFrameScaled` 同一个办法；
 * 求解器给的 box 与图幅等大（缩放 = 1），渲染一回来页面上的框就是 box。
 */
function commitPlan(res: Extract<QuickLayoutResponse, { ok: true }>): void {
  useDocumentStore.getState().commit(hist('quickLayout'), (d) => {
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
  })
}
