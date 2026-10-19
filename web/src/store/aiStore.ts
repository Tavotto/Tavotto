import { create } from 'zustand'
import { t } from '@/i18n'
import { currentProjectId } from '@/lib/session'
import {
  AI_REQUEST_NOT_FOUND,
  agentById,
  agentDisplayName,
  aiCancel,
  aiRevert,
  aiRun,
  ApiError,
  effectiveAgent,
  fetchAiCapabilities,
  fetchAiHistory,
  type AiHistoryEntry,
  fetchAiRequest,
  fetchAiSession,
  isDefiniteRunFailure,
  usableAgents,
  type AiSessionDetail,
  type AiAgentId,
  type AiCapabilities,
  type AiDeltaKind,
  type AiRefreshOutcome,
} from '@/lib/api'
import type { DraftTarget, LedgerWrite } from '@/lib/aiDraft'

export type AiStatus = 'running' | 'done' | 'failed' | 'timeout' | 'cancelled' | 'reverted'
/** 改动作用范围：决定发给后端的上下文粒度（gid/label 怎么拼） */
export type AiScope = 'element' | 'axes' | 'figure'

/** 一条 CLI 输出：正文进气泡，思考/动作折叠成过程 */
export interface AiEntry {
  kind: AiDeltaKind
  text: string
  /** 仍在逐字流入，渲染时带闪烁光标 */
  streaming?: boolean
  /**
   * 这一条到达的时刻（ms，前端时钟）。助手面板拿相邻两条的差算每步耗时与「已思考 Ns」
   * （2026-10-07 设计审计 §6.2）；缺省（旧夹具）时不显示耗时，绝不编一个数。
   */
  at?: number
}

export interface AiSession {
  id: string
  agent: AiAgentId
  /**
   * 发起时的显示名快照。历史条目活得比一次探测长：capabilities 暂时取不到
   * （后端重启 / 刷新失败）时，标签不能变成空白，也不该退回一个内部 id。
   */
  agentLabel: string
  prompt: string
  script: string
  /** 发起时选中的面板与元素，用于结束后精准刷新 */
  /** 会话所属的项目（发起那一刻认领的 pj）：撤销 / 中止都发往它，不跟着此刻的项目走（#589） */
  project: string | null
  panelId: string | null
  fileId: string | null
  gid: string | null
  /** 发起时的作用范围与目标名，历史视图靠它说明「改的是哪儿」 */
  scope: AiScope
  target: string
  entries: AiEntry[]
  status: AiStatus
  changed: boolean
  diff: string
  error?: string
  startedAt: number
  /** 会话结束（done / failed / timeout / cancelled）的时刻：状态行的总耗时与最后一步的耗时以它收尾 */
  finishedAt?: number
  /**
   * 后端的 `ai.done` 已经落过一次（终态只认第一次）：重复 / 迟到的 `ai.done` 不改写它。
   * 本地先标的 `cancelled` 不算——真正的结局（diff、changed）还要靠随后那条 `ai.done` 补上。
   */
  finalized?: boolean
  /**
   * 查询接回时拿到的是**临时快照**（非 running / cancelled，却还不是终态：如另一个服务进程的 `interrupt_all()` 刚写了
   * `interrupted`、pump 还没收尾）。本地 status 按 `toStatus` 映射（interrupted → failed），这个标志让它照样留在
   * `isOpenSession` 里继续对账，直到拿到终态（`finish` 置 finalized）
   */
  provisional?: boolean
  /**
   * 刷新 / 重连 / 回切之后从后端历史**接回来**的运行中会话（只读占位，PR4b）：此前的输出不再回放，
   * 之后的 `ai.delta` / `ai.done` 按 session id 认领到它上面；它同样锁住同一脚本的发送、可中止。
   */
  restored?: boolean
  /** 接回的占位对应的请求 id（来自历史库）：与未落定的提交按它对上，**对上就等于已被接受**，不必再让用户去查 */
  requestId?: string
}

/**
 * 一次**已点发送、后端还没给出会话 id** 的提交（ADR 0132）。它是「不得让同一条 AI 修改被无意执行两次」
 * 的前端半边：
 * - `sending`：请求在路上（`aiRun` 有 30 s 超时）。
 * - `unconfirmed`：已发出，但没收到明确答复（超时 / 断网 / 网关错）。**后端可能已经接受并在改脚本**，
 *   所以此态禁用一切「发送 / 重试」，只允许 `queryRequest`（按同一个 rid 问后端）。
 * - `not_received`：问过了，后端明确说没登记过——此时才允许用**同一个 rid** 重发（后端按 rid 去重，
 *   与迟到的原请求赛跑也不会双跑）。
 * 后端明确拒绝（4xx / 带稳定 code 的 5xx）不在这里：那是失败，提交记录当场删掉，下一次发送换新 rid。
 */
export type SubmissionPhase = 'sending' | 'unconfirmed' | 'not_received'

export interface AiSubmission {
  rid: string
  project: string | null
  fileId: string
  panelId: string
  /**
   * 面板对应的脚本。**发送锁按脚本判、不按面板**：同一份脚本可以产出多个面板文件，A 面板的请求没有答复时，
   * 切到同一脚本的 B 面板重发同一条指令会把脚本改两遍（Codex #1098）。
   */
  script: string | null
  prompt: string
  /** 发出那一刻钉住的目标（重发不跟着此刻的选择走） */
  target: DraftTarget
  canvas: string | null
  overrides: unknown[]
  phase: SubmissionPhase
}

/** 早于 HTTP 响应到达的 SSE 事件，等会话建好再按序回放 */
type EarlyEvent =
  | { t: 'delta'; kind: AiDeltaKind; text: string }
  | { t: 'done'; p: DonePayload }

export interface DonePayload {
  session: string
  status: string
  changed: boolean
  diff: string
  error?: string
  client_request_id?: string
  refresh?: AiRefreshOutcome
}

/** 发送前要先把提交记进后端账本；没记成就不发（界面一句话 + 重试） */
/** 发送的项目在中途变了（切项目会换代）：这次发送作废，不发送、不写账本 */
export class ProjectChangedError extends Error {
  constructor() {
    super('project-changed')
  }
}

export class AiLedgerError extends Error {
  constructor() {
    super('ledger-unavailable')
  }
}

export type SubmitOutcome = 'accepted' | 'unconfirmed'
/** `already_run`：后端登记过它、但任务历史被用户删了（结局不可知）——已执行过，绝不重发 */
/** `superseded`：这条请求已被改写后的新指令取代（后端的墓碑预留）——一定没执行也不会再执行，不能再提供同 rid 重发 */
export type QueryOutcome = 'accepted' | 'already_run' | 'not_received' | 'unreachable' | 'gone' | 'superseded'

interface AiState {
  /**
   * 用户**首选**的 Agent。它可能暂时不可用——那时实际派活的是
   * `effectiveAgent(preferred, caps)`，但这里保持原样：首选恢复可用后
   * 它还该是默认项，悄悄改掉等于替用户做了决定。
   */
  agent: AiAgentId | null
  /** 用户选择的作用范围；目标不支持时由面板降级，不改这里 */
  scope: AiScope
  sessions: AiSession[]
  /**
   * 项目代际：`clear()` 时 +1。每个 await 之后、写状态之前核一次——包括「写进别的 store」
   * （撤销之后标脏渲染、发状态提示），那些由调用方在 `revert()` 回 `true` 之后才做（#605 的教训：
   * 被调的一方先写了状态，调用方回头再判就晚了）。助手面板以它为 key 重挂，草稿 / 错误 /
   * 历史视图这些组件内状态跟着换代。
   */
  generation: number
  /** 本机 CLI 实测能力；null = 尚未探测（界面显示「正在检测」，不是「未安装」） */
  caps: AiCapabilities | null
  /** 每个 Agent 各自的模型 / 推理强度选择（能力不同构，不共用） */
  models: Record<AiAgentId, string>
  efforts: Record<AiAgentId, string>
  loadCaps: (refresh?: boolean) => Promise<void>
  setAgent: (a: AiAgentId) => void
  setScope: (s: AiScope) => void
  setModel: (agent: AiAgentId, model: string) => void
  setEffort: (agent: AiAgentId, effort: string) => void
  /** 已点发送、后端还没给会话 id 的提交，按请求 id 索引（见 `AiSubmission`） */
  submissions: Record<string, AiSubmission>
  /**
   * 发一条新任务（生成新的请求 id）。后端明确拒绝时**抛出**（提交记录已删，可换新 id 重试）；
   * 超时 / 断网等「不知道后端收到没有」回 `'unconfirmed'`，不抛——那不是失败。
   */
  start: (req: {
    /**
     * **发送所属的（项目 id, 项目代次）：调用方在第一个 await 之前固定、一路传进来**。`start` 不再自己读「当前项目」——
     * 点发送后 `await draftSnapshot()` 期间切到了另一个项目，再读就会把同名文件的指令发到别的项目里。
     * 代次变了（切过项目）= 这次发送作废：抛 `ProjectChangedError`，不发送、不写账本。
     */
    project: string | null
    generation: number
    prompt: string
    fileId: string
    panelId: string
    gid: string | null
    label: string | null
    scope: AiScope
    target: string
    overrides: unknown[]
    canvas?: string | null
    script?: string | null
    /** 提交时草稿的写入戳（发送前已把草稿写成功拿到的）；确认后后端按「草稿戳没变」清草稿，不比文本 */
    draftStamp?: number | null
  }) => Promise<SubmitOutcome>
  /**
   * 把后端此刻仍在跑的会话接回来（只读占位），并把本地还当它在跑、后端却已结束的会话收尾
   * （断线期间的 `ai.done` 没收到）。刷新 / SSE 重连 / 面板挂载时调用；只读，按项目隔离，失败静默。
   */
  restoreRunning: () => Promise<void>
  /** 问后端「这个请求 id 你登记过吗」：只读，不会让任何东西被执行 */
  queryRequest: (rid: string) => Promise<QueryOutcome>
  /**
   * 对本地所有**非终态**的会话各读一次后端快照、按快照收尾（`accept` 之后任务在 SSE 断线期间结束，事件流不重放，
   * 没有这一步会话会永远停在 running、把脚本锁死）。`useAiDraft.reconcile` 的每个入口（挂载 / 焦点 / 可见 / SSE 重连）
   * 都会调它；读失败 = 不知道，不动。
   */
  settleRunningSessions: () => Promise<void>
  /** 后端明确说没收到之后，用**同一个**请求 id 重发 */
  resend: (rid: string) => Promise<SubmitOutcome>
  /** 放弃等待这条提交（用户改写后重新发）：之后的发送换新 id。仅 `not_received` 时由界面提供 */
  dismissSubmission: (rid: string) => void
  /** 刷新 / 重挂后按草稿里的 rid 还原「已发出未确认」 */
  restoreSubmission: (sub: Omit<AiSubmission, 'phase'>) => void
  appendDelta: (sid: string, kind: AiDeltaKind, text: string, rid?: string) => void
  /** 回 `true` = 这是本标签页此刻持有的会话、且这是它的第一条 `ai.done`；`false` 时调用方不许再做任何副作用（#589） */
  finish: (p: DonePayload) => boolean
  /** 回 `false` = 这次撤销的结果被丢弃了（期间换过项目）：调用方不许再往别的 store 里写它的后果 */
  revert: (sid: string) => Promise<boolean>
  cancel: (sid: string) => Promise<void>
  /**
   * 切项目：对话历史属于旧项目，整份丢掉并换代（#589）。在途的 `start` / `revert` / `cancel` 回来时
   * 代际对不上就不落地。**不取消后端任务**：切项目不是「我不要这次修改了」，它照样改完、记进 A 的
   * 历史（`/api/ai/history` 按项目存），切回 A 在历史里看得到。Agent 首选、作用范围、模型 / 强度、
   * `caps` 是本机偏好与探测，不属于项目，不清。
   */
  clear: () => void
}

const MAX_LINES = 600

/** 早到事件缓冲的上限：只为「事件比 HTTP 响应先到」的那几十毫秒，不是日志 */
const EARLY_MAX_SIDS = 32
const EARLY_MAX_EVENTS = 500
const early = new Map<string, EarlyEvent[]>()
/** 账本写入「不知道」写没写成的那条指令（按 项目×文件）：重试沿用同一个 rid */
const ledgerUnknown = new Map<string, { sub: AiSubmission; sentJson: string }>()
/**
 * 被放弃的旧等待（rid → 它属于哪个 项目×面板×脚本）：下一次发新 rid 时，随新账本行的 PUT 带 `supersedes`，由后端在同一个事务里删并写墓碑。
 * 发送时按**与 `submissionBlocks` 同一个判据**挑：同项目，且同面板或 `sameScript`——同一份脚本的兄弟面板会被对账还原出别的面板发起的
 * 等待，「改写后再发」之后从这个面板发也必须带上它，否则旧 rid 的慢 POST 在新指令之后登记，两条都执行。不按路径字符串做 key：
 * `sameScript` 把分隔符不同的写法、旧数据的绝对路径与它的相对后缀都认作同一份，字符串 key 会把它们分进不同的桶。
 * 每一项都是**完整的提交**（类型上不可省）：取代被后端拒绝（`ai_supersede_conflict`，旧请求其实已登记——可能是本页的慢 POST，
 * 也可能是别的标签页还原账本行后发出的）时，原样还原它、继续锁住这份脚本并按它的 rid 查询，不存在「无从还原」的那一路
 */
const supersedeQueue = new Map<string, AiSubmission>()
const queueSupersede = (sub: AiSubmission): void => {
  supersedeQueue.set(sub.rid, sub)
}
/**
 * **非终态会话的唯一谓词**：还没拿到后端确定的终态快照 / `ai.done`（`!finalized`）。
 * 本地 `cancel` 成功后状态先标成 `cancelled`，但那只是「请求中止已被接受」，后端的最终结局（changed / diff / 是否真的停下）
 * 还没到——所以 running 和「cancelled 但未 finalized」都要继续对账，直到后端给出确定的终态；查询接回的临时快照
 * （`interrupted` 还没收尾：另一个服务进程的 `interrupt_all()` 刚写了 status）在本地映射成 `failed` 但同样没 finalized，也要继续。
 * 那种会话由 `accept` 标 `provisional`（本地 status 已不是 running / cancelled，靠这个标志留在谓词里）。
 * `settleRunningSessions` 与安全网轮询都调它，不各写一份。
 */
export const isOpenSession = (x: Pick<AiSession, 'status' | 'finalized' | 'provisional'>): boolean =>
  !x.finalized && (x.status === 'running' || x.status === 'cancelled' || x.provisional === true)

/**
 * 一份会话快照是不是**终态**：优先认后端明确给的 `final`（`_pump` 收尾、changed / diff / refresh 都确定之后）。
 * 没有 `final` 字段的后端退回 #1098 的判据：非 running；而 `cancelled` / `interrupted` 必须带着 `ended_ms`（pump 收尾后才写；
 * `interrupt_all()` 与 cancel 一样只先写 status）。
 * 没有详情（404）时无从判断，按 status 走。
 * `cancel` 之后后端内存里的 status 立刻是 `cancelled`、但结果还没算出来——那不是终态，过早收尾会把之后真正的 `ai.done` 当重复丢掉。
 */
export function snapshotIsFinal(
  d: Pick<AiSessionDetail, 'status' | 'final' | 'ended_ms'> | null | undefined,
  fallbackStatus: string,
): boolean {
  if (typeof d?.final === 'boolean') return d.final
  const status = typeof d?.status === 'string' && d.status ? d.status : fallbackStatus
  if (status === 'running') return false
  if ((status === 'cancelled' || status === 'interrupted') && d) return typeof d.ended_ms === 'number'
  return true
}

/**
 * 终态快照是转录的**权威来源**：用快照的条目替换本地 entries（断线期间丢掉的补回来，按顺序对齐、不重复）。
 * 本地有快照里没有的条目（不在流式中、也不是快照某条的前缀）保留，按它在本地的相对位置插回。
 */
export function mergeTranscript(local: AiEntry[], snapshot: { kind: string; text: string }[]): AiEntry[] {
  const snap: AiEntry[] = snapshot.map((t) => ({ kind: t.kind as AiDeltaKind, text: t.text }))
  const out: AiEntry[] = []
  let sp = 0
  for (const l of local) {
    let k = -1
    for (let i = sp; i < snap.length; i++) {
      if (snap[i].kind === l.kind && snap[i].text === l.text) {
        k = i
        break
      }
    }
    if (k >= 0) {
      for (let i = sp; i < k; i++) out.push({ ...snap[i], at: l.at })
      out.push({ ...snap[k], ...(l.at !== undefined ? { at: l.at } : {}) }) // 本地已有的那条保留它到达的时刻
      sp = k + 1
      continue
    }
    // 没对上：只有快照里**确实有**一条同类、以它开头的完整条目时，它（流式半截 / 前缀）才算被取代；
    // 否则保留——后端不持久化 delta，断线前已看到的半截在快照里本来就没有，丢掉就是让用户看过的输出消失
    const superseded = snap.some((s) => s.kind === l.kind && s.text.startsWith(l.text))
    if (!superseded) out.push({ ...l, streaming: false })
  }
  for (let i = sp; i < snap.length; i++) out.push(snap[i])
  return out
}

/** `settleRunningSessions` 在途的会话（代次 + 项目 + id），同一个会话同一时刻只读一次详情 */
const settling = new Set<string>()

/**
 * 把一个**已存在**的会话按后端的终态快照收尾（补转录、`finish`、收尾提示）。按会话 id 工作，不依赖提交是否还在：
 * `settleRunningSessions` 与「快照查询回来时提交已被占位认领删掉」（`accept` 找不到提交）共用这一处。
 */
function settleSessionTo(sid: string, done: DonePayload, transcript: AiSessionDetail['transcript'] | undefined): void {
  const { get, set } = { get: useAiStore.getState, set: useAiStore.setState }
  const cur = get().sessions.find((y) => y.id === sid)
  if (!cur || cur.finalized) return
  if (transcript?.length) {
    // 终态快照是权威：替换本地 entries（断线期间丢的补回来），不是只在本地为空时才应用
    set((s) => ({
      sessions: s.sessions.map((y) => (y.id === sid ? { ...y, entries: mergeTranscript(y.entries, transcript) } : y)),
    }))
  }
  if (get().finish(done)) settledNotifier?.(done)
}

const supersedesFor = (project: string | null, fileId: string, script: string | null): string[] =>
  [...supersedeQueue.values()]
    .filter((o) => (o.project ?? null) === (project ?? null) && submissionBlocks(o, fileId, script))
    .map((o) => o.rid)

/**
 * 会话在「建好之后才到的 `ai.done`」那条路上也要有和直达路径一样的提示（状态栏）。副作用在
 * `useServerEvents`，而它 import 本 store——回调注册制（同 `setEngine500Handler`）避免成环。
 */
let settledNotifier: ((p: DonePayload) => void) | null = null
export function setAiSettledNotifier(fn: ((p: DonePayload) => void) | null): void {
  settledNotifier = fn
}

/**
 * 提交账本的落盘（草稿记录里的 rid）。**注入而不是 import**：本 store 在 Codex 内嵌画布与 playground 的包里
 * 也会被带进来（`projectStore` 切项目要 `clear()`），而那两个宿主没有助手面板，不得带上草稿模块
 * （`lib/aiDraft.hosts.test.ts` 用 import 图钉住）。桌面 / 浏览器宿主在 `lib/aiDraftLedger.ts` 里注册。
 */
export interface SubmissionLedger {
  markSubmitted: (
    project: string | null,
    fileId: string,
    text: string,
    rid: string,
    sent: DraftTarget,
    script: string | null,
    draftStamp: number | null,
    supersedes: string[],
  ) => Promise<LedgerWrite>
  removeAccepted: (project: string | null, fileId: string, rid: string) => Promise<void>
}
let ledger: SubmissionLedger | null = null
export function setAiSubmissionLedger(l: SubmissionLedger | null): void {
  ledger = l
}
// 落盘都是请求后端：markSubmitted 在 spawn 之前**等它写成功**（start 里 await，失败不发）；`removeAccepted`（后端 accept 事务）尽力而为。
// **账本行的删除只发生在后端事务里**（accept、`/api/ai/run` 明确拒绝时后端自己删、新 PUT 带 `supersedes`）——客户端没有 DELETE
const markSubmitted = (...a: Parameters<SubmissionLedger['markSubmitted']>): Promise<LedgerWrite> | undefined =>
  ledger?.markSubmitted(...a)
const removeAccepted = (...a: Parameters<SubmissionLedger['removeAccepted']>): void => void ledger?.removeAccepted(...a)

/** 请求 id：安全上下文里用 `randomUUID`，否则用 `getRandomValues` 拼 v4（后端只要求 `[A-Za-z0-9_-]{8,64}`） */
export function newRequestId(): string {
  const c = globalThis.crypto
  if (c?.randomUUID) return c.randomUUID()
  const b = new Uint8Array(16)
  if (c?.getRandomValues) c.getRandomValues(b)
  else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

const KNOWN_STATUS: AiStatus[] = ['running', 'done', 'failed', 'timeout', 'cancelled', 'reverted']
/** 后端还有 `interrupted`（重启时被中断）等前端不单列的终态：按失败呈现，不当成「还在跑」 */
const toStatus = (s: string): AiStatus => ((KNOWN_STATUS as string[]).includes(s) ? (s as AiStatus) : 'failed')

const LS_AGENT = 'tavotto.ai.agent'
const LS_PREFS = 'tavotto.ai.prefs'

/**
 * 记住的首选 Agent。**不再拿硬编码联合类型校验**：合法性只有后端的注册表
 * 说了算，这里只把存过的字符串原样带回来。存了个已经不存在的 Agent 也不会
 * 崩——`effectiveAgent` 会落到第一个可用的，而用户的首选值留着。
 */
function readAgent(): AiAgentId | null {
  try {
    const v = localStorage.getItem(LS_AGENT)
    if (typeof v === 'string' && v.trim()) return v
  } catch {
    /* 用默认值 */
  }
  return null
}

function readPrefs(): { models: AiState['models']; efforts: AiState['efforts'] } {
  try {
    const raw = localStorage.getItem(LS_PREFS)
    const v = raw ? JSON.parse(raw) : null
    if (v && typeof v === 'object') {
      return { models: v.models ?? {}, efforts: v.efforts ?? {} }
    }
  } catch {
    /* 用默认值 */
  }
  return { models: {}, efforts: {} }
}

/** 丢掉不在 CLI 当前清单里的记忆选择。
 *
 * CLI 换代后旧模型会被服务端直接拒收（gpt-5 之于 ChatGPT 账号：400
 * invalid_request_error），而选择是持久化的——不清理就永远卡在报错上。
 * 清单为空 = 后端没给出可选项（跟随 CLI 默认），此时不动用户的选择。
 */
export function prunePrefs<T extends Record<AiAgentId, string>>(
  prefs: T,
  allowed: (agent: AiAgentId) => string[],
): T {
  let changed = false
  const out = { ...prefs }
  for (const agent of Object.keys(out)) {
    const value = out[agent]
    const list = allowed(agent)
    if (value && list.length > 0 && !list.includes(value)) {
      delete out[agent]
      changed = true
    }
  }
  return changed ? out : prefs
}

function persistPrefs(models: AiState['models'], efforts: AiState['efforts']): void {
  try {
    localStorage.setItem(LS_PREFS, JSON.stringify({ models, efforts }))
  } catch {
    /* 忽略存储失败 */
  }
}

export const useAiStore = create<AiState>((set, get) => ({
  agent: readAgent(),
  scope: 'element',
  sessions: [],
  generation: 0,
  caps: null,
  ...readPrefs(),

  /**
   * 探测本机 Agent。**失败时向调用方抛出**，`caps` 一个字节都不动——
   * 清空的话用户点一次「重新检测」就会看到「全部未安装」，而那是假的。
   * 设置页据此显示一条非破坏性提示（aria-live），启动那次静默吞掉。
   */
  loadCaps: async (refresh = false) => {
    const caps = await fetchAiCapabilities(refresh)
    set((s) => {
      // 记忆里的模型/强度若已不在该 Agent 当前的清单里就丢弃，回落到默认值
      const models = prunePrefs(s.models, (a) => agentById(caps, a)?.models ?? [])
      const efforts = prunePrefs(s.efforts, (a) => agentById(caps, a)?.efforts ?? [])
      if (models !== s.models || efforts !== s.efforts) persistPrefs(models, efforts)
      // **首选值不动**：暂时不可用不等于用户改了主意，恢复后它还该是默认项。
      // 实际派活的 Agent 由 effectiveAgent 现算（见 start / AiPanel）。
      return { caps, models, efforts }
    })
  },

  setAgent: (agent) => {
    set({ agent })
    try {
      localStorage.setItem(LS_AGENT, agent)
    } catch {
      /* 忽略存储失败 */
    }
  },

  setScope: (scope) => set({ scope }),

  setModel: (agent, model) => {
    set((s) => {
      const models = { ...s.models, [agent]: model }
      persistPrefs(models, s.efforts)
      return { models }
    })
  },

  setEffort: (agent, effort) => {
    set((s) => {
      const efforts = { ...s.efforts, [agent]: effort }
      persistPrefs(s.models, efforts)
      return { efforts }
    })
  },

  submissions: {},

  start: async ({ project, generation, prompt, fileId, panelId, gid, label, scope, target, overrides, canvas, script = null, draftStamp = null }) => {
    // 发任务前再确认一次：首选那个可能刚被关掉 / 刚被检测成不可用
    if (!effectiveAgent(get().agent, get().caps)) throw new Error('no-usable-agent')
    if (get().generation !== generation) throw new ProjectChangedError()
    // 同一面板、或同一份脚本的别的面板上还有没落定的提交（在路上 / 未确认 / 待重发）：不许再发。
    // 界面已经锁了；这是 store 层的兜底（直接调、竞态都过不去）
    if (Object.values(get().submissions).some((x) => x.project === project && submissionBlocks(x, fileId, script)))
      throw new Error('submission-pending')
    const sent: DraftTarget = { scope, gid, label, target }
    // 上一次这条指令的账本写入「不知道」写没写成（响应丢了 / 5xx）：同一条指令保持**同一个 rid** 幂等重试（后端同 rid 重复 PUT
    // 不占名额）。换了指令 / 目标就换新 rid，旧 rid 放进 `supersedes` 让后端在写新行的同一个事务里清掉
    const ledgerKey = `${project ?? ''}\u0000${fileId}`
    const sentJson = JSON.stringify(sent)
    const prior = ledgerUnknown.get(ledgerKey)
    const reuse = !!prior && prior.sub.prompt === prompt && prior.sentJson === sentJson
    const rid = reuse ? prior!.sub.rid : newRequestId()
    if (prior && !reuse) queueSupersede(prior.sub)
    ledgerUnknown.delete(ledgerKey)
    const supersedes = supersedesFor(project ?? null, fileId, script ?? null).filter((x) => x !== rid)
    const sub: AiSubmission = {
      rid,
      project,
      fileId,
      panelId,
      script,
      prompt,
      target: sent,
      canvas: canvas ?? null,
      overrides,
      phase: 'sending',
    }
    set((s) => ({ submissions: { ...s.submissions, [rid]: sub } }))
    // spawn 之前先把账本写进后端（原文 + 目标）：刷新 / 崩溃 / 换端口之后还认得「这条发出过」
    const dropSub = () =>
      set((s) => {
        const { [rid]: _gone, ...rest } = s.submissions
        return { submissions: rest }
      })
    const marked = markSubmitted(project, fileId, prompt, rid, sent, script, draftStamp, supersedes)
    let recorded: LedgerWrite | undefined
    try {
      recorded = marked ? await marked : undefined
    } catch (e) {
      dropSub() // 后端明确拒绝记账（如未确认的提交已满，`ai_pending_limit`）：不发，原因原样交给界面
      // 账本写入是 await：拒绝回来时已经切了项目，它说的是那个项目的事——不还原、不查询（还原进当前 store，旧项目的会话就会
      // 挂到新项目上、锁住同名脚本的面板）。旧项目的待确认行还在后端（整个事务回滚了），回到那个项目时对账会还原它
      if (get().generation !== generation) throw new ProjectChangedError()
      if (e instanceof ApiError && e.body?.code === 'ai_supersede_conflict') {
        // 要取代的旧 rid 其实已被后端登记（旧请求到达了）：它不能再被当成「没收到」取代，出队；但**不能就此忘掉它**——
        // 放弃时提交已从界面收走，什么都不留的话重试按钮一放开、旧任务又恰好已结束（runningHere 不再挡），新 rid 不带 supersedes
        // 发出去，两条都执行。所以把放弃的那条原样还原成「未确认」（照旧锁住同面板 / 同脚本），并立刻按 rid 查询接回它的会话
        for (const x of supersedes) {
          const restored = supersedeQueue.get(x)
          supersedeQueue.delete(x)
          if (!restored) continue
          get().restoreSubmission(restored)
          void get().queryRequest(x)
        }
      }
      throw e
    }
    if (recorded === 'ok') {
      for (const x of supersedes) supersedeQueue.delete(x) // 后端已在同一事务里删掉它们
    } else if (recorded) {
      // 账本没写成：**不发**。丢了响应之后没有 rid 可查，刷新就会放行同一条指令换新 id 再执行
      dropSub()
      if (recorded === 'unknown') {
        // 不知道后端写没写：保持同一个 rid，重试时幂等重放，**不换新 rid**
        ledgerUnknown.set(ledgerKey, { sub, sentJson })
      } else if (reuse) {
        queueSupersede(sub) // 明确拒绝，但上一次「不知道」的尝试可能已经写入：让之后的新写入顺手清掉它
      }
      throw new AiLedgerError()
    }
    // 账本写入是 await：期间切过项目就作废（提交的 project 是调用方固定下来的那个，不是此刻的当前项目）
    if (get().generation !== generation) {
      dropSub()
      queueSupersede(sub) // 账本可能已经写进后端、却不会发出：之后这个面板再发时随新行带 supersedes 清掉
      throw new ProjectChangedError()
    }
    return dispatch(rid, generation)
  },

  resend: async (rid) => {
    const sub = get().submissions[rid]
    // 只有「后端明确说没收到」才允许用同一个 id 重发；其余相位的重发就是盲目重复执行
    if (!sub || sub.phase !== 'not_received') return 'unconfirmed'
    if (!effectiveAgent(get().agent, get().caps)) throw new Error('no-usable-agent')
    return dispatch(rid, get().generation)
  },

  queryRequest: (rid) => settleFromSnapshot(rid),

  settleRunningSessions: async () => {
    const born = get().generation
    const pj = currentProjectId()
    for (const x of get().sessions) {
      if (!isOpenSession(x) || x.project !== pj) continue
      // 在途去重：同一代次 + 项目 + 会话只读一次详情（对账入口与安全网轮询可能同时到）
      const key = `${born}\u0000${x.project ?? ''}\u0000${x.id}`
      if (settling.has(key)) continue
      settling.add(key)
      let snap
      try {
        snap = await readSessionDetail(x.id, x.project)
      } finally {
        settling.delete(key)
      }
      const d = snap.ok ? snap.detail : null
      if (born !== get().generation || typeof d?.status !== 'string' || !snapshotIsFinal(d, d.status)) continue
      const done: DonePayload = {
        session: x.id,
        status: d.status,
        changed: !!d.changed,
        diff: d.diff ?? '',
        ...(d.error ? { error: d.error } : {}),
        ...(d.refresh ? { refresh: d.refresh } : {}),
      }
      settleSessionTo(x.id, done, d.transcript)
    }
  },

  dismissSubmission: (rid) => {
    const sub = get().submissions[rid]
    if (!sub) return
    // 放弃这条等待（「改写后再发」）：**不 DELETE**。「后端没收到」的判断可能是错的（原请求可能还在路上），客户端单方面删了
    // 账本行，之后它又被还原、新 rid 却已经执行过。记下这个 rid，下一次发这个面板时随新行的 PUT 带 `supersedes`，由后端在同一个事务里删
    queueSupersede(sub)
    set((s) => {
      const { [rid]: _gone, ...rest } = s.submissions
      return { submissions: rest }
    })
  },

  restoreSubmission: (sub) => {
    const existing = get().submissions[sub.rid]
    if (existing) {
      // 对账再次带来同一条（焦点 / 重连）：还停在「未确认」就再自动按快照收尾一次（有上限，在途去重）
      if (existing.phase === 'unconfirmed') autoSettleRestored(sub.rid)
      return
    }
    set((s) => ({ submissions: { ...s.submissions, [sub.rid]: { ...sub, phase: 'unconfirmed' } } }))
    // 这条请求对应的运行中会话已经作为占位接回来了（`restoreRunning` 先到）：后端的运行中记录就是它被接受的证据，
    // 走**同一个**「已被接受」函数 `accept`（与 `settleFromSnapshot` 的收尾同一处），不再摆「未确认」
    const placeholder = get().sessions.find((x) => x.requestId === sub.rid)
    if (placeholder) acceptRunning(sub.rid, placeholder)
    // 没有占位：别等用户手动点「查询结果」——自动对它按快照收尾（刷新期间任务可能已经结束，运行中历史是空的、事件也没有）
    else autoSettleRestored(sub.rid)
  },

  appendDelta: (sid, kind, text, rid) => {
    if (!get().sessions.some((x) => x.id === sid)) {
      bufferEarly(sid, rid, { t: 'delta', kind, text })
      return
    }
    set((s) => ({
      sessions: s.sessions.map((x) => {
        if (x.id !== sid) return x
        const entries = [...x.entries]
        const last = entries.at(-1)
        const streamingLast = last?.kind === 'message' && last.streaming ? last : null

        if (kind === 'delta') {
          // 逐字流入当前气泡；没有在流的就新开一个
          if (streamingLast) entries[entries.length - 1] = { ...streamingLast, text: streamingLast.text + text }
          else entries.push({ kind: 'message', text, streaming: true, at: Date.now() })
        } else if (kind === 'message') {
          // 终稿替换流式内容（后端给的是完整段落）
          if (streamingLast) entries[entries.length - 1] = { kind: 'message', text, at: streamingLast.at }
          else entries.push({ kind: 'message', text, at: Date.now() })
        } else {
          // 过程事件先给流式气泡定稿，免得插到半截文字后面
          if (streamingLast) entries[entries.length - 1] = { kind: 'message', text: streamingLast.text, at: streamingLast.at }
          entries.push({ kind, text, at: Date.now() })
        }
        return { ...x, entries: entries.slice(-MAX_LINES) }
      }),
    }))
  },

  finish: (p) => {
    const { session, status, changed, diff, error } = p
    const cur = get().sessions.find((x) => x.id === session)
    if (!cur) {
      // 事件先于 `aiRun` 的响应到：先留着，会话建好之后回放（否则会话永远停在 running）
      bufferEarly(session, p.client_request_id, { t: 'done', p })
      return false
    }
    // 终态只认第一条 `ai.done`：重复 / 迟到的不改写（cancelled 后到的 done 改回 done 之类）
    if (cur.finalized) return false
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === session
          ? {
              ...x,
              finalized: true,
              status: toStatus(status || 'done'),
              changed,
              diff,
              error,
              finishedAt: x.finishedAt ?? Date.now(),
              // 会话结束，光标不该继续闪
              entries: x.entries.map((e) => (e.streaming ? { ...e, streaming: false } : e)),
            }
          : x,
      ),
    }))
    return true
  },

  revert: async (sid) => {
    const target = get().sessions.find((x) => x.id === sid)
    if (!target) return false
    const born = get().generation
    await aiRevert(sid, target.project)
    if (born !== get().generation) return false
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === sid ? { ...x, status: 'reverted', changed: false } : x,
      ),
    }))
    return true
  },

  cancel: async (sid) => {
    const target = get().sessions.find((x) => x.id === sid)
    if (!target) return
    const born = get().generation
    const res = await aiCancel(sid, target.project)
    // 换代之后会话列表已清空、sid 不复用，下面这次按 sid 写本来就落空——这一判是防御，量不出来
    if (born !== get().generation) return
    // 后端说 `ok: false` = 这个会话已经结束（或不认得它）：真实结局由 `ai.done` 给出，
    // 不抢先标成 cancelled——那会把真实的 done / failed 盖掉
    if (!res?.ok) return
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === sid && x.status === 'running'
          ? { ...x, status: 'cancelled', finishedAt: x.finishedAt ?? Date.now() }
          : x,
      ),
    }))
  },

  restoreRunning: async () => {
    // 在途的恢复按**项目代次 + 项目 id** 分键：A 的请求还在途时切到 B，B 要发起自己的请求，不能拿到 A 的 promise
    // （A 的结果按代次丢弃后，B 就再也不会恢复了）
    const gen = get().generation
    const pj = currentProjectId()
    if (restoring && restoring.gen === gen && restoring.pj === pj) return restoring.p
    const mine: { gen: number; pj: string | null; p: Promise<void> } = {
      gen,
      pj,
      p: doRestore().finally(() => {
        if (restoring === mine) restoring = null
      }),
    }
    restoring = mine
    return mine.p
  },

  clear: () => {
    ledgerUnknown.clear()
    supersedeQueue.clear()
    // 提交记录也属于旧项目：在途请求回来时代际对不上，不落会话（但接受了的仍会删草稿，见 `accept`）。
    // 草稿文件里的 rid 还在——切回 A 会还原成「已发出未确认」，由用户查询，而不是盲目重发
    early.clear()
    autoSettle.attempts.clear()
    restoring = null // 旧代次的在途恢复作废（它回来时代次对不上、结果会被丢弃）；新项目挂载时发起自己的
    set((s) => ({ sessions: [], submissions: {}, generation: s.generation + 1 }))
  },
}))

/**
 * **自动收尾还原出来的未确认提交**：不再等用户手动点「查询结果」，更不依赖暂存事件与并行请求的时序。
 * 只读：`settleFromSnapshot` 只问后端、绝不 `aiRun`；后端回 404（没登记过）也只是进入 `not_received`——
 * 允许同 rid 重发这一步**仍然需要用户点击**。所以自动做是安全的（不会让任何东西被执行）。
 * 有上限（每个 rid 最多 `AUTO_SETTLE_MAX` 次，换代清零）并去重（同一个 rid 在途时只查一次）；「查询结果」按钮保留作手动兜底。
 */
const AUTO_SETTLE_MAX = 5
const autoSettle = { inflight: new Set<string>(), attempts: new Map<string, number>() }
function autoSettleRestored(rid: string): void {
  const st = useAiStore.getState()
  const sub = st.submissions[rid]
  if (!sub || sub.phase !== 'unconfirmed') return
  // 去重 / 计数的键带上**项目代次 + 项目 id**（同 `restoring`）：A→B→A 之后新一代的 A 不被上一代还在途的标记挡住
  const key = `${st.generation}\u0000${sub.project ?? ''}\u0000${rid}`
  if (autoSettle.inflight.has(key)) return
  const n = autoSettle.attempts.get(key) ?? 0
  if (n >= AUTO_SETTLE_MAX) return
  autoSettle.attempts.set(key, n + 1)
  autoSettle.inflight.add(key)
  settleFromSnapshot(rid)
    .catch(() => undefined) // 自动的后台收尾：任何意外都只是留在「未确认」，手动按钮兜底，不抛未处理的拒绝
    .finally(() => autoSettle.inflight.delete(key))
}

/**
 * 一条未落定的提交遇到了带同一个请求 id 的运行中会话（占位）= 这条请求**已被后端接受**，仍在运行。
 * 判断「已被接受」只有 `accept` 一处：这里不另写一套，只是把占位当作「请求快照说 running」交给它——
 * 它清账本（草稿按「草稿戳没变」才删，不比文本）、把提交的归属并到已有的同 id 会话上、去掉未确认状态。
 * 两个入口（`restoreRunning` 与 `reconcile`→`restoreSubmission`）谁先到都走这里。
 */
function acceptRunning(rid: string, session: AiSession): void {
  accept(rid, session.id, session.script, {
    kind: 'queried',
    status: session.status, // 占位自己的状态：running，或已取消但后端还没收尾的 cancelled，不覆盖成 running
    changed: false,
    detail: null,
    adopt: true,
  })
}

let restoring: { gen: number; pj: string | null; p: Promise<void> } | null = null
/** 恢复进行中暂存过的 session id → 它带的 request id：恢复结束时没被任何会话认领的要清掉（有未落定提交关心的除外） */
const restoreBuffered = new Map<string, string | undefined>()
/** 正在拉历史的那一小段里到的事件也要留着：占位还没建，它们不是「别人的」 */
let restoringActive = 0

/** 恢复结束：暂存了却没被任何会话认领的事件清掉（只留有未落定提交关心的，那是 accept 的回放用的） */
function dropUnclaimedRestoreBuffer(): void {
  const subs = useAiStore.getState().submissions
  for (const [sid, rid] of restoreBuffered) {
    const wanted = rid ? !!subs[rid] : Object.keys(subs).length > 0
    if (!wanted) early.delete(sid)
  }
  restoreBuffered.clear()
}

async function doRestore(): Promise<void> {
  const { get, set } = { get: useAiStore.getState, set: useAiStore.setState }
  const born = get().generation
  const pj = currentProjectId()
  restoringActive++
  try {
    // 运行中清单**一次请求取全**：后端对 `status=running` 不分页（运行中的会话天然有上限，翻页期间集合会变，offset 翻页会漏掉
    // 仍在运行的）。`limit` 只是给不认这个约定的老后端的兜底。
    let list: AiHistoryEntry[]
    try {
      // `open` = 还没结束：running，或已取消 / 已中断但后端 `_pump` 还没收尾（status 是 cancelled / interrupted、ended_ms 为空）——
      // 后两者没有占位、没有轮询，之后到的 `ai.done` 会被丢掉
      const res = await fetchAiHistory({ status: 'open', limit: 100 }, pj)
      if (!Array.isArray(res?.sessions)) return
      list = res.sessions
    } catch {
      return // 联系不上 / 老后端：什么都不动，下一次重连再来
    }
    // 拉的过程中换了项目：这些会话属于旧项目，不落
    if (born !== get().generation || pj !== currentProjectId()) return
    const caps = get().caps
    for (const e of list) {
      // interrupted 且还开着：另一个共用数据目录的服务进程刚 `interrupt_all()`、它的 pump 还没收尾。本页连着的是另一个进程，
      // 收不到那边的 `ai.done`、也不会再有启动清理——只能靠对账：接回成临时快照（`provisional`，本地映射成 failed，不给「停止」），
      // 直到详情 `final: true` 才收尾
      if (e.status !== 'running' && e.status !== 'cancelled' && e.status !== 'interrupted') continue
      if (get().sessions.some((x) => x.id === e.id)) continue
      const session: AiSession = {
        id: e.id,
        project: pj,
        agent: e.provider,
        agentLabel: agentDisplayName(caps, e.provider),
        prompt: e.prompt,
        script: e.script ?? '',
        panelId: null,
        fileId: null,
        gid: null,
        scope: (['element', 'axes', 'figure'].includes(e.scope ?? '') ? e.scope : 'figure') as AiScope,
        target: e.target ?? '',
        entries: [],
        status: e.status === 'cancelled' ? 'cancelled' : e.status === 'interrupted' ? 'failed' : 'running',
        ...(e.status === 'interrupted' ? { provisional: true } : {}),
        changed: false,
        diff: '',
        startedAt: e.started_ms,
        restored: true,
        ...(e.client_request_id ? { requestId: e.client_request_id } : {}),
      }
      set((s) => ({ sessions: [...s.sessions, session] }))
      const pending = early.get(e.id)
      early.delete(e.id)
      for (const ev of pending ?? []) {
        if (ev.t === 'delta') get().appendDelta(e.id, ev.kind, ev.text)
        else if (get().finish(ev.p)) settledNotifier?.(ev.p)
      }
      // 对应的提交（`reconcile` 先到、占位后到）已在列表里：后端的运行中记录就是它被接受的证据——直接并上去
      const rid = e.client_request_id
      const sub = rid ? get().submissions[rid] : undefined
      if (rid && sub) acceptRunning(rid, get().sessions.find((x) => x.id === e.id) as AiSession)
    }
    // 本地还当它在跑、后端的运行中清单里却没有：它在断线期间结束了，`ai.done` 没收到——以后端为准收尾。
    // 与 `reconcile` 各入口走的是**同一个** `settleRunningSessions`，不再留第二条收尾路径。
    // **不跳过刚建的占位**：历史是个比事件流早的快照，任务可能在事件流就绪之前就结束了——建完一律读一次详情按快照收尾（多一次请求）
    await get().settleRunningSessions()
  } finally {
    restoringActive--
    if (restoringActive === 0) dropUnclaimedRestoreBuffer()
  }
}

function bufferEarly(sid: string, rid: string | undefined, ev: EarlyEvent): void {
  const subs = useAiStore.getState().submissions
  if (restoringActive > 0) {
    // 恢复进行中（历史请求在途）：账本可能还没加载，带 request id 但此刻对不上任何提交的事件**也要留着**——
    // 之后历史响应里若还有这条 running 记录，要回放给它（否则 ai.done 被丢、界面永远停在运行中、脚本被锁）。
    // 恢复完成后只回放与返回会话匹配的，其余清掉（`dropUnclaimedRestoreBuffer`）
    restoreBuffered.set(sid, rid)
  } else {
    // 没有任何在途提交 = 这是别人的 / 已经丢弃的会话的事件，不留；带了 rid 的必须对得上一条提交
    if (Object.keys(subs).length === 0 || (rid && !subs[rid])) return
  }
  let list = early.get(sid)
  if (!list) {
    if (early.size >= EARLY_MAX_SIDS) early.delete(early.keys().next().value as string)
    list = []
    early.set(sid, list)
  }
  if (list.length < EARLY_MAX_EVENTS) list.push(ev)
}

/**
 * 读一个会话的详情快照（**全文件只有这一处调 `fetchAiSession`**，`settleFromSnapshot` 与 `settleRunningSessions` 共用）：
 * `{ ok: true, detail }`；后端明确说没有详情（404）→ `{ ok: true, detail: null }`；其余失败 = 不知道 → `{ ok: false }`。
 */
async function readSessionDetail(
  sid: string,
  project: string | null,
): Promise<{ ok: true; detail: AiSessionDetail | null } | { ok: false }> {
  try {
    return { ok: true, detail: await fetchAiSession(sid, project) }
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return { ok: true, detail: null }
    return { ok: false }
  }
}

/**
 * **「首个 POST 不确定」引出的所有分支，都收成这一个函数：向后端要这条请求的快照，按快照的真实状态收尾。**
 * 用户点「查询结果」、同 id 重发被去重（`deduped: true`）、……都走这里——各写各的，就会漏掉另一半
 * （去重的响应被当成「刚启动」、永远等不到的 `ai.done` 把脚本锁死，#1098 的这一族问题）。
 * 快照 = `GET /api/ai/requests/<rid>`（登记过吗 / 当前状态）+ `GET /api/ai/sessions/<sid>`（转录 / diff / refresh 结局）：
 * - 没登记过（后端自己的 404）→ `not_received`，才允许同 rid 重发；
 * - 登记过但历史被删 → `already_run`，一定不重发；
 * - 读失败（含读详情暂时失败）→ `unreachable`，提交与账本都留着，可再查——**绝不凭摘要收尾**；
 * - 否则按快照状态建会话：终态走正常的完成路径（含 refresh 警告），仍在跑才是 running。
 */
async function settleFromSnapshot(rid: string): Promise<QueryOutcome> {
  const { get, set } = { get: useAiStore.getState, set: useAiStore.setState }
    const sub = get().submissions[rid]
    if (!sub) return 'gone'
    const born = get().generation
    let found
    try {
      found = await fetchAiRequest(rid, sub.project)
    } catch (e) {
      if (born !== get().generation) return 'gone'
      // 只有后端自己的「没登记过」是结论；404 来自别处（老后端没这个端点）、断网、5xx 都不是
      if (e instanceof ApiError && e.status === 404 && e.body?.code === AI_REQUEST_NOT_FOUND) {
        set((s) =>
          s.submissions[rid] ? { submissions: { ...s.submissions, [rid]: { ...s.submissions[rid], phase: 'not_received' } } } : s,
        )
        return 'not_received'
      }
      return 'unreachable'
    }
    if (born !== get().generation) {
      removeAccepted(sub.project, sub.fileId, rid)
      return 'gone'
    }
    if (found.status === 'superseded') {
      set((s) => {
        const { [rid]: _gone, ...rest } = s.submissions
        return { submissions: rest }
      })
      return 'superseded'
    }
    if (found.status === 'history_deleted') {
      // 执行过、只是记录被删：没有会话可接，但**一定不能重发**。提交与草稿一并收掉
      removeAccepted(sub.project, sub.fileId, rid)
      set((s) => {
        const { [rid]: _gone, ...rest } = s.submissions
        return { submissions: rest }
      })
      return 'already_run'
    }
    // 补全转录 / diff / 执行器 / refresh 结局（刷新之后这些都在后端）。只有「后端明确说没有详情」（404）才凭摘要往下走；
    // 读详情**暂时失败**不收尾——摘要里没有 refresh 结局，凭它把会话标成终态会把「改成了、项目没刷新」悄悄吞掉，
    // 且提交记录一删就再也查不回来。保持「已发出未确认」，让用户再查一次
    const snap = await readSessionDetail(found.session, sub.project)
    if (!snap.ok) return 'unreachable'
    const detail = snap.detail
    if (born !== get().generation) {
      removeAccepted(sub.project, sub.fileId, rid)
      return 'gone'
    }
    accept(rid, found.session, found.script, {
      kind: 'queried',
      status: found.status,
      changed: found.changed,
      detail,
    })
    return 'accepted'
}

/** 一次发送（新发或同 id 重发）：只回 `accepted` / `unconfirmed`，后端明确拒绝时抛 */
async function dispatch(rid: string, born: number): Promise<SubmitOutcome> {
  const { get, set } = { get: useAiStore.getState, set: useAiStore.setState }
  const sub = get().submissions[rid]
  if (!sub) return 'unconfirmed'
  const caps = get().caps
  const agent = effectiveAgent(get().agent, caps) as AiAgentId
  const info = agentById(caps, agent)
  const model = get().models[agent] ?? info?.default_model ?? null
  const effort = info?.efforts.length ? (get().efforts[agent] ?? info.default_effort ?? null) : null
  set((s) => ({ submissions: { ...s.submissions, [rid]: { ...s.submissions[rid], phase: 'sending' } } }))
  let res
  try {
    res = await aiRun(
      {
        agent,
        id: sub.fileId,
        prompt: sub.prompt,
        gid: sub.target.gid,
        label: sub.target.label,
        overrides: sub.overrides,
        model,
        effort,
        scope: sub.target.scope,
        target: sub.target.target,
        canvas: sub.canvas,
        client_request_id: rid,
      },
      sub.project,
    )
  } catch (e) {
    if (isDefiniteRunFailure(e)) {
      // 后端明确拒绝 = 一定没执行：提交记录作废（下一次发送换新 id），文本留给用户改。账本行由**后端**在这次拒绝里删掉
      // （`/api/ai/run` 的拒绝路径），客户端不再 DELETE
      if (born === get().generation) {
        set((s) => {
          const { [rid]: _gone, ...rest } = s.submissions
          return { submissions: rest }
        })
      }
      throw e
    }
    // 超时 / 断网 / 网关错 / 读不出响应：**不知道**后端收到没有。绝不当失败，不开普通重试
    if (born === get().generation) {
      set((s) =>
        s.submissions[rid]
          ? { submissions: { ...s.submissions, [rid]: { ...s.submissions[rid], phase: 'unconfirmed' } } }
          : s,
      )
    }
    return 'unconfirmed'
  }
  // 发起之后换了项目：这次任务属于 A（后端照样跑完、记进 A 的历史），B 的面板里不许出现它——
  // 但它**已经被接受**了，A 的草稿该删
  if (born !== get().generation) {
    removeAccepted(sub.project, sub.fileId, rid)
    return 'accepted'
  }
  if (res.deduped) {
    // 去重的响应 = 这条请求**早就在后端了**（首个 POST 才是真正发起的那次），会话可能早已结束、SSE 又断着：
    // 不能当成「刚启动」建 running 会话（再也等不到 ai.done，`runningHere` 永久锁死）——拿快照、按真实状态收尾
    const out = await settleFromSnapshot(rid)
    if (out === 'unreachable' || out === 'not_received') {
      if (born === get().generation)
        set((s) =>
          s.submissions[rid]
            ? { submissions: { ...s.submissions, [rid]: { ...s.submissions[rid], phase: 'unconfirmed' } } }
            : s,
        )
      return 'unconfirmed'
    }
    return 'accepted'
  }
  accept(rid, res.session, res.script, { kind: 'fresh', agent, agentLabel: agentDisplayName(caps, agent) })
  return 'accepted'
}

/**
 * 后端给出了会话 id = 这条请求**已被接受**：此刻（也只有此刻）删草稿，建会话，回放早到的事件。
 * 事件回放在建会话**之后**走公开的 `appendDelta` / `finish`，与直达路径同一份逻辑。
 */
function accept(
  rid: string,
  sid: string,
  script: string,
  how:
    | { kind: 'fresh'; agent: AiAgentId; agentLabel: string }
    | {
        kind: 'queried'
        status: string
        changed: boolean
        detail: AiSessionDetail | null
        /**
         * 认领一个已存在的占位（还没有终态快照）：**保持占位自己的非终态**（running，或 cancelled 但后端还没收尾），
         * 只并入提交的归属——不能因为「请求已被接受」就把已经取消的占位改回 running（按钮会重新亮起「中止」，后端又拒绝第二次取消）
         */
        adopt?: boolean
      },
): void {
  const { get, set } = { get: useAiStore.getState, set: useAiStore.setState }
  const sub = get().submissions[rid]
  if (!sub) {
    // 提交已经没了（快照查询在途期间，占位被认领、提交被删）：但这份快照可能是终态——会话还在列表里、没收尾的，
    // 按会话 id 把终态应用上去，否则它永远停在 running、脚本被锁
    if (how.kind === 'queried') {
      const d = how.detail
      const st = typeof d?.status === 'string' && d.status ? d.status : how.status
      if (snapshotIsFinal(d, st))
        settleSessionTo(
          sid,
          {
            session: sid,
            status: st,
            changed: !!(how.changed || d?.changed),
            diff: d?.diff ?? '',
            ...(d?.error ? { error: d.error } : {}),
            ...(d?.refresh ? { refresh: d.refresh } : {}),
          },
          d?.transcript,
        )
    }
    return
  }
  removeAccepted(sub.project, sub.fileId, rid)
  const caps = get().caps
  const detail = how.kind === 'queried' ? how.detail : null
  const agent: AiAgentId = how.kind === 'fresh' ? how.agent : (detail?.agent ?? effectiveAgent(get().agent, caps) ?? '')
  // 详情是比「请求状态」更晚的快照：请求状态说 running、详情已经 done（任务在两次 GET 之间结束了），
  // 又恰好错过了 SSE 的 ai.done——以详情为准，否则会话永远停在 running，`runningHere` 把这个脚本锁死
  const status =
    how.kind === 'queried'
      ? typeof detail?.status === 'string' && detail.status
        ? detail.status
        : how.status
      : 'running'
  const settled = how.kind === 'queried' && !how.adopt && snapshotIsFinal(detail, status)
  const session: AiSession = {
    id: sid,
    project: sub.project,
    agent,
    agentLabel: how.kind === 'fresh' ? how.agentLabel : agentDisplayName(caps, agent),
    prompt: sub.prompt,
    script,
    panelId: sub.panelId,
    fileId: sub.fileId,
    gid: sub.target.gid,
    scope: sub.target.scope,
    target: sub.target.target,
    entries: (detail?.transcript ?? []).map((e) => ({ kind: e.kind as AiDeltaKind, text: e.text })),
    status: toStatus(status),
    changed: how.kind === 'queried' ? !!(how.changed || detail?.changed) : false,
    diff: detail?.diff ?? '',
    startedAt: Date.now(),
    ...(settled ? { finalized: true, finishedAt: Date.now() } : {}),
    ...(!settled && status !== 'running' && status !== 'cancelled' ? { provisional: true } : {}),
    ...(settled && detail?.error ? { error: detail.error } : {}),
  }
  set((s) => {
    const { [rid]: _gone, ...rest } = s.submissions
    // 同一个会话 id 已经作为「接回的占位」（刷新后 `restoreRunning`）在列表里：不能再 push 一份（两份同 id 会让
    // 列表重复、事件只认第一份）。把提交带来的归属（面板 / 目标 / 原文）并上去，保留占位已累积的过程输出与状态
    const prev = s.sessions.find((x) => x.id === sid)
    if (prev) {
      return {
        submissions: rest,
        sessions: s.sessions.map((x) =>
          x.id === sid
            ? {
                ...x,
                panelId: session.panelId,
                fileId: session.fileId,
                gid: session.gid,
                scope: session.scope,
                target: session.target,
                prompt: session.prompt,
                restored: false,
                entries: settled && detail?.transcript?.length
                  ? mergeTranscript(x.entries, detail.transcript)
                  : x.entries.length
                    ? x.entries
                    : session.entries,
                ...(x.finalized
                  ? {}
                  : {
                      status: session.status,
                      changed: session.changed || x.changed,
                      diff: session.diff || x.diff,
                      ...(session.finalized
                        ? { finalized: true, finishedAt: session.finishedAt, ...(session.error ? { error: session.error } : {}) }
                        : {}),
                    }),
              }
            : x,
        ),
      }
    }
    return { submissions: rest, sessions: [...s.sessions, session] }
  })
  const pending = early.get(sid)
  early.delete(sid)
  for (const ev of pending ?? []) {
    if (ev.t === 'delta') get().appendDelta(sid, ev.kind, ev.text)
    else if (get().finish(ev.p)) settledNotifier?.(ev.p)
  }
  // 经「查询请求」接回来的已结束会话：`ai.done` 没收到，终态是读出来的——收尾提示（包括「改成了、项目没刷新」
  // 的警告）要和直达路径同一份，不能只悄悄把它标成完成（Codex #1098）
  if (settled) {
    settledNotifier?.({
      session: sid,
      status: session.status,
      changed: session.changed,
      diff: session.diff,
      ...(session.error ? { error: session.error } : {}),
      ...(detail?.refresh ? { refresh: detail.refresh } : {}),
    })
  }
}

/** 会话标签：优先用当前 capabilities 的显示名，探测不可用时回退到快照。 */
export const sessionAgentLabel = (
  caps: AiCapabilities | null,
  session: Pick<AiSession, 'agent' | 'agentLabel'>,
): string => agentDisplayName(caps, session.agent, session.agentLabel)

/** 一个可用的 Agent 都没有 = AI 面板要给设置入口，而不是一个死掉的选择器 */
export const hasUsableAgent = (caps: AiCapabilities | null): boolean =>
  usableAgents(caps).length > 0

/** 作用域名；是函数不是常量表——常量在模块求值时就把语言定死了 */
export const scopeLabel = (scope: AiScope): string => t(`scope.${scope}`, { ns: 'ai' })

/**
 * 脚本路径的比较身份：统一分隔符、去掉 `./` 前缀与重复斜杠（不改大小写，与后端 `script_busy` 的 realpath 同向保守）。
 * 它与 `sameScript` ↔ 后端 `ai_history._script_identity` / `same_script` 严格同源（后端在写账本的事务里用它判「同一份脚本还有没落定的」），
 * 两边共用用例表 `sameScriptCases.json`
 */
const scriptIdentity = (p: string): string => p.replace(/\\/g, '/').replace(/\/{2,}/g, '/').replace(/^(\.\/)+/, '')
const isAbsolutePath = (p: string): boolean => p.startsWith('/') || /^[A-Za-z]:\//.test(p)

/**
 * 两个脚本路径是不是同一份脚本：比**完整的项目相对路径**，不比文件名——`a/plot.py` 与 `b/plot.py` 是两份脚本，
 * 按文件名比会让一个的未确认请求锁住另一个（也会把待确认记录显示在不相干的面板上）。
 * 一边是绝对路径（旧数据）时，另一边的相对路径必须是它的路径后缀。
 */
export function sameScript(a: string, b: string): boolean {
  const x = scriptIdentity(a)
  const y = scriptIdentity(b)
  if (x === y) return true
  const xa = isAbsolutePath(x)
  const ya = isAbsolutePath(y)
  if (xa && !ya) return x.endsWith('/' + y)
  if (ya && !xa) return y.endsWith('/' + x)
  return false
}

/** 这条没落定的提交是否锁住 (fileId, script) 这个面板：同面板，或同一份脚本（按文件名比，同 `isSessionOf`） */
export function submissionBlocks(
  sub: Pick<AiSubmission, 'fileId' | 'script'>,
  fileId: string,
  script: string | null | undefined,
): boolean {
  if (sub.fileId === fileId) return true
  return !!script && !!sub.script && sameScript(sub.script, script)
}

/** 路径取文件名：会话与面板可能一个存绝对路径一个存相对路径 */
export const scriptName = (p: string) => p.split(/[\\/]/).pop() ?? p

/**
 * 会话是否属于这个面板。以「同一个脚本」为准——同一份脚本可能产出多个
 * stem（Fig11_xps_chemistry_a/_b），改了脚本这些面板全都受影响，都该看到
 * 这条记录和它的回滚按钮。
 */
export function isSessionOf(
  s: AiSession,
  panel: { id: string; fileId: string; script?: string | null },
): boolean {
  if (s.panelId === panel.id || s.fileId === panel.fileId) return true
  return !!panel.script && sameScript(s.script, panel.script)
}

/**
 * **安全网轮询**：终态的来源有三个——事件（`ai.done`）、对账入口（挂载 / 焦点 / 可见 / 重连 / 接受）、本轮询；任何一个都足以收尾。
 * 事件和对账都可能错过（SSE 还没连上任务就结束了、断线期间结束……），逐个补时序只会越补越多——所以只要本地还有
 * 非终态会话（running 或接回的占位），就每 15 s 走一次 `settleRunningSessions`（只读快照）：任何错过的 `ai.done`
 * 最多 15 s 后被收尾、发送锁随之解开。没有非终态会话时不轮询；页面不可见时暂停，恢复可见时立即跑一次；
 * 换项目（`clear()`）后会话清空，轮询自然停。
 */
export const SAFETY_NET_MS = 15_000
let netTimer: ReturnType<typeof setInterval> | null = null
const hasOpenSessions = (): boolean => useAiStore.getState().sessions.some(isOpenSession)
const pageHidden = (): boolean => typeof document !== 'undefined' && document.visibilityState === 'hidden'
function safetyNetTick(): void {
  if (pageHidden()) return
  void useAiStore.getState().settleRunningSessions()
}
function syncSafetyNet(): void {
  const need = hasOpenSessions()
  if (need && netTimer === null) netTimer = setInterval(safetyNetTick, SAFETY_NET_MS)
  else if (!need && netTimer !== null) {
    clearInterval(netTimer)
    netTimer = null
  }
}
useAiStore.subscribe(syncSafetyNet)
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (!pageHidden() && hasOpenSessions()) safetyNetTick()
  })
}
