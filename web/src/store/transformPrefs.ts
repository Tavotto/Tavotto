import { create } from 'zustand'
import type { RefPoint, TransformOp } from '@/lib/transform'
import { REF_POINTS } from '@/lib/transform'

/**
 * 变换面板（ADR 0185 M4 第二批）的 UI 偏好与「上一次变换」记忆。
 *
 * 偏好（参考点、W / H 等比锁、缩放描边和效果）是个人习惯，存 localStorage（`tavotto.transform`），不进文档。
 * 缺省：参考点左上（X / Y 的含义与加这个面板之前一样）、不锁比例、不缩放描边（Illustrator 的缺省）。
 *
 * 记忆（不持久化、不进历史）：`lastOp` 给 ⌥⌘D 再次变换；`lastDup` 给 ⌘D 的「重复复制和位移」——记下上一次 ⌘D 的
 * 原件与副本，副本若之后被挪 / 转过，再按 ⌘D 就按同样的位移与角度差再复制一份。
 */
const LS_KEY = 'tavotto.transform'

interface Persisted {
  refPoint: RefPoint
  lockRatio: boolean
  scaleStrokes: boolean
}

function read(): Persisted {
  const d: Persisted = { refPoint: 'tl', lockRatio: false, scaleStrokes: false }
  try {
    const raw = JSON.parse(localStorage.getItem(LS_KEY) ?? '{}') as Partial<Persisted>
    if (raw && typeof raw === 'object') {
      if (REF_POINTS.includes(raw.refPoint as RefPoint)) d.refPoint = raw.refPoint as RefPoint
      if (typeof raw.lockRatio === 'boolean') d.lockRatio = raw.lockRatio
      if (typeof raw.scaleStrokes === 'boolean') d.scaleStrokes = raw.scaleStrokes
    }
  } catch {
    /* 读不到就用缺省 */
  }
  return d
}

interface TransformPrefsState extends Persisted {
  set: (patch: Partial<Persisted>) => void
  /** 上一次通过变换面板 / 镜像做的变换（⌥⌘D 重放） */
  lastOp: TransformOp | null
  /** 上一次 ⌘D：原件 id 与副本 id 一一对应 */
  lastDup: { from: string[]; to: string[] } | null
  remember: (op: TransformOp) => void
  rememberDup: (from: string[], to: string[]) => void
}

export const useTransformPrefs = create<TransformPrefsState>((set, get) => ({
  ...read(),
  lastOp: null,
  lastDup: null,
  set: (patch) => {
    set(patch)
    const { refPoint, lockRatio, scaleStrokes } = get()
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ refPoint, lockRatio, scaleStrokes }))
    } catch {
      /* 存不了就只活在本会话 */
    }
  },
  remember: (op) => set({ lastOp: op }),
  rememberDup: (from, to) => set({ lastDup: { from, to } }),
}))
