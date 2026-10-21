import { effectiveOverride } from '@/lib/effectiveOverride'
import { ArtifactValidationError, adoptionGuardOf } from '@/lib/artifactValidation'
import { rejectArtifactRenders, renderKeyOf } from './renderStore'
import type { PanelObject } from '@/types/document'
/**
 * 导出作业的编排 —— **全产品只有这一条链**（ADR 0031）。
 *
 * ```text
 * prepareExport(input)   请求成形 + 就地校验（不发网络）
 * validateExport(input)  真的开始之前能看出来的问题（重名 / 目录写不了）
 * runExport(input)       起作业 → 跟进度 → 落最终状态
 * cancelExport()         取消（清临时文件；最终目录一个字节没动过）
 * ```
 *
 * ### 三件被刻意分开的事
 *
 * **1. 作业活在 store 里，不活在对话框里。** 关掉导出对话框**不取消**已经
 * 起来的作业（§九），所以进度不能挂在一个会被卸载的组件上。
 *
 * **2. SSE 是加速器，不是唯一通道。** 进度经 `export.progress` 推过来，但
 * 浏览器演练场、断线、以及任何 SSE 没连上的场合都必须照样能拿到终局——
 * 所以还有一条轮询。两条路进的是同一个 `applyJob()`，晚到的旧快照按
 * `job_id` + 终局状态挡掉，不会把已完成的作业倒回"进行中"。
 *
 * **3. 失败保留用户设置。** `lastInput` 一直留着，重试不需要用户把文件名、
 * 格式、PPI 重新填一遍（§九）。
 */
import { create } from 'zustand'
import { msg, type UiMessage } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import {
  ApiError,
  backendErrorMsg,
  cancelExport as apiCancel,
  engineErrorMsg,
  exportState,
  startExport,
  validateExport as apiValidate,
  type ExportJob,
  type ExportValidation,
} from '@/lib/api'
import { buildExportRequest, type ExportRequestInput } from '@/lib/exportRequest'
import { getOriginalOutputSpec } from '@/lib/originalSpec'
import { findFigurePanel, useDocumentStore } from '@/store/documentStore'
import type { FilenameReason } from '@/lib/exportName'
import { filenameProblem } from '@/lib/exportRequest'
import { captureMoment, markMoment, type MomentSnapshot } from '@/lib/timelineCheckpoint'

/** 轮询间隔。SSE 通的时候它几乎不出场；不通的时候它是唯一的通道 */
const POLL_MS = 600

/**
 * 终局状态：到了这里就不再轮询，晚到的推送也不再改状态。
 *
 * **`unknown` 必须在这里面。** 后端重启或作业过期之后 `/api/export/state`
 * 回的就是它；不当终局的话 `running` 永远是 true、轮询每 600ms 问一次一个
 * 不存在的作业，而对话框停在"进行中"再也出不来（PR #214 评审）。
 * 它与 `failed` 是两件事——**我们不知道那些文件写出来没有**，界面得这么说。
 */
const TERMINAL = new Set(['done', 'partial', 'failed', 'cancelled', 'conflict', 'unknown'])

export type PreparedExport = (
  | (ReturnType<typeof buildExportRequest> & { error: null })
  | { request: null; names: []; revision: null; error: ArtifactValidationError }
) & {
  /** 文件名不合法的原因；合法为 null。**在输入的那一刻就知道**（§六） */
  filenameProblem: FilenameReason | null
}

/**
 * 「每张图单独导出」的一批作业（2026-10-21）。**后端协议不变**：一次作业仍只导一张原图，
 * 批 = 对话框按图依次发多个普通作业（每张各自按它自己的原图口径 preflight / proof report，
 * 走的就是单张导出的那条路）；这里只记**进度与各张的结果**，并在收尾时合成一份汇总作业
 * 给结果块读（`aggregateBatch`）。
 */
export interface ExportBatch {
  /** 要导的图（figureId），按顺序 */
  ids: string[]
  /** 各张的显示名（与 `ids` 对齐；进度句与失败行用） */
  names: string[]
  /**
   * 两个阶段：**prepare** 把每张图的完整请求输入（格式 / PPI / 透明 / 严格核验 / 规范 / 文档快照 / 各自的原图口径检查与留档）
   * 在点「导出」那一刻一次冻结下来；**send** 只按冻结的输入依次发——之后用户改控件、改排版、关了对话框，
   * 同一次点击里后面的图也还是点击那一刻的设置和内容（Codex #1295 P1）。
   */
  phase: 'prepare' | 'send'
  /** 已冻结的各张：`pos` = 它在 `ids` 里的位置（进度句用） */
  prepared: { pos: number; name: string; input: ExportRequestInput }[]
  /** send 阶段正在导 / 即将导的是 `prepared` 里第几张（0 起） */
  index: number
  /** 已收尾的各张作业（落了终局的；撞名停住的那一张不在里面，等用户决定后重发） */
  results: ExportJob[]
  /** 与 `results` 对齐的图名（一张图的作业没有任何产物行却失败了时，结果里要点得出是哪张） */
  resultNames: string[]
  /** 根本没起来的几张（文件名不合法 / 原图此刻导不出 / 起作业失败）：名字 + 那条报错的一句话（批结束后仍可见） */
  failedStarts: { name: string; reason: string }[]
  /** 用户点了取消：当前这张取消、剩余的不再发 */
  cancelRequested: boolean
  /** 收尾了：`aggregate` 是汇总作业 */
  done: boolean
  aggregate: ExportJob | null
}

interface ExportState {
  batch: ExportBatch | null
  /** 当前（或最近一次）作业的完整快照。**不是增量** */
  job: ExportJob | null
  /** 有没有作业在飞 */
  running: boolean
  /** 起作业本身失败了（网络 / 请求不合法）；作业内部的失败在 `job.error` 里 */
  startError:
    | { code: 'bad_filename'; message: FilenameReason }
    | { code: 'start_failed'; message: UiMessage }
    | null
  pollError: UiMessage | null
  refreshing: boolean
  cancelError: UiMessage | null
  cancelState: 'idle' | 'pending' | 'requested'
  /** 上一次的输入。失败之后重试用它，用户不必重填 */
  lastInput: ExportRequestInput | null
  /**
   * 导出**开始那一刻**的文档快照指纹。完成时与当时的文档一比，
   * 就能说出「导出期间这份文档又被改过」（§三）。
   */
  startedRevision: string | null
  /** 完成时文档已经不是导出时那一份了 */
  editedDuringExport: boolean
  /**
   * **这个标签页自己起的那个作业**。
   *
   * `export.progress` 是**项目级广播**：同一个项目的另一个标签页在导出时，
   * 它的快照也会推到这里来。没有归属判据的话，本标签页会把别人的进度与结果
   * 显示成自己的；`resetExportState()` 之后一个在飞的轮询也能把状态再填回去
   * （PR #214 第四轮评审）。
   *
   * `null` = 这个标签页此刻没有自己的作业，**任何快照都不收**。
   */
  ownedJobId: string | null
  /**
   * 起这次导出那一刻的快照（`captureMoment`，文档取导出请求里的那一份）。导出要跑很久：
   * 完成时用户可能已经在同项目里换了排版（换排版不调 `resetExportState`）——「导出」点只
   * 打给被导出的那一份；也可能在同一份里接着改——节点拍的是被导出的内容，不是之后的样子
   * （Codex #679）。
   *
   * **用完即放**：快照里是整份文档与各面板的 SVG 图源，大排版能到几 MB。作业一进终局
   * （打完点或不打）、换项目 / 换文档（`resetExportState`）、起作业本身失败，都立刻清掉——
   * 全局 store 不该替上一个项目留着这些。
   */
  momentSnapshot: MomentSnapshot | null
}

export const useExportStore = create<ExportState>(() => ({
  batch: null,
  job: null,
  running: false,
  startError: null,
  pollError: null,
  refreshing: false,
  cancelError: null,
  cancelState: 'idle',
  lastInput: null,
  startedRevision: null,
  editedDuringExport: false,
  ownedJobId: null,
  momentSnapshot: null,
}))

/** 把一批作业的结果合成一份汇总作业：结果块（`ResultBlock`）按一个作业的口径读它，不另起一套 */
export function aggregateBatch(
  b: Pick<ExportBatch, 'results' | 'resultNames' | 'failedStarts' | 'cancelRequested'>,
): ExportJob {
  const failedRow = (name: string, reason?: string): ExportJob['outputs'][number] => ({
    format: '',
    name,
    url: null,
    bytes: null,
    dimensions: { px: null, mm: null },
    vector: false,
    status: 'failed',
    replaced: false,
    // 有具体原因的用 `batch_start_failed`（原样显示那句话），没有的退回通用失败
    error: reason ? { code: 'batch_start_failed', params: { message: reason } } : { code: 'export_failed', params: {} },
  })
  const outputs = [
    ...b.results.flatMap((j, i) =>
      // 作业失败 / 丢了却没有任何产物行：补一行点名是哪张，汇总的失败数才对得上
      (j.status === 'failed' || j.status === 'unknown') && j.outputs.length === 0
        ? [failedRow(b.resultNames[i] ?? '')]
        : j.outputs,
    ),
    ...b.failedStarts.map((f) => failedRow(f.name, f.reason)),
  ]
  const written = outputs.some((o) => o.status !== 'failed' && !!o.name)
  const bad =
    b.failedStarts.length > 0 || b.results.some((j) => j.status !== 'done' && j.status !== 'cancelled')
  const status: ExportJob['status'] =
    b.cancelRequested || b.results.some((j) => j.status === 'cancelled')
      ? written
        ? 'partial'
        : 'cancelled'
      : !bad
        ? 'done'
        : written
          ? 'partial'
          : 'failed'
  const last = b.results[b.results.length - 1]
  return {
    job_id: last?.job_id ?? 'batch',
    status,
    outputs,
    warnings: b.results.flatMap((j) => j.warnings ?? []),
    conflicts: [],
    export_dir: [...b.results].reverse().find((j) => j.export_dir)?.export_dir,
    error: b.results.find((j) => j.error)?.error ?? null,
  }
}

function finishedBatch(b: ExportBatch): ExportBatch {
  return { ...b, done: true, aggregate: aggregateBatch(b) }
}

/** 开一批。已有作业在飞 / 已有一批没收尾 / 没有图 → 不开 */
export function beginExportBatch(ids: string[], names: string[]): boolean {
  const s = useExportStore.getState()
  if (s.running || (s.batch && !s.batch.done) || ids.length === 0) return false
  useExportStore.setState({
    batch: {
      ids,
      names,
      phase: 'prepare',
      prepared: [],
      index: 0,
      results: [],
      resultNames: [],
      failedStarts: [],
      cancelRequested: false,
      done: false,
      aggregate: null,
    },
  })
  return true
}

/**
 * prepare 阶段收尾：整批的完整请求输入**在同一个同步块里**一次算好（`buildBatchInput`，中间没有任何 await），
 * 这里一次记下——冻结好的转 send，没法冻结的（文件名不合法 / 原图导不出）记成失败；一张都冻结不了就直接收尾。
 */
export function recordBatchFrozen(
  items: ({ ok: true; pos: number; name: string; input: ExportRequestInput } | { ok: false; name: string; reason: string })[],
): void {
  const b = useExportStore.getState().batch
  if (!b || b.done || b.phase !== 'prepare') return
  const prepared = items.flatMap((i) => (i.ok ? [{ pos: i.pos, name: i.name, input: i.input }] : []))
  const failedStarts = items.flatMap((i) => (i.ok ? [] : [{ name: i.name, reason: i.reason }]))
  const next: ExportBatch = { ...b, prepared, failedStarts: [...b.failedStarts, ...failedStarts] }
  useExportStore.setState({
    batch: b.cancelRequested || prepared.length === 0 ? finishedBatch(next) : { ...next, phase: 'send', index: 0 },
  })
}

/** send 阶段：当前这张落了终局（撞名停住的不走这里）：记下结果、换下一张；取消了或已是最后一张就收尾 */
export function recordBatchJob(job: ExportJob): void {
  const b = useExportStore.getState().batch
  if (!b || b.done || b.phase !== 'send') return
  advanceSend({ ...b, results: [...b.results, job], resultNames: [...b.resultNames, b.prepared[b.index]?.name ?? ''] })
}

/** send 阶段：这一张起作业本身失败了（校验被拒 / 网络断）：记下名字和那条报错的一句话，继续下一张 */
export function recordBatchStartFailure(reason = ''): void {
  const b = useExportStore.getState().batch
  if (!b || b.done || b.phase !== 'send') return
  advanceSend({ ...b, failedStarts: [...b.failedStarts, { name: b.prepared[b.index]?.name ?? '', reason }] })
}

function advanceSend(b: ExportBatch): void {
  const next = { ...b, index: b.index + 1 }
  useExportStore.setState({
    batch: next.index >= next.prepared.length || next.cancelRequested ? finishedBatch(next) : next,
  })
}

/** 取消：当前这张由 `cancelCurrentExport` 取消，这里让剩余的不再发；还没起的那一刻也能收尾 */
export function requestBatchCancel(): void {
  const s = useExportStore.getState()
  const b = s.batch
  if (!b || b.done) return
  const next = { ...b, cancelRequested: true }
  // 此刻没有作业在飞（冻结阶段 / 两张之间 / 撞名停住）：直接收尾，已写出的如实报
  useExportStore.setState({ batch: s.running ? next : finishedBatch(next) })
}

/** 请求成形 + 就地校验。**不发网络**，输入框每敲一个字都可以调。 */
export function prepareExport(input: ExportRequestInput): PreparedExport {
  const problem = filenameProblem(input.filename, input.formats)
  try {
    return { ...buildExportRequest(input), error: null, filenameProblem: problem }
  } catch (error) {
    if (!(error instanceof ArtifactValidationError)) throw error
    return { request: null, names: [], revision: null, error, filenameProblem: problem }
  }
}

/** 真的开始之前能看出来的问题（重名、目录写不写得了、PPI 有没有意义）。 */
export async function validateExportRequest(
  input: ExportRequestInput,
): Promise<ExportValidation | null> {
  try {
    const prepared = prepareExport(input)
    return prepared.error ? null : await apiValidate(prepared.request)
  } catch {
    // 校验拿不到答案 ≠ 没有问题。回 null，界面据此**不说**"一切正常"
    return null
  }
}

let pollTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 代次。**每一次「这个标签页换了个上下文」都 +1**：起一次新导出、
 * 或者 `resetExportState()`（换项目 / 换文档）。
 *
 * `ownedJobId` 挡得住"别人的作业"，挡不住**一个还在 await 里的
 * `/api/export/start`**：用户在它返回之前切了项目，`resetExportState()` 清了
 * 归属，而那个 continuation 回来之后会**无条件地重新认领**旧项目的作业，
 * 于是新项目里显示着旧项目的结果，链接还被补上了新项目的 pj
 * （PR #214 第五轮评审）。
 *
 * 代次在 await **之前**取，回来一比就知道自己是不是已经作废了。
 */
let generation = 0

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

/**
 * 收下一份作业快照（SSE 或轮询来的都走这里）。
 *
 * **晚到的旧快照挡掉**：同一个作业已经进终局之后，一条在网络上多绕了两圈的
 * "running" 会把界面倒回进行中，用户于是看着一个永远转不完的圈。
 */
function rejectExportSources(input: ExportRequestInput | null, reason: string, params?: Record<string, unknown>): void {
  if (!input) return
  const panels = input.scope === 'original' ? (input.panel ? [input.panel] : []) : input.doc.objects.filter((o): o is PanelObject=>o.type==='panel')
  for (const p of panels) {
    const guard = adoptionGuardOf(p)
    if (!guard) continue
    if (reason === 'background_visibility_required' &&
        (params?.file_id !== p.fileId || params?.bytes_sha256 !== guard.bytesSha256 || params?.size_bytes !== guard.sizeBytes ||
         !effectiveOverride(p.overrides, 'figure', 'facecolor') || typeof effectiveOverride(p.overrides, 'figure', 'transparent')?.value === 'boolean')) continue
    rejectArtifactRenders(p.fileId, guard, new ArtifactValidationError(reason), renderKeyOf(p))
  }
}

export function applyExportJob(job: ExportJob): void {
  const s = useExportStore.getState()
  // **只收自己那个作业的快照。** 判据是归属（job_id 对不对得上），不是
  // 「我现在闲着就收下」——后者会让别的标签页的进度、以及切项目之后一个
  // 迟到的轮询，把这里的状态填回去
  if (s.ownedJobId == null || job.job_id !== s.ownedJobId) return
  if (s.job && s.job.job_id === job.job_id && TERMINAL.has(s.job.status)) return
  for (const error of [job.error, ...job.outputs.map((output) => output.error)]) {
    if (error?.code === 'artifact_source_unavailable')
      rejectExportSources(s.lastInput, String(error.params?.reason ?? 'source_changed'), error.params)
  }
  const terminal = TERMINAL.has(job.status)
  if (terminal) stopPolling()
  // 排版时间线的关键时刻（ADR 0101）：导出**交付了文件**的那一刻打一个点。
  // 上面那道闸保证同一个作业只会进一次终局，所以这里不会重复打
  if (job.status === 'done' || job.status === 'partial') void markMoment('export', s.momentSnapshot)
  useExportStore.setState({
    job,
    running: !terminal,
    pollError: terminal ? null : s.pollError,
    ...(terminal ? { cancelError: null, cancelState: 'idle' as const, refreshing: false } : {}),
    // 终局之后快照再没有用处（同一作业只进一次终局）：立刻放掉
    momentSnapshot: terminal ? null : s.momentSnapshot,
    editedDuringExport:
      terminal && s.startedRevision != null
        ? liveRevision(s.lastInput) !== s.startedRevision
        : s.editedDuringExport,
  })
}

/**
 * 「现在再导一次会不会出来另一个文件」——用**此刻的文档**重算一遍指纹。
 *
 * 必须现取文档，不能拿 `lastInput.doc`：那一份是导出开始时冻住的引用，
 * 拿它跟自己比永远相等，那条判据于是恒成立、恒不报警，而它看起来完全正常
 * （空的 diff 与"没变化"长得一模一样）。
 *
 * 量的是**载荷**而不是某个自增计数器：改个画布名、折叠个侧栏、撤销又重做
 * 一次，导出结果一模一样，那就不该在完成时冒一句"导出期间文档被编辑过"。
 */
export function liveRevision(input: ExportRequestInput | null): string | null {
  if (!input) return null
  try {
    const doc = useDocumentStore.getState().doc
    const figureId = input.figureId ?? null
    const panel = figureId && input.panel
      ? (findFigurePanel(figureId, input.panel.id)?.panel ?? null)
      : null
    if (input.scope === 'original' && input.panel && !panel) return null
    const spec = figureId ? getOriginalOutputSpec(figureId, panel) : null
    return buildExportRequest({ ...input, doc, panel, spec }).revision
  } catch {
    return null
  }
}

/**
 * 起一个导出作业。
 *
 * 回的是**起没起来**，不是"导完了"——终局经 store 状态到达界面。
 * 这不是绕弯：关掉对话框之后作业继续跑，那时已经没有一个 await 在等它了。
 */
export async function runExport(input: ExportRequestInput): Promise<ExportJob | null> {
  if (useExportStore.getState().running) return null
  const prepared = prepareExport(input)
  if (prepared.error) {
    rejectExportSources(input, prepared.error.reason)
    useExportStore.setState({
      startError: { code: 'start_failed', message: engineErrorMsg(prepared.error) },
      lastInput: input,
    })
    return null
  }
  if (prepared.filenameProblem) {
    useExportStore.setState({
      startError: { code: 'bad_filename', message: prepared.filenameProblem },
      lastInput: input,
    })
    return null
  }
  stopPolling()
  useExportStore.setState({
    job: null,
    running: true,
    startError: null,
    pollError: null,
    refreshing: false,
    cancelError: null,
    cancelState: 'idle',
    lastInput: input,
    startedRevision: prepared.revision,
    editedDuringExport: false,
    // 起之前先清空归属：这一刻起，旧作业的迟到快照一律不收
    ownedJobId: null,
    momentSnapshot: captureMoment(input.doc),
  })
  const mine = ++generation
  let job: ExportJob
  try {
    job = await startExport(prepared.request)
  } catch (err) {
    if (mine !== generation) return null
    if (err instanceof ArtifactValidationError || (err instanceof ApiError && err.body?.code === 'artifact_source_unavailable')) {
      const params = err instanceof ApiError ? err.body.params as Record<string, unknown> | undefined : undefined
      rejectExportSources(input, err instanceof ArtifactValidationError ? err.reason : String(params?.reason ?? 'source_changed'), params)
    }
    if (mine !== generation) return null
    useExportStore.setState({
      running: false,
      startError: { code: 'start_failed', message: exportFailure(err) },
      momentSnapshot: null, // 作业没起来：这一刻不会有终局，快照放掉
    })
    return null
  }
  // await 期间换过项目 / 又起了一次导出：这一份回执已经作废，**一个字都不写**
  if (mine !== generation) return null
  // 认领：从这一刻起只收它的快照。**这里不顺手把 `job` 也写进去**——写了的话
  // 下面那句 `applyExportJob()` 会撞上自己刚放进去的终局快照，被「已经进过
  // 终局」那道守卫挡掉，`running` 于是永远停在 true
  useExportStore.setState({ ownedJobId: job.job_id })
  // **终局也走 applyExportJob()**：同步跑完的作业（小图、桌面本机）与轮询
  // 回来的走同一条落点，否则"导出期间文档又被改过"这条判断只在慢路径上存在
  applyExportJob(job)
  if (!TERMINAL.has(job.status)) schedulePoll(job.job_id)
  return job
}

/**
 * 排下一次轮询。
 *
 * **续不续，要先问这一轮还是不是当下那个作业的。** `stopPolling()` 清得掉
 * 定时器，清不掉一个**已经发出去的** `/api/export/state`：它的 continuation
 * 迟到回来时，`applyExportJob()` 会按归属把快照挡掉——挡得对——但紧接着那句
 * 无条件的 `schedulePoll(旧作业)` 会先 `stopPolling()` **把新作业的定时器
 * 掐掉**，再去轮询那个旧作业。SSE 不通的场合（浏览器演练场、断线）轮询是唯一
 * 通道，于是新导出早就完成了，界面还永远停在"进行中"（PR #214 第七轮评审）。
 *
 * 判据取**排这一轮的那一刻**的代次 + 作业归属，两个都要：代次管"换项目/又起
 * 了一次"，归属管"这个 id 还是不是我认领的那个"。拒收路径同样要问——那条路
 * 恰恰是陈旧快照必经的路。
 */
function exportFailure(error: unknown): UiMessage {
  if (error instanceof ArtifactValidationError) return engineErrorMsg(error)
  return error instanceof ApiError
    ? backendErrorMsg(error)
    : msg('export.connectionFailed', { app: PRODUCT_NAME }, 'dialogs')
}

function currentJob(jobId: string, mine: number): boolean {
  const s = useExportStore.getState()
  return mine === generation && s.ownedJobId === jobId && s.running
}

function schedulePoll(jobId: string): void {
  stopPolling()
  pollTimer = setTimeout(() => void refreshExportStatus(jobId), POLL_MS)
}

/** Retry the existing job, never start a second export to recover its status. */
export async function refreshExportStatus(jobId = useExportStore.getState().ownedJobId): Promise<void> {
  const mine = generation
  if (!jobId || !currentJob(jobId, mine) || useExportStore.getState().refreshing) return
  stopPolling()
  useExportStore.setState({ refreshing: true })
  try {
    const fresh = await exportState(jobId)
    if (!currentJob(jobId, mine)) return
    applyExportJob(fresh)
    if (fresh.job_id === jobId) useExportStore.setState({ pollError: null })
    if (currentJob(jobId, mine)) schedulePoll(jobId)
  } catch (error) {
    // A failed request is not a failed/cancelled job. Keep its identity and input;
    // pause polling until retry instead of hiding a persistent expired session.
    if (currentJob(jobId, mine)) useExportStore.setState({ pollError: exportFailure(error) })
  } finally {
    if (mine === generation && useExportStore.getState().ownedJobId === jobId) {
      useExportStore.setState({ refreshing: false })
    }
  }
}

/** An acknowledgement means cancellation was requested, not that files were removed. */
export async function cancelCurrentExport(): Promise<boolean> {
  const s = useExportStore.getState()
  const job = s.job
  const mine = generation
  if (!job || !currentJob(job.job_id, mine) || s.cancelState !== 'idle') return false
  useExportStore.setState({ cancelState: 'pending', cancelError: null })
  try {
    const res = await apiCancel(job.job_id)
    if (!currentJob(job.job_id, mine)) return false
    useExportStore.setState({
      cancelState: res.cancelling ? 'requested' : 'idle',
      cancelError: res.cancelling ? null : msg('export.cancelNotAccepted', undefined, 'dialogs'),
    })
    return res.cancelling
  } catch (error) {
    if (currentJob(job.job_id, mine)) {
      useExportStore.setState({ cancelState: 'idle', cancelError: exportFailure(error) })
    }
    return false
  }
}

/**
 * 换个页面 / 换个文档：把作业状态清掉（**不取消正在跑的那个**）。
 *
 * `ownedJobId` 一并清掉，所以此刻还在飞的那个轮询回来之后什么都不会写——
 * 只停轮询是不够的，请求已经发出去了。
 */
export function resetExportState(): void {
  stopPolling()
  // 代次 +1：此刻还在 await 里的那次 `startExport()` 回来之后什么都不写
  generation += 1
  useExportStore.setState({
    job: null,
    running: false,
    startError: null,
    pollError: null,
    refreshing: false,
    cancelError: null,
    cancelState: 'idle',
    lastInput: null,
    startedRevision: null,
    editedDuringExport: false,
    ownedJobId: null,
    batch: null,
    // 上个项目 / 上一份文档的快照（整份文档 + 面板图源）不留到这里之后
    momentSnapshot: null,
  })
}
