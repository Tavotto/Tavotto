import { create } from 'zustand'

/**
 * 检查器折叠偏好。
 *
 * 「更多」的展开状态**按角色**记忆并持久化：同一角色在不同面板之间切换
 * 不重置（旧实现挂在面板组件的 useState 上，换面板全部归零，见审计 P4）。
 * 「源文件与高级」只在会话内按角色记忆、**不持久化**——默认关闭是这一层
 * 的契约（写回/历史是高风险低频动作，不该跨会话保持敞开）。
 *
 * 画布页的折叠行（`canvas:*`）互不排斥、**跨会话记住**（2026-10-07 设计审计 §9.3）：那几组是
 * 「我习惯把吸附开着看」这种个人偏好，不是高风险动作；其余摘要行仍只在会话内记。
 *
 * 这是 UI 偏好，存 localStorage（`tavotto.inspector`），不进文档 schema。
 */

const LS_KEY = 'tavotto.inspector'

interface InspectorPrefsState {
  /** role → 「更多」是否展开；未记录 = 默认收起 */
  moreOpen: Record<string, boolean>
  /** role → 「源文件与高级」是否展开（会话内） */
  advancedOpen: Record<string, boolean>
  /** `角色:折叠行 id` → 摘要行是否展开（会话内；「更多」之外的各摘要行） */
  foldOpen: Record<string, boolean>
  setMoreOpen: (role: string, open: boolean) => void
  setFoldOpen: (key: string, open: boolean) => void
  setAdvancedOpen: (role: string, open: boolean) => void
}

interface Persisted {
  moreOpen?: Record<string, boolean>
  canvasFolds?: Record<string, boolean>
}

function readAll(): Persisted {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Persisted
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

const record = (v: unknown): Record<string, boolean> =>
  typeof v === 'object' && v ? (v as Record<string, boolean>) : {}

function readPersisted(): Record<string, boolean> {
  return record(readAll().moreOpen)
}

/** 画布页折叠行的跨会话记忆（只认 `canvas:` 前缀的键） */
const CANVAS_FOLD = 'canvas:'
function readCanvasFolds(): Record<string, boolean> {
  return Object.fromEntries(Object.entries(record(readAll().canvasFolds)).filter(([k]) => k.startsWith(CANVAS_FOLD)))
}

function persist(state: Pick<InspectorPrefsState, 'moreOpen' | 'foldOpen'>): void {
  try {
    const canvasFolds = Object.fromEntries(Object.entries(state.foldOpen).filter(([k]) => k.startsWith(CANVAS_FOLD)))
    localStorage.setItem(LS_KEY, JSON.stringify({ moreOpen: state.moreOpen, canvasFolds }))
  } catch {
    /* 存储失败就只活在本会话 */
  }
}

export const useInspectorPrefs = create<InspectorPrefsState>((set, get) => ({
  moreOpen: readPersisted(),
  advancedOpen: {},
  foldOpen: readCanvasFolds(),
  setFoldOpen: (key, open) => {
    set((s) => ({ foldOpen: { ...s.foldOpen, [key]: open } }))
    if (key.startsWith(CANVAS_FOLD)) persist(get())
  },
  setMoreOpen: (role, open) => {
    set((s) => ({ moreOpen: { ...s.moreOpen, [role]: open } }))
    persist(get())
  },
  setAdvancedOpen: (role, open) =>
    set((s) => ({ advancedOpen: { ...s.advancedOpen, [role]: open } })),
}))
