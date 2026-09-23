/**
 * 探针界面的状态（ADR 0075）。录制本身的数据在 `core.ts`，这里只放「界面
 * 现在处于哪一步」：设置页的「开始」与画布上的浮动面板靠它接上。
 */
import { create } from 'zustand'
import { isDesktop, revealExportedFile } from '@/lib/desktop'
import { perfSubscribe, perfSegmentCount } from './core'
import {
  cancelProbe,
  finishProbe,
  quickSummary,
  saveReport,
  startProbe,
} from './session'
import { abortSynthetic, runStandardTest, STANDARD_PASSES } from './synthetic'

export type ProbePhase = 'off' | 'recording' | 'picking' | 'running' | 'done'

interface ProbeState {
  phase: ProbePhase
  segments: number
  /** 自动测试正在跑第几轮（1 起） */
  pass: number
  notice: 'not_draggable' | 'no_data' | null
  result: { file: string; dir: string | null; fps: number | null; jankPct: number | null } | null
  /** 在文件管理器里定位失败时的完整路径（失败绝不静默，与导出对话框同一条） */
  revealFailedPath: string | null
  start: () => boolean
  beginPick: () => void
  cancelPick: () => void
  runAt: (x: number, y: number) => Promise<void>
  stopTest: () => void
  finish: () => Promise<void>
  /** 桌面：在访达 / 资源管理器里显示报告 */
  reveal: () => void
  discard: () => void
  close: () => void
}

let unsub: (() => void) | null = null

export const usePerfProbeStore = create<ProbeState>((set, get) => ({
  phase: 'off',
  segments: 0,
  pass: 0,
  notice: null,
  result: null,
  revealFailedPath: null,

  start: () => {
    if (get().phase !== 'off' && get().phase !== 'done') return false
    if (!startProbe()) return false
    unsub?.()
    unsub = perfSubscribe(() => set({ segments: perfSegmentCount() }))
    set({ phase: 'recording', segments: 0, pass: 0, notice: null, result: null })
    return true
  },
  beginPick: () => {
    if (get().phase === 'recording') set({ phase: 'picking', notice: null })
  },
  cancelPick: () => {
    if (get().phase === 'picking') set({ phase: 'recording' })
  },
  runAt: async (x, y) => {
    if (get().phase !== 'picking') return
    set({ phase: 'running', pass: 1 })
    const r = await runStandardTest(x, y, (_p, i) => set({ pass: i + 1 }))
    if (get().phase !== 'running') return
    set({ phase: 'recording', pass: 0, notice: r === 'not_draggable' || r === 'no_target' ? 'not_draggable' : null })
  },
  stopTest: () => abortSynthetic(),
  finish: async () => {
    const phase = get().phase
    if (phase === 'off' || phase === 'done') return
    if (phase === 'running') abortSynthetic()
    unsub?.()
    unsub = null
    const report = await finishProbe()
    if (!report || report.segments.length === 0) {
      set({ phase: 'done', notice: 'no_data', result: null })
      return
    }
    const saved = await saveReport(report)
    const s = quickSummary(report)
    set({
      phase: 'done',
      notice: null,
      revealFailedPath: null,
      result: { file: saved.name, dir: saved.dir, fps: s.fps, jankPct: s.jankPct },
    })
    get().reveal()
  },
  reveal: () => {
    const r = get().result
    if (!r?.dir || !isDesktop()) return
    const { dir, file } = r
    void revealExportedFile(dir, file).then((ok) => {
      if (!ok) set({ revealFailedPath: `${dir}/${file}` })
    })
  },
  discard: () => {
    abortSynthetic()
    unsub?.()
    unsub = null
    cancelProbe()
    set({ phase: 'off', segments: 0, pass: 0, notice: null, result: null })
  },
  close: () => set({ phase: 'off', notice: null, result: null, revealFailedPath: null }),
}))

export const STANDARD_PASS_COUNT = STANDARD_PASSES.length
