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
 * 每个脚本一条的请求 id：同一次「生成」在**不确定是否已送达**（断网 / 超时 / 网关错）后重试要复用它，
 * 后端据此去重，不会再起第二个付费 Agent；明确拒绝或已拿到会话后作废（下一次换新 id）。
 */
const pendingRequestIds = new Map<string, string>()

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
    set((s) => ({
      entries: {
        ...s.entries,
        [script]: { phase: 'running', session: null, error: null, reason: null, status: null, adopted: null, library, path },
      },
    }))
    const mine = get().entries[script]
    let rid = pendingRequestIds.get(script)
    if (!rid) {
      rid = newRequestId()
      pendingRequestIds.set(script, rid)
    }
    try {
      // 与既有 AI 流程同一套「实际派活的 Agent」：用户选了 Claude 或 Codex 不可用时不能默认成 codex
      const ai = useAiStore.getState()
      if (!ai.caps) await ai.loadCaps().catch(() => undefined)
      const { agent: preferred, caps, models, efforts } = useAiStore.getState()
      const agent = effectiveAgent(preferred, caps)
      const info = agent ? agentById(caps, agent) : undefined
      const model = agent ? (models[agent] ?? info?.default_model ?? null) : null
      const effort = agent && info?.efforts.length ? (efforts[agent] ?? info.default_effort ?? null) : null
      const { session } = await startConvert(
        { script, library, path, client_request_id: rid, ...(agent ? { agent, model, effort } : {}) },
        pj,
      )
      pendingRequestIds.delete(script)
      if (!guard.still()) return
      if (get().entries[script] !== mine) {
        // 等 POST 期间用户点了「停止」/ 换了脚本流水：后端已起了会话，要真的取消，不能让它悄悄跑完
        try {
          await aiCancel(session, pj)
        } catch {
          /* 取消失败：会话自己会收尾 */
        }
        return
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
          set1(script, {
            phase: 'failed',
            reason: failureCode(sess.task?.unmet as { constraint_id: string; reason_code: string }[] | undefined),
          })
        }
        return
      }
    } catch (e) {
      if (isDefiniteRunFailure(e)) pendingRequestIds.delete(script)
      if (!guard.still()) return
      if (get().entries[script] !== mine && !get().entries[script]?.session) return // 已被停止：别再写回
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

  clear: (script) =>
    set((s) => {
      const { [script]: _gone, ...rest } = s.entries
      return { entries: rest }
    }),
}))

// 换项目：上一个项目的流水一律丢弃（脚本名可能在两个项目里重名）
onCurrentProjectChange(() => useConvertStore.setState({ entries: {} }))
