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
  createPreparationSession,
  fetchPreparationSession,
  type CapturedFigureDescriptor,
  type PreparationActionKind,
  type PreparationPhase,
  type PreparationReport,
  type PreparationTarget,
} from '@/lib/api'
import { currentAttempt, prepView } from '@/lib/preparationText'
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
  /** 这个条目是从明确的「打开并运行」入口打开的：卡片上的「继续 / 再试一次」（重新打开）沿用这份意图，不让用户为同一个目的点两次 */
  wantsRun: boolean
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
  open: (target: PreparationTarget, opts?: { show?: boolean; run?: boolean }) => Promise<void>
  /** 只读补拉（SSE 提示 / 轮询 / 重连） */
  refresh: (key: string) => Promise<void>
  /** SSE `preparation.session`：只是「重读」的提示 */
  onHint: (sessionId: string) => void
  /** 事件流（重）连上：断线期间的进展一律以 GET 补拉（重启过的后端 404 → 重建只读检查） */
  refreshAll: () => void
  /** 认领后端生成的动作（run / cancel / recheck / prepare_dependencies）；报告里没有这个动作就什么都不做 */
  act: (key: string, kind: PreparationActionKind) => Promise<void>
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

/** 成功的报告里这次尝试真正捕获到的图（老后端没有这个字段 → 空） */
export const capturedOf = (report: PreparationReport | null): CapturedFigureDescriptor[] =>
  report?.phase === 'completed' && report.outcome.kind === 'succeeded' ? (report.captured ?? []) : []

/* ---------------------------------------------------------- 模块级账本（活得比一次 setState 长） */

const timers = new Map<string, ReturnType<typeof setTimeout>>()
const pollStep = new Map<string, number>()
const inflight = new Map<string, Promise<void>>()

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
    if (report.phase === 'completed' && (before?.phase !== 'completed' || attemptChanged)) {
      void onCompleted(report, owner)
      if (entry(key)?.armed) {
        // 「打开并运行」认领过：这一轮到头了——恰好一张图就直接进入编辑（多张要用户挑；没有图 / 静态不进）。
        // 这次无参数运行替换掉了旧图名（`unlinked_stems`，T09b）时不自动进入：卡片详情里「哪些旧图不再关联、怎么恢复」是用户必须看到的
        // 用户正看着这张卡（聚焦且展开）才自动进入：放到后台（角标）或收起之后他可能正在编辑别的图，不抢他的工作区——角标上仍有「进入编辑」
        patch(key, () => ({ armed: false }))
        const figures = capturedOf(report)
        const watching = get().focus === key && useUiStore.getState().guideCard === 'card'
        if (watching && figures.length === 1 && (report.unlinked_stems ?? []).length === 0) {
          void autoEnterEdit(key, figures[0], owner)
        }
      }
    } else if (entry(key)?.armed && endedWithoutEdit(report)) {
      patch(key, () => ({ armed: false }))
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
  const check = async (key: string, target: PreparationTarget, restarted: boolean) => {
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
  const armIfReady = async (key: string) => {
    const e = entry(key)
    const report = e?.report
    if (!e || !report || e.failure || e.pending || e.connection !== 'ok' || 'id' in e.target) return
    if (!report.actions.some((a) => a.kind === 'run_when_ready')) return
    // 只在卡片会说「可以运行了」（`ready`，没有要用户先看的事）时代认领：要先答的事（运行目录 / 环境 / 数据 / 必填参数）、要装的组件（用户该先看见
    // 装什么、装到哪）、弹窗 / 来源不明的提醒、重启后重建的会话都停在各自的卡上，由用户点那张卡的主按钮（它同样是 run_when_ready）
    const missing = missingArgsOf(report, e.target.argv ?? [])
    if (prepView(e, { argsChanged: draftDiffers(e.target), editReady: false, missingArgs: missing }).state !== 'ready') return
    // 沿用了上次成功的参数：一键运行之前让用户看一眼「将沿用 N 个参数」（卡上有清除），所以这一次不代认领
    if (reusedArgCount(e) > 0) return
    await get().act(key, 'run_when_ready')
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
      if (prev && !sameTarget) {
        targetGens.set(key, (targetGens.get(key) ?? 0) + 1)
        stopTimer(key)
        pollStep.delete(key)
        inflight.delete(key)
      }
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
            pending: null,
            // 「进入编辑」的记录属于那份目标的结果：换了参数就是另一批图，旧图的编辑不许混进新结果的判断
            editing: sameTarget ? (prev?.editing ?? []) : [],
            editRenders: sameTarget ? (prev?.editRenders ?? {}) : {},
            // 「打开并运行」的意图记号只属于明确点过它的这一次；同参数再打开不带过去，换参数时看旧会话里还有没有在等的意图
            armed: false,
            wantsRun: !!opts?.run,
            transferFrom: sameTarget
              ? (prev?.transferFrom ?? null)
              : prev?.report?.intent?.status === 'active'
                ? prev.report.session_id
                : null,
          },
        },
      }))
      if (opts?.show !== false) useUiStore.getState().setGuideCard('card')
      await check(key, target, false)
      if (opts?.run) await armIfReady(key)
    },

    refresh: (key) => {
      const running = inflight.get(key)
      if (running) return running
      const e = entry(key)
      if (!e) return Promise.resolve()
      const sid = e.report?.session_id
      if (!sid) {
        // 还没有任何报告（第一次检查断线了）：重新检查，仍是只读
        return check(key, e.target, false)
      }
      const g = guardOf(key)
      const pj = e.pj
      const promise = (async () => {
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
          inflight.delete(key)
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

    act: async (key, kind) => {
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
        // 认领了「打开并运行」：这一轮跑完时自动进入编辑（先记下，再接这份报告——它可能已经是 running）
        patch(key, () => ({ pending: null, ...(kind === 'run_when_ready' ? { armed: true, transferFrom: null } : {}) }))
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
          patch(key, () => ({ connection: 'lost' }))
        } else {
          // 修订变了 / 检查之后世界变了 / 影响变了 / 现在不能运行：零副作用的拒绝——说一句，重读报告
          const { code, params } = apiCode(err)
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
async function autoEnterEdit(key: string, figure: CapturedFigureDescriptor, owner: ProjectEpochGuard): Promise<void> {
  try {
    const { enterEdit } = await import('@/store/prepEnterEdit')
    if (!owner.still()) return
    const store = useProjectPreparationStore.getState
    await enterEdit({ epoch: () => store().epoch, note: (id, render) => store().noteEditing(key, id, render) }, figure)
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
  const sig = (s: typeof state) =>
    [
      s.inputRemapGeneration,
      s.probeResultsGeneration,
      s.env?.project?.workdir?.mode ?? '',
      String(s.env?.project?.workdir?.decided ?? ''),
      s.env?.project?.python ?? '',
      s.env?.project?.consent ?? '',
    ].join('|')
  if (sig(state) !== sig(prev)) useProjectPreparationStore.getState().recheckIdle()
})

/** 只给测试用：缩短看门狗与轮询节奏（产品里的阈值不变） */
export function __setPreparationTimingForTests(opts: { requestTimeoutMs?: number; pollMs?: readonly number[] }): void {
  if (opts.requestTimeoutMs !== undefined) REQUEST_TIMEOUT_MS = opts.requestTimeoutMs
  if (opts.pollMs !== undefined) POLL_MS = opts.pollMs
}
