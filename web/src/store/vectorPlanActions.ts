/**
 * 改图助手改画布矢量对象的**落地**（ADR 0212 第 6 期，`kind: 'vector'` 的修改方案卡）。
 * 方案怎么算全在后端（`/api/vector-edit/plan`：模型出补丁 → 同一套补丁引擎重算 → 只核被动到的对象，**只算不写、不读盘**）；
 * 这里只做五件事，结构照 `quickLayoutActions`（整版卡）：
 *
 * * **发请求**：把画布做成内存快照（`lib/vectorEdit.snapshotOf`，只有矢量对象与子图外框）连同用户的话发出去，轮询到方案；
 * * **出卡**：前后两份整版预览（状态中立的 `layout-preview`，不登记不提交）+ 复述清单；预览出不来 = 失败卡 + 重试（用户没看过就不让应用）；
 * * **应用 = 一次 commit**（⌘Z 一步撤回）：落位交给 `planAgentMerge`（与「并进 Agent 的改动」同一份规则），应用那一刻再核一遍基线；
 * * **基线**：方案点名的对象与出发时不同 = 「图改过了」，不写；
 * * **每个 await 之后**核项目代际与文档 / 画布：对不上就丢掉，A 的方案绝不落进 B。
 */
import { fetchLayoutPreviewPng, createVectorEditPlan, getVectorEditPlan, EngineError, type VectorEditResult, type ChangePlan } from '@/lib/api'
import { canvasSegmentOf } from '@/lib/exportRequest'
import { changePlanEnabled } from '@/lib/changePlanSwitch'
import { engineTransport } from '@/lib/engineTransport'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId } from '@/lib/session'
import { baselineHolds, hasEdits, landedHolds, landVector, sameJson, snapshotOf, touchedCount, vectorSelectionOf, type VectorSnapshot } from '@/lib/vectorEdit'
import { msg, t as translate } from '@/i18n'
import { currentRunSelection, newRequestId, useAiStore } from './aiStore'
import { POLL_TIMEOUT, useChangePlanStore, type PlanEntry, type VectorHandle } from './changePlanStore'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'

const POLL_MS = 1200
const POLL_MAX_MS = 4 * 60 * 1000
const POLL_ERRORS_MAX = 3
/** 整版预览最多等多久（前后两份并行） */
export const VECTOR_PREVIEW_TIMEOUT_MS = 45_000

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

interface Ctx {
  documentId: string
  loadSeq: number
  canvasId: string
}

const contextNow = (): Ctx => {
  const s = useDocumentStore.getState()
  return { documentId: s.documentId, loadSeq: s.loadSeq, canvasId: s.activeCanvasId }
}
const sameContext = (c: Ctx): boolean => {
  const n = contextNow()
  return n.documentId === c.documentId && n.loadSeq === c.loadSeq && n.canvasId === c.canvasId
}

/** 这张画布上已经有一份矢量编辑方案在生成（后端对同一项目也是一次一份） */
export function vectorPlanBusy(): boolean {
  const c = contextNow()
  const s = useChangePlanStore.getState()
  return s.order.some((k) => {
    const e = s.entries[k]
    return !!e && e.kind === 'vector' && e.status === 'generating' && e.documentId === c.documentId && e.loadSeq === c.loadSeq && e.canvasId === c.canvasId
  })
}

/**
 * 这条指令能不能走矢量编辑：开关开着、不在内嵌画布 / playground（没有后端事务）、选区里有矢量对象、这张画布上没有别的在生成。
 * 判据的另一半（没有目标图时才走这条路）在 `AiPanel`：有目标图的指令照旧先出图内修改方案卡 / 改脚本。
 */
export function canRunVectorEdit(selection: readonly string[]): boolean {
  if (engineTransport() || !changePlanEnabled(useAiStore.getState().caps) || vectorPlanBusy()) return false
  return vectorSelectionOf(useDocumentStore.getState().doc.objects, selection).length > 0
}

/**
 * 「让助手页签留着」的一次性意图（`hooks/useSelectionRouting.holdAssistantTabForNextSelection`）：那个模块反过来 import 一堆 store，
 * 静态 import 会成环——由助手面板（`AiPanel`）在模块加载时交进来，没交（单测）= 什么都不做。
 */
let holdAssistantTab: (() => void) | null = null
export function setVectorAssistantHold(fn: () => void): void {
  holdAssistantTab = fn
}

const hist = (count: number) => msg('history.applyChangePlan', { count }, 'workspace')

/**
 * 发起一次矢量编辑方案。→ 方案卡的键；没发（没有选区矢量对象 / 已有一份在生成）= null。
 * 失败（没连 AI、请求被拒…）也留一张失败卡，带稳定码与可走的下一步，不报错卡住。
 */
export function runVectorEdit(instruction: string, selection: readonly string[]): string | null {
  const store = useChangePlanStore.getState
  const doc = useDocumentStore.getState()
  const sel = vectorSelectionOf(doc.doc.objects, selection)
  if (!sel.length || vectorPlanBusy()) return null
  const ctx = contextNow()
  const snap = snapshotOf(doc.doc, ctx.canvasId)
  const guard = captureProjectEpoch()
  const key = newRequestId()
  const text = instruction.trim()

  const entry: PlanEntry = {
    key,
    from: 'assistant',
    kind: 'vector',
    pj: currentProjectId(),
    panelId: '',
    fileId: '',
    source: { fileId: '', script: null },
    documentId: ctx.documentId,
    loadSeq: ctx.loadSeq,
    canvasId: ctx.canvasId,
    createdAt: Date.now(),
    status: 'generating',
    instruction: text,
    unchecked: {},
    request: { instruction: text },
    comments: [],
    commentCount: 0,
    drafts: {},
    vector: {
      count: 0,
      apply: () => 'failed',
      regenerate: () => {
        // 重来按此刻的快照与选区（不是出发时的）：之前选的对象还在就还选它们，不在了就按此刻选中的
        const now = useDocumentStore.getState().doc.objects
        const keep = vectorSelectionOf(now, sel)
        void runVectorEdit(text, keep.length ? keep : useSelectionStore.getState().ids)
      },
      holds: () => true,
      landed: () => false,
    },
  }
  store().addLayoutEntry(entry)
  void drive(key, ctx, snap, sel, text, guard)
  return key
}

/** 发请求、轮询到方案、出卡（后台跑，卡片已经在对话里「生成中」了） */
async function drive(key: string, ctx: Ctx, snap: VectorSnapshot, sel: string[], text: string, guard: ReturnType<typeof captureProjectEpoch>): Promise<void> {
  const store = useChangePlanStore.getState
  const alive = () => guard.still() && sameContext(ctx) && store().entries[key]?.status === 'generating'
  const fail = (code: string, params?: Record<string, unknown>) => {
    if (store().entries[key]) store().patchEntry(key, { status: 'failed', failure: { code, params } })
  }

  let planId: string
  try {
    const task = await createVectorEditPlan({
      canvas: snap.canvas as never,
      instruction: text,
      selection: sel,
      ...currentRunSelection(),
      client_request_id: key,
    })
    planId = task.plan_id
  } catch (err) {
    if (guard.still() && store().entries[key]) fail(err instanceof EngineError && err.code ? err.code : 'change_plan_failed', err instanceof EngineError ? err.params : undefined)
    return
  }

  const t0 = Date.now()
  let errors = 0
  while (alive()) {
    await sleep(POLL_MS)
    if (!alive()) break
    if (Date.now() - t0 > POLL_MAX_MS) {
      fail(POLL_TIMEOUT)
      return
    }
    let view
    try {
      view = await getVectorEditPlan(planId)
    } catch (err) {
      // 偶发的网络错误不立刻判死：连着几次才算
      if (++errors >= POLL_ERRORS_MAX && alive()) fail(err instanceof EngineError && err.code ? err.code : 'change_plan_failed', err instanceof EngineError ? err.params : undefined)
      continue
    }
    errors = 0
    if (!alive()) break
    if (view.state === 'failed') {
      fail(view.code)
      return
    }
    if (view.state === 'ready') {
      await present(key, ctx, snap, view.plan, guard.still)
      return
    }
  }
  // 途中换了文档 / 画布 / 项目：这份方案属于别处，丢掉（项目换了整表已清）
  if (guard.still() && store().entries[key]?.status === 'generating') store().dismiss(key)
}

/** 方案回来了：核对 → 出两张预览 → 卡变成待确认（或失败卡 / 空方案卡） */
async function present(key: string, ctx: Ctx, snap: VectorSnapshot, result: VectorEditResult, still: () => boolean): Promise<void> {
  const store = useChangePlanStore.getState
  const live = () => still() && sameContext(ctx) && store().entries[key]?.status === 'generating'
  const patch = store().patchEntry
  const fail = (code: string) => patch(key, { status: 'failed', failure: { code } })
  const prev = store().entries[key]?.vector
  if (!prev) return
  const handle = (extra: Partial<VectorHandle>): VectorHandle => ({ ...prev, result, ...extra })

  // 没有任何改动：补丁里的操作全被丢了（几何内核没装好 / 点名的对象不存在……），或模型说做不到
  if (!hasEdits(result)) {
    patch(key, { vector: handle({}) })
    // 只因几何内核没装好而一条都做不了：一句人话（其余编辑照常，只是这一步这台电脑做不了）；别的原因列在失败卡下面
    const un = result.unhandled ?? []
    const kernelOnly = (result.errors ?? []).some((e) => e.code === 'kernel_unavailable') || (un.length > 0 && un.every((u) => u.code === 'kernel_unavailable'))
    fail(kernelOnly ? 'vector_kernel_unavailable' : 'change_plan_empty')
    return
  }
  const doc = useDocumentStore.getState().doc
  const merged = landVector(snap, result, doc)
  const count = touchedCount(result)
  // 生成期间点名的对象被改过：不出图，卡片按「图改过了」走（`holds()` 为假，派生状态是 stale）
  const make = (extra: Partial<VectorHandle> = {}): VectorHandle => {
    const h: VectorHandle = handle({
      count,
      holds: () => sameContext(ctx) && baselineHolds(snap, result, useDocumentStore.getState().doc.objects),
      regenerate: prev.regenerate,
      landed: () => sameContext(ctx) && landedHolds(snap, result, useDocumentStore.getState().doc.objects),
      apply: () => applyVector(snap, result, ctx, count),
      ...extra,
    })
    return h
  }
  if (!merged) {
    patch(key, { status: 'ready', plan: synthPlan(key, result, '', ''), vector: make() })
    return
  }

  const pageW = doc.page.w
  const pageH = doc.page.h
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), VECTOR_PREVIEW_TIMEOUT_MS)
  let beforeSrc = ''
  let afterSrc = ''
  try {
    const [b, a] = await Promise.all([
      fetchLayoutPreviewPng(canvasSegmentOf({ page: doc.page, objects: doc.objects }), abort.signal),
      fetchLayoutPreviewPng(canvasSegmentOf({ page: { ...doc.page, w: pageW, h: pageH }, objects: merged.objects }), abort.signal),
    ])
    beforeSrc = URL.createObjectURL(b)
    afterSrc = URL.createObjectURL(a)
  } catch {
    abort.abort()
    if (live()) fail('change_plan_render_failed')
    return
  } finally {
    clearTimeout(timer)
  }
  if (!live()) {
    URL.revokeObjectURL(beforeSrc)
    URL.revokeObjectURL(afterSrc)
    return
  }
  patch(key, { status: 'ready', plan: synthPlan(key, result, beforeSrc, afterSrc), beforeSrc, afterSrc, vector: make() })
}

/** 卡与弹窗吃的方案外形：每条复述一组（整批要或不要，不画勾选）；框都不要（复述与对象没有一一对应） */
function synthPlan(id: string, r: VectorEditResult, before: string, after: string): ChangePlan {
  const echo = (r.echo ?? []).filter((x) => typeof x === 'string' && x.trim())
  const lines = echo.length ? echo : [translate('changePlan.dialog.vectorN', { ns: 'ai', count: touchedCount(r) })]
  return {
    id,
    version: 1,
    source: 'assistant',
    base: { target: '', fingerprint: '' },
    base_patches: [],
    candidate_patches: [],
    groups: lines.map((label, i) => ({ key: `e${i}`, label: String(label), atomic: true, changes: [], boxes: { after: [] } })),
    unhandled: [],
    preview: { before, after },
  }
}

/** 一次 commit 把方案写进文档；每道守卫的理由见各行 */
function applyVector(snap: VectorSnapshot, result: VectorEditResult, ctx: Ctx, count: number): 'applied' | 'stale' | 'failed' {
  if (!sameContext(ctx)) return 'stale'
  const s = useDocumentStore.getState()
  // 手势进行中（拖动等）：commit 会并进那次手势的事务，取消手势就把改动一起撤掉——不写
  if (s.txn) return 'failed'
  const merged = landVector(snap, result, s.doc)
  if (!merged) return 'stale'
  // 删掉的对象会退出选区，选区一变右栏就按选区切到「属性」，卡片上的「已应用」看不见了——这一次让右栏留在助手页签
  // （要在 commit 之前：文档一变选区就被清理，那时再留已经晚了）
  const picked = new Set(useSelectionStore.getState().ids)
  if ((result.deleted ?? []).some((id) => picked.has(id))) holdAssistantTab?.()
  s.commit(hist(count), (d) => {
    d.objects = structuredClone(merged.objects)
    if (merged.layoutGroups) d.layoutGroups = structuredClone(merged.layoutGroups)
    else delete d.layoutGroups
  })
  // 真的进了文档才算应用（只读 / 被接管的窗口里 commit 什么都不做）
  const now = useDocumentStore.getState().doc
  if (!sameJson(now.objects, merged.objects)) return 'failed'
  // 删掉的对象不再留在选区里
  useSelectionStore.getState().prune(new Set(now.objects.map((o) => o.id)))
  return 'applied'
}
