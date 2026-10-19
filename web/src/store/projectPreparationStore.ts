/**
 * 准备会话的**前端持有者**（T09，ADR 0116）：报告投影、参数快照、订阅与网络状态——仅此而已。
 *
 * ### 它不做什么
 *
 * * **不判业务事实**：phase / outcome / 能不能运行 / 下一步是什么动作，全部读后端 `prepsession` 的报告；这里没有
 *   第二份 ready 判据，按钮只来自报告里后端生成的 `actions` 与 `requirements`（`lib/preparationText.ts` 翻译）。
 * * **不自动重跑**：重连、应用重启（旧会话 404 `unknown_or_restarted`）之后只**重建检查会话**（只读），
 *   要不要再运行由用户点后端给的 `run` 动作；作答了环境 / 运行目录 / 数据位置之后只 `recheck`（只读）。
 * * **不把网络失联当执行失败**：取报告的 HTTP 等待有自己的看门狗（`REQUEST_TIMEOUT_MS`），超时只把 `connection`
 *   标成 `lost` 并继续按退避补拉；报告与 phase 原样保留——后台那次执行有它自己的工作预算与取消（ADR 0053）。
 *
 * ### 三个不同的动作
 *
 * `uiStore.setGuideCard('pill' | 'closed')`（缩成角标 / 收起引导卡，只改呈现；订阅照旧，收起时原对话框接着展示 input）·
 * `cancel()`（认领后端的 `cancel` 动作：按 owner 退役**本会话**的工作）· 切项目 `clear()`（换代、停轮询、丢订阅与
 * 晚到响应；**后端什么都不取消**，用户的执行 / 已授权的安装照常跑完，切回来重新打开时会话复用）。
 *
 * ### 迟到响应
 *
 * 项目代际（`clear()` 换代，A → B → A 回到同一个项目 id 也已换代）+ 发请求那一刻的项目 id + 同一会话里
 * `config_revision` 只许前进、同一修订里 `observation_seq` 只许前进；会话 id 换了（重建）只认重建那次的回包。
 */
import { create } from 'zustand'
import {
  actOnPreparationSession,
  ApiError,
  cancelNativeSession,
  createPreparationSession,
  fetchPreparationSession,
  type CapturedFigureDescriptor,
  type PreparationActionKind,
  type PreparationPhase,
  type PreparationReport,
  type PreparationTarget,
} from '@/lib/api'
import { currentAttempt, finishedWithFigures, prepView } from '@/lib/preparationText'
import { visiblePrepSurface } from '@/lib/prepSurface'
import { currentProjectId } from '@/lib/session'
import { captureProjectEpoch, type ProjectEpochGuard } from '@/lib/projectEpoch'
import { missingRequirements, readTokens, type ScriptArgsSchema } from '@/lib/scriptArgsForm'
import { useEnvStore } from '@/store/envStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useUiStore } from '@/store/uiStore'

/** 取报告这一次 HTTP 等待的上限（连接事实，不是后台工作的预算） */
let REQUEST_TIMEOUT_MS = 15_000
/** 补拉的退避节奏：有活动的会话按它轮询（SSE 提示之外的兜底），失联时同样按它重试 */
let POLL_MS: readonly number[] = [500, 1000, 2000, 4000]

/** 已经有过尝试、已结束的阶段：后端复用这样的会话时只给 `run`（没有 `run_when_ready`） */
const ENDED_PHASES: ReadonlySet<PreparationPhase> = new Set(['completed', 'partial', 'action_required', 'cancelled'])

/**
 * 已结束的会话上卡片「可以直接再跑」吗：结果卡（跑完 / 没出图 / 没登记——用户点的就是再跑一次）或主按钮本身就是报告里的 `run`（失败后的再试一次、取消后的再运行）。
 * 有要用户先答的事（参数 / 目录 / 依赖 / 数据 / 环境 / 弹窗提醒…）时主按钮不是 run，停在各自的卡上，不代认领
 */
function rerunnableView(v: ReturnType<typeof prepView>): boolean {
  // `edit_opening` / `edit_ready` 是已完成会话叠上「本地已进入过编辑」的视图：后端那边同样是可重跑的 completed，
  // 本地编辑记录不能把再点一次 ▶ 变成只重开旧结果（Codex #1044 r4285899694）
  if (
    ['completed', 'completed_unlinked', 'completed_partial', 'no_figure', 'no_figure_raster', 'not_registered', 'edit_opening', 'edit_ready'].includes(
      v.state,
    )
  )
    return true
  return v.primary?.kind === 'action' && v.primary.action === 'run'
}

/** 后端还在推进、值得补拉的 phase（其余是等用户 / 终局） */
const LIVE_PHASES: ReadonlySet<PreparationPhase> = new Set([
  'running',
  'awaiting_runtime_input',
  'preparing_environment',
  'scanning',
])

export type PrepConnection = 'ok' | 'lost'

export interface PrepEntry {
  key: string
  /** 打开那一刻冻结的目标（含参数快照）：之后再编辑草稿不影响这份会话 */
  target: PreparationTarget
  /** 发起时认领的项目 */
  pj: string | null
  report: PreparationReport | null
  /** 这份报告落地的时刻（毫秒）：脚本库据此判它比最近一次注册表刷新新还是旧 */
  reportAt?: number
  connection: PrepConnection
  /** 旧会话已不在（应用重启 / 回收）、已经重新检查过——**没有**自动运行 */
  restarted: boolean
  /** 最近一次被后端拒绝的动作（稳定码 + 参数）：说一句，报告已重读 */
  rejection: { code: string; params?: Record<string, unknown> } | null
  /** 没能开始检查（目标不存在 / 不是脚本 / 越界…）：稳定码 + 参数 */
  failure: { code: string; params?: Record<string, unknown>; message: string } | null
  /** 正在发出的请求（防连点）：动作 kind 或 `check`（创建 / 重建检查会话） */
  pending: PreparationActionKind | 'check' | null
  /** 用户点「进入编辑」加进画布的图（`first_edit_ready` 由渲染态观察，不是后端事实） */
  editing: string[]
  /**
   * 入口动作为每张图创建 / 复用的面板与渲染键：「编辑渲染可用」只认这把键的非 stale 精确 manifest
   * （按文件 id 扫会把 `markStale()` 留下的旧渲染、别的 override 变体当成这一次）。
   */
  editRenders: Record<string, EditRender>
  /**
   * 用户认领过「打开并运行」（`run_when_ready`）、这次运行还没走到尽头：会话 `completed` 且恰好捕获到一张图时自动进入编辑，
   * 不再要用户点「进入编辑」。**不是后端事实**（只是这个窗口里的用户意图记号）：多张图要用户挑、失败 / 取消 / 没出图都不进入。
   */
  armed: boolean
  /** 认领 `run_when_ready` 的响应丢了（连接失败）：服务端收没收到不知道——之后的报告里有意图就证实收到了（`armed`），仍有 `run_when_ready` 动作就是没收到 */
  armMaybe: boolean
  /** 点击 `run_when_ready` 那一刻报告里已有的意图 id：`armMaybe` 下已跑完的报告只有带着另一个意图 id 才属于这次点击，不是更早的运行 */
  armMaybeBaseline: string | null
  /** 点击那一刻的报告坐标：补拉到的报告还没越过它（同会话、同修订、观察序号没前进）就说明服务端可能还没提交认领，不能凭「仍有 `run_when_ready`」断定没收到 */
  armMaybeFrom: { session_id: string; config_revision: number; observation_seq: number } | null
  /** 这个条目是从明确的「打开并运行」入口打开的：卡片上的「继续 / 再试一次」（重新打开）沿用这份意图，不让用户为同一个目的点两次 */
  wantsRun: boolean
  /**
   * 这次 `open` 是否允许在已跑完的同配置会话上认领 `run` 重跑（`opts.rerun !== false`）。卡上「继续」是 `false`：这条策略要活过它自己的恢复路径
   * （首次检查断线后 `refresh` 的无会话恢复），否则恢复后落到已跑完的会话会把用户脚本再跑一遍（Codex #1152 补评 r4305863214）。
   * 每次 `open` 重新设定、不继承：新的明确「打开并运行」入口（`rerun` 未设）仍允许重跑
   */
  allowRerun: boolean
  /** 同一脚本换了参数（另一份运行配置）时，旧会话里还在等的运行意图：「打开并运行」带它去后端原子转移，不并存两份 */
  transferFrom: string | null
}

export interface EditRender {
  panelId: string
  renderKey: string
}

interface PreparationState {
  epoch: number
  entries: Record<string, PrepEntry>
  /** 引导卡此刻展示哪一份（呈现在 `uiStore.guideCard`） */
  focus: string | null

  /**
   * 打开（或复用）一个目标的检查会话并聚焦面板。脚本目标的参数草稿在这一刻取一份拷贝。
   * `run: true` = 用户在一个**明确的「打开并运行」入口**上点了它：检查完、报告里有 `run_when_ready` 动作且必填参数齐了，
   * 就代用户认领它（授权合同的摘要由 `act` 回显）——这是唯一一条「打开」之后自动认领的路径；其余入口只检查
   */
  /** `rerun: false`：卡上「继续」带着运行意图重开——只认领新的 `run_when_ready`，落到已跑完的同配置会话就复用那份结果，不重跑 */
  open: (target: PreparationTarget, opts?: { show?: boolean; run?: boolean; rerun?: boolean }) => Promise<void>
  /** 只读补拉（SSE 提示 / 轮询 / 重连） */
  refresh: (key: string) => Promise<void>
  /** SSE `preparation.session`：只是「重读」的提示 */
  onHint: (sessionId: string) => void
  /** 事件流（重）连上：断线期间的进展一律以 GET 补拉（重启过的后端 404 → 重建只读检查） */
  refreshAll: () => void
  /** 认领后端生成的动作（run / cancel / recheck / prepare_dependencies）；报告里没有这个动作就什么都不做 */
  act: (key: string, kind: PreparationActionKind, retried?: boolean, opts?: { arm?: boolean }) => Promise<void>
  /**
   * 「停止运行」且这次运行有桌面拥有的进程：结束它（后端同一个端点连准备计划一起取消）。走与 `act` 同一套 pending / 拒绝 / 连接状态：
   * 请求中按钮置忙；被拒显示后端稳定码对应的那一句；连接失败 = `connection: 'lost'`（卡片说「连不上」并继续补拉），不再吞掉
   */
  stopOwned: (key: string, nativeSessionId: string) => Promise<void>
  /** 环境 / 运行目录 / 数据位置答完了：空闲的会话只读地重新检查（不运行） */
  recheckIdle: () => void
  /** 「进入编辑」：记下加进画布的那张图与它的面板 / 渲染键（呈现层用它观察首次编辑渲染） */
  noteEditing: (key: string, assetId: string, render?: EditRender) => void
  /** 卡片改看扫描结果：只放下聚焦，会话与它的后端状态原样保留（再点开同一目标会复用） */
  blur: () => void
  /** 换项目：属于旧项目的一切原地丢掉，在途响应失去落地资格；**后端什么都不取消** */
  clear: () => void
}

export const keyOfTarget = (target: PreparationTarget): string =>
  'id' in target ? `asset:${target.id}` : `script:${target.script}`

/**
 * 脚本目标：参数草稿在**这一刻**取拷贝（空草稿 = 不带参数，请求体里没有 argv）。
 * `reuse`（#982）只由明确的「打开 / 再次打开脚本」动作传：草稿为空时让后端沿用上次成功的运行配置；草稿有参数时用户给的赢。
 * 目标构造点仍只有这一处——`recheckIdle` / 草稿变化不经这里发请求，所以会话目标不会在「沿用」与「不沿用」之间翻转。
 */
export function scriptTarget(script: string, opts?: { reuse?: boolean }): PreparationTarget {
  const d = useScriptArgvStore.getState().drafts[script]
  if (!d || d.tokens.length === 0) return opts?.reuse ? { script, reuse_last_success: true } : { script }
  return { script, argv: [...d.tokens], argv_sensitive: d.sensitive }
}

/** 这个条目的会话正在沿用上次成功的参数：返回个数（值不回前端）；没有沿用 = 0 */
export function reusedArgCount(entry: PrepEntry): number {
  const t = entry.target
  if ('id' in t || !t.reuse_last_success || (t.argv?.length ?? 0) > 0) return 0
  return entry.report?.target.argv_count ?? 0
}

/** 草稿与会话冻结的参数是否已经不同（用户在面板里改了参数）：不同就需要按新参数重新检查 */
export function draftDiffers(target: PreparationTarget): boolean {
  if ('id' in target) return false
  return targetChanged(target, scriptTarget(target.script))
}

/** 两个目标的运行参数是否不同（同一个 key 下：资产目标恒等，脚本目标比 argv 与敏感标记） */
function targetChanged(a: PreparationTarget, b: PreparationTarget): boolean {
  if ('id' in a || 'id' in b) return false
  const x = a.argv ?? []
  const y = b.argv ?? []
  if (x.length !== y.length || !!a.argv_sensitive !== !!b.argv_sensitive) return true
  return x.some((t, i) => t !== y[i])
}

/** 报告里后端给的参数 schema（`script_arguments` 待办的载荷）；没有 argparse 证据就没有 */
export const argsSchemaOf = (report: PreparationReport | null): ScriptArgsSchema | null => {
  const req = report?.requirements.find((r) => r.kind === 'script_arguments')
  return req && req.kind === 'script_arguments' ? req.payload.schema : null
}

/** 草稿按后端 schema 还差几个必填参数；没有 schema（没有 argparse 证据 / 表单关闭）= null，不判。卡片与「打开并运行」同一把尺子 */
export function missingArgsOf(report: PreparationReport | null, tokens: readonly string[]): number | null {
  const schema = argsSchemaOf(report)
  return schema ? missingRequirements(schema, readTokens(schema, [...tokens]), [...tokens]).count : null
}

/**
 * 本地「必填参数没填齐」闸门的唯一入口（卡片与「打开并运行」同一把尺子）：后端核验过本会话沿用了上次成功的配置（`reusedArgCount` > 0，
 * 值不回前端、草稿为空）时，必填项由那份配置满足，不判缺；用户草稿非空时仍按草稿判，后端没沿用而草稿也空的真缺参照旧判。
 */
export function missingArgsFor(entry: PrepEntry, tokens: readonly string[]): number | null {
  if (tokens.length === 0 && reusedArgCount(entry) > 0) return 0
  return missingArgsOf(entry.report, tokens)
}

/** 成功的报告里这次尝试真正捕获到的图（老后端没有这个字段 → 空） */
export const capturedOf = (report: PreparationReport | null): CapturedFigureDescriptor[] =>
  finishedWithFigures(report) ? (report?.captured ?? []) : []

/** 判据唯一出处在 `lib/preparationText`（store 与卡片同一份，#1134 Codex r4310249719） */
export { finishedWithFigures }

/* ---------------------------------------------------------- 模块级账本（活得比一次 setState 长） */

const timers = new Map<string, ReturnType<typeof setTimeout>>()
const pollStep = new Map<string, number>()
const inflight = new Map<string, Promise<void>>()
// 飞行中的「新建 / 复用 / 重建」检查：同一目标又来一次 open（同一次点击落成两次调用、卡开着时再点 ▶）搭这一趟，不再发第二个创建请求
const checkFlight = new Map<string, Promise<void>>()

function stopTimer(key: string): void {
  const t = timers.get(key)
  if (t) clearTimeout(t)
  timers.delete(key)
}

function stopAllTimers(): void {
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
  pollStep.clear()
}

/** 带看门狗的一次请求：超时只中止这一次 HTTP 等待（抛 `AbortError`），不碰任何后台工作 */
async function withWatchdog<T>(run: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), REQUEST_TIMEOUT_MS)
  try {
    return await run(ctl.signal)
  } finally {
    clearTimeout(t)
  }
}

/** 网络层失败（断线 / 看门狗超时）——与后端给出的结构化拒绝是两回事 */
const isConnectionFailure = (e: unknown): boolean => !(e instanceof ApiError)

const isRestarted = (e: unknown): boolean =>
  e instanceof ApiError &&
  e.status === 404 &&
  (e.body as { code?: string } | undefined)?.code === 'preparation_session_not_found'

const apiCode = (e: unknown): { code: string; params?: Record<string, unknown>; message: string } => {
  const body = (e instanceof ApiError ? e.body : {}) as { code?: string; params?: Record<string, unknown> }
  return {
    code: body.code || 'internal_error',
    params: body.params,
    message: e instanceof Error ? e.message : String(e),
  }
}

/** 同一会话里报告只许前进：修订大的赢；同一修订里观察序号不倒退 */
function newer(cur: PreparationReport | null, next: PreparationReport): boolean {
  if (!cur || cur.session_id !== next.session_id) return true
  if (next.config_revision !== cur.config_revision) return next.config_revision > cur.config_revision
  return next.observation_seq >= cur.observation_seq
}

/** 同一个 key 下「目标（参数）换过几次」：在途的检查 / 补拉 / 动作带着发起时的戳，参数换了就失去落地资格 */
const targetGens = new Map<string, number>()

export const useProjectPreparationStore = create<PreparationState>((set, get) => {
  /** 回包资格戳 = 代（换项目）× 目标代（同 key 换参数）；两者任一变了，在途响应都不许落地 */
  const stampOf = (key: string): number => get().epoch * 1_000_000 + (targetGens.get(key) ?? 0)
  const entry = (key: string): PrepEntry | undefined => get().entries[key]

  const patch = (key: string, fn: (e: PrepEntry) => Partial<PrepEntry>) =>
    set((s) => {
      const e = s.entries[key]
      if (!e) return s
      return { entries: { ...s.entries, [key]: { ...e, ...fn(e) } } }
    })

  /** 发起时记下：项目代际 + 本 key 的回包戳（换项目 / 同 key 换参数任一变了都失去落地资格） */
  const guardOf = (key: string) => captureProjectEpoch(() => stampOf(key))
  /** 这次回包还有没有资格落地：同一代、同一项目、条目还在 */
  const live = (key: string, g: ProjectEpochGuard) => g.still() && !!entry(key)

  const accept = (key: string, report: PreparationReport, opts?: { session?: boolean }) => {
    const e = entry(key)
    if (!e) return
    // 会话 id 换了：只认「重建检查会话」那次的回包（`session`），旧会话的迟到 GET 不许把它换回去
    if (e.report && e.report.session_id !== report.session_id && !opts?.session) return
    if (!newer(e.report, report)) return
    const before = e.report
    const advanced =
      !before ||
      before.session_id !== report.session_id ||
      before.config_revision !== report.config_revision ||
      before.observation_seq !== report.observation_seq
    // 同一目标的后端状态前进了（更高修订 / 换了会话 / 开了新一轮尝试）：上一轮的「进入编辑」记录属于旧结果，
    // 不许让新一轮跑完的 `completed` 把旧资产当成当前结果、压掉「进入编辑」
    const newRound =
      !!before &&
      (before.session_id !== report.session_id ||
        report.config_revision > before.config_revision ||
        (report.provider.attempt_id ?? null) !== (before.provider.attempt_id ?? null))
    const attemptChanged =
      !!before &&
      before.session_id === report.session_id &&
      (report.provider.attempt_id ?? null) !== (before.provider.attempt_id ?? null)
    // 动态 import 之后项目 / 代可能已经换了：回调带着发起时的归属，落地前复核
    const owner = captureProjectEpoch(() => get().epoch)
    // `rejection` 不在这里清：被拒之后重读到的新修订正是要配着那一句看的；下一次动作 / 重新打开才收起它
    patch(key, () => ({
      report,
      // 只有报告真的前进了才换落地时刻：重连后补拉到同一份旧快照（会话 / 修订 / 观察序号都没变）不能因此显得比注册表刷新更新
      ...(advanced ? { reportAt: Date.now() } : {}),
      connection: 'ok',
      ...(newRound
        ? { editing: [], editRenders: {}, ...(before?.session_id === report.session_id ? { restarted: false } : {}) }
        : {}),
    }))
    // 以后端报告为准恢复「自动进入编辑」（Codex #1044）：意图只由「打开并运行」的认领创建，报告里它还在（active / launched）、会话没跑完，
    // 而这个条目是从「打开并运行」入口打开的（刷新 / 切项目后重新打开）或认领响应丢了（`armMaybe`）——就是那次认领的延续，不依赖前端内存
    const cur = entry(key)
    const intentLive = report.intent?.status === 'active' || report.intent?.status === 'launched'
    // 已跑完的报告：只有 `armMaybe`（本窗口刚点过、响应丢了）且意图是点击之后才出现的（id 与点击时不同）才算这次点击的——
    // 快脚本在第一次补拉前就跑完了；`wantsRun`（刷新后重新打开）读到的 completed 是旧运行，不重开自动编辑
    const thisClick = !!cur?.armMaybe && report.intent?.id !== cur.armMaybeBaseline
    if (cur && !cur.armed && intentLive && (!finishedWithFigures(report) ? cur.wantsRun || cur.armMaybe : thisClick)) {
      patch(key, () => ({ armed: true, armMaybe: false }))
    } else if (cur?.armMaybe && report.actions.some((a) => a.kind === 'run_when_ready')) {
      // 认领没落地：别留着——但补拉赢了 POST 的提交竞态时报告还是点击前的样子：观察越过点击前那份才算数，否则继续不确定
      const from = cur.armMaybeFrom
      const passed =
        !from ||
        report.session_id !== from.session_id ||
        report.config_revision > from.config_revision ||
        report.observation_seq > from.observation_seq
      if (passed) patch(key, () => ({ armMaybe: false, armMaybeFrom: null }))
    }
    if (finishedWithFigures(report) && (!finishedWithFigures(before) || attemptChanged)) {
      void onCompleted(report, owner)
      if (entry(key)?.armed) {
        // 「打开并运行」认领过：这一轮到头了——恰好一张图就直接进入编辑（多张要用户挑；没有图 / 静态不进）。
        // 这次无参数运行替换掉了旧图名（`unlinked_stems`，T09b）时不自动进入：卡片详情里「哪些旧图不再关联、怎么恢复」是用户必须看到的
        // 用户正看着这张卡（聚焦且展开）才自动进入：放到后台（角标）或收起之后他可能正在编辑别的图，不抢他的工作区——角标上仍有「进入编辑」
        patch(key, () => ({ armed: false }))
        const figures = capturedOf(report)
        const watching = get().focus === key && useUiStore.getState().guideCard === 'card'
        // 部分捕获同理：有图没捕获到（`failed_figures`）是用户必须看到的，不自动进入把卡片盖掉
        if (
          watching &&
          figures.length === 1 &&
          (report.unlinked_stems ?? []).length === 0 &&
          (report.failed_figures ?? []).length === 0
        ) {
          void autoEnterEdit(key, figures[0], owner, report)
        }
      }
    } else if (endedWithoutEdit(report)) {
      // 失败 / 取消 / 没出图：这次「打开并运行」的意图到此退役——不论当时 armed 与否，连恢复用的 `wantsRun` / `armMaybe` 一起清，
      // 否则之后卡上普通的「重试」跑出的 running 报告（后端意图仍是 launched）会把它重新武装、一次成功就抢工作区。用户再点「打开并运行」才重新武装
      const e = entry(key)
      if (e && (e.armed || e.wantsRun || e.armMaybe)) {
        patch(key, () => ({ armed: false, wantsRun: false, armMaybe: false, armMaybeFrom: null, armMaybeBaseline: null }))
      }
    }
    // 同一会话里的依赖作业刚装完（T09b）：画布上因「要先准备依赖」停着的渲染与原授权框作答之后一样重排——同一份
    // 需求两个展示面，下游效果只有一种（重排的是渲染请求，不是脚本首跑；首跑仍由用户点报告里的 run）
    const depDone = (r: PreparationReport | null) =>
      r?.provider.dependency?.state === 'done' ? r.provider.dependency.plan_id : null
    if (before?.session_id === report.session_id && depDone(report) && depDone(report) !== depDone(before)) {
      void onDependencyPrepared(owner)
    }
  }

  /** 补拉节奏：有活动就按退避继续，没有活动（等用户 / 终局）且连接正常就停 */
  const schedule = (key: string) => {
    stopTimer(key)
    const e = entry(key)
    if (!e) return
    const live = e.connection === 'lost' || (e.report !== null && LIVE_PHASES.has(e.report.phase))
    if (!live) {
      pollStep.delete(key)
      return
    }
    const step = pollStep.get(key) ?? 0
    pollStep.set(key, step + 1)
    const delay = POLL_MS[Math.min(step, POLL_MS.length - 1)]
    timers.set(
      key,
      setTimeout(() => {
        timers.delete(key)
        void get().refresh(key)
      }, delay),
    )
  }

  /** 检查会话（新建 / 复用 / 重建）。只读：后端不执行任何用户代码 */
  const check = (key: string, target: PreparationTarget, restarted: boolean): Promise<void> => {
    // 同一键上已有检查在飞（目标换了会在 open 里先清掉登记）：搭它，不另发创建请求。入口不止 open——事件流重连的 `refreshAll`
    // 在第一趟检查回来前（条目还没有报告）也会落到这里（#1137 Windows 分片 trace：第二个 POST 与重连后的补拉 GET 同一毫秒）
    const flying = checkFlight.get(key)
    if (flying) return flying
    const p = runCheck(key, target, restarted).finally(() => {
      if (checkFlight.get(key) === p) checkFlight.delete(key)
    })
    checkFlight.set(key, p)
    return p
  }
  const runCheck = async (key: string, target: PreparationTarget, restarted: boolean) => {
    const g = guardOf(key)
    const pj = g.pj
    patch(key, () => ({ pending: 'check' }))
    try {
      const report = await withWatchdog((signal) => createPreparationSession(target, pj, signal))
      if (!live(key, g)) return
      patch(key, () => ({ pending: null, failure: null, restarted }))
      accept(key, report, { session: true })
    } catch (e) {
      if (!live(key, g)) return
      if (isConnectionFailure(e)) patch(key, () => ({ pending: null, connection: 'lost' }))
      else patch(key, () => ({ pending: null, failure: apiCode(e) }))
    } finally {
      if (live(key, g)) schedule(key)
    }
  }

  /**
   * 「打开并运行」入口的下半截：检查完了，报告里有后端的 `run_when_ready` 动作、会话也没卡在必填参数上，就代用户认领它。
   * 只由 `open(..., { run: true })` 调——也就是用户明确点了那个入口；其余任何路径（刷新 / 重连 / 重建会话 / `recheckIdle`）都不走这里。
   * 必填参数没填齐时不认领（卡片已换成参数卡，填完用卡上的主按钮）。
   */
  const armIfReady = async (key: string, allowRerun = true) => {
    const e = entry(key)
    const report = e?.report
    if (!e || !report || e.failure || e.pending || e.connection !== 'ok' || 'id' in e.target) return
    // 后端对已经跑完的会话只给 `run`（没有 `run_when_ready`）：显式「打开并运行」点在它上面就是要再跑一次，认领 `run` 并武装这一次的新尝试
    // （旧的 completed 结果不触发自动进入：武装只在动作成功之后，且 `accept` 只在 completed 的「新一轮」上进入）
    const rerun =
      !report.actions.some((a) => a.kind === 'run_when_ready') &&
      report.actions.some((a) => a.kind === 'run') &&
      ENDED_PHASES.has(report.phase)
    if (!rerun && !report.actions.some((a) => a.kind === 'run_when_ready')) return
    // 只有明确的「打开并运行」入口（脚本行 ▶ / 接入中心试运行）才在已跑完的会话上再跑；「继续」回到已有结果就复用它
    if (rerun && !allowRerun) return
    // 只在卡片会说「可以运行了」（`ready`，没有要用户先看的事）时代认领：要先答的事（运行目录 / 环境 / 数据 / 必填参数）、要装的组件（用户该先看见
    // 装什么、装到哪）、弹窗 / 来源不明的提醒、重启后重建的会话都停在各自的卡上，由用户点那张卡的主按钮（它同样是 run_when_ready）
    const missing = missingArgsFor(e, e.target.argv ?? [])
    const view = prepView(e, { argsChanged: draftDiffers(e.target), editReady: false, missingArgs: missing })
    const state = view.state
    if (rerun ? !rerunnableView(view) : state !== 'ready') return
    // 沿用了上次成功的参数：一键运行之前让用户看一眼「将沿用 N 个参数」（卡上有清除），所以这一次不代认领
    if (reusedArgCount(e) > 0) return
    if (!rerun) {
      await get().act(key, 'run_when_ready')
      return
    }
    // 武装标记随这一次调用走（不放模块级状态：A 项目在途的请求不能影响同名脚本键下 B 项目的动作）
    await get().act(key, 'run', false, { arm: true })
  }

  return {
    epoch: 0,
    entries: {},
    focus: null,

    blur: () => set({ focus: null }),

    open: async (target, opts) => {
      const key = keyOfTarget(target)
      const pj = currentProjectId()
      const prev = entry(key)
      // 同一脚本换了参数再打开：上一份报告属于旧参数的会话，不许留（留着的话，新会话建不出来时 refresh 会去补拉旧会话，
      // 报告和动作就按旧参数展示 / 执行，而条目记着新目标，「草稿不同」的提示也就不亮了）。同参数再打开才保留不闪空
      // 沿用标志换了（「清除」）也是另一个会话（不同运行配置），上一份报告不许留；但它不算「草稿与会话冻结的参数不同」（`draftDiffers`）
      const reuseOf = (t: PreparationTarget) => !('id' in t) && !!t.reuse_last_success
      const sameTarget = !!prev && !targetChanged(prev.target, target) && reuseOf(prev.target) === reuseOf(target)
      // 同参数再点一次「打开并运行」是同一个意图：已有的认领记号（含响应丢了的 `armMaybe`）留着——否则第二次检查回来前脚本
      // 已跑完，`accept` 拿到的是没有 `armMaybe` 的已完成报告，答应过的「准备完自动进入编辑」就丢了（Codex #1044 r4285653253）
      const keepArm = sameTarget && !!opts?.run && !!prev
      if (prev && !sameTarget) {
        targetGens.set(key, (targetGens.get(key) ?? 0) + 1)
        stopTimer(key)
        pollStep.delete(key)
        inflight.delete(key)
        checkFlight.delete(key)
      }
      // 同一目标的首次检查还在飞（慢机器上同一次点击落成两次 open）：下面的 `check` 会搭这一趟，不再发第二个创建请求。
      // 否则两趟检查先后回来，后一趟把先一趟已置上的 `pending`（run_when_ready 在飞）清掉，防连点的闸就开了，认领发两次（#1137）
      set((s) => ({
        focus: key,
        entries: {
          ...s.entries,
          [key]: {
            key,
            target,
            pj,
            // 同一目标再次打开：先留着上一份报告（新报告到了按会话 id 换），不闪成空
            report: sameTarget ? (prev?.report ?? null) : null,
            connection: 'ok',
            restarted: false,
            rejection: null,
            failure: null,
            // 同一目标再次打开（卡开着时点素材库 ▶）：飞行中的动作还在飞，防连点的 `pending` 不能被抹掉，
            // 否则第二次点击会发出第二个 `run`。换了目标 / 参数是另一份会话，动作随旧会话作废
            pending: sameTarget ? (prev?.pending ?? null) : null,
            // 「进入编辑」的记录属于那份目标的结果：换了参数就是另一批图，旧图的编辑不许混进新结果的判断
            editing: sameTarget ? (prev?.editing ?? []) : [],
            editRenders: sameTarget ? (prev?.editRenders ?? {}) : {},
            // 「打开并运行」的意图记号只属于明确点过它的这一次；同参数只打开不运行不带过去，换参数时看旧会话里还有没有在等的意图
            armed: keepArm ? prev.armed : false,
            armMaybe: keepArm ? prev.armMaybe : false,
            armMaybeBaseline: keepArm ? prev.armMaybeBaseline : null,
            armMaybeFrom: keepArm ? prev.armMaybeFrom : null,
            wantsRun: !!opts?.run,
            allowRerun: opts?.rerun !== false,
            transferFrom: sameTarget
              ? (prev?.transferFrom ?? null)
              : prev?.report?.intent?.status === 'active'
                ? prev.report.session_id
                : null,
          },
        },
      }))
      if (opts?.show !== false) useUiStore.getState().setGuideCard('card')
      // 同一目标上有动作在飞：它的回包自带最新报告，这里再 `check` 一次既多余，又会把 `pending` 改写成 'check'
      // 再清空——防连点的闸就开了
      if (sameTarget && prev?.pending && prev.pending !== 'check') return
      await check(key, target, false)
      if (opts?.run) await armIfReady(key, entry(key)?.allowRerun !== false)
    },

    refresh: (key) => {
      const running = inflight.get(key)
      if (running) return running
      const e = entry(key)
      if (!e) return Promise.resolve()
      const sid = e.report?.session_id
      if (!sid) {
        // 还没有任何报告（第一次检查断线了）：重新检查，仍是只读。若这是「打开并运行」的首次检查（`wantsRun`），恢复后接着
        // 代认领——用户的一键意图不因第一次请求超时而丢（Codex #1044 r4290881812）；`armIfReady` 的其余前提照旧
        const g = guardOf(key)
        return check(key, e.target, false).then(() => {
          if (live(key, g) && entry(key)?.wantsRun) return armIfReady(key, entry(key)?.allowRerun !== false)
        })
      }
      const g = guardOf(key)
      const pj = e.pj
      let promise!: Promise<void> // 收尾要认出自己的登记
      promise = (async () => {
        try {
          const report = await withWatchdog((signal) => fetchPreparationSession(sid, pj, signal))
          if (!live(key, g)) return
          accept(key, report)
        } catch (err) {
          if (!live(key, g)) return
          if (isRestarted(err)) {
            // 应用重启过 / 会话被回收：重建检查会话（只读），不猜那次执行的结局，不自动重跑
            inflight.delete(key)
            await check(key, entry(key)!.target, true)
            return
          }
          if (isConnectionFailure(err)) patch(key, () => ({ connection: 'lost' }))
        } finally {
          // 只删自己：项目切换后同键下 B 的刷新不能被 A 迟到的收尾顶掉
          if (inflight.get(key) === promise) inflight.delete(key)
          if (live(key, g)) schedule(key)
        }
      })()
      inflight.set(key, promise)
      return promise
    },

    onHint: (sessionId) => {
      for (const e of Object.values(get().entries)) {
        if (e.report?.session_id === sessionId) {
          pollStep.delete(e.key)
          void get().refresh(e.key)
        }
      }
    },

    refreshAll: () => {
      for (const e of Object.values(get().entries)) {
        pollStep.delete(e.key)
        void get().refresh(e.key)
      }
    },

    act: async (key, kind, retried = false, opts) => {
      const e = entry(key)
      const report = e?.report
      if (!e || !report || e.pending) return
      const action = report.actions.find((a) => a.kind === kind)
      if (!action) return
      const g = guardOf(key)
      const pj = e.pj
      patch(key, () => ({ pending: kind, rejection: null }))
      const transferFrom = kind === 'run_when_ready' ? e.transferFrom : null
      try {
        // 动作 POST 与创建 / 轮询走同一个看门狗：连接卡住时超时落进下面的「连接失败 → 补拉报告」，不重发
        const res = await withWatchdog((signal) =>
          actOnPreparationSession(
            report.session_id,
            {
              action_id: action.id,
              expected_config_revision: report.config_revision,
              ...(action.impact.impact_digest ? { impact_digest: action.impact.impact_digest } : {}),
              ...(transferFrom ? { transfer_from: transferFrom } : {}),
            },
            pj,
            signal,
          ),
        )
        if (!live(key, g)) return
        // 认领了「打开并运行」：这一轮跑完时自动进入编辑（先记下，再接这份报告——它可能已经是 running）。认领成功就是确知的结论：
        // 之前响应丢了留下的 `armMaybe` 一组恢复记号一并清掉，否则这轮跑完后重复的已完成报告会被当成那次旧点击的恢复而再次武装（Codex #1044 r4285832844）
        patch(key, () => ({
          pending: null,
          ...(kind === 'run_when_ready'
            ? { armed: true, transferFrom: null, armMaybe: false, armMaybeBaseline: null, armMaybeFrom: null }
            : opts?.arm
              ? { armed: true }
              : {}),
        }))
        pollStep.delete(key)
        accept(key, res.report)
      } catch (err) {
        if (!live(key, g)) return
        patch(key, () => ({ pending: null }))
        if (isRestarted(err)) {
          await check(key, e.target, true)
          return
        }
        if (isConnectionFailure(err)) {
          // 不知道后端收没收到：不重发（同一个动作 id 重发是幂等的，但「是否已认领」以报告为准），只补拉事实
          patch(key, () => ({ connection: 'lost', ...(kind === 'run_when_ready' ? { armMaybe: true,
                armMaybeBaseline: report.intent?.id ?? null,
                armMaybeFrom: { session_id: report.session_id, config_revision: report.config_revision, observation_seq: report.observation_seq },
              } : {}) }))
        } else {
          // 修订变了 / 检查之后世界变了 / 影响变了 / 现在不能运行：零副作用的拒绝——说一句，重读报告
          const { code, params } = apiCode(err)
          // 另一份运行配置的意图还在、而前端丢了旧会话 id（刷新 / 切项目）：后端把旧会话 id 带回来了——带着它转移，只重试一次
          const old = params?.session_id
          if (kind === 'run_when_ready' && code === 'preparation_run_intent_conflict' && typeof old === 'string' && !retried) {
            patch(key, () => ({ transferFrom: old }))
            await get().act(key, kind, true)
            return
          }
          // 旧意图不能转移（已执行 / 已取消）：下一次点击不再带它，别让「转移」成为永远的死胡同
          patch(key, () => ({
            rejection: { code, params },
            ...(code === 'preparation_run_intent_not_transferable' ? { transferFrom: null } : {}),
          }))
        }
        await get().refresh(key)
      } finally {
        if (live(key, g)) schedule(key)
      }
    },

    stopOwned: async (key, nativeSessionId) => {
      const e = entry(key)
      if (!e || e.pending) return
      const g = guardOf(key)
      patch(key, () => ({ pending: 'cancel', rejection: null }))
      try {
        await cancelNativeSession(nativeSessionId)
        if (!live(key, g)) return
        patch(key, () => ({ pending: null }))
      } catch (err) {
        if (!live(key, g)) return
        patch(key, () => ({ pending: null }))
        if (isConnectionFailure(err)) patch(key, () => ({ connection: 'lost' }))
        else {
          const { code, params } = apiCode(err)
          patch(key, () => ({ rejection: { code, params } }))
        }
      }
      if (!live(key, g)) return
      await get().refresh(key)
      if (live(key, g)) schedule(key)
    },

    recheckIdle: () => {
      for (const e of Object.values(get().entries)) {
        if (e.pending || !e.report) continue
        if (e.report.actions.some((a) => a.kind === 'recheck')) void get().act(e.key, 'recheck')
      }
    },

    noteEditing: (key, assetId, render) =>
      patch(key, (e) => ({
        editing: e.editing.includes(assetId) ? e.editing : [...e.editing, assetId],
        editRenders: render ? { ...e.editRenders, [assetId]: render } : e.editRenders,
      })),

    clear: () => {
      stopAllTimers()
      inflight.clear()
      checkFlight.clear()
      set((s) => ({ epoch: s.epoch + 1, entries: {}, focus: null }))
    },
  }
})

/**
 * 「打开并运行」这一轮没有（也不会）走到进入编辑：意图终结了、或这次尝试以失败 / 取消 / 没图收场。
 * 一次 safe 以 `safe_file_operation_blocked` 收场、后端还没记下「为什么没回退」时，可能紧接着就是同会话的回退尝试，先不撤。
 */
function endedWithoutEdit(report: PreparationReport): boolean {
  const intent = report.intent?.status
  if (intent === 'cancelled' || intent === 'revoked') return true
  if (report.phase === 'cancelled' || report.phase === 'partial') return true
  if (report.outcome.kind === 'failed') {
    const last = currentAttempt(report)
    return !(report.outcome.code === 'safe_file_operation_blocked' && !last?.declined)
  }
  return false
}

/** 自动进入编辑：动态 import（`prepEnterEdit` 反过来 import 本 store）；回来时项目 / 代已换了就整个丢掉 */
async function autoEnterEdit(
  key: string,
  figure: CapturedFigureDescriptor,
  owner: ProjectEpochGuard,
  report: PreparationReport,
): Promise<void> {
  // 调度那一刻的身份：同一脚本换参数重开（`open()` 递增 `targetGens`、换会话 / 尝试）之后，旧配置跑出的图不许再被打开或记到新条目上
  const gen = targetGens.get(key) ?? 0
  const sid = report.session_id
  const attempt = report.provider.attempt_id ?? null
  try {
    const { enterEdit } = await import('@/store/prepEnterEdit')
    if (!owner.still()) return
    const store = useProjectPreparationStore.getState
    // 守卫 = 这份结果还是这个条目的当前结果（key + 目标代 + 会话 + 尝试）且用户还在看这张卡（聚焦 + 展开）
    const sameResult = () => {
      const cur = store().entries[key]?.report
      return (targetGens.get(key) ?? 0) === gen && cur?.session_id === sid && (cur.provider.attempt_id ?? null) === attempt
    }
    await enterEdit(
      {
        epoch: () => store().epoch,
        note: (id, render) => {
          if (sameResult()) store().noteEditing(key, id, render)
        },
        // 素材清单的 await 期间用户可能已把卡放到后台 / 收起 / 切去别的卡 / 换参数重开：落地前复核，别抢他的工作区
        stillWanted: () => {
          const surface = visiblePrepSurface({
            focus: store().focus,
            entries: store().entries,
            guideCard: useUiStore.getState().guideCard,
            hidden: false, // 教程 / 引导不会替用户收起一张展开的卡；守卫只关心「展开且聚焦」
          })
          return sameResult() && surface?.key === key && surface.mode === 'card'
        },
      },
      figure,
    )
  } catch {
    /* 尽力而为：自动进入没成，卡片上的「进入编辑」仍在 */
  }
}

/**
 * 一次尝试成功：素材库与画布上同一脚本的图要看到新结果（与试运行成功后同一串刷新）。只刷新清单与渲染态，
 * **不执行**：已经在画布上的这些图按热会话重画，脚本不再跑。
 * 动态 import 回来之后，发起这次回调的项目 / 代还是当前的吗（`owner.still()`）？不是就整个丢掉（A 的素材 id 不许去动 B 的 store）。
 */
async function onCompleted(report: PreparationReport, owner: ProjectEpochGuard): Promise<void> {
  // 导入即扫描的快照随结果更新（零执行、后端单飞）：这个脚本现在连着可编辑的图了，「显示项目检查结果」不再说它待准备
  // 素材库脚本行的「已关联」同理（与 registry.changed 事件同一个出口，幂等去重）
  void Promise.all([import('@/store/projectScanStore'), import('@/store/scriptLibraryStore')])
    .then(([{ useProjectScanStore }, { useScriptLibraryStore }]) => {
      if (!owner.still()) return
      void useProjectScanStore.getState().start({ force: true, reason: 'refresh' })
      void useScriptLibraryStore.getState().load()
    })
    .catch(() => undefined)
  const ids = (report.captured ?? []).map((d) => d.asset_id).filter(Boolean)
  if (!ids.length) return
  try {
    const [{ useRuntimeAssetStore }, { useAssetStore }, { useRenderStore }] = await Promise.all([
      import('@/store/runtimeAssetStore'),
      import('@/store/assetStore'),
      import('@/store/renderStore'),
    ])
    if (!owner.still()) return
    const runtime = useRuntimeAssetStore.getState()
    runtime.invalidate(ids)
    runtime.bumpPreview(ids)
    void runtime.loadAssets()
    void useAssetStore.getState().load()
    useRenderStore.getState().markStale(ids)
  } catch {
    /* 清单刷新是尽力而为；registry.changed 事件 / 手动刷新会补上 */
  }
}

/** 会话里的依赖准备装完了：画布上停在依赖门上的渲染重排（与 `depRepairStore` 装完之后同一个出口） */
async function onDependencyPrepared(owner: ProjectEpochGuard): Promise<void> {
  try {
    const { useRenderStore } = await import('@/store/renderStore')
    if (!owner.still()) return
    useRenderStore.getState().retryEnvironmentFailures()
  } catch {
    /* 尽力而为：下一次编辑 / 手动重试会补上 */
  }
}

// 环境 / 运行目录 / 数据位置有了答案（采用环境、选了目录、指认了数据、改指作废）：空闲的会话只读地重新检查。
// 下一步（是否运行）仍由后端的报告给出、由用户点——这里绝不认领 `run`。
useEnvStore.subscribe((state, prev) => {
  // 项目的环境快照第一次落地（未加载 → 有值）且里面没有任何「用户答过」的痕迹（运行目录没定过、没明确采用过环境）：这是启动时的被动读取，
  // 不是答案变了——会话的检查读的是后端事实（创建请求只带目标，不带这份快照）。慢机器上快照比会话创建晚到，若算作变化就会在
  // 用户还没点任何东西时给停在「运行目录」等卡上的会话发一次 recheck。
  // 快照里已有答过的痕迹（用户在快照到达前就答了、setter 因 env 还是空而退回整份重读；或别的窗口答过）仍照旧 recheck：宁可多一次只读检查，也不留陈旧的卡
  // 「还没有这个项目的快照」有两种形状：整份 env 为空（启动），或 `resetProject()` 留下的占位 `{ open: false }`（已有环境后再进项目）
  const projectUnloaded = (s: typeof state) => s.env === null || s.env.project?.open === false
  const passiveFirstLoad =
    projectUnloaded(prev) &&
    !projectUnloaded(state) &&
    !state.env?.project?.workdir?.decided &&
    state.env?.project?.consent !== 'confirmed'
  const sig = (s: typeof state) =>
    [
      s.inputRemapGeneration,
      s.probeResultsGeneration,
      ...(passiveFirstLoad
        ? []
        : [
            s.env?.project?.workdir?.mode ?? '',
            String(s.env?.project?.workdir?.decided ?? ''),
            s.env?.project?.python ?? '',
            s.env?.project?.consent ?? '',
          ]),
    ].join('|')
  if (sig(state) !== sig(prev)) useProjectPreparationStore.getState().recheckIdle()
})

/** 只给测试用：缩短看门狗与轮询节奏（产品里的阈值不变） */
export function __setPreparationTimingForTests(opts: { requestTimeoutMs?: number; pollMs?: readonly number[] }): void {
  if (opts.requestTimeoutMs !== undefined) REQUEST_TIMEOUT_MS = opts.requestTimeoutMs
  if (opts.pollMs !== undefined) POLL_MS = opts.pollMs
}
