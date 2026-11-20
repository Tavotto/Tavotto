/**
 * 一键排版的两个选项（ADR 0199 §一）：目标宽度、子图间距与「同时按规范修文字和线条」。记在本机（每个人手感不同），**不进 .tavotto 文档**。
 * 工具条的小下拉与命令面板共用这一处：两个入口跑出来的是同一份选择。读写都包 try/catch（隐私窗口 / 存储被禁时
 * 退回默认值，功能照常）。
 */
export const QUICK_LAYOUT_TARGETS = ['single', 'double', 'page'] as const
export type QuickLayoutTarget = (typeof QUICK_LAYOUT_TARGETS)[number]

export const QUICK_LAYOUT_GAPS = [2, 3, 4] as const

export const DEFAULT_QUICK_LAYOUT_TARGET: QuickLayoutTarget = 'double'
export const DEFAULT_QUICK_LAYOUT_GAP = 3

const KEY = 'tavotto.quickLayout'

export interface QuickLayoutPrefs {
  target: QuickLayoutTarget
  gapMm: number
  /** 排版时顺手把文字和线条按当前规范修好（默认开；缺 / 坏 = 开） */
  fixSpec: boolean
}

export function readQuickLayoutPrefs(): QuickLayoutPrefs {
  const out: QuickLayoutPrefs = { target: DEFAULT_QUICK_LAYOUT_TARGET, gapMm: DEFAULT_QUICK_LAYOUT_GAP, fixSpec: true }
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Partial<QuickLayoutPrefs> | null
    if (raw && (QUICK_LAYOUT_TARGETS as readonly unknown[]).includes(raw.target)) out.target = raw.target as QuickLayoutTarget
    if (raw && (QUICK_LAYOUT_GAPS as readonly unknown[]).includes(raw.gapMm)) out.gapMm = raw.gapMm as number
    if (raw && typeof raw.fixSpec === 'boolean') out.fixSpec = raw.fixSpec
  } catch {
    /* 没有存储 / 内容坏了：用默认值 */
  }
  return out
}

export function writeQuickLayoutPrefs(prefs: QuickLayoutPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs))
  } catch {
    /* 写不进去也不影响这一次排版 */
  }
}
