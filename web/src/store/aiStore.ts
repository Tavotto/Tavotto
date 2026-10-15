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
import type { DraftTarget } from '@/lib/aiDraft'

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

export type SubmitOutcome = 'accepted' | 'unconfirmed'
/** `already_run`：后端登记过它、但任务历史被用户删了（结局不可知）——已执行过，绝不重发 */
export type QueryOutcome = 'accepted' | 'already_run' | 'not_received' | 'unreachable' | 'gone'

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
  }) => Promise<SubmitOutcome>
  /** 问后端「这个请求 id 你登记过吗」：只读，不会让任何东西被执行 */
  queryRequest: (rid: string) => Promise<QueryOutcome>
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
const EARLY_MAX_SIDS = 16
const EARLY_MAX_EVENTS = 500
const early = new Map<string, EarlyEvent[]>()

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
  markSubmitted: (project: string | null, fileId: string, text: string, rid: string, sent: DraftTarget) => Promise<void>
  clearSubmitted: (project: string | null, fileId: string, rid: string) => Promise<void>
  removeAccepted: (project: string | null, fileId: string, rid: string) => Promise<void>
}
let ledger: SubmissionLedger | null = null
export function setAiSubmissionLedger(l: SubmissionLedger | null): void {
  ledger = l
}
// 落盘都是请求后端：markSubmitted 在 spawn 之前**等它写完**（start 里 await）；其余是尽力而为、不挡界面
const markSubmitted = (...a: Parameters<SubmissionLedger['markSubmitted']>): Promise<void> | undefined =>
  ledger?.markSubmitted(...a)
const clearSubmitted = (...a: Parameters<SubmissionLedger['clearSubmitted']>): void => void ledger?.clearSubmitted(...a)
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

  start: async ({ prompt, fileId, panelId, gid, label, scope, target, overrides, canvas, script = null }) => {
    // 发任务前再确认一次：首选那个可能刚被关掉 / 刚被检测成不可用
    if (!effectiveAgent(get().agent, get().caps)) throw new Error('no-usable-agent')
    const project = currentProjectId()
    // 同一面板、或同一份脚本的别的面板上还有没落定的提交（在路上 / 未确认 / 待重发）：不许再发。
    // 界面已经锁了；这是 store 层的兜底（直接调、竞态都过不去）
    if (Object.values(get().submissions).some((x) => x.project === project && submissionBlocks(x, fileId, script)))
      throw new Error('submission-pending')
    const rid = newRequestId()
    const sent: DraftTarget = { scope, gid, label, target }
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
    const marked = markSubmitted(project, fileId, prompt, rid, sent)
    if (marked) await marked
    return dispatch(rid, get().generation)
  },

  resend: async (rid) => {
    const sub = get().submissions[rid]
    // 只有「后端明确说没收到」才允许用同一个 id 重发；其余相位的重发就是盲目重复执行
    if (!sub || sub.phase !== 'not_received') return 'unconfirmed'
    if (!effectiveAgent(get().agent, get().caps)) throw new Error('no-usable-agent')
    return dispatch(rid, get().generation)
  },

  queryRequest: async (rid) => {
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
    if (found.status === 'history_deleted') {
      // 执行过、只是记录被删：没有会话可接，但**一定不能重发**。提交与草稿一并收掉
      removeAccepted(sub.project, sub.fileId, rid)
      set((s) => {
        const { [rid]: _gone, ...rest } = s.submissions
        return { submissions: rest }
      })
      return 'already_run'
    }
    // 补全转录 / diff / 执行器（刷新之后这些都在后端）；失败就用查询给的摘要
    const detail = await fetchAiSession(found.session, sub.project).catch(() => null)
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
  },

  dismissSubmission: (rid) => {
    const sub = get().submissions[rid]
    if (!sub) return
    clearSubmitted(sub.project, sub.fileId, rid)
    set((s) => {
      const { [rid]: _gone, ...rest } = s.submissions
      return { submissions: rest }
    })
  },

  restoreSubmission: (sub) => {
    if (get().submissions[sub.rid]) return
    set((s) => ({ submissions: { ...s.submissions, [sub.rid]: { ...sub, phase: 'unconfirmed' } } }))
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

  clear: () => {
    // 提交记录也属于旧项目：在途请求回来时代际对不上，不落会话（但接受了的仍会删草稿，见 `accept`）。
    // 草稿文件里的 rid 还在——切回 A 会还原成「已发出未确认」，由用户查询，而不是盲目重发
    early.clear()
    set((s) => ({ sessions: [], submissions: {}, generation: s.generation + 1 }))
  },
}))

function bufferEarly(sid: string, rid: string | undefined, ev: EarlyEvent): void {
  const subs = useAiStore.getState().submissions
  const pending = Object.keys(subs).length > 0
  // 没有任何在途提交 = 这是别人的 / 已经丢弃的会话的事件，不留；带了 rid 的必须对得上一条提交
  if (!pending || (rid && !subs[rid])) return
  let list = early.get(sid)
  if (!list) {
    if (early.size >= EARLY_MAX_SIDS) early.delete(early.keys().next().value as string)
    list = []
    early.set(sid, list)
  }
  if (list.length < EARLY_MAX_EVENTS) list.push(ev)
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
      // 后端明确拒绝 = 一定没执行：提交记录作废（下一次发送换新 id），文本留给用户改
      clearSubmitted(sub.project, sub.fileId, rid)
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
    | { kind: 'queried'; status: string; changed: boolean; detail: AiSessionDetail | null },
): void {
  const { get, set } = { get: useAiStore.getState, set: useAiStore.setState }
  const sub = get().submissions[rid]
  if (!sub) return
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
  const settled = how.kind === 'queried' && status !== 'running'
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
  }
  set((s) => {
    const { [rid]: _gone, ...rest } = s.submissions
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

/** 这条没落定的提交是否锁住 (fileId, script) 这个面板：同面板，或同一份脚本（按文件名比，同 `isSessionOf`） */
export function submissionBlocks(
  sub: Pick<AiSubmission, 'fileId' | 'script'>,
  fileId: string,
  script: string | null | undefined,
): boolean {
  if (sub.fileId === fileId) return true
  return !!script && !!sub.script && scriptName(sub.script) === scriptName(script)
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
  return !!panel.script && scriptName(s.script) === scriptName(panel.script)
}
