import { create } from 'zustand'
import { t } from '@/i18n'
import {
  agentById,
  agentDisplayName,
  aiCancel,
  aiRevert,
  aiRun,
  effectiveAgent,
  fetchAiCapabilities,
  usableAgents,
  type AiAgentId,
  type AiCapabilities,
  type AiDeltaKind,
} from '@/lib/api'

export type AiStatus = 'running' | 'done' | 'failed' | 'timeout' | 'cancelled' | 'reverted'
/** 改动作用范围：决定发给后端的上下文粒度（gid/label 怎么拼） */
export type AiScope = 'element' | 'axes' | 'figure'

/** 一条 CLI 输出：正文进气泡，思考/动作折叠成过程 */
export interface AiEntry {
  kind: AiDeltaKind
  text: string
  /** 仍在逐字流入，渲染时带闪烁光标 */
  streaming?: boolean
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
}

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
  }) => Promise<void>
  appendDelta: (sid: string, kind: AiDeltaKind, text: string) => void
  finish: (p: { session: string; status: string; changed: boolean; diff: string; error?: string }) => void
  revert: (sid: string) => Promise<void>
  cancel: (sid: string) => Promise<void>
  clear: () => void
}

const MAX_LINES = 600
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

  start: async ({ prompt, fileId, panelId, gid, label, scope, target, overrides, canvas }) => {
    // 发任务前再确认一次：首选那个可能刚被关掉 / 刚被检测成不可用
    const caps = get().caps
    const agent = effectiveAgent(get().agent, caps)
    if (!agent) throw new Error('no-usable-agent')
    const info = agentById(caps, agent)
    const model = get().models[agent] ?? info?.default_model ?? null
    const effort = info?.efforts.length
      ? (get().efforts[agent] ?? info.default_effort ?? null)
      : null
    const res = await aiRun({
      agent, id: fileId, prompt, gid, label, overrides,
      model, effort, scope, target, canvas,
    })
    const session: AiSession = {
      id: res.session,
      agent,
      agentLabel: agentDisplayName(caps, agent),
      prompt,
      script: res.script,
      panelId,
      fileId,
      gid,
      scope,
      target,
      entries: [],
      status: 'running',
      changed: false,
      diff: '',
      startedAt: Date.now(),
    }
    set((s) => ({ sessions: [...s.sessions, session] }))
  },

  appendDelta: (sid, kind, text) =>
    set((s) => ({
      sessions: s.sessions.map((x) => {
        if (x.id !== sid) return x
        const entries = [...x.entries]
        const last = entries.at(-1)
        const streamingLast = last?.kind === 'message' && last.streaming ? last : null

        if (kind === 'delta') {
          // 逐字流入当前气泡；没有在流的就新开一个
          if (streamingLast) entries[entries.length - 1] = { ...streamingLast, text: streamingLast.text + text }
          else entries.push({ kind: 'message', text, streaming: true })
        } else if (kind === 'message') {
          // 终稿替换流式内容（后端给的是完整段落）
          if (streamingLast) entries[entries.length - 1] = { kind: 'message', text }
          else entries.push({ kind: 'message', text })
        } else {
          // 过程事件先给流式气泡定稿，免得插到半截文字后面
          if (streamingLast) entries[entries.length - 1] = { kind: 'message', text: streamingLast.text }
          entries.push({ kind, text })
        }
        return { ...x, entries: entries.slice(-MAX_LINES) }
      }),
    })),

  finish: ({ session, status, changed, diff, error }) =>
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === session
          ? {
              ...x,
              status: (status as AiStatus) || 'done',
              changed,
              diff,
              error,
              // 会话结束，光标不该继续闪
              entries: x.entries.map((e) => (e.streaming ? { ...e, streaming: false } : e)),
            }
          : x,
      ),
    })),

  revert: async (sid) => {
    await aiRevert(sid)
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === sid ? { ...x, status: 'reverted', changed: false } : x,
      ),
    }))
  },

  cancel: async (sid) => {
    await aiCancel(sid)
    set((s) => ({
      sessions: s.sessions.map((x) => (x.id === sid ? { ...x, status: 'cancelled' } : x)),
    }))
  },

  clear: () => set({ sessions: [] }),
}))

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
