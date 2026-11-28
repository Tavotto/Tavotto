import { create } from 'zustand'
import { msg } from '@/i18n'
import {
  EngineError,
  createChangePlan,
  applyChangePlanScript,
  discardChangePlan,
  getChangePlan,
  reviseChangePlan,
  restoreChangePlanScript,
  undoChangePlanScript,
  type ChangePlan,
  type ChangePlanGroup,
  type ChangePlanPatch,
  type VectorEditResult,
  type ReviewAnchor,
  type ReviewComment,
} from '@/lib/api'
import { refuseArtifactOperation } from '@/lib/artifactValidation'
import { applyGroups, canonicalPatches, effectiveStatus, samePatches, toOverrides, type PlanSource } from '@/lib/changePlanApply'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { writeChangePlanCache } from '@/lib/changePlanSwitch'
import type { ChangeGroup } from '@/lib/vectorChanges'
import { captureProjectEpoch, type ProjectEpochGuard } from '@/lib/projectEpoch'
import { apiUrlFor, onCurrentProjectChange } from '@/lib/session'
import { releaseOwned } from '@/lib/styleOwned'
import type { PanelObject, PanelOverride } from '@/types/document'
import { currentRunSelection, newRequestId, useAiStore, type AiScope } from './aiStore'
import { scriptHandoffPrompt } from '@/lib/assistantScriptContext'
import { MAX_ASSISTANT_INSTRUCTION } from '@/lib/assistantPlanRoute'
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
  /** `script`：脚本修改方案（ADR 0206 PR 6）；「重新生成」按同一份请求再来 */
  mode?: 'script'
  /**
   * 审阅来源的脚本方案（ADR 0206 修订 2026-11-26，用户点「改脚本做剩下的」才有）：只带来源方案 id 与要转的意见 id，
   * 意见原文由后端取自存档、请求里不带文字
   */
  scriptFrom?: { plan_id: string; comment_ids: string[] }
  /** 卡上 / 气泡里显示的原话（发给后端的 `instruction` 可能带了「图内已改好的部分」交接说明；缺省 = `instruction`） */
  display?: string
}

/** 高级版：在某一版「修改后」的图上写的一条批注（只存本机，不上传审阅服务）；`label` 是元素名 / 「框选」/ 「整体」的显示名 */
export interface PlanNote {
  id: string
  anchor: ReviewAnchor
  label: string
  text: string
}

/** 对数轴下限那类确认的回答：`yes` = 一起改（照方案应用）、`no` = 不改（这一组整组不要） */
export type ConfirmAnswer = 'yes' | 'no'

/** 一张卡的一版：每一版是后端自己的一份方案（各有 id 与两张图），逐组勾选是各版自己的 */
export interface PlanVersionRec {
  planId: string
  plan: ChangePlan
  beforeSrc: string
  afterSrc: string
  unchecked: Record<string, boolean>
  /** 方案要用户确认的组（`group.confirm`）各自的回答；没有 = 还没回答 */
  answers?: Record<string, ConfirmAnswer>
}

/** 一键排版方案卡的两个动作：应用 = 现有的那一次 commit（一步撤销不变）；重来 = 按同样的选项再排一次 */
export interface LayoutHandle {
  apply: () => ApplyResult
  regenerate: () => void
  /** 参与排版的图的张数（卡片一句话用） */
  count: number
}

/**
 * 改图助手改画布矢量对象的方案（ADR 0212 第 6 期，`kind: 'vector'`）：后端算好的方案原样存着（前端不重算），
 * 「应用」与「重新生成」由 `vectorPlanActions` 交进来（同一键排版的 `LayoutHandle`，store 不 import 它免得成环）。
 */
export interface VectorHandle extends LayoutHandle {
  /** 后端回的方案（复述 / 对象 / 核对 / 没能照做的）；生成中 / 失败时还没有 */
  result?: VectorEditResult
  /** 按对象分组、翻成界面语言的改动清单（`lib/vectorChanges`）；空 = 退回显示后端的复述原文 */
  groups?: ChangeGroup[]
  /** 模型给的全部补丁（第 6c 期：卡片按步勾选，取消某几步后用剩下的重算） */
  ops?: unknown[]
  /** 取消勾选后正在重算：「应用」先不可点、对比图跟着更新 */
  busy?: boolean
  /** 因依赖了没勾的那步而自动取消的步（组 key）：清单行下面写一句原因 */
  blocked?: Record<string, true>
  /** 勾选变了：用勾上的步重新算方案（防抖由实现自己管，旧的结果作废） */
  recompute?: (force?: boolean) => void
  /** 上一次按勾选重算没成：「应用」不可点（旧方案里有用户已经勾掉的步），界面给一句灰字和「重试」 */
  failed?: boolean
  /** 方案点名的对象此刻还是不是出发时那样（= 待确认；撤回之后也是；不是又不是已应用 = 「图改过了」） */
  holds: () => boolean
  /** 方案已经写进文档的样子还在不在（= 已应用；按对象状态判，不按历史条目——之后的回写会换掉那一条历史） */
  landed: () => boolean
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
/**
 * 连接本身用不了的不适用码：脚本方案同样要模型（同一条连接），也走不通——图内方案被这些码拒了就**直接**落回旧的直接改脚本，
 * 不再发一次注定失败的脚本方案请求、也不留卡。其余不适用码（太长……）才值得再试脚本方案。
 */
export const CONNECTION_UNUSABLE_CODES: ReadonlySet<string> = new Set(['change_plan_no_connection', 'change_plan_unsupported_connection'])

/**
 * 方案路径不适用、要落回改脚本：带着**这一次调用自己的**后端码作为返回值（不走共享变量——两个面板几乎同时发起时不会串值，
 * 也不会读到上一次留下的旧码）。
 */
export interface Fallthrough {
  kind: 'fallthrough'
  code: string
}
export const isFallthrough = (x: unknown): x is Fallthrough => typeof x === 'object' && x !== null && (x as Fallthrough).kind === 'fallthrough'

export const ASSISTANT_FALLTHROUGH_CODES: ReadonlySet<string> = new Set([
  'change_plan_no_connection',
  'change_plan_unsupported_connection',
  'change_plan_native_unsupported',
  'change_plan_not_editable',
  'invalid_change_plan',
  'change_plan_too_large',
  'change_plan_script_unsupported',
])

export interface PlanEntry {
  /** 本地键 = `client_request_id`（同一请求去重，也是卡片的 React key） */
  key: string
  /** 方案从哪来：审阅意见（默认）/ 改图助手里的一条指令 / 一键排版 */
  from?: 'review' | 'assistant' | 'quick_layout'
  /**
   * `layout` = 一键排版的整版方案（不挂在某张图上，`panelId` 为空）；`vector` = 改图助手改画布矢量对象的方案（同样不挂在某张图上，
   * 一张卡只含一类，ADR 0206）；缺省 = 一张图的方案
   */
  kind?: 'figure' | 'layout' | 'vector'
  /** 一键排版方案的落地与重来（由 `quickLayoutActions` 交进来；store 不 import 它，免得成环） */
  layout?: LayoutHandle
  /** 矢量编辑方案（`kind: 'vector'`）的内容与落地 / 重来 */
  vector?: VectorHandle
  /** 助手来源：用户的原话（对话里的用户气泡；改动清单里这一组的标题） */
  instruction?: string
  /** 助手来源的改脚本后路（见 `ScriptFallback`） */
  fallback?: ScriptFallback
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
  /** 生成 / 重改进行中后端报的阶段（`render_before` → `model` | `agent` → `render_after`，只前进）：卡片的任务清单按它勾步骤 */
  phase?: PlanPhase
  /** 修改前 / 修改后两张图的完整地址（已带项目参数） */
  beforeSrc?: string
  afterSrc?: string
  failure?: PlanFailure
  /** 每个改动组是否被取消勾选（没有的键按勾选算）；多版时是此刻正在看的那一版的 */
  unchecked: Record<string, boolean>
  /** 方案里要用户确认的组各自的回答（组 key → 回答）；多版时是此刻正在看的那一版的 */
  answers?: Record<string, ConfirmAnswer>
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
  /** 脚本方案卡接在哪张卡之后（「改脚本做剩下的」）：同一条指令只画一次用户气泡，这张卡挂在那一轮下面 */
  followsKey?: string
  /** 脚本修改方案：应用 / 撤销在途（防连点） */
  scriptBusy?: boolean
  /** 一次动作没成时卡片上的一句灰字（下一次动作会清掉） */
  notice?: 'apply_failed' | 'undo_failed' | 'restore_failed' | 'revise_stale'
  /** 应用后由这张卡标成「已处理」的意见 id（撤销时只还原这几条） */
  resolvedIds?: string[]
  /** 标已处理 / 还原没成：审阅页签上一句灰字 + 重试（不自动再试，免得失败时反复打后端） */
  resolveFailed?: boolean
  /** 回复草稿：意见 id → 用户改过的文字；null = 已发送（不再出草稿框） */
  drafts: Record<string, string | null>
}

export type PlanPhase = 'render_before' | 'model' | 'agent' | 'render_after'
const PHASE_RANK: Record<PlanPhase, number> = { render_before: 0, model: 1, agent: 1, render_after: 2 }
/** 只前进：轮询乱序 / 旧回包不让已勾的步骤退回去；不认识的阶段名不动 */
export function nextPhase(cur: PlanPhase | undefined, seen: unknown): PlanPhase | undefined {
  if (typeof seen !== 'string' || !(seen in PHASE_RANK)) return cur
  const p = seen as PlanPhase
  return cur && PHASE_RANK[cur] >= PHASE_RANK[p] ? cur : p
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
  return e.plan && e.planId && e.beforeSrc && e.afterSrc ? [{ planId: e.planId, plan: e.plan, beforeSrc: e.beforeSrc, afterSrc: e.afterSrc, unchecked: e.unchecked, answers: e.answers }] : []
}

/** 正在看的是第几版（1 起；按出现顺序数，不看后端的 `version` 字段——从旧版再续改会得到同一个后端版本号） */
export function versionNumber(e: PlanEntry): number {
  const i = versionsOf(e).findIndex((v) => v.planId === e.planId)
  return i < 0 ? (e.plan?.version ?? 1) : i + 1
}

/** 还等着用户回答的确认（对数轴下限 / 脚本方案数据会变）：有就先问、不给「查看 / 应用」；一次只问一个 */
export function pendingConfirm(e: Pick<PlanEntry, 'plan' | 'answers'>): ChangePlanGroup | undefined {
  return e.plan?.groups.find((g) => g.confirm && !e.answers?.[g.key])
}

/** 用户答了「不改」的组（整组不进方案；它那条意见在没处理列表里写一句原因） */
export const declinedGroups = (e: Pick<PlanEntry, 'plan' | 'answers'>): ChangePlanGroup[] => (e.plan?.groups ?? []).filter((g) => e.answers?.[g.key] === 'no')

/** 数据会变的脚本方案答了「先不改」：卡片落成一句灰字（本地码，后端不发） */
export const DATA_DECLINED = 'change_plan_script_data_declined'

/** 这张卡上所有版本（含在途的那一版）的后端方案 id：放弃时一并收掉 */
const allPlanIds = (e: PlanEntry): string[] => [...new Set([...versionsOf(e).map((v) => v.planId), ...(e.planId ? [e.planId] : []), ...(e.revising ? [e.revising] : [])])]

interface ChangePlanState {
  entries: Record<string, PlanEntry>
  /** 创建顺序（旧 → 新） */
  order: string[]
  /** 弹窗里正在看的那一份 */
  dialogKey: string | null
  /** 打开弹窗时指定的初始视图（卡片上的批注入口）；弹窗读到后清掉，不传 = 照旧先看对比 */
  dialogView: 'compare' | 'annotate' | null

  /** 为某张图发起一次方案；同一张图同一批意见的在途 / 待确认方案直接聚焦（返回它的键） */
  requestFromReview: (panelId: string, req: PlanRequest) => Promise<string | null>
  /**
   * 改图助手里的一条指令先出方案（ADR 0206 PR 4）。→ 方案键；`{ kind: 'fallthrough', code }` = 这条路不适用（带这次调用自己的后端码）（没连上能用的模型 / native / 没脚本…，
   * 同步就被拒），调用方照旧走改脚本路径（本函数没留下任何卡）；null = 没发（同一张图已有一份在途 / 图不在了）。
   */
  requestFromAssistant: (
    panelId: string,
    req: { instruction: string; target?: string },
    fallback: ScriptFallback,
  ) => Promise<string | null | Fallthrough>
  /** 把这条助手指令交给现有的改脚本路径（`aiStore.start`）：「改脚本做剩下的」/ 全部都要改脚本时自动续走 / 失败后的「改脚本做」 */
  runScript: (key: string, opts?: { legacyOnly?: boolean }) => Promise<boolean>
  /** 失败后「重试」/ 图改过后「重新生成」：用同一份请求再来一份，旧卡换成新卡 */
  regenerate: (key: string) => Promise<string | null>
  /** 一键排版出的整版方案（`quickLayoutActions` 调）：条目已带方案与两张预览图的 blob 地址，直接是待确认 */
  addLayoutEntry: (e: PlanEntry) => void
  openDialog: (key: string, opts?: { view?: 'compare' | 'annotate' }) => void
  closeDialog: () => void
  /** 弹窗取走初始视图（取一次就清，之后换版本回到对比） */
  consumeDialogView: () => 'compare' | 'annotate' | null
  toggleGroup: (key: string, groupKey: string) => void
  /**
   * 回答方案里的一个确认（对数轴下限）：`yes` = 一起改，照方案应用；`no` = 不改，这一组整组不要（按「前端重算」口径取消勾选、
   * 它那条意见进没处理列表）；没有一组剩下 = 与空方案一样，卡变成「没法直接改图」那张失败卡。
   */
  answerConfirm: (key: string, groupKey: string, answer: ConfirmAnswer) => void
  /** `previewSrc`：弹窗里按勾选组合出的那张图（只应用部分组时给，卡片据此挂实际应用的那一张） */
  apply: (key: string, opts?: { previewSrc?: string }) => ApplyResult
  undo: (key: string) => boolean
  /** 脚本修改方案的应用（后端写脚本，不进文档历史）：对不上方案那一版 = 「图已改过」 */
  applyScript: (key: string) => Promise<ApplyResult>
  /** 脚本修改方案的撤销（按备份还原；⌘Z 不撤脚本） */
  undoScript: (key: string) => Promise<boolean>
  /** 冲突卡（生成期间脚本被改过）上的「恢复到开始前的版本」：用户点了才按起跑快照还原，脚本之后又变了就拒绝 */
  restoreScript: (key: string) => Promise<boolean>
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

/**
 * 元素显示名的出口（`engineLabel`：mathtext 读作 min⁻¹、按界面语言重组）在 `components/` 里，store 不能反向 import 界面层；
 * 由 `changePlan/reviewSync` 在模块加载时交进来。没交（单测）= 原样。
 */
let labeler: (raw: string) => string = (raw) => raw
export function setChangePlanLabeler(fn: (raw: string) => string): void {
  labeler = fn
}

/** 整版 / 画布级的方案（不挂在某张图上）：一键排版与矢量编辑；它们的落地与重来由各自的 handle 管 */
export const isCanvasPlan = (e: Pick<PlanEntry, 'kind'> | undefined): boolean => e?.kind === 'layout' || e?.kind === 'vector'

/** 整版 / 画布级方案的落地与重来 */
export const canvasHandleOf = (e: Pick<PlanEntry, 'kind' | 'layout' | 'vector'>): LayoutHandle | undefined => (e.kind === 'vector' ? e.vector : e.layout)

/** 整版方案的两张预览图是 blob 地址（本机内存里的 PNG）：条目没了就放掉 */
function revokeLayoutImages(e: PlanEntry | undefined): void {
  if (!isCanvasPlan(e) || !e) return
  for (const u of [e.beforeSrc, e.afterSrc]) if (u?.startsWith('blob:')) URL.revokeObjectURL(u)
}

/**
 * 审阅来源的卡上可以「改脚本做剩下的」的意见 id：没处理列表里原因是「要改脚本」、且后端方案里存着原文的那几条。
 * 助手来源 / 一键排版 / 脚本方案卡没有（它们各有自己的路）。
 */
export function reviewScriptIds(e: Pick<PlanEntry, 'from' | 'kind' | 'plan'>): string[] {
  const plan = e.plan
  if (!plan || e.from === 'assistant' || isCanvasPlan(e) || plan.kind === 'script') return []
  const stored = new Set((plan.comments ?? []).map((c) => c.id))
  return [...new Set(plan.unhandled.filter((u) => u.reason === 'needs_script' && u.comment_id && stored.has(u.comment_id)).map((u) => u.comment_id as string))]
}

/** 这份方案一处图内修改都没有、没处理的全是「要改脚本」（助手来源据此把整条指令交给改脚本那条路） */
function allNeedScript(plan: ChangePlan): boolean {
  return plan.groups.length === 0 && plan.unhandled.length > 0 && plan.unhandled.every((u) => u.reason === 'needs_script')
}

/**
 * 这份方案一处图内修改都没有、没处理的全是「不是改图的要求」（聊天 / 提问 / 要解释，ADR 0206 修订 2026-11-27）：
 * 助手来源据此不出卡，把用户那句话原样交给普通助手对话（`runScript` 的聊天分支）。混合情形（有能改的、或还有别的原因）照旧出卡。
 */
export function allOutOfScope(plan: ChangePlan): boolean {
  return plan.kind !== 'script' && plan.groups.length === 0 && plan.unhandled.length > 0 && plan.unhandled.every((u) => u.reason === 'out_of_scope')
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

  /**
   * 「再改一版」的新版到手时撤回上一版的应用：与 `undo` 同一判据（还是我们写的那份、历史栈顶还是那次 commit），
   * 另外用户正拖到一半（文档事务开着）也不撤——撤回会先收掉那一笔手势、再撤掉的就是它了。撤回了 / 本来就已撤回 = true。
   */
  const undoForRevise = (key: string): boolean => {
    const e = get().entries[key]
    const panel = e ? panelOf(e) : null
    if (!e || !panel || !e.plan) return false
    // 等的这段时间里用户自己点了「撤销」：图已经回到改前，没有要撤的
    if (samePatches(panel.overrides, e.plan.base_patches)) return true
    if (useDocumentStore.getState().txn || !canUndoApplied(e, panel.overrides)) return false
    return get().undo(key)
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
      if (view.state === 'running') {
        const ph = nextPhase(e?.phase, view.phase)
        if (ph !== e?.phase) patch(key, { phase: ph })
        continue
      }
      if (view.state === 'failed') {
        // 助手来源：这条路不适用（线程里才解析出连接不支持结构化调用 / 没连上……）与同步被拒同一处理——不留失败卡，
        // 接着走现有的改脚本路径，用户的指令照旧交给 Agent（出的是改脚本那一轮自己的结果 / 错误卡）
        if (!revising && e?.from === 'assistant' && ASSISTANT_FALLTHROUGH_CODES.has(view.code)) {
          if ((view as { replay_required?: boolean }).replay_required) replay(e)
          // 脚本方案自己被拒（别再试脚本方案，免得循环）/ 连接用不了（脚本方案同样走不通）：直接落回旧路径
          void get().runScript(key, { legacyOnly: e.request.mode === 'script' || CONNECTION_UNUSABLE_CODES.has(view.code) })
          return
        }
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
          // 已应用的卡（「再改一版」）：新版到手这一刻才撤回上一版的应用；撤不回（期间又改过这张图）就不动文档，
          // 新版照样入列，卡片按文档派生成「原图改过了」，另说一句
          const stale = e.status === 'applied' && !undoForRevise(key)
          const e0 = get().entries[key] ?? e
          const prev = versionsOf(e).map((v) => (v.planId === e.planId ? { ...v, unchecked: e.unchecked, answers: e.answers } : v))
          const rec: PlanVersionRec = { planId, plan, beforeSrc: src(plan.preview.before), afterSrc: src(plan.preview.after), unchecked: {}, answers: {} }
          patch(key, { versions: [...prev, rec], planId, plan, beforeSrc: rec.beforeSrc, afterSrc: rec.afterSrc, unchecked: {}, answers: {}, notes: notesOf(e0).filter((n) => e0.sentNotes?.[n.id] !== n.text), sentNotes: undefined, revising: undefined, reviseError: undefined, notice: stale ? 'revise_stale' : undefined, ...(e.status === 'applied' ? { status: 'ready' as const, appliedPartial: undefined, appliedSrc: undefined } : {}) })
        }
        if ((view as { replay_required?: boolean }).replay_required) replay(e)
        return
      }
      // 助手来源：整条指令都要改脚本（没有一处图内修改、没有别的原因）= 不算失败，接着走现有的改脚本路径
      // 助手来源：整条都不是改图的要求（聊天 / 提问）= 不出卡，悄悄交给普通助手对话（不走脚本方案、不加交接句）
      if (e.from === 'assistant' && view.code === 'change_plan_empty' && allOutOfScope(plan)) {
        if ((view as { replay_required?: boolean }).replay_required) replay(e)
        patch(key, { plan })
        void get().runScript(key)
        return
      }
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

  /**
   * 还没应用的图内修改先应用（同一次 commit、正常进时间线）再改脚本：脚本一改图源就变了，待确认的方案会判「图已改过」再也应用不了。
   * 应用没成（图已改过 / 写不进）→ null（不续走，卡上说一句）；否则回「有无图内修改」与取此刻有效状态的函数。
   */
  const applyPendingFirst = (key: string, e: PlanEntry): { hasChanges: boolean; statusNow: () => PlanStatus } | null => {
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
        return null
      }
      if (st === 'ready' && get().apply(key) !== 'applied') return null
    }
    return { hasChanges, statusNow }
  }

  /**
   * 审阅来源的「改脚本做剩下的」（ADR 0206 修订 2026-11-26）：只由用户点按钮触发；请求只带来源方案 id + 要转的意见 id，
   * 意见文字由后端取自存档的方案（外来文本不经前端再传一遍）。出一张新的脚本方案卡（`mode: 'script'`），挂在原卡后面。
   */
  const runReviewScript = async (key: string): Promise<boolean> => {
    const e = get().entries[key]
    const ids = e ? reviewScriptIds(e) : []
    if (!e || !e.planId || !ids.length || e.scriptStarted) return false
    const gate = e.panelId
    if (pending.has(gate) || get().order.some((k) => get().entries[k]?.panelId === gate && get().entries[k]?.status === 'generating')) return false
    pending.add(gate)
    try {
      if (!applyPendingFirst(key, e)) return false
      const out = await start(e.panelId, { mode: 'script', scriptFrom: { plan_id: e.planId, comment_ids: ids }, ...(e.request.share_id ? { share_id: e.request.share_id } : {}) })
      if (isFallthrough(out) || !out) return false
      patch(out, { followsKey: key })
      patch(key, { scriptStarted: true })
      return true
    } finally {
      pending.delete(gate)
    }
  }

  const start = async (panelId: string, req: PlanRequest, fallback?: ScriptFallback, keepOnFallthrough = false): Promise<string | null | Fallthrough> => {
    const ds = useDocumentStore.getState()
    const panel = ds.doc.objects.find((o): o is PanelObject => o.id === panelId && o.type === 'panel')
    if (!panel) return null
    const key = newRequestId()
    const guard = captureProjectEpoch()
    // 只给了分享记录 id（后端取最新版本的未处理意见）：界面上的编号 / 称呼取审阅页签里同一份记录的未处理意见
    const detail = useReviewStore.getState().detail
    const from = req.scriptFrom
    const source = from ? Object.values(get().entries).find((x) => allPlanIds(x).includes(from.plan_id)) : undefined
    const comments = from
      ? (source?.comments ?? []).filter((c) => from.comment_ids.includes(c.id))
      : (req.comments ?? (req.share_id && detail?.share_id === req.share_id ? detail.comments.filter((c) => !c.resolved) : []))
    const assistant = typeof req.instruction === 'string'
    const entry: PlanEntry = {
      key,
      ...(assistant ? { from: 'assistant' as const, instruction: req.display ?? req.instruction, fallback } : {}),
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
        return { kind: 'fallthrough', code: 'change_plan_not_editable' }
      }
      fail(key, 'change_plan_not_editable')
      return key
    }
    try {
      const task = await createChangePlan({
        id: panel.fileId,
        patches: panel.overrides,
        ...(assistant
          ? { source: 'assistant' as const, instruction: req.instruction, ...(req.target ? { target: req.target } : {}), ...(req.mode === 'script' ? { mode: 'script' as const } : {}) }
          : req.scriptFrom
            ? { source: 'review' as const, mode: 'script' as const, from_plan: req.scriptFrom.plan_id, comment_ids: req.scriptFrom.comment_ids }
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
        return { kind: 'fallthrough', code }
      }
      fail(key, code, err instanceof EngineError ? err.params : undefined)
    }
    return key
  }

  return {
    entries: {},
    order: [],
    dialogKey: null,
    dialogView: null,

    requestFromReview: async (panelId, req) => {
      const same = (e: PlanEntry) =>
        e.panelId === panelId &&
        (e.status === 'generating' || e.status === 'ready') &&
        !e.request.scriptFrom &&
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
        const out = await start(panelId, req)
        return isFallthrough(out) ? null : out // 审阅来源没有「落回改脚本」
      } finally {
        pending.delete(gate)
      }
    },

    requestFromAssistant: async (panelId, req, fallback) => {
      // 同一时刻只发一份（与审阅来源共用这把门）；同一张图已有在途方案时不再起第二份
      const busy = get().order.some((k) => get().entries[k]?.panelId === panelId && get().entries[k]?.status === 'generating')
      if (busy || pending.has(panelId)) return null
      pending.add(panelId)
      try {
        const out = await start(panelId, req, fallback, false)
        if (!isFallthrough(out)) return out
        // 连接本身用不了：脚本方案同样要模型，走不通——直接落回旧路径，不发注定失败的脚本方案请求
        if (CONNECTION_UNUSABLE_CODES.has(out.code)) return out
        // 图内方案这条路不适用：先试脚本方案（Agent 在副本里改、看过再应用）；它也不适用才落回现有的直接改脚本（调用方发）
        return await start(panelId, { instruction: req.instruction, ...(req.target ? { target: req.target } : {}), mode: 'script' }, fallback, false)
      } finally {
        pending.delete(panelId)
      }
    },

    runScript: async (key, opts) => {
      const e = get().entries[key]
      // 审阅来源：只能由用户点按钮走到这里（没有任何自动续走），交给「审阅意见 → 脚本方案」
      if (e && e.from !== 'assistant' && !isCanvasPlan(e)) return runReviewScript(key)
      const fb = e?.fallback
      if (!e || !fb || e.from !== 'assistant') return false
      // 发送那一刻的项目 / 文档 / 面板：途中换了就不替他发（换项目整表已清；换文档 / 画布 / 面板被删这里挡）
      let panel = panelOf(e)
      const ai = useAiStore.getState()
      if (!panel || panel.fileId !== fb.fileId || ai.generation !== fb.generation) {
        fail(key, 'change_plan_script_failed')
        return false
      }
      // 聊天 / 提问（整条都是 out_of_scope）：没有图内修改要先应用、不再试脚本方案，原话直接交给普通助手对话；发出去了就把这张卡撤掉
      if (e.plan && allOutOfScope(e.plan)) {
        try {
          await ai.start({
            project: fb.project,
            generation: fb.generation,
            prompt: fb.prompt,
            fileId: fb.fileId,
            panelId: fb.panelId,
            gid: fb.gid,
            label: fb.label,
            scope: fb.scope,
            target: fb.target,
            overrides: panel.overrides,
            canvas: fb.canvas,
            script: fb.script,
            chat: true,
          })
        } catch {
          patch(key, { status: 'failed', failure: { code: 'change_plan_script_failed' } })
          return false
        }
        drop(key)
        return true
      }
      // 还没应用的图内修改先应用（同一次 commit、正常进时间线）再改脚本：脚本一改图源就变了，待确认的方案会判「图已改过」再也应用不了，
      // 图内那部分就丢了。应用没成（图已改过 / 写不进）= 不续走，卡上说一句
      // 与卡片 / `apply()` 同一个有效状态（⌘Z 撤回过的「已应用」又是待确认，存着的 `e.status` 不算数）
      const pre = applyPendingFirst(key, e)
      if (!pre) return false
      panel = panelOf(e) ?? panel
      const { hasChanges, statusNow } = pre
      // 上下文按**实际**情况写：真正落在文档里的才写「已改好」
      const appliedNow = hasChanges && statusNow() === 'applied'
      // 图内已经改（或待用户应用）的部分，确定性地写进指令：Agent 不在脚本里重做它们（`lib/assistantScriptContext`）
      const manifest = exactPanelManifest(useRenderStore.getState(), panel)
      const labels = new Map((manifest?.elements ?? []).map((x) => [x.gid, x.label ? labeler(x.label).trim() : '']))
      const prompt = scriptHandoffPrompt(fb.prompt, e.plan, {
        applied: appliedNow,
        unchecked: e.unchecked,
        labelOf: (gid) => labels.get(gid) || undefined,
      })
      // 脚本修改也先出方案卡（ADR 0206 PR 6）：Agent 在副本里改、渲染出对比，看过再应用；这条路不适用（没连上模型 / native…）就落回现有的直接改脚本
      const out: string | null | Fallthrough = opts?.legacyOnly ? { kind: 'fallthrough', code: 'legacy_only' } : await start(e.panelId, { instruction: prompt.length <= MAX_ASSISTANT_INSTRUCTION ? prompt : fb.prompt, display: fb.prompt, ...(e.request.target ? { target: e.request.target } : {}), mode: 'script' }, fb)
      if (!isFallthrough(out)) {
        if (out) patch(out, { followsKey: key })
        patch(key, e.plan && e.plan.groups.length > 0 ? { scriptStarted: true } : { status: 'handoff', scriptStarted: true, failure: undefined })
        return out !== null
      }
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
      if (isCanvasPlan(e)) {
        drop(key)
        canvasHandleOf(e)?.regenerate()
        return null
      }
      const ids = allPlanIds(e)
      drop(key)
      for (const id of ids) void discardChangePlan(id).catch(() => undefined)
      // 助手来源：「重新生成」不再悄悄绕去旧路径——这条路不适用时留一张带「改脚本做」的失败卡
      const out = await start(e.panelId, e.request, e.fallback, e.from === 'assistant')
      return isFallthrough(out) ? null : out
    },

    // 还有没回答的确认（对数轴下限）就不让看 / 应用：先问清楚，免得出一张坍掉的图
    openDialog: (key, opts) => {
      const e = get().entries[key]
      if (e && pendingConfirm(e)) return
      set({ dialogKey: key, dialogView: opts?.view ?? null })
    },
    closeDialog: () => set({ dialogKey: null, dialogView: null }),
    consumeDialogView: () => {
      const v = get().dialogView
      if (v) set({ dialogView: null })
      return v
    },
    toggleGroup: (key, groupKey) => {
      set((s) => {
        const e = s.entries[key]
        return e ? { entries: { ...s.entries, [key]: { ...e, unchecked: { ...e.unchecked, [groupKey]: !e.unchecked[groupKey] } } } } : {}
      })
      // 矢量编辑方案：取消 / 勾回某一步，按勾上的步重新算（对象、对比图、核对都跟着变）
      get().entries[key]?.vector?.recompute?.()
    },

    answerConfirm: (key, groupKey, answer) => {
      const e = get().entries[key]
      const g = e?.plan?.groups.find((x) => x.key === groupKey)
      if (!e || !g?.confirm || e.answers?.[groupKey]) return
      const answers = { ...e.answers, [groupKey]: answer }
      const unchecked = answer === 'no' ? { ...e.unchecked, [groupKey]: true } : e.unchecked
      const remains = (e.plan?.groups ?? []).some((x) => answers[x.key] !== 'no')
      patch(key, answer === 'no' && !remains ? { answers, unchecked, status: 'failed', failure: { code: g.confirm.kind === 'script_data_changed' ? DATA_DECLINED : 'change_plan_empty' } } : { answers, unchecked })
    },

    addLayoutEntry: (entry) => set((s) => ({ entries: { ...s.entries, [entry.key]: entry }, order: [...s.order, entry.key] })),

    apply: (key, opts) => {
      const e = get().entries[key]
      const plan = e?.plan
      if (!e || !plan) return 'failed'
      if (pendingConfirm(e)) return 'nothing'
      if (plan.kind === 'script') {
        void get().applyScript(key)
        return 'nothing'
      }
      // 一键排版：落地就是现有的那一次 commit（核对「图还是出发时那样」也在里面）；对不上 = 「图已改过」，不写
      if (isCanvasPlan(e)) {
        const h = useDocumentStore.getState()
        // ⌘Z 撤回过的「已应用」又是待确认：可以再应用（派生状态见 `effectiveStatus`；矢量编辑按对象状态判，不按历史条目）
        const st = e.kind === 'vector' ? (e.vector?.holds() ? 'ready' : 'stale') : effectiveStatus(e, null, null, { past: h.past, future: h.future })
        if (st === 'stale') {
          patch(key, { status: 'stale', notice: undefined })
          return 'stale'
        }
        const handle = canvasHandleOf(e)
        if (st !== 'ready' || !handle) return 'failed'
        const r = handle.apply()
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

    applyScript: async (key) => {
      const e = get().entries[key]
      if (!e?.plan || e.plan.kind !== 'script' || !e.planId || e.scriptBusy) return 'failed'
      const panel = panelOf(e)
      const st = effectiveStatus(e, panel?.overrides ?? null, panel ? sourceOf(panel) : null)
      if (st === 'stale') {
        patch(key, { status: 'stale', notice: undefined })
        return 'stale'
      }
      if (st !== 'ready') return 'failed'
      // 数据会变的方案：回答「就这样改」之前不写；回答了就把确认带给后端（后端不信前端，没带就拒）
      const ask = e.plan.groups.find((g) => g.kind === 'script' && g.confirm?.kind === 'script_data_changed')
      if (ask && e.answers?.[ask.key] !== 'yes') return 'failed'
      const guard = captureProjectEpoch()
      patch(key, { scriptBusy: true, notice: undefined })
      try {
        const applied = ask ? await applyChangePlanScript(e.planId, { confirmDataChange: true }) : await applyChangePlanScript(e.planId)
        if (!guard.still() || !get().entries[key]) return 'failed'
        patch(key, { status: 'applied', scriptBusy: false, notice: undefined, plan: { ...e.plan, applied } })
        set((s) => (s.dialogKey === key ? { dialogKey: null } : {}))
        return 'applied'
      } catch (err) {
        if (!guard.still() || !get().entries[key]) return 'failed'
        // 脚本在生成之后被改过：不写，卡片转「图已改过」+「重新生成」；其余（备份写不进 / 替换失败）一句灰字，脚本没动
        if (err instanceof EngineError && err.code === 'change_plan_figure_changed') {
          patch(key, { status: 'stale', scriptBusy: false, notice: undefined })
          return 'stale'
        }
        patch(key, { scriptBusy: false, notice: 'apply_failed' })
        return 'failed'
      }
    },

    undoScript: async (key) => {
      const e = get().entries[key]
      if (!e?.plan || e.plan.kind !== 'script' || !e.planId || e.status !== 'applied' || e.scriptBusy) return false
      const guard = captureProjectEpoch()
      patch(key, { scriptBusy: true, notice: undefined })
      try {
        await undoChangePlanScript(e.planId)
        if (!guard.still() || !get().entries[key]) return false
        // 脚本回到改前：同一份方案又是待确认（应用时后端仍会核基线）
        patch(key, { status: 'ready', scriptBusy: false, notice: undefined, plan: { ...e.plan, applied: null } })
        return true
      } catch {
        if (!guard.still() || !get().entries[key]) return false
        patch(key, { scriptBusy: false, notice: 'undo_failed' })
        return false
      }
    },

    restoreScript: async (key) => {
      const e = get().entries[key]
      const p = e?.failure?.params
      if (!e || e.scriptBusy || typeof p?.backup !== 'string' || typeof p?.script !== 'string' || typeof p?.current_sha256 !== 'string') return false
      const guard = captureProjectEpoch()
      patch(key, { scriptBusy: true, notice: undefined })
      try {
        await restoreChangePlanScript({ backup: p.backup, script: p.script, expect_sha256: p.current_sha256 })
        if (!guard.still() || !get().entries[key]) return false
        get().dismiss(key)
        return true
      } catch {
        if (!guard.still() || !get().entries[key]) return false
        patch(key, { scriptBusy: false, notice: 'restore_failed' })
        return false
      }
    },

    undo: (key) => {
      const e = get().entries[key]
      if (e?.plan?.kind === 'script') {
        void get().undoScript(key)
        return true
      }
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
      if (isCanvasPlan(e)) return drop(key)
      patch(key, { status: 'discarded' })
      set((s) => (s.dialogKey === key ? { dialogKey: null } : {}))
      // 后端那几份（各版）可以收了；卡上的图仍是已经取到的地址，过期也只是图不显示，卡片文字不变
      for (const id of allPlanIds(e)) void discardChangePlan(id).catch(() => undefined)
    },

    revise: async (key) => {
      const e = get().entries[key]
      const notes = e ? notesOf(e) : []
      if (!e || !e.planId || (e.status !== 'ready' && e.status !== 'applied')) return 'failed'
      if (e.revising !== undefined) return 'busy'
      if (!notes.length) return 'nothing'
      if (e.status === 'applied') {
        // 已应用的卡批注重改：此刻就撤不回（之后又改过这张图）= 不发请求、什么都不动，免得白等一版。
        // 撤得回也**先不撤**：图保持已应用，新版拿到时才撤回这一次（`poll` 的 revise 分支），没拿到就什么都不丢
        const panel = panelOf(e)
        if (!panel || !canUndoApplied(e, panel.overrides)) {
          patch(key, { reviseError: { code: REVISE_UNDO_BLOCKED } })
          return 'failed'
        }
      }
      const guard = captureProjectEpoch()
      patch(key, { revising: '', phase: undefined, reviseError: undefined, sentNotes: Object.fromEntries(notes.map((n) => [n.id, n.text])), noteTexts: { ...e.noteTexts, ...Object.fromEntries(notes.map((n) => [n.id, n.text])) } })
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
        const recs = versionsOf(e).map((v) => (v.planId === e.planId ? { ...v, unchecked: e.unchecked, answers: e.answers } : v))
        const to = recs.find((v) => v.planId === planId)
        if (!to) return {}
        return { entries: { ...s.entries, [key]: { ...e, versions: recs, planId, plan: to.plan, beforeSrc: to.beforeSrc, afterSrc: to.afterSrc, unchecked: to.unchecked, answers: to.answers ?? {}, reviseError: undefined } } }
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

/** 已应用的卡批注重改时撤不回这一次应用（本地码，弹窗里一句灰字） */
export const REVISE_UNDO_BLOCKED = 'revise_undo_blocked'

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
