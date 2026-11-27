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
import { fetchLayoutPreviewPng, createVectorEditPlan, getVectorEditPlan, EngineError, planVectorEditOps, type VectorEditResult, type ChangePlan } from '@/lib/api'
import { canvasSegmentOf } from '@/lib/exportRequest'
import { changePlanEnabled } from '@/lib/changePlanSwitch'
import { engineTransport } from '@/lib/engineTransport'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId } from '@/lib/session'
import { changeGroups, type ChangeGroup } from '@/lib/vectorChanges'
import { cropToFrame, previewFrame, baselineHolds, hasEdits, landedHolds, landVector, sameJson, snapshotOf, touchedCount, vectorSelectionOf, type VectorSnapshot } from '@/lib/vectorEdit'
import type { CanvasObject } from '@/types/document'
import { currentLocale, msg, t as translate } from '@/i18n'
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
      locale: currentLocale(),
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
  // 渲染对比图期间用户切走了画布 / 文档：这份方案属于别处，条目收掉（不收就是一张永远「生成中」的卡，一次一份的锁也一直挂着）；项目换了整表已清
  const dropAbandoned = () => {
    if (still() && store().entries[key]?.status === 'generating') store().dismiss(key)
  }
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
  const groups = changeGroups(snap, result)
  const allOps = Array.isArray(result.ops) ? result.ops : []
  // 当前生效的方案（取消勾选后会换成用剩下的步重算出来的）：应用、基线核对、已应用判据都读它，不读最初那一份
  const st = { result, seq: 0, busy: false, failed: false, okKey: '{}' }
  const make = (extra: Partial<VectorHandle> = {}): VectorHandle => ({
    ...prev,
    result,
    groups,
    // 每一步都有对应的改动行才能按步勾选（老后端没有 `op`、或补丁与行对不上 = 整批要或不要）
    ...(groups.length > 0 && groups.every((g) => g.op !== undefined) && allOps.length > 0 ? { ops: allOps } : {}),
    count: touchedCount(result),
    holds: () => sameContext(ctx) && baselineHolds(snap, st.result, useDocumentStore.getState().doc.objects),
    regenerate: prev.regenerate,
    landed: () => sameContext(ctx) && landedHolds(snap, st.result, useDocumentStore.getState().doc.objects),
    apply: () => (st.busy || st.failed || !hasEdits(st.result) ? 'failed' : applyVector(snap, st.result, ctx, touchedCount(st.result))),
    ...extra,
  })
  if (!merged) {
    patch(key, { status: 'ready', plan: synthPlan(key, result, groups, '', ''), vector: make() })
    return
  }

  const pageW = doc.page.w
  const pageH = doc.page.h
  // 取景：对比图与卡片缩略图只画被改动的那一块（加留白），一眼看见改了什么；取消勾选重算后仍用这个框（改前那张不用重画）
  const frame = previewFrame(doc.page, doc.objects, merged.objects, result)
  const segment = (objects: CanvasObject[]) => canvasSegmentOf(cropToFrame({ page: { ...doc.page, w: pageW, h: pageH }, objects }, frame))
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), VECTOR_PREVIEW_TIMEOUT_MS)
  let beforeSrc = ''
  let afterSrc = ''
  try {
    const [b, a] = await Promise.all([
      fetchLayoutPreviewPng(canvasSegmentOf(cropToFrame({ page: doc.page, objects: doc.objects }, frame)), abort.signal),
      fetchLayoutPreviewPng(segment(merged.objects), abort.signal),
    ])
    beforeSrc = URL.createObjectURL(b)
    afterSrc = URL.createObjectURL(a)
  } catch {
    abort.abort()
    if (live()) fail('change_plan_render_failed')
    else dropAbandoned()
    return
  } finally {
    clearTimeout(timer)
  }
  if (!live()) {
    URL.revokeObjectURL(beforeSrc)
    URL.revokeObjectURL(afterSrc)
    dropAbandoned()
    return
  }

  /** 勾选变了：用勾上的步重算（同一份内存快照、补丁模式）；后面依赖被取消那步而失败的，自动取消并记一句原因 */
  const recompute = async (force = false) => {
    const seq = ++st.seq
    const cur = () => store().entries[key]
    const e0 = cur()
    if (!e0 || e0.status !== 'ready' || !allOps.length) return
    // 勾回到上一次算成功的那个状态：手里的方案就是它的，不用再算（失败的标记也随之解除）
    if (!force && JSON.stringify(Object.fromEntries(Object.entries(e0.unchecked).filter(([, v]) => v))) === st.okKey) {
      st.busy = false
      st.failed = false
      patch(key, { vector: { ...e0.vector!, busy: false, failed: false } })
      return
    }
    const mine = () => st.seq === seq && still() && sameContext(ctx) && !!cur()
    const before = e0.vector?.blocked ?? {}
    st.busy = true
    patch(key, { vector: { ...e0.vector!, busy: true } })
    const unchecked = { ...e0.unchecked }
    const blocked: Record<string, true> = { ...before }
    // 用户自己又勾回来的步，不再算「跟着取消」
    for (const k of Object.keys(blocked)) if (!unchecked[k]) delete blocked[k]
    const groupOf = (op: number) => groups.find((g) => g.op === op)
    // 勾上的步：有对应改动行的看勾选，没有改动行的（本来就没动什么）一直带着
    const chosen = allOps.map((_, i) => i).filter((i) => !groupOf(i) || !unchecked[groupOf(i)!.key])
    let res: VectorEditResult | null = null
    try {
      for (;;) {
        if (!chosen.length) break
        const r = await planVectorEditOps(snap.canvas, chosen.map((i) => allOps[i]))
        if (!mine()) return
        const bad = (r.errors ?? []).find((x) => typeof x.index === 'number' && x.code !== 'checks_failed')
        if (r.ok || !bad || r.objects) {
          res = r
          break
        }
        // 错误的下标是子集里的第几步：映回原来的步号，那一步自动取消
        const orig = chosen[bad.index as number]
        const g = groupOf(orig)
        if (g) {
          unchecked[g.key] = true
          blocked[g.key] = true
        }
        chosen.splice(bad.index as number, 1)
      }
    } catch {
      // 重算没成（网络 / 后端）：手里的方案是按旧勾选算的，不能再拿去应用（会把用户勾掉的步写进去）——
      // 应用不可点，界面给一句话和「重试」；勾回上一次算成功的状态、或重试成功后恢复
      if (mine()) {
        st.busy = false
        st.failed = true
        patch(key, { vector: { ...cur()!.vector!, busy: false, failed: true } })
      }
      return
    }
    if (!mine()) return
    const next: VectorEditResult = res ? { ...res, ops: chosen.map((i) => allOps[i]), unhandled: result.unhandled } : { ok: true, errors: [], objects: [], deleted: [], echo: [], changes: [], ops: [], unhandled: result.unhandled }
    st.result = next
    const now = useDocumentStore.getState().doc
    const mergedNow = hasEdits(next) ? landVector(snap, next, now) : null
    let nextAfter = beforeSrc
    if (mergedNow) {
      try {
        const png = await fetchLayoutPreviewPng(segment(mergedNow.objects))
        if (!mine()) return
        nextAfter = URL.createObjectURL(png)
      } catch {
        // 对比图没更新出来：沿用旧的那张，应用以勾选为准（弹窗里另有一句说明）
        nextAfter = cur()?.afterSrc ?? beforeSrc
      }
    }
    st.busy = false
    st.failed = false
    st.okKey = JSON.stringify(Object.fromEntries(Object.entries(unchecked).filter(([, v]) => v)))
    const e1 = cur()!
    const old = e1.afterSrc
    if (old && old !== nextAfter && old !== beforeSrc && old.startsWith('blob:')) URL.revokeObjectURL(old)
    patch(key, { unchecked, afterSrc: nextAfter, vector: { ...e1.vector!, result: next, count: touchedCount(next), busy: false, failed: false, blocked } })
  }
  patch(key, { status: 'ready', plan: synthPlan(key, result, groups, beforeSrc, afterSrc), beforeSrc, afterSrc, vector: make({ recompute: (force) => void recompute(force) }) })
}

/** 卡与弹窗吃的方案外形：每条复述一组（整批要或不要，不画勾选）；框都不要（复述与对象没有一一对应） */
function synthPlan(id: string, r: VectorEditResult, groups: ChangeGroup[], before: string, after: string): ChangePlan {
  const echo = (r.echo ?? []).filter((x) => typeof x === 'string' && x.trim())
  const lines = groups.length ? groups.map((g) => g.subject) : echo.length ? echo : [translate('changePlan.dialog.vectorN', { ns: 'ai', count: touchedCount(r) })]
  return {
    id,
    version: 1,
    source: 'assistant',
    base: { target: '', fingerprint: '' },
    base_patches: [],
    candidate_patches: [],
    groups: lines.map((label, i) => ({ key: groups[i]?.key ?? `e${i}`, label: String(label), atomic: true, changes: [], boxes: { after: [] } })),
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
