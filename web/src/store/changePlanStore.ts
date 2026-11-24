import { create } from 'zustand'
import { msg } from '@/i18n'
import {
  EngineError,
  createChangePlan,
  discardChangePlan,
  getChangePlan,
  type ChangePlan,
  type ChangePlanPatch,
  type ReviewComment,
} from '@/lib/api'
import { refuseArtifactOperation } from '@/lib/artifactValidation'
import { applyGroups, canonicalPatches, effectiveStatus, samePatches, toOverrides, type PlanSource } from '@/lib/changePlanApply'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { captureProjectEpoch, type ProjectEpochGuard } from '@/lib/projectEpoch'
import { apiUrlFor, onCurrentProjectChange } from '@/lib/session'
import { releaseOwned } from '@/lib/styleOwned'
import type { PanelObject, PanelOverride } from '@/types/document'
import { newRequestId } from './aiStore'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'
import { useReviewStore } from './reviewStore'
import { requestRender } from './renderScheduler'

/**
 * 修改方案卡的前端状态（ADR 0206）。**前端只存与遥控**：方案怎么生成、渲染改前改后都是引擎的事（`/api/change-plans*`，只算不写）；
 * 这里把一份方案翻译成助手里的两张卡与弹窗要画的东西，并把「应用」落成**一次** `documentStore.commit`（进排版时间线，⌘Z 一步撤回）。
 *
 * - 一条 `PlanEntry` = 用户「交给 Agent 修改」的一次请求 + 它的结局：generating 生成中 · ready 待确认 · applied 已应用 ·
 *   discarded 已放弃 · stale 图已改过（基线对不上）· failed 失败（稳定 code，卡片上一句话 + 一个可走的下一步，绝不报错卡住）。
 * - 按「项目 + 图」分键：条目带发起时的 `pj` 与面板对象 id；换项目整表清空并作废在途轮询（项目代际，`lib/projectEpoch`）。
 * - 应用前核基线：这张图此刻的 override（规范化）≠ 方案的 `base_patches` → 卡片转 stale，**不写**。
 */
export type PlanStatus = 'generating' | 'ready' | 'applied' | 'discarded' | 'stale' | 'failed'

export interface PlanFailure {
  /** 后端的稳定码（`change_plan_*`），或本地的 `change_plan_poll_timeout` */
  code: string
  params?: Record<string, unknown>
}

/** 交给后端的意见来源：整批意见（快照）或本机分享记录 id（后端取最新版本的未处理意见） */
export interface PlanRequest {
  comments?: ReviewComment[]
  share_id?: string
}

export interface PlanEntry {
  /** 本地键 = `client_request_id`（同一请求去重，也是卡片的 React key） */
  key: string
  pj: string | null
  /** 画布上的面板对象 id */
  panelId: string
  fileId: string
  /** 发起时的图源身份（文件 + 脚本 + 磁盘修订）：换素材 / 脚本改了就不再适用 */
  source: PlanSource
  documentId: string
  loadSeq: number
  canvasId: string
  createdAt: number
  status: PlanStatus
  planId?: string
  plan?: ChangePlan
  /** 修改前 / 修改后两张图的完整地址（已带项目参数） */
  beforeSrc?: string
  afterSrc?: string
  failure?: PlanFailure
  /** 每个改动组是否被取消勾选（没有的键按勾选算） */
  unchecked: Record<string, boolean>
  request: PlanRequest
  /** 请求那一刻的意见快照（审阅意见卡的编号小圆、改动清单的称呼 / 编号）；只给 `share_id` 时可能为空 */
  comments: ReviewComment[]
  commentCount: number
  /** 已应用时：写进文档那一份（规范化后）；撤销前核对「还是我写的那份」 */
  appliedPatches?: ChangePlanPatch[]
  /** 应用那一次 commit 的历史条目：「撤销」只在它还是栈顶时撤（之后又改了别的，撤的就是别的了） */
  appliedEntry?: unknown
  /** 实际应用的那一张图（只应用了部分组时，对比里按勾选组合出的那张）；没有 = 全量候选图 / 现场缩略图 */
  appliedSrc?: string
  /** 只应用了一部分组：卡片挂的不能是全量候选图 */
  appliedPartial?: boolean
  /** 一次动作没成时卡片上的一句灰字（下一次动作会清掉） */
  notice?: 'apply_failed' | 'undo_failed'
}

export type ApplyResult = 'applied' | 'stale' | 'nothing' | 'failed'

interface ChangePlanState {
  entries: Record<string, PlanEntry>
  /** 创建顺序（旧 → 新） */
  order: string[]
  /** 弹窗里正在看的那一份 */
  dialogKey: string | null

  /** 为某张图发起一次方案；同一张图同一批意见的在途 / 待确认方案直接聚焦（返回它的键） */
  requestFromReview: (panelId: string, req: PlanRequest) => Promise<string | null>
  /** 失败后「重试」/ 图改过后「重新生成」：用同一份请求再来一份，旧卡换成新卡 */
  regenerate: (key: string) => Promise<string | null>
  openDialog: (key: string) => void
  closeDialog: () => void
  toggleGroup: (key: string, groupKey: string) => void
  /** `previewSrc`：弹窗里按勾选组合出的那张图（只应用部分组时给，卡片据此挂实际应用的那一张） */
  apply: (key: string, opts?: { previewSrc?: string }) => ApplyResult
  undo: (key: string) => boolean
  discard: (key: string) => void
  /** 收起一张失败 / 空方案的卡 */
  dismiss: (key: string) => void
  clear: () => void
}

const POLL_MS = 1200
const POLL_MAX_MS = 4 * 60 * 1000
const POLL_ERRORS_MAX = 3
/** 本地的轮询超时码：文案复用「生成失败」 */
export const POLL_TIMEOUT = 'change_plan_poll_timeout'

const hist = (key: string, values?: Record<string, unknown>) => msg(`history.${key}`, values, 'workspace')
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** 文档里的这张图（只认当前这份文档、当前这张画布） */
function panelOf(e: Pick<PlanEntry, 'panelId' | 'documentId' | 'loadSeq' | 'canvasId'>): PanelObject | null {
  const s = useDocumentStore.getState()
  if (s.documentId !== e.documentId || s.loadSeq !== e.loadSeq || s.activeCanvasId !== e.canvasId) return null
  const o = s.doc.objects.find((x) => x.id === e.panelId)
  return o?.type === 'panel' ? o : null
}

const pending = new Set<string>()

/** 这张图此刻的图源身份（磁盘素材带修订；runtime 面板没有稳定修订，只认文件 + 脚本） */
export function sourceOf(panel: PanelObject, mtime = useAssetStore.getState().byId[panel.fileId]?.mtime): PlanSource {
  return { fileId: panel.fileId, script: panel.script ?? null, ...(panel.fileKind !== 'runtime' && mtime !== undefined ? { mtime } : {}) }
}

/**
 * 「撤销」走 `runUndoRedo`（唯一的撤销入口，让位判据在那里）。它的模块（`hooks/useKeyboard`）反过来 import 命令面板与一堆 store，
 * 静态 import 会成环——所以由 `useKeyboard` 在模块加载时交进来（与一键排版胶囊的「撤销」同一做法）。
 */
let undoRunner: (() => void) | null = null
export function setChangePlanUndoRunner(run: () => void): void {
  undoRunner = run
}

export const useChangePlanStore = create<ChangePlanState>((set, get) => {
  const patch = (key: string, p: Partial<PlanEntry>) =>
    set((s) => (s.entries[key] ? { entries: { ...s.entries, [key]: { ...s.entries[key], ...p } } } : {}))
  const drop = (key: string) =>
    set((s) => {
      const { [key]: _gone, ...rest } = s.entries
      return { entries: rest, order: s.order.filter((k) => k !== key), dialogKey: s.dialogKey === key ? null : s.dialogKey }
    })
  const fail = (key: string, code: string, params?: Record<string, unknown>) =>
    patch(key, { status: 'failed', failure: { code, params } })

  /** 后端说热 worker 被任务动过、已作废：按这张图此刻的列表重放一次（与 specfix 同名同义） */
  const replay = (e: PlanEntry) => {
    const p = panelOf(e)
    if (p) requestRender(p, true)
  }

  const poll = async (key: string, planId: string, guard: ProjectEpochGuard) => {
    const t0 = Date.now()
    let errors = 0
    const alive = () => guard.still() && get().entries[key]?.planId === planId && get().entries[key]?.status === 'generating'
    while (alive()) {
      await sleep(POLL_MS)
      if (!alive()) return
      if (Date.now() - t0 > POLL_MAX_MS) return fail(key, POLL_TIMEOUT)
      let view
      try {
        view = await getChangePlan(planId)
        errors = 0
      } catch (err) {
        if (!alive()) return
        const code = err instanceof EngineError ? err.code : ''
        // 方案没了（过期 / 被放弃）是终态；其余（断线、代理页）容忍几次再说失败
        if (code === 'change_plan_unknown') return fail(key, code)
        if (++errors >= POLL_ERRORS_MAX) return fail(key, 'change_plan_failed')
        continue
      }
      if (!alive()) return
      const e = get().entries[key]
      if (view.state === 'running') continue
      if (view.state === 'failed') {
        fail(key, view.code, view.params)
        if ((view as { replay_required?: boolean }).replay_required) replay(e)
        return
      }
      // ready：没有一条能落成修改的（change_plan_empty）也带方案（里面只有 unhandled），卡片按失败的一句话 + 未处理列表画
      const plan = view.plan
      const src = (p: string) => apiUrlFor(p, e.pj)
      patch(key, {
        plan,
        beforeSrc: src(plan.preview.before),
        afterSrc: src(plan.preview.after),
        ...(view.code === 'change_plan_empty'
          ? { status: 'failed' as const, failure: { code: 'change_plan_empty' } }
          : { status: 'ready' as const, failure: undefined }),
      })
      if ((view as { replay_required?: boolean }).replay_required) replay(e)
      return
    }
  }

  const start = async (panelId: string, req: PlanRequest): Promise<string | null> => {
    const ds = useDocumentStore.getState()
    const panel = ds.doc.objects.find((o): o is PanelObject => o.id === panelId && o.type === 'panel')
    if (!panel) return null
    const key = newRequestId()
    const guard = captureProjectEpoch()
    // 只给了分享记录 id（后端取最新版本的未处理意见）：界面上的编号 / 称呼取审阅页签里同一份记录的未处理意见
    const detail = useReviewStore.getState().detail
    const comments = req.comments ?? (req.share_id && detail?.share_id === req.share_id ? detail.comments.filter((c) => !c.resolved) : [])
    const entry: PlanEntry = {
      key,
      pj: guard.pj,
      panelId,
      fileId: panel.fileId,
      source: sourceOf(panel),
      documentId: ds.documentId,
      loadSeq: ds.loadSeq,
      canvasId: ds.activeCanvasId,
      createdAt: Date.now(),
      status: 'generating',
      unchecked: {},
      request: req,
      comments,
      commentCount: comments.length,
    }
    set((s) => ({ entries: { ...s.entries, [key]: entry }, order: [...s.order, key] }))
    try {
      refuseArtifactOperation(panel)
    } catch {
      fail(key, 'change_plan_not_editable')
      return key
    }
    try {
      const task = await createChangePlan({
        id: panel.fileId,
        patches: panel.overrides,
        source: 'review',
        ...(req.comments ? { comments: req.comments } : { share_id: req.share_id }),
        client_request_id: key,
      })
      if (!guard.still() || !get().entries[key]) return key
      patch(key, { planId: task.plan_id })
      void poll(key, task.plan_id, guard)
    } catch (err) {
      if (!guard.still() || !get().entries[key]) return key
      const code = err instanceof EngineError && err.code ? err.code : 'change_plan_failed'
      fail(key, code, err instanceof EngineError ? err.params : undefined)
    }
    return key
  }

  return {
    entries: {},
    order: [],
    dialogKey: null,

    requestFromReview: async (panelId, req) => {
      const same = (e: PlanEntry) =>
        e.panelId === panelId &&
        (e.status === 'generating' || e.status === 'ready') &&
        e.request.share_id === req.share_id &&
        JSON.stringify((e.request.comments ?? []).map((c) => c.id)) === JSON.stringify((req.comments ?? []).map((c) => c.id))
      const hit = get().order.map((k) => get().entries[k]).find(same)
      if (hit) return hit.key
      // 同一时刻只发一份：连点两下不会出两张卡
      const gate = `${panelId}`
      if (pending.has(gate)) return null
      pending.add(gate)
      try {
        return await start(panelId, req)
      } finally {
        pending.delete(gate)
      }
    },

    regenerate: async (key) => {
      const e = get().entries[key]
      if (!e) return null
      const planId = e.planId
      drop(key)
      if (planId) void discardChangePlan(planId).catch(() => undefined)
      return start(e.panelId, e.request)
    },

    openDialog: (key) => set({ dialogKey: key }),
    closeDialog: () => set({ dialogKey: null }),
    toggleGroup: (key, groupKey) =>
      set((s) => {
        const e = s.entries[key]
        return e ? { entries: { ...s.entries, [key]: { ...e, unchecked: { ...e.unchecked, [groupKey]: !e.unchecked[groupKey] } } } } : {}
      }),

    apply: (key, opts) => {
      const e = get().entries[key]
      const plan = e?.plan
      if (!e || !plan) return 'failed'
      const panel = panelOf(e)
      // 以文档为准：⌘Z 撤回过的「已应用」又是待确认，可以再应用；对不上方案的改前（生成之后这张图被改过）就不写
      const st = effectiveStatus(e, panel?.overrides ?? null, panel ? sourceOf(panel) : null)
      if (st === 'stale') {
        patch(key, { status: 'stale', notice: undefined })
        return 'stale'
      }
      if (st !== 'ready' || !panel) return 'failed'
      const keys = plan.groups.filter((g) => !e.unchecked[g.key]).map((g) => g.key)
      if (!keys.length) return 'nothing'
      const target: ChangePlanPatch[] =
        keys.length === plan.groups.length ? plan.candidate_patches : applyGroups(plan.base_patches, plan.groups, keys)
      writeOverrides(e.panelId, target, hist('applyChangePlan', { count: keys.length }))
      const after = panelOf(e)
      // 写没落下（写入被挡 / 这张图已不在）：不装作应用了
      if (!after || !samePatches(after.overrides, target)) {
        patch(key, { notice: 'apply_failed' })
        return 'failed'
      }
      patch(key, {
        status: 'applied',
        appliedPatches: canonicalPatches(after.overrides),
        appliedEntry: useDocumentStore.getState().past.at(-1),
        appliedPartial: keys.length !== plan.groups.length,
        appliedSrc: keys.length !== plan.groups.length ? opts?.previewSrc : undefined,
        notice: undefined,
      })
      set({ dialogKey: null })
      requestRender(after, true)
      return 'applied'
    },

    undo: (key) => {
      const e = get().entries[key]
      const panel = e ? panelOf(e) : null
      // 只撤我写的那一条：还是应用时那份、且历史栈顶还是那次 commit；之后又改了别的，撤的就是别的了
      if (!e || !panel || e.status !== 'applied' || !canUndoApplied(e, panel.overrides)) {
        if (e) patch(key, { notice: 'undo_failed' })
        return false
      }
      undoRunner?.()
      const after = panelOf(e)
      if (!after || !e.plan || !samePatches(after.overrides, e.plan.base_patches)) {
        patch(key, { notice: 'undo_failed' })
        return false
      }
      // 状态不用改：卡片以文档为准，撤回之后自然又是「待确认」
      patch(key, { notice: undefined })
      requestRender(after, true)
      return true
    },

    discard: (key) => {
      const e = get().entries[key]
      if (!e) return
      patch(key, { status: 'discarded' })
      set((s) => (s.dialogKey === key ? { dialogKey: null } : {}))
      // 后端那份可以收了；卡上的图仍是已经取到的地址，过期也只是图不显示，卡片文字不变
      if (e.planId) void discardChangePlan(e.planId).catch(() => undefined)
    },

    dismiss: (key) => {
      const e = get().entries[key]
      drop(key)
      if (e?.planId) void discardChangePlan(e.planId).catch(() => undefined)
    },

    // 不去后端放弃：此刻的项目已经不是发起时那个，请求会落到别的项目上；后端有 TTL 与条数上限
    clear: () => set({ entries: {}, order: [], dialogKey: null }),
  }
})

/** 这份已应用的方案此刻还能一步撤回：图还是我们写的那份，且历史栈顶还是那次 commit */
export function canUndoApplied(e: Pick<PlanEntry, 'appliedPatches' | 'appliedEntry'>, current: readonly unknown[]): boolean {
  const s = useDocumentStore.getState()
  return !!e.appliedEntry && s.past.at(-1) === e.appliedEntry && !!e.appliedPatches && samePatches(current, e.appliedPatches)
}

/** 一次 commit 把这张图的 override 换成 `patches`（⌘Z 一步回；与一键修复同一套「样式写的」登记注销） */
function writeOverrides(panelId: string, patches: readonly ChangePlanPatch[], label: ReturnType<typeof hist>): void {
  const next = toOverrides(patches)
  useDocumentStore.getState().commit(label, (d) => {
    const obj = d.objects.find((o) => o.id === panelId)
    if (obj?.type !== 'panel') return
    // 按生效那条比（重复条目 last-wins）：值变了、有了、没了的都算写过
    const eff = (list: PanelOverride[], x: PanelOverride) => effectiveOverride(list, x.gid, x.prop)
    const written = [...obj.overrides, ...next].filter((x) => {
      const a = eff(obj.overrides, x)
      const b = eff(next, x)
      return !a || !b || JSON.stringify(a.value) !== JSON.stringify(b.value)
    })
    obj.overrides = next
    releaseOwned(d, obj, written)
    // 方案里每条 patch 的 `identity` 是引擎渲染它时取的：按「从存储读回来」处理，不让提交时的身份抄写按此刻 manifest 重抄
  }, { overrides: 'restored' })
}

// 换项目：旧项目的方案卡与在途轮询一律作废（A 的东西不留在 B 的面板里）
onCurrentProjectChange(() => {
  pending.clear()
  useChangePlanStore.getState().clear()
})
