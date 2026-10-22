/**
 * 「生成可编辑版本」（ADR 0143）的前端持有者：每个脚本一条 `idle → running → ready → adopting → adopted` 流水，
 * 失败落 `failed`（一句话 + 重试）。后端会话走既有的 `/api/ai/sessions/<sid>` 轮询（不接 aiStore：那条是改面板脚本的）。
 * 新脚本缺 Python 包要用户先决定时，任务以 `needs_input` 终局但候选留着：条目落 `deps`（依赖卡片：问一次 / 名单外点名 / 系统 Python），
 * 用户点主按钮 → `resuming`（后端装依赖并接着校验**同一个候选**，不重跑 Agent；关闭 / 停止会把取消传到正在进行的安装）→ `ready`。
 * 原脚本从不被这条流水改写；采用 = 后端新建兄弟文件，随后用既有的「打开并运行」接进画布（原位图面板不动）。
 */
import { create } from 'zustand'
import {
  ApiError,
  adoptConvert,
  agentById,
  aiCancel,
  backendErrorText,
  discardConvert,
  effectiveAgent,
  fetchAiSession,
  fetchConvert,
  isDefiniteRunFailure,
  replanConvertDependencies,
  resolveConvertDependencies,
  startConvert,
  type ConvertDependencyCard,
  type ConvertSourceLanguage,
  type ConvertStatus,
} from '@/lib/api'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { newRequestId, useAiStore } from '@/store/aiStore'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'

export type ConvertPhase = 'running' | 'deps' | 'resuming' | 'installed' | 'ready' | 'adopting' | 'adopted' | 'failed'

/** 一次续跑是哪种动作（装 / 换环境重判 / 装好后继续）：「暂时连不上」后重试，对账得知后端还没动时据此决定要不要重发 */
export interface ResumeKind {
  replanning: boolean
  /** 装依赖时前端回显的卡片摘要；replan / 继续没有 */
  digest: string | null
  /** 停止已被后端受理后对账连不上留下的「暂时连不上」：重试只对账（不重发任何 POST），并仍按「正在停止」处理结果 */
  stopped?: boolean
}

export interface ConvertEntry {
  phase: ConvertPhase
  session: string | null
  /** 失败时的一句话（已按当前语言渲染） */
  error: string | null
  /** 任务没过检查时的原因码（`render.capture` 的 `no_matplotlib_figure` 等）；卡片按它给一句话 */
  reason: string | null
  status: ConvertStatus | null
  /** 采用后新建的脚本（项目相对路径） */
  adopted: string | null
  /** 位图库名；R / MATLAB 来源没有 */
  library: string | null
  sourceLanguage: ConvertSourceLanguage
  path: string | null
  /** Agent 停下说明“哪一步换不了”的那一句（`needs_input`；后端已清洗并限长）；没有 = null */
  detail: string | null
  /** `deps` / `resuming` 时的依赖卡片事实（后端 `convertdeps` 的 card）；没有 = null */
  card: ConvertDependencyCard | null
  /** 系统 Python / 内置 runtime 卡上点了「选择环境」那一刻的环境身份；之后身份变了 = 用户真的换了环境，自动重新判定。没点过 / 已用掉 = null */
  envBase: string | null
  /** `resuming` 里这一轮是「按新环境重新检查」（卡片说“正在按新环境重新检查…”），不是装包 */
  replanning: boolean
  /** 续跑的 POST / 轮询遇到传输错误、对账也没连上时留下的动作（`failed` + `resume_unreachable`）：重试先对账，不去轮询已终结的 AI 会话；没有 = null */
  lost: ResumeKind | null
  /** 续跑期间点了「停止」、正在等后端对账（`resuming` 里显示“正在停止…”）：停止可能来得太晚（依赖已过提交点），不能先清卡 */
  stopping?: boolean
  /** `running`（AI 任务阶段）里点了「停止」却被拒 / 只是转交给别的进程，结果任务仍然做完了：`ready` 卡改说「转换已经完成，停止来得太晚」 */
  stoppedLate?: boolean
}

interface ConvertState {
  entries: Record<string, ConvertEntry>
  /** `language` 缺省 python（`library` 必填）；`r` / `matlab` 时 `library` 传 null */
  start: (script: string, library: string | null, path: string | null, language?: ConvertSourceLanguage) => Promise<void>
  adopt: (script: string) => Promise<string | null>
  discard: (script: string) => Promise<void>
  /** 依赖卡片的主按钮（问一次 / 名单外）：回显卡片摘要，后端装 + 接着校验同一个候选；`system` 卡片没有这个动作 */
  resolveDeps: (script: string) => Promise<void>
  /** 系统 Python / 内置 runtime 卡的主按钮：记下此刻的环境身份再打开现有的环境选择器（不另造第二个选择器）；选完且解释器真的变了由 `replanDeps` 接手 */
  chooseEnv: (script: string) => void
  /** 换了环境之后：按此刻的项目解释器重新判定**同一个**候选（后端 `replan`，不重跑 Agent）；结果是新卡 / 继续校验 */
  replanDeps: (script: string) => Promise<void>
  /** 停止来得太晚、依赖已经装好（`installed` 阶段）后用户点「继续」：接着校验同一个候选 */
  continueAfterInstall: (script: string) => Promise<void>
  stop: (script: string) => Promise<void>
  clear: (script: string) => void
}

export const POLL_MS = 1500

/**
 * 每个（项目, 脚本）一条的请求 id：同一次「生成」在**不确定是否已送达**（断网 / 超时 / 网关错）后重试要复用它，
 * 后端据此去重，不会再起第二个付费 Agent；明确拒绝或已拿到会话后作废（下一次换新 id）。
 * 键含发起那一刻的项目身份——两个项目里同名脚本的未决 id 互不相干（A 的未决 id 不会被 B 的成功删掉）。
 */
const pendingRequestIds = new Map<string, string>()
const scopeKey = (pj: string | null, script: string) => `${pj ?? ''}\u0000${script}`

/**
 * 每个（项目, 脚本）当前有效的“发起次序”：只有最新一次 `start` 能把失败写回条目。
 * 停止后立刻重试时，旧 POST 晚到的失败带着旧次序，核对不上就不写（不覆盖重试后的新会话）；停止 / 清除 / 换项目时作废。
 */
const attempts = new Map<string, { n: number; rid: string }>()

/**
 * 后端已**受理**的会话（POST 成功拿到 session id）：之后轮询 / 取候选的临时失败（断网、5xx）落到「重试」时，
 * 重试要**续查同一会话**，不能再 POST（会起第二个付费 Agent 或撞 `ai_script_busy`，首个候选也找不回）。
 * 只在明确终局（任务失败 / 放弃 / 停止 / 采用 / 会话已不存在 / 换项目）后清。
 */
const acceptedSessions = new Map<string, string>()
let attemptSeq = 0

const set1 = (script: string, patch: Partial<ConvertEntry>) =>
  useConvertStore.setState((s) => ({
    entries: { ...s.entries, [script]: { ...(s.entries[script] as ConvertEntry), ...patch } },
  }))

/** 后端失败原因 → 一句话（任务的 unmet 里挑第一条能说清的；说不清就笼统一句，由卡片的文案键给） */
function failureCode(unmet: { constraint_id: string; reason_code: string }[] | undefined): string {
  const first = (unmet ?? []).find((u) => u.constraint_id !== 'correction') ?? unmet?.[0]
  return first ? first.reason_code : 'unknown'
}

/** 任务结局里的依赖卡片事实（`unmet[].reason_code === 'dependency_decision'` 的那条的 `data`） */
function dependencyCardOf(
  unmet: { reason_code: string; data?: ConvertDependencyCard }[] | undefined,
): ConvertDependencyCard | null {
  return (unmet ?? []).find((u) => u.reason_code === 'dependency_decision' && u.data)?.data ?? null
}

/** 渲染环境的身份：全局解释器 + 本项目的来源 / 解释器。选环境前后各取一次，不同 = 解释器确实变了 */
const envIdentity = (): string => {
  const env = useEnvStore.getState().env
  return [env?.python ?? '', env?.project?.source ?? '', env?.project?.python ?? ''].join('\u0000')
}

/** 对账得到的后端真实状态 → 条目。回 true = 还在续跑（继续轮询） */
function applyConvertStatus(script: string, pj: string | null, status: ConvertStatus): boolean {
  if (status.state === 'resuming') return true
  if (status.state === 'discarded' && useConvertStore.getState().entries[script]?.stopping) {
    // 用户点了停止、后端确已删掉候选：这才清卡
    useConvertStore.getState().clear(script)
    return false
  }
  if (status.state === 'ready') {
    set1(script, {
      phase: 'ready',
      status,
      card: null,
      replanning: false,
      lost: null,
      stopping: false,
      stoppedLate: !!useConvertStore.getState().entries[script]?.stopping,
    })
  } else if (status.state === 'awaiting_dependencies' && status.dependency_card) {
    set1(script, { phase: 'deps', card: status.dependency_card, replanning: false, lost: null, stopping: false })
  } else if (status.state === 'deps_installed') {
    // 取消来得太晚：依赖已装好，候选保留，等用户点「继续」
    set1(script, { phase: 'installed', status, card: null, replanning: false, lost: null, stopping: false })
  } else {
    acceptedSessions.delete(scopeKey(pj, script))
    set1(script, {
      phase: 'failed',
      reason: status.failure?.reason ?? 'unknown',
      card: null,
      replanning: false,
      lost: null,
      stopping: false,
    })
  }
  return false
}

/**
 * 停止已被本进程接受（或会话已取消 / 以 `dependency_installed_late` 终局）后，按转换自己的状态（`GET /api/ai/convert/<sid>`）决定清不清：
 * 停止可能晚于依赖安装的提交点（环境已换代，后端把候选留成 `deps_installed`），这时不能清——要出「依赖已经装好了……」+「继续 / 放弃」。
 * `deps_installed` → 如实展示；候选 404 / `discarded` / 其余 → 清；
 * 409（任务线程还没收尾，meta 尚未落盘）→ 保持「正在停止…」稍后再问。条目被取代 / 换项目就静默退出。
 * 传输类错误（网络 / 超时 / 5xx 等非 404 非 409）同样重试，最多 `SETTLE_TRANSPORT_RETRIES` 次；仍不行**不清卡**
 * （停止已受理，但候选可能已被留成 `deps_installed`，清了用户就再也找不回）：落「暂时连不上」（`failed` + `resume_unreachable`，
 * `lost.stopped`），重试 / × 都先对账。只有明确的 404 / `discarded` / 其余真实状态才清。
 */
const SETTLE_TRANSPORT_RETRIES = 5

async function settleAfterStop(script: string, session: string, pj: string | null): Promise<void> {
  const guard = captureProjectEpoch()
  const mine = () => guard.still() && useConvertStore.getState().entries[script]?.session === session
  let transportFailures = 0
  for (;;) {
    let status: ConvertStatus | null = null
    try {
      status = await fetchConvert(session, pj)
    } catch (e) {
      if (!mine()) return
      if (e instanceof ApiError && e.status === 409) {
        await new Promise((r) => setTimeout(r, POLL_MS))
        if (!mine()) return
        continue
      }
      if (!(e instanceof ApiError && e.status === 404)) {
        transportFailures += 1
        if (transportFailures <= SETTLE_TRANSPORT_RETRIES) {
          await new Promise((r) => setTimeout(r, POLL_MS))
          if (!mine()) return
          continue
        }
        set1(script, {
          phase: 'failed',
          reason: 'resume_unreachable',
          error: null,
          card: null,
          replanning: false,
          lost: { replanning: true, digest: null, stopped: true },
          stopping: false,
        })
        return
      }
      // 404（候选已删）：本进程已终止任务，没有可展示的了
    }
    if (!mine()) return
    // 停止已被接受就不会再有 ready（提交闸门之后的取消被拒，走另一条对账）；唯一要留下的是「晚于安装提交点」的 deps_installed
    if (status?.state === 'deps_installed') {
      applyConvertStatus(script, pj, status)
    } else {
      useConvertStore.getState().clear(script)
    }
    return
  }
}

/**
 * 起一个「接着走同一个候选」的后端作业（装依赖 / 按新环境重新判定 / 装好后继续），再轮询它到结局。
 * 状态一律读 `GET /api/ai/convert/<sid>`（转换自己的状态），**从不**回头轮询那条已终结的 AI 会话。
 *
 * 传输类错误不直接判失败：POST 或轮询出错（网络 / 超时 / 5xx / 响应丢失，或 409 `convert_not_awaiting_dependencies`
 * 之类说明后端状态已变）→ 先对账；按后端真实状态展示（resuming 继续等 / ready 可采用 / 又停住换新卡 / 失败 / 已不存在）。
 * 对账也连不上 → 落「暂时连不上」（`failed` + `resume_unreachable`，保留动作 `lost`）；重试先对账，
 * 只有后端仍在等用户（`awaiting_dependencies`）且是同一张卡时才重发 POST，已在续跑 / 已 ready 绝不重发。
 * 被关闭 / 停止 / 重试覆盖或换项目就静默退出。
 */
async function driveResume(
  script: string,
  session: string,
  kind: ResumeKind,
  post: ((pj: string | null) => Promise<unknown>) | null,
  opts: { reconcileFirst?: boolean } = {},
): Promise<void> {
  const guard = captureProjectEpoch()
  const pj = currentProjectId()
  const get = useConvertStore.getState
  set1(script, { phase: 'resuming', error: null, reason: null, replanning: kind.replanning, envBase: null, lost: null })
  const mine = () => {
    const now = get().entries[script]
    return !!now && now.session === session
  }
  // 重试路径允许在对账发现后端仍在等用户时重发一次 POST；POST 自己出错后的对账不重发（可能是响应丢了）
  let canResend = !!opts.reconcileFirst && post !== null
  let reconcile = !!opts.reconcileFirst
  if (post && !opts.reconcileFirst) {
    try {
      await post(pj)
    } catch {
      if (!guard.still() || !mine()) return
      reconcile = true
    }
  }
  for (;;) {
    if (!reconcile) {
      await new Promise((r) => setTimeout(r, POLL_MS))
      if (!guard.still()) return
      const now = get().entries[script]
      if (!now || now.session !== session || now.phase !== 'resuming') return // 被关闭 / 停止 / 重试覆盖
    }
    let status: ConvertStatus
    try {
      status = await fetchConvert(session, pj)
    } catch (e) {
      if (!guard.still() || !mine()) return // 关闭 / 停止时候选被后端删掉，轮询会 404：那是用户自己点的，条目已经清了
      if (e instanceof ApiError && e.status === 404) {
        acceptedSessions.delete(scopeKey(pj, script))
        if (get().entries[script]?.stopping) {
          get().clear(script) // 停止已被后端受理、候选已删：这才清卡
          return
        }
        set1(script, { phase: 'failed', error: backendErrorText(e), reason: null, card: null, replanning: false, lost: null })
        return
      }
      if (!reconcile) {
        reconcile = true // 轮询瞬时失败：马上对账一次
        continue
      }
      set1(script, { phase: 'failed', reason: 'resume_unreachable', error: null, replanning: false, lost: kind, stopping: false })
      return
    }
    if (!guard.still() || !mine()) return
    const wasReconcile = reconcile
    reconcile = false
    if (wasReconcile && canResend && post && status.state === 'awaiting_dependencies' && status.dependency_card) {
      canResend = false
      const card = status.dependency_card
      // 后端还停在原处、没收到那次点击：同一张卡（摘要一致）或 replan 才替用户重发；卡变了让用户看新卡再点
      if (kind.replanning || (kind.digest !== null && card.impact_digest === kind.digest)) {
        try {
          await post(pj)
        } catch {
          if (!guard.still() || !mine()) return
          reconcile = true
        }
        continue
      }
    }
    if (applyConvertStatus(script, pj, status)) continue
    return
  }
}

/** 重试「暂时连不上」的续跑：先对账；仍在等用户且是同一张卡才重发同一个动作 */
function resumeAfterLost(script: string, session: string, kind: ResumeKind): Promise<void> {
  if (kind.stopped) {
    // 停止已受理后对账连不上：只对账，不重发任何动作；结果按「正在停止」处理（候选已删 → 清卡）
    set1(script, { stopping: true })
    return driveResume(script, session, kind, null, { reconcileFirst: true })
  }
  return driveResume(script, session, kind, postFor(session, kind), { reconcileFirst: true })
}

/** 按动作种类造 POST（装 = 回显摘要；其余 = replan 入口） */
const postFor = (session: string, kind: ResumeKind) => (pj: string | null) =>
  !kind.replanning && kind.digest !== null
    ? resolveConvertDependencies(session, kind.digest, pj)
    : replanConvertDependencies(session, pj)

export const useConvertStore = create<ConvertState>((set, get) => ({
  entries: {},

  start: async (script, library, path, language = 'python') => {
    const guard = captureProjectEpoch()
    const pj = currentProjectId()
    // 续查只认「失败卡上留着的、且登记为已受理」的那个会话（条目被重置后遗留的登记不算）
    const prior = get().entries[script]
    // 续跑（装 / 重判 / 继续）遇到传输错误落下的「暂时连不上」：先以转换自己的状态对账，再决定要不要重发——
    // 不能去轮询那条已终结的 AI 会话（它永远是 needs_input，会盖掉已经 ready 的候选）
    if (prior?.phase === 'failed' && prior.session && prior.lost) {
      await resumeAfterLost(script, prior.session, prior.lost)
      return
    }
    const resumable = prior?.phase === 'failed' && prior.session ? prior.session : null
    set((s) => ({
      entries: {
        ...s.entries,
        [script]: {
          phase: 'running',
          session: null,
          error: null,
          reason: null,
          status: null,
          adopted: null,
          library,
          sourceLanguage: language,
          path,
          detail: null,
          card: null,
          envBase: null,
          replanning: false,
          lost: null,
        },
      },
    }))
    const mine = get().entries[script]
    const key = scopeKey(pj, script)
    const attempt = ++attemptSeq
    let rid = pendingRequestIds.get(key)
    if (!rid) {
      rid = newRequestId()
      pendingRequestIds.set(key, rid)
    }
    attempts.set(key, { n: attempt, rid })
    const isCurrent = () => attempts.get(key)?.n === attempt
    try {
      let session = resumable && acceptedSessions.get(key) === resumable ? resumable : undefined
      if (!session) {
        // 与既有 AI 流程同一套「实际派活的 Agent」：用户选了 Claude 或 Codex 不可用时不能默认成 codex
        const ai = useAiStore.getState()
        if (!ai.caps) await ai.loadCaps().catch(() => undefined)
        const { agent: preferred, caps, models, efforts } = useAiStore.getState()
        const agent = effectiveAgent(preferred, caps)
        const info = agent ? agentById(caps, agent) : undefined
        const model = agent ? (models[agent] ?? info?.default_model ?? null) : null
        const effort = agent && info?.efforts.length ? (efforts[agent] ?? info.default_effort ?? null) : null
        const posted = await startConvert(
          {
            script,
            // Python 来源带位图库；R / MATLAB 来源带语言、不带库（后端据此分流并复核文件内容）
            ...(language === 'python' ? { library: library ?? undefined } : { source_language: language }),
            path,
            client_request_id: rid,
            ...(agent ? { agent, model, effort } : {}),
          },
          pj,
        )
        session = posted.session
        // 只有当前 attempt 能清 id：被取代的旧 POST（同一 id）先成功时不清，等新 attempt 自己落定
        if (isCurrent()) pendingRequestIds.delete(key)
        if (!guard.still()) return
        if (!isCurrent() || get().entries[script] !== mine) {
          // 等 POST 期间用户点了「停止」/ 换了脚本流水：后端已起了会话，要真的取消，不能让它悄悄跑完
          // 例外：更新的一次发起复用了同一个请求 id（停止后立刻重试）——后端去重成同一个会话，那是新一次在用的，不取消
          const newer = attempts.get(key)
          if (!isCurrent() && newer && newer.rid === rid) return
          try {
            await aiCancel(session, pj)
          } catch {
            /* 取消失败：会话自己会收尾 */
          }
          return
        }
        acceptedSessions.set(key, session)
      }
      set1(script, { session })
      for (;;) {
        await new Promise((r) => setTimeout(r, POLL_MS))
        if (!guard.still()) return
        const cur = get().entries[script]
        if (!cur || cur.session !== session || cur.phase !== 'running') return // 被放弃 / 重试覆盖
        let sess: Awaited<ReturnType<typeof fetchAiSession>>
        try {
          sess = await fetchAiSession(session, pj)
        } catch (e) {
          if (!guard.still()) return
          // 停止已发出、会话已不存在：没有可对账的了，清卡（与「最终 cancelled」同一结局）
          if (e instanceof ApiError && e.status === 404 && get().entries[script]?.stopping) {
            get().clear(script)
            return
          }
          throw e
        }
        if (!guard.still()) return
        if (!sess.final && sess.status === 'running') continue
        const wasStopping = !!get().entries[script]?.stopping
        if (sess.task?.outcome === 'completed' && sess.status === 'done') {
          let status: ConvertStatus
          try {
            status = await fetchConvert(session, pj)
          } catch (e) {
            if (!guard.still()) return
            if (e instanceof ApiError && e.status === 404 && wasStopping) {
              get().clear(script) // 停止期间候选已不存在：这才清
              return
            }
            throw e
          }
          if (!guard.still()) return
          // 与续跑阶段的「正在停止」对账是同一套：ready → 可采用（stoppedLate 说明），discarded → 清，其余如实展示
          if (wasStopping) applyConvertStatus(script, pj, status)
          else set1(script, { phase: 'ready', status })
        } else if (sess.status === 'cancelled') {
          // 取消可能晚于依赖安装的提交点（候选留成 deps_installed）：先对账再清
          await settleAfterStop(script, session, pj)
        } else if (
          sess.task?.outcome === 'needs_input' &&
          (sess.task.unmet ?? []).some((u: { reason_code: string }) => u.reason_code === 'dependency_installed_late')
        ) {
          await settleAfterStop(script, session, pj)
        } else if (sess.task?.outcome === 'needs_input' && dependencyCardOf(sess.task.unmet)) {
          // 新脚本缺包、要用户先决定：候选留在后端等着（不删），卡片的按钮接着走同一个候选
          set1(script, { phase: 'deps', card: dependencyCardOf(sess.task.unmet), stopping: false })
        } else if (sess.task?.outcome === 'needs_input') {
          // Agent 停下说明：Python 里没有可靠的逐一对应。再试一次也是同样的结论，卡片只说原因
          acceptedSessions.delete(key)
          const unmet = sess.task.unmet ?? []
          const why = unmet.find((u) => typeof u.detail === 'string' && u.detail)?.detail ?? null
          set1(script, { phase: 'failed', reason: 'needs_input', detail: why, stopping: false })
        } else {
          acceptedSessions.delete(key) // 任务明确终局失败：重试起新会话
          set1(script, {
            phase: 'failed',
            reason: failureCode(sess.task?.unmet as { constraint_id: string; reason_code: string }[] | undefined),
            stopping: false,
          })
        }
        return
      }
    } catch (e) {
      if (isDefiniteRunFailure(e) && isCurrent()) {
        pendingRequestIds.delete(key)
        acceptedSessions.delete(key) // 会话已不存在 / 被明确拒绝：没有可续查的了
      }
      if (!guard.still()) return
      if (!isCurrent()) return // 已被停止 / 被更新的一次发起取代：旧 POST 的失败不能写进新会话的条目
      set1(script, { phase: 'failed', error: backendErrorText(e) })
    }
  },

  adopt: async (script) => {
    const cur = get().entries[script]
    if (!cur?.session) return null
    const guard = captureProjectEpoch()
    set1(script, { phase: 'adopting', error: null, reason: null })
    try {
      const made = await adoptConvert(cur.session, currentProjectId())
      if (!guard.still()) return null
      acceptedSessions.delete(scopeKey(currentProjectId(), script))
      set1(script, { phase: 'adopted', adopted: made.script })
      return made.script
    } catch (e) {
      if (!guard.still()) return null
      set1(script, { phase: 'failed', error: backendErrorText(e) })
      return null
    }
  },

  resolveDeps: async (script) => {
    const cur = get().entries[script]
    if (!cur?.session || cur.phase !== 'deps' || !cur.card || cur.card.kind === 'system') return
    const digest = cur.card.impact_digest
    // 前端回显它看到的卡片摘要：服务端不自回显（对不上就换新卡片、不装）
    const kind: ResumeKind = { replanning: false, digest }
    await driveResume(script, cur.session, kind, postFor(cur.session, kind))
  },

  chooseEnv: (script) => {
    const cur = get().entries[script]
    if (!cur?.session || cur.phase !== 'deps' || !cur.card) return
    set1(script, { envBase: envIdentity() })
    useUiStore.getState().openEngineEnvPicker()
  },

  replanDeps: async (script) => {
    const cur = get().entries[script]
    if (!cur?.session || cur.phase !== 'deps' || !cur.card) return
    const kind: ResumeKind = { replanning: true, digest: null }
    await driveResume(script, cur.session, kind, postFor(cur.session, kind))
  },

  continueAfterInstall: async (script) => {
    const cur = get().entries[script]
    if (!cur?.session || cur.phase !== 'installed') return
    // 已不缺包：replan 入口直接接着校验同一个候选（不是“换了环境”，replanning=false 只影响文案）
    const kind: ResumeKind = { replanning: false, digest: null }
    await driveResume(script, cur.session, kind, (pj) => replanConvertDependencies(cur.session as string, pj))
  },

  discard: async (script) => {
    const cur = get().entries[script]
    get().clear(script)
    if (cur?.session && (cur.phase === 'ready' || cur.phase === 'deps' || cur.phase === 'resuming' || cur.phase === 'installed')) {
      try {
        await discardConvert(cur.session, currentProjectId())
      } catch {
        /* 放弃失败不挡路：候选 24 小时后自己过期 */
      }
    }
  },

  stop: async (script) => {
    const cur = get().entries[script]
    if (cur?.session && cur.phase === 'resuming') {
      // 续跑期间的停止可能来得太晚（依赖步骤已过提交点）：不能先清卡——先进「正在停止…」，
      // 由 driveResume 的轮询按后端真实状态收尾（候选已删 → 清卡；deps_installed → 「继续 / 放弃」卡）
      const session = cur.session
      const pj = currentProjectId()
      const guard = captureProjectEpoch()
      set1(script, { stopping: true })
      try {
        await discardConvert(session, pj)
      } catch {
        // discard 请求本身失败：不知道后端到底停没停——先对账再按结果展示
        if (!guard.still()) return
        if (get().entries[script]?.session !== session) return
        try {
          const status = await fetchConvert(session, pj)
          if (!guard.still() || get().entries[script]?.session !== session) return
          if (!applyConvertStatus(script, pj, status)) return
          set1(script, { stopping: false }) // 还在续跑、取消没送到：回到可再点停止的状态
        } catch (e) {
          if (!guard.still() || get().entries[script]?.session !== session) return
          if (e instanceof ApiError && e.status === 404) get().clear(script)
          else set1(script, { stopping: false })
        }
      }
      return
    }
    if (cur?.session && cur.phase === 'failed' && cur.lost) {
      // 「暂时连不上」不等于后端停了：依赖 POST 可能已受理、安装 / 校验仍在跑。× 与续跑中的停止同一条路——
      // 先进「正在停止…」、发 discard，再对账（候选已删 → 清卡；deps_installed → 「继续」；ready → 可采用）；对账再连不上就回到「暂时连不上」
      const session = cur.session
      const kind = cur.lost
      const pj = currentProjectId()
      const guard = captureProjectEpoch()
      set1(script, { phase: 'resuming', stopping: true, lost: null, error: null, reason: null, replanning: kind.replanning })
      try {
        await discardConvert(session, pj)
      } catch {
        /* discard 请求本身失败：不知道后端停没停——下面照样先对账再按结果展示 */
      }
      if (!guard.still() || get().entries[script]?.session !== session) return
      await driveResume(script, session, kind, null, { reconcileFirst: true })
      return
    }
    if (cur?.session && cur.phase === 'running') {
      // AI 任务阶段：先发取消、**看回复**再决定清不清。`ok:true, mode:"local"` = 本进程已终止 → 先对账一次转换状态（停止可能晚于安装提交点，候选会留成 `deps_installed`）再清；
      // `ok:false`（refused_committing 等：任务已进提交闸门）或 `mode:"owner"`（只是转交给别的进程、结局未定）→ 条目留着，
      // 丢了会话号，候选一旦 ready 就找不回、Retry 还会再起一次付费 Agent。留下的条目由 start() 的轮询按会话 `final` + 转换状态对账。
      const session = cur.session
      const guard = captureProjectEpoch()
      let res: { ok: boolean; mode?: string } | null = null
      set1(script, { stopping: true }) // 先进「正在停止…」：回复到之前不知道结局，不能先清
      try {
        res = await aiCancel(session, currentProjectId())
      } catch {
        res = null // 请求本身失败：不知道取消到没到——不清，也不进「正在停止」，用户可再点
      }
      if (!guard.still() || get().entries[script]?.session !== session) return
      if (res === null) {
        set1(script, { stopping: false })
        return
      }
      // 本进程已终止任务，但终止可能晚于依赖安装的提交点（候选留成 deps_installed）：先对账再决定清不清，不立即清
      if (res.ok && res.mode !== 'owner') await settleAfterStop(script, session, currentProjectId())
      // 否则保持 stopping：start() 的轮询按会话 final + 转换状态对账
      return
    }
    get().clear(script)
    if (cur?.session && (cur.phase === 'deps' || cur.phase === 'installed')) {
      // 任务已经以 needs_input 终局，没有可取消的任务了；候选由 discard 删除
      try {
        await discardConvert(cur.session, currentProjectId())
      } catch {
        /* 放弃失败不挡路：候选 24 小时后自己过期 */
      }
    } else if (cur?.session) {
      try {
        await aiCancel(cur.session, currentProjectId())
      } catch {
        /* 取消失败：会话自己会收尾 */
      }
    }
  },

  clear: (script) => {
    attempts.delete(scopeKey(currentProjectId(), script))
    acceptedSessions.delete(scopeKey(currentProjectId(), script))
    set((s) => {
      const { [script]: _gone, ...rest } = s.entries
      return { entries: rest }
    })
  },
}))

// 换项目：上一个项目的流水一律丢弃（脚本名可能在两个项目里重名）
onCurrentProjectChange(() => {
  attempts.clear()
  acceptedSessions.clear()
  useConvertStore.setState({ entries: {} })
})

// 用户在环境选择器里选完、解释器确实变了：armed 的系统 / 内置卡自动按新环境重新判定（没变 = 什么都不做，保持原卡）
useEnvStore.subscribe((state, prev) => {
  if (state.env === prev.env) return
  const now = envIdentity()
  for (const [script, e] of Object.entries(useConvertStore.getState().entries)) {
    if (e.phase === 'deps' && e.card && e.envBase !== null && e.envBase !== now) {
      void useConvertStore.getState().replanDeps(script)
    }
  }
})
