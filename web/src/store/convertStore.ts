/**
 * 「生成可编辑版本」（ADR 0143）的前端持有者：每个脚本一条 `idle → running → ready → adopting → adopted` 流水，
 * 失败落 `failed`（一句话 + 重试）。后端会话走既有的 `/api/ai/sessions/<sid>` 轮询（不接 aiStore：那条是改面板脚本的）。
 * 原脚本从不被这条流水改写；采用 = 后端新建兄弟文件，随后用既有的「打开并运行」接进画布（原位图面板不动）。
 */
import { create } from 'zustand'
import {
  adoptConvert,
  agentById,
  aiCancel,
  backendErrorText,
  discardConvert,
  effectiveAgent,
  fetchAiSession,
  fetchConvert,
  isDefiniteRunFailure,
  startConvert,
  type ConvertStatus,
} from '@/lib/api'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { newRequestId, useAiStore } from '@/store/aiStore'

export type ConvertPhase = 'running' | 'ready' | 'adopting' | 'adopted' | 'failed'

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
  library: string
  path: string | null
}

interface ConvertState {
  entries: Record<string, ConvertEntry>
  start: (script: string, library: string, path: string | null) => Promise<void>
  adopt: (script: string) => Promise<string | null>
  discard: (script: string) => Promise<void>
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

export const useConvertStore = create<ConvertState>((set, get) => ({
  entries: {},

  start: async (script, library, path) => {
    const guard = captureProjectEpoch()
    const pj = currentProjectId()
    // 续查只认「失败卡上留着的、且登记为已受理」的那个会话（条目被重置后遗留的登记不算）
    const prior = get().entries[script]
    const resumable = prior?.phase === 'failed' && prior.session ? prior.session : null
    set((s) => ({
      entries: {
        ...s.entries,
        [script]: { phase: 'running', session: null, error: null, reason: null, status: null, adopted: null, library, path },
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
          { script, library, path, client_request_id: rid, ...(agent ? { agent, model, effort } : {}) },
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
        const sess = await fetchAiSession(session, pj)
        if (!guard.still()) return
        if (!sess.final && sess.status === 'running') continue
        if (sess.task?.outcome === 'completed' && sess.status === 'done') {
          const status = await fetchConvert(session, pj)
          if (!guard.still()) return
          set1(script, { phase: 'ready', status })
        } else if (sess.status === 'cancelled') {
          get().clear(script)
        } else {
          acceptedSessions.delete(key) // 任务明确终局失败：重试起新会话
          set1(script, {
            phase: 'failed',
            reason: failureCode(sess.task?.unmet as { constraint_id: string; reason_code: string }[] | undefined),
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

  discard: async (script) => {
    const cur = get().entries[script]
    get().clear(script)
    if (cur?.session && cur.phase === 'ready') {
      try {
        await discardConvert(cur.session, currentProjectId())
      } catch {
        /* 放弃失败不挡路：候选 24 小时后自己过期 */
      }
    }
  },

  stop: async (script) => {
    const cur = get().entries[script]
    get().clear(script)
    if (cur?.session) {
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
