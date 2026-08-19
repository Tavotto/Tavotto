import { create } from 'zustand'
import { t } from '@/i18n'
import {
  aiCancel,
  aiRevert,
  aiRun,
  fetchAiCapabilities,
  type AiCapabilities,
  type AiDeltaKind,
} from '@/lib/api'

export type AiAgent = 'codex' | 'claude'
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
  agent: AiAgent
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
  agent: AiAgent
  /** 用户选择的作用范围；目标不支持时由面板降级，不改这里 */
  scope: AiScope
  sessions: AiSession[]
  /** 本机 CLI 实测能力；null = 尚未探测 */
  caps: AiCapabilities | null
  /** 每 provider 各自的模型 / 推理强度选择（能力不同构，不共用） */
  models: Partial<Record<AiAgent, string>>
  efforts: Partial<Record<AiAgent, string>>
  loadCaps: (refresh?: boolean) => Promise<void>
  setAgent: (a: AiAgent) => void
  setScope: (s: AiScope) => void
  setModel: (agent: AiAgent, model: string) => void
  setEffort: (agent: AiAgent, effort: string) => void
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
const LS_AGENT = 'magplot.ai.agent'
const LS_PREFS = 'magplot.ai.prefs'

function readAgent(): AiAgent {
  try {
    const v = localStorage.getItem(LS_AGENT)
    if (v === 'codex' || v === 'claude') return v
  } catch {
    /* 用默认值 */
  }
  return 'codex'
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
export function prunePrefs<T extends Partial<Record<AiAgent, string>>>(
  prefs: T,
  allowed: (agent: AiAgent) => string[],
): T {
  let changed = false
  const out = { ...prefs }
  for (const agent of Object.keys(out) as AiAgent[]) {
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

  loadCaps: async (refresh = false) => {
    try {
      const caps = await fetchAiCapabilities(refresh)
      set((s) => {
        // 当前选中的 provider 未安装时自动落到已安装的那个
        const cur = caps.providers[s.agent]
        const other: AiAgent = s.agent === 'codex' ? 'claude' : 'codex'
        const agent = cur?.installed ? s.agent : caps.providers[other]?.installed ? other : s.agent
        // 记忆里的模型/强度若已不在本机 CLI 的清单里就丢弃，回落到默认值
        const models = prunePrefs(s.models, (a) => caps.providers[a]?.models ?? [])
        const efforts = prunePrefs(s.efforts, (a) => caps.providers[a]?.efforts ?? [])
        if (models !== s.models || efforts !== s.efforts) persistPrefs(models, efforts)
        return { caps, agent, models, efforts }
      })
    } catch {
      /* 探测失败保留上次结果 */
    }
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
    const agent = get().agent
    const caps = get().caps?.providers[agent]
    const model = get().models[agent] ?? caps?.default_model ?? null
    const effort = caps?.efforts.length
      ? (get().efforts[agent] ?? caps.default_effort ?? null)
      : null
    const res = await aiRun({
      agent, id: fileId, prompt, gid, label, overrides,
      model, effort, scope, target, canvas,
    })
    const session: AiSession = {
      id: res.session,
      agent,
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

export const AGENT_LABEL: Record<AiAgent, string> = {
  codex: 'Codex',
  claude: 'Claude',
}

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
