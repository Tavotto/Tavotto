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
import {
  cancelExport as apiCancel,
  exportState,
  startExport,
  validateExport as apiValidate,
  type ExportJob,
  type ExportValidation,
} from '@/lib/api'
import { buildExportRequest, type ExportRequestInput } from '@/lib/exportRequest'
import { getOriginalOutputSpec } from '@/lib/originalSpec'
import { useDocumentStore } from '@/store/documentStore'
import { findFigurePanel } from '@/store/workspace'
import type { FilenameReason } from '@/lib/exportName'
import { filenameProblem } from '@/lib/exportRequest'

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

export interface PreparedExport {
  request: ReturnType<typeof buildExportRequest>['request']
  names: string[]
  revision: string
  /** 文件名不合法的原因；合法为 null。**在输入的那一刻就知道**（§六） */
  filenameProblem: FilenameReason | null
}

interface ExportState {
  /** 当前（或最近一次）作业的完整快照。**不是增量** */
  job: ExportJob | null
  /** 有没有作业在飞 */
  running: boolean
  /** 起作业本身失败了（网络 / 请求不合法）；作业内部的失败在 `job.error` 里 */
  startError: { code: string; message: string } | null
  /** 上一次的输入。失败之后重试用它，用户不必重填 */
  lastInput: ExportRequestInput | null
  /**
   * 导出**开始那一刻**的文档快照指纹。完成时与当时的文档一比，
   * 就能说出「导出期间这份文档又被改过」（§三）。
   */
  startedRevision: string | null
  /** 完成时文档已经不是导出时那一份了 */
  editedDuringExport: boolean
}

export const useExportStore = create<ExportState>(() => ({
  job: null,
  running: false,
  startError: null,
  lastInput: null,
  startedRevision: null,
  editedDuringExport: false,
}))

/** 请求成形 + 就地校验。**不发网络**，输入框每敲一个字都可以调。 */
export function prepareExport(input: ExportRequestInput): PreparedExport {
  const built = buildExportRequest(input)
  return { ...built, filenameProblem: filenameProblem(input.filename, input.formats) }
}

/** 真的开始之前能看出来的问题（重名、目录写不写得了、PPI 有没有意义）。 */
export async function validateExportRequest(
  input: ExportRequestInput,
): Promise<ExportValidation | null> {
  try {
    return await apiValidate(prepareExport(input).request)
  } catch {
    // 校验拿不到答案 ≠ 没有问题。回 null，界面据此**不说**"一切正常"
    return null
  }
}

let pollTimer: ReturnType<typeof setTimeout> | null = null

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
export function applyExportJob(job: ExportJob): void {
  const s = useExportStore.getState()
  if (s.job && s.job.job_id === job.job_id && TERMINAL.has(s.job.status)) return
  if (s.job && s.job.job_id !== job.job_id && s.running) return
  const terminal = TERMINAL.has(job.status)
  if (terminal) stopPolling()
  useExportStore.setState({
    job,
    running: !terminal,
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
    const panel = figureId ? (findFigurePanel(figureId)?.panel ?? null) : null
    const spec = figureId ? getOriginalOutputSpec(figureId) : null
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
  const prepared = prepareExport(input)
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
    lastInput: input,
    startedRevision: prepared.revision,
    editedDuringExport: false,
  })
  let job: ExportJob
  try {
    job = await startExport(prepared.request)
  } catch (err) {
    useExportStore.setState({
      running: false,
      startError: { code: 'start_failed', message: String(err) },
    })
    return null
  }
  // **终局也走 applyExportJob()**：同步跑完的作业（小图、桌面本机）与轮询
  // 回来的走同一条落点，否则"导出期间文档又被改过"这条判断只在慢路径上存在
  applyExportJob(job)
  if (!TERMINAL.has(job.status)) schedulePoll(job.job_id)
  return job
}

function schedulePoll(jobId: string): void {
  stopPolling()
  pollTimer = setTimeout(() => {
    void exportState(jobId)
      .then((fresh) => {
        applyExportJob(fresh)
        if (!TERMINAL.has(fresh.status)) schedulePoll(jobId)
      })
      .catch(() => {
        // 一次拉不到不等于作业没了（后端重启、网络抖动）。继续拉；
        // 真的没了的话下一次会回 `status: 'unknown'`，那才是结论
        schedulePoll(jobId)
      })
  }, POLL_MS)
}

/** 取消当前作业。回「有没有东西可取消」。 */
export async function cancelCurrentExport(): Promise<boolean> {
  const job = useExportStore.getState().job
  if (!job || TERMINAL.has(job.status)) return false
  try {
    const res = await apiCancel(job.job_id)
    return res.cancelling
  } catch {
    return false
  }
}

/** 换个页面 / 换个文档：把作业状态清掉（**不取消正在跑的那个**）。 */
export function resetExportState(): void {
  stopPolling()
  useExportStore.setState({
    job: null,
    running: false,
    startError: null,
    lastInput: null,
    startedRevision: null,
    editedDuringExport: false,
  })
}
