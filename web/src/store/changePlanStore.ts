import { create } from 'zustand'
import { msg } from '@/i18n'
import {
  EngineError,
  createChangePlan,
  discardChangePlan,
  getChangePlan,
  reviseChangePlan,
  type ChangePlan,
  type ChangePlanPatch,
  type ReviewAnchor,
  type ReviewComment,
} from '@/lib/api'
import { refuseArtifactOperation } from '@/lib/artifactValidation'
import { applyGroups, canonicalPatches, effectiveStatus, samePatches, toOverrides, type PlanSource } from '@/lib/changePlanApply'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { writeChangePlanCache } from '@/lib/changePlanSwitch'
import { captureProjectEpoch, type ProjectEpochGuard } from '@/lib/projectEpoch'
import { apiUrlFor, onCurrentProjectChange } from '@/lib/session'
import { releaseOwned } from '@/lib/styleOwned'
import type { PanelObject, PanelOverride } from '@/types/document'
import { currentRunSelection, newRequestId, useAiStore, type AiScope } from './aiStore'
import { scriptHandoffPrompt } from '@/lib/assistantScriptContext'
import { engineLabel } from '@/components/inspector/roles/registry'
import { exactPanelManifest, useRenderStore } from './renderStore'
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
export type PlanStatus = 'generating' | 'ready' | 'applied' | 'discarded' | 'stale' | 'failed' | 'handoff'

export interface PlanFailure {
  /** 后端的稳定码（`change_plan_*`），或本地的 `change_plan_poll_timeout` */
  code: string
  params?: Record<string, unknown>
}

/** 交给后端的意见来源：整批意见（快照）或本机分享记录 id（后端取最新版本的未处理意见）；助手来源是用户的一条指令 */
export interface PlanRequest {
  comments?: ReviewComment[]
  share_id?: string
  /** 审阅页签里这些意见的编号（意见 id → 页签上的序号）：审阅意见卡与改动清单用它，与页签上看到的编号一致 */
  numbers?: Record<string, number>
  /** 助手来源（ADR 0206 PR 4）：用户在改图助手里的原话与选中的元素 gid */
  instruction?: string
  target?: string
}

/** 高级版：在某一版「修改后」的图上写的一条批注（只存本机，不上传审阅服务）；`label` 是元素名 / 「框选」/ 「整体」的显示名 */
export interface PlanNote {
  id: string
  anchor: ReviewAnchor
  label: string
  text: string
}

/** 一张卡的一版：每一版是后端自己的一份方案（各有 id 与两张图），逐组勾选是各版自己的 */
export interface PlanVersionRec {
  planId: string
  plan: ChangePlan
  beforeSrc: string
  afterSrc: string
  unchecked: Record<string, boolean>
}

/** 一键排版方案卡的两个动作：应用 = 现有的那一次 commit（一步撤销不变）；重来 = 按同样的选项再排一次 */
export interface LayoutHandle {
  apply: () => ApplyResult
  regenerate: () => void
  /** 参与排版的图的张数（卡片一句话用） */
  count: number
}

/**
 * 助手来源的「改脚本」后路（现有改脚本路径，`aiStore.start`）所需的发送快照：发送那一刻固定下来，
 * 之后不读此刻的面板选择 / 草稿（轮询期间用户可能已经换了元素 / 范围）。
 */
export interface ScriptFallback {
  project: string | null
  generation: number
  prompt: string
  fileId: string
  panelId: string
  gid: string | null
  label: string | null
  scope: AiScope
  target: string
  canvas: string
  script: string | null
}

/** 助手来源的方案请求在这些后端码下**不算失败**：这条路不适用（没连上能用的模型 / native / 没脚本），照旧走改脚本路径 */
export const ASSISTANT_FALLTHROUGH_CODES: ReadonlySet<string> = new Set([
  'change_plan_no_connection',
  'change_plan_unsupported_connection',
  'change_plan_native_unsupported',
  'change_plan_not_editable',
  'invalid_change_plan',
  'change_plan_too_large',
])

export interface PlanEntry {
  /** 本地键 = `client_request_id`（同一请求去重，也是卡片的 React key） */
  key: string
  /** 方案从哪来：审阅意见（默认）/ 改图助手里的一条指令 / 一键排版 */
  from?: 'review' | 'assistant' | 'quick_layout'
  /** `layout` = 一键排版的整版方案（不挂在某张图上，`panelId` 为空）；缺省 = 一张图的方案 */
  kind?: 'figure' | 'layout'
  /** 一键排版方案的落地与重来（由 `quickLayoutActions` 交进来；store 不 import 它，免得成环） */
  layout?: LayoutHandle
  /** 助手来源：用户的原话（对话里的用户气泡；改动清单里这一组的标题） */
  instruction?: string
  /** 助手来源的改脚本后路（见 `ScriptFallback`） */
  fallback?: ScriptFallback
  /**
   * 助手来源：方案一落定（出卡 / 失败 / 交给改脚本）就调一次、随即清掉——输入框里的指令与后端草稿留到这一刻才删，
   * 方案生成中刷新页面指令还在输入框里，不会丢。
   */
  onSettle?: () => void
  /** 助手来源：已经把这条指令交给改脚本那条路了（卡上不再给「改脚本做剩下的」；全是要改脚本时卡只剩一行灰字） */
  scriptStarted?: boolean
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
  /** 每个改动组是否被取消勾选（没有的键按勾选算）；多版时是此刻正在看的那一版的 */
  unchecked: Record<string, boolean>
  /** 这张卡出过的各版（旧 → 新）；`planId` / `plan` / `beforeSrc` / `afterSrc` / `unchecked` 是正在看的那一版的镜像。没有 = 只有一版 */
  versions?: PlanVersionRec[]
  /** 批注重改（高级版）：正在写的批注（重改成功后清空） */
  notes?: PlanNote[]
  /** 写过的批注原话（批注 id → 文字）：后面的版本里没处理的批注按它显示 */
  noteTexts?: Record<string, string>
  /** 这次重改提交的批注快照（id → 文字）：回包只清掉仍与它一致的批注 */
  sentNotes?: Record<string, string>
  /** 按批注重改进行中：新一版的方案 id（请求还没回时是空串） */
  revising?: string
  /** 上一次按批注重改没成：稳定码（弹窗里一句灰字，下一次动作清掉） */
  reviseError?: PlanFailure
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
  /** 应用后由这张卡标成「已处理」的意见 id（撤销时只还原这几条） */
  resolvedIds?: string[]
  /** 标已处理 / 还原没成：审阅页签上一句灰字 + 重试（不自动再试，免得失败时反复打后端） */
  resolveFailed?: boolean
  /** 回复草稿：意见 id → 用户改过的文字；null = 已发送（不再出草稿框） */
  drafts: Record<string, string | null>
}

export type ApplyResult = 'applied' | 'stale' | 'nothing' | 'failed'
export type ReviseResult = 'started' | 'not_licensed' | 'nothing' | 'busy' | 'failed'

/** 一条批注的字数上限（与引擎 `changeplan.MAX_COMMENT_CHARS` 一致） */
export const MAX_NOTE_CHARS = 500
/** 一次重改最多带几条批注（与引擎 `changeplan.MAX_COMMENTS` 一致） */
export const MAX_NOTES = 30

export const notesOf = (e: Pick<PlanEntry, 'notes'>): PlanNote[] => e.notes ?? []

/** 各版（旧 → 新）；没存过的老条目 = 只有正在看的这一版 */
export function versionsOf(e: PlanEntry): PlanVersionRec[] {
  if (e.versions?.length) return e.versions
  return e.plan && e.planId && e.beforeSrc && e.afterSrc ? [{ planId: e.planId, plan: e.plan, beforeSrc: e.beforeSrc, afterSrc: e.afterSrc, unchecked: e.unchecked }] : []
}

/** 正在看的是第几版（1 起；按出现顺序数，不看后端的 `version` 字段——从旧版再续改会得到同一个后端版本号） */
export function versionNumber(e: PlanEntry): number {
  const i = versionsOf(e).findIndex((v) => v.planId === e.planId)
  return i < 0 ? (e.plan?.version ?? 1) : i + 1
}

/** 这张卡上所有版本（含在途的那一版）的后端方案 id：放弃时一并收掉 */
const allPlanIds = (e: PlanEntry): string[] => [...new Set([...versionsOf(e).map((v) => v.planId), ...(e.planId ? [e.planId] : []), ...(e.revising ? [e.revising] : [])])]

interface ChangePlanState {
  entries: Record<string, PlanEntry>
  /** 创建顺序（旧 → 新） */
  order: string[]
  /** 弹窗里正在看的那一份 */
  dialogKey: string | null

  /** 为某张图发起一次方案；同一张图同一批意见的在途 / 待确认方案直接聚焦（返回它的键） */
  requestFromReview: (panelId: string, req: PlanRequest) => Promise<string | null>
  /**
   * 改图助手里的一条指令先出方案（ADR 0206 PR 4）。→ 方案键；`'fallthrough'` = 这条路不适用（没连上能用的模型 / native / 没脚本…，
   * 同步就被拒），调用方照旧走改脚本路径（本函数没留下任何卡）；null = 没发（同一张图已有一份在途 / 图不在了）。
   */
  requestFromAssistant: (
    panelId: string,
    req: { instruction: string; target?: string },
    fallback: ScriptFallback,
    onSettle?: () => void,
  ) => Promise<string | null | 'fallthrough'>
  /** 把这条助手指令交给现有的改脚本路径（`aiStore.start`）：「改脚本做剩下的」/ 全部都要改脚本时自动续走 / 失败后的「改脚本做」 */
  runScript: (key: string) => Promise<boolean>
  /** 失败后「重试」/ 图改过后「重新生成」：用同一份请求再来一份，旧卡换成新卡 */
  regenerate: (key: string) => Promise<string | null>
  /** 一键排版出的整版方案（`quickLayoutActions` 调）：条目已带方案与两张预览图的 blob 地址，直接是待确认 */
  addLayoutEntry: (e: PlanEntry) => void
  openDialog: (key: string) => void
  closeDialog: () => void
  toggleGroup: (key: string, groupKey: string) => void
  /** `previewSrc`：弹窗里按勾选组合出的那张图（只应用部分组时给，卡片据此挂实际应用的那一张） */
  apply: (key: string, opts?: { previewSrc?: string }) => ApplyResult
  undo: (key: string) => boolean
  discard: (key: string) => void
  /** 高级版：按批注重改，同一张卡出下一版（出好后自动切到新版） */
  revise: (key: string) => Promise<ReviseResult>
  /** 弹窗里切到某一版（各版的勾选各记各的） */
  setViewVersion: (key: string, planId: string) => void
  saveNote: (key: string, note: PlanNote) => void
  removeNote: (key: string, id: string) => void
  /** 收起一张失败 / 空方案的卡 */
  dismiss: (key: string) => void
  clear: () => void
  /** 收尾状态（标已处理的记录、草稿）直接并进条目 */
  patchEntry: (key: string, p: Partial<PlanEntry>) => void
}

const POLL_MS = 1200
const POLL_MAX_MS = 4 * 60 * 1000
const POLL_ERRORS_MAX = 3
/** 本地的轮询超时码：文案复用「生成失败」 */
export const POLL_TIMEOUT = 'change_plan_poll_timeout'

const hist = (key: string, values?: Record<string, unknown>) => msg(`history.${key}`, values, 'workspace')
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** 文档里的这张图（只认当前这份文档、当前这张画布） */
export function panelOf(e: Pick<PlanEntry, 'panelId' | 'documentId' | 'loadSeq' | 'canvasId'>): PanelObject | null {
  const s = useDocumentStore.getState()
  if (s.documentId !== e.documentId || s.loadSeq !== e.loadSeq || s.activeCanvasId !== e.canvasId) return null
  const o = s.doc.objects.find((x) => x.id === e.panelId)
  return o?.type === 'panel' ? o : null
}

const pending = new Set<string>()

/** 一键排版方案的两张预览图是 blob 地址（本机内存里的 PNG）：条目没了就放掉 */
function revokeLayoutImages(e: PlanEntry | undefined): void {
  if (e?.kind !== 'layout') return
  for (const u of [e.beforeSrc, e.afterSrc]) if (u?.startsWith('blob:')) URL.revokeObjectURL(u)
}

/** 这份方案一处图内修改都没有、没处理的全是「要改脚本」（助手来源据此把整条指令交给改脚本那条路） */
function allNeedScript(plan: ChangePlan): boolean {
  return plan.groups.length === 0 && plan.unhandled.length > 0 && plan.unhandled.every((u) => u.reason === 'needs_script')
}

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
      revokeLayoutImages(s.entries[key])
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

  const poll = async (key: string, planId: string, guard: ProjectEpochGuard, mode: 'create' | 'revise' = 'create') => {
    const t0 = Date.now()
    let errors = 0
    const revising = mode === 'revise'
    const alive = () =>
      guard.still() && (revising ? get().entries[key]?.revising === planId : get().entries[key]?.planId === planId && get().entries[key]?.status === 'generating')
    // 续改没成：旧版原样留着，弹窗里一句灰字
    const failRevise = (code: string, params?: Record<string, unknown>) => patch(key, { revising: undefined, reviseError: { code, params } })
    const failed = (code: string, params?: Record<string, unknown>) => (revising ? failRevise(code, params) : fail(key, code, params))
    while (alive()) {
      await sleep(POLL_MS)
      if (!alive()) return
      if (Date.now() - t0 > POLL_MAX_MS) return failed(POLL_TIMEOUT)
      let view
      try {
        view = await getChangePlan(planId)
        errors = 0
      } catch (err) {
        if (!alive()) return
        const code = err instanceof EngineError ? err.code : ''
        // 方案没了（过期 / 被放弃）是终态；其余（断线、代理页）容忍几次再说失败
        if (code === 'change_plan_unknown') return failed(code)
        if (++errors >= POLL_ERRORS_MAX) return failed('change_plan_failed')
        continue
      }
      if (!alive()) return
      const e = get().entries[key]
      if (view.state === 'running') continue
      if (view.state === 'failed') {
        failed(view.code, view.params)
        if ((view as { replay_required?: boolean }).replay_required) replay(e)
        return
      }
      // ready：没有一条能落成修改的（change_plan_empty）也带方案（里面只有 unhandled），卡片按失败的一句话 + 未处理列表画
      const plan = view.plan
      const src = (p: string) => apiUrlFor(p, e.pj)
      if (revising) {
        if (view.code === 'change_plan_empty') failRevise('change_plan_empty')
        else {
          // 新一版入列并切过去；各版的勾选各记各的，批注随这次重改清空
          // 重改期间新写 / 改过的批注是用户的新工作：只清掉仍与提交时一致的那些
          const e0 = get().entries[key] ?? e
          const prev = versionsOf(e).map((v) => (v.planId === e.planId ? { ...v, unchecked: e.unchecked } : v))
          const rec: PlanVersionRec = { planId, plan, beforeSrc: src(plan.preview.before), afterSrc: src(plan.preview.after), unchecked: {} }
          patch(key, { versions: [...prev, rec], planId, plan, beforeSrc: rec.beforeSrc, afterSrc: rec.afterSrc, unchecked: {}, notes: notesOf(e0).filter((n) => e0.sentNotes?.[n.id] !== n.text), sentNotes: undefined, revising: undefined, reviseError: undefined, notice: undefined })
        }
        if ((view as { replay_required?: boolean }).replay_required) replay(e)
        return
      }
      // 助手来源：整条指令都要改脚本（没有一处图内修改、没有别的原因）= 不算失败，接着走现有的改脚本路径
      if (e.from === 'assistant' && view.code === 'change_plan_empty' && allNeedScript(plan)) {
        if ((view as { replay_required?: boolean }).replay_required) replay(e)
        patch(key, { plan })
        void get().runScript(key)
        return
      }
      patch(key, {
        versions: [{ planId, plan, beforeSrc: src(plan.preview.before), afterSrc: src(plan.preview.after), unchecked: {} }],
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

  const start = async (panelId: string, req: PlanRequest, fallback?: ScriptFallback, keepOnFallthrough = false, onSettle?: () => void): Promise<string | null | 'fallthrough'> => {
    const ds = useDocumentStore.getState()
    const panel = ds.doc.objects.find((o): o is PanelObject => o.id === panelId && o.type === 'panel')
    if (!panel) return null
    const key = newRequestId()
    const guard = captureProjectEpoch()
    // 只给了分享记录 id（后端取最新版本的未处理意见）：界面上的编号 / 称呼取审阅页签里同一份记录的未处理意见
    const detail = useReviewStore.getState().detail
    const comments = req.comments ?? (req.share_id && detail?.share_id === req.share_id ? detail.comments.filter((c) => !c.resolved) : [])
    const assistant = typeof req.instruction === 'string'
    const entry: PlanEntry = {
      key,
      ...(assistant ? { from: 'assistant' as const, instruction: req.instruction, fallback, onSettle } : {}),
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
      drafts: {},
      request: req,
      comments,
      commentCount: comments.length,
    }
    set((s) => ({ entries: { ...s.entries, [key]: entry }, order: [...s.order, key] }))
    try {
      refuseArtifactOperation(panel)
    } catch {
      if (assistant && !keepOnFallthrough) {
        drop(key)
        return 'fallthrough'
      }
      fail(key, 'change_plan_not_editable')
      return key
    }
    try {
      const task = await createChangePlan({
        id: panel.fileId,
        patches: panel.overrides,
        ...(assistant
          ? { source: 'assistant' as const, instruction: req.instruction, ...(req.target ? { target: req.target } : {}) }
          : { source: 'review' as const, ...(req.comments ? { comments: req.comments } : { share_id: req.share_id }) }),
        // 输入框选的那条连接与型号：结构化调用用同一条，不顺延到别的（审阅来源同理）
        ...currentRunSelection(),
        client_request_id: key,
      })
      if (!guard.still() || !get().entries[key]) return key
      patch(key, { planId: task.plan_id })
      void poll(key, task.plan_id, guard)
    } catch (err) {
      if (!guard.still() || !get().entries[key]) return key
      const code = err instanceof EngineError && err.code ? err.code : 'change_plan_failed'
      // 助手来源：这条路不适用就悄悄撤掉这张卡，让调用方走旧路径（用户的指令一个字不丢）
      if (assistant && !keepOnFallthrough && ASSISTANT_FALLTHROUGH_CODES.has(code)) {
        drop(key)
        return 'fallthrough'
      }
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
        JSON.stringify(Object.keys(e.request.numbers ?? {}).sort()) === JSON.stringify(Object.keys(req.numbers ?? {}).sort()) &&
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

    requestFromAssistant: async (panelId, req, fallback, onSettle) => {
      // 同一时刻只发一份（与审阅来源共用这把门）；同一张图已有在途方案时不再起第二份
      const busy = get().order.some((k) => get().entries[k]?.panelId === panelId && get().entries[k]?.status === 'generating')
      if (busy || pending.has(panelId)) return null
      pending.add(panelId)
      try {
        return await start(panelId, req, fallback, false, onSettle)
      } finally {
        pending.delete(panelId)
      }
    },

    runScript: async (key) => {
      const e = get().entries[key]
      const fb = e?.fallback
      if (!e || !fb || e.from !== 'assistant') return false
      // 发送那一刻的项目 / 文档 / 面板：途中换了就不替他发（换项目整表已清；换文档 / 画布 / 面板被删这里挡）
      let panel = panelOf(e)
      const ai = useAiStore.getState()
      if (!panel || panel.fileId !== fb.fileId || ai.generation !== fb.generation) {
        fail(key, 'change_plan_script_failed')
        return false
      }
      // 还没应用的图内修改先应用（同一次 commit、正常进时间线）再改脚本：脚本一改图源就变了，待确认的方案会判「图已改过」再也应用不了，
      // 图内那部分就丢了。应用没成（图已改过 / 写不进）= 不续走，卡上说一句
      // 与卡片 / `apply()` 同一个有效状态（⌘Z 撤回过的「已应用」又是待确认，存着的 `e.status` 不算数）
      const hasChanges = !!e.plan && e.plan.groups.some((g) => !e.unchecked[g.key] && g.changes.length > 0)
      const statusNow = () => {
        const cur = get().entries[key] ?? e
        const p = panelOf(cur)
        return effectiveStatus(cur, p?.overrides ?? null, p ? sourceOf(p) : null)
      }
      if (hasChanges) {
        const st = statusNow()
        if (st === 'stale') {
          patch(key, { status: 'stale', notice: undefined })
          return false
        }
        if (st === 'ready') {
          const r = get().apply(key)
          if (r !== 'applied') return false
        }
        panel = panelOf(e) ?? panel
      }
      // 上下文按**实际**情况写：真正落在文档里的才写「已改好」
      const appliedNow = hasChanges && statusNow() === 'applied'
      // 图内已经改（或待用户应用）的部分，确定性地写进指令：Agent 不在脚本里重做它们（`lib/assistantScriptContext`）
      const manifest = exactPanelManifest(useRenderStore.getState(), panel)
      const labels = new Map((manifest?.elements ?? []).map((x) => [x.gid, x.label ? engineLabel(x.label).trim() : '']))
      const prompt = scriptHandoffPrompt(fb.prompt, e.plan, {
        applied: appliedNow,
        unchecked: e.unchecked,
        labelOf: (gid) => labels.get(gid) || undefined,
      })
      try {
        await ai.start({
          project: fb.project,
          generation: fb.generation,
          prompt,
          fileId: fb.fileId,
          panelId: fb.panelId,
          gid: fb.gid,
          label: fb.label,
          scope: fb.scope,
          target: fb.target,
          overrides: panel.overrides,
          canvas: fb.canvas,
          script: fb.script,
        })
      } catch {
        // 没发出去（没有可用的 Agent / 还有没落定的提交 / 账本写不进…）：卡上一句话 + 重试，指令不丢
        patch(key, { status: 'failed', failure: { code: 'change_plan_script_failed' } })
        return false
      }
      // 全部都要改脚本：这张卡只剩一行灰字（用户的气泡由那一轮会话自己画）；部分能改的卡留着，只收起「改脚本做剩下的」
      patch(key, e.plan && e.plan.groups.length > 0 ? { scriptStarted: true } : { status: 'handoff', scriptStarted: true, failure: undefined })
      return true
    },

    regenerate: async (key) => {
      const e = get().entries[key]
      if (!e) return null
      if (e.kind === 'layout') {
        drop(key)
        e.layout?.regenerate()
        return null
      }
      const ids = allPlanIds(e)
      drop(key)
      for (const id of ids) void discardChangePlan(id).catch(() => undefined)
      // 助手来源：「重新生成」不再悄悄绕去旧路径——这条路不适用时留一张带「改脚本做」的失败卡
      const out = await start(e.panelId, e.request, e.fallback, e.from === 'assistant')
      return out === 'fallthrough' ? null : out
    },

    openDialog: (key) => set({ dialogKey: key }),
    closeDialog: () => set({ dialogKey: null }),
    toggleGroup: (key, groupKey) =>
      set((s) => {
        const e = s.entries[key]
        return e ? { entries: { ...s.entries, [key]: { ...e, unchecked: { ...e.unchecked, [groupKey]: !e.unchecked[groupKey] } } } } : {}
      }),

    addLayoutEntry: (entry) => set((s) => ({ entries: { ...s.entries, [entry.key]: entry }, order: [...s.order, entry.key] })),

    apply: (key, opts) => {
      const e = get().entries[key]
      const plan = e?.plan
      if (!e || !plan) return 'failed'
      // 一键排版：落地就是现有的那一次 commit（核对「图还是出发时那样」也在里面）；对不上 = 「图已改过」，不写
      if (e.kind === 'layout') {
        const h = useDocumentStore.getState()
        // ⌘Z 撤回过的「已应用」又是待确认：可以再应用（派生状态见 `effectiveStatus`）
        const st = effectiveStatus(e, null, null, { past: h.past, future: h.future })
        if (st === 'stale') {
          patch(key, { status: 'stale', notice: undefined })
          return 'stale'
        }
        if (st !== 'ready' || !e.layout) return 'failed'
        const r = e.layout.apply()
        if (r === 'applied') {
          patch(key, { status: 'applied', appliedEntry: useDocumentStore.getState().past.at(-1), notice: undefined })
          set((s) => (s.dialogKey === key ? { dialogKey: null } : {}))
        } else if (r === 'stale') patch(key, { status: 'stale', notice: undefined })
        else patch(key, { notice: 'apply_failed' })
        return r
      }
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
      // 一键排版的方案没有后端那一份，也没有「已放弃」的卡可留：直接收掉（再排一次只要几秒）
      if (e.kind === 'layout') return drop(key)
      patch(key, { status: 'discarded' })
      set((s) => (s.dialogKey === key ? { dialogKey: null } : {}))
      // 后端那几份（各版）可以收了；卡上的图仍是已经取到的地址，过期也只是图不显示，卡片文字不变
      for (const id of allPlanIds(e)) void discardChangePlan(id).catch(() => undefined)
    },

    revise: async (key) => {
      const e = get().entries[key]
      const notes = e ? notesOf(e) : []
      if (!e || !e.planId || e.status !== 'ready') return 'failed'
      if (e.revising !== undefined) return 'busy'
      if (!notes.length) return 'nothing'
      const guard = captureProjectEpoch()
      patch(key, { revising: '', reviseError: undefined, sentNotes: Object.fromEntries(notes.map((n) => [n.id, n.text])), noteTexts: { ...e.noteTexts, ...Object.fromEntries(notes.map((n) => [n.id, n.text])) } })
      try {
        const task = await reviseChangePlan(e.planId, {
          notes: notes.map((n) => ({ id: n.id, anchor: n.anchor, text: n.text })),
          client_request_id: newRequestId(),
        })
        if (!guard.still() || !get().entries[key]) return 'failed'
        if ('ok' in task) {
          patch(key, { revising: undefined, reviseError: { code: 'not_licensed' } })
          return 'not_licensed'
        }
        patch(key, { revising: task.plan_id })
        void poll(key, task.plan_id, guard, 'revise')
        return 'started'
      } catch (err) {
        if (!guard.still() || !get().entries[key]) return 'failed'
        const code = err instanceof EngineError && err.code ? err.code : 'change_plan_failed'
        patch(key, { revising: undefined, reviseError: { code, params: err instanceof EngineError ? err.params : undefined } })
        return 'failed'
      }
    },

    setViewVersion: (key, planId) =>
      set((s) => {
        const e = s.entries[key]
        if (!e || e.revising !== undefined || e.planId === planId) return {}
        const recs = versionsOf(e).map((v) => (v.planId === e.planId ? { ...v, unchecked: e.unchecked } : v))
        const to = recs.find((v) => v.planId === planId)
        if (!to) return {}
        return { entries: { ...s.entries, [key]: { ...e, versions: recs, planId, plan: to.plan, beforeSrc: to.beforeSrc, afterSrc: to.afterSrc, unchecked: to.unchecked, reviseError: undefined } } }
      }),

    saveNote: (key, note) =>
      set((s) => {
        const e = s.entries[key]
        if (!e) return {}
        const cur = notesOf(e)
        if (!cur.some((n) => n.id === note.id) && cur.length >= MAX_NOTES) return {}
        const notes = cur.some((n) => n.id === note.id) ? cur.map((n) => (n.id === note.id ? note : n)) : [...cur, note]
        return { entries: { ...s.entries, [key]: { ...e, notes } } }
      }),

    removeNote: (key, id) =>
      set((s) => {
        const e = s.entries[key]
        return e ? { entries: { ...s.entries, [key]: { ...e, notes: notesOf(e).filter((n) => n.id !== id) } } } : {}
      }),

    dismiss: (key) => {
      const e = get().entries[key]
      drop(key)
      if (e) for (const id of allPlanIds(e)) void discardChangePlan(id).catch(() => undefined)
    },

    patchEntry: (key, p) => patch(key, p),

    // 不去后端放弃：此刻的项目已经不是发起时那个，请求会落到别的项目上；后端有 TTL 与条数上限
    clear: () => {
      for (const e of Object.values(get().entries)) revokeLayoutImages(e)
      set({ entries: {}, order: [], dialogKey: null })
    },
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

// 探测到的能力里带着开关：记一份在本机，下次还没探测完时（比如一进来就点一键排版）也按用户的选择办（`lib/changePlanSwitch`）
useAiStore.subscribe((s, prev) => {
  const v = s.caps?.change_plan_enabled
  if (typeof v === 'boolean' && v !== prev.caps?.change_plan_enabled) writeChangePlanCache(v)
})

// 助手来源的方案一落定（不再是「生成中」），把输入框里那条指令与后端草稿放掉（`PlanEntry.onSettle`）
useChangePlanStore.subscribe((s) => {
  for (const e of Object.values(s.entries)) {
    if (e.onSettle && e.status !== 'generating') {
      const fn = e.onSettle
      useChangePlanStore.setState((cur) => (cur.entries[e.key] ? { entries: { ...cur.entries, [e.key]: { ...cur.entries[e.key], onSettle: undefined } } } : {}))
      try {
        fn()
      } catch {
        /* 清草稿失败不影响方案 */
      }
    }
  }
})
