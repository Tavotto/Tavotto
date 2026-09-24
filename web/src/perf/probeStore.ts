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
  type PerfReport,
  type SavedReport,
} from './session'
import { abortSynthetic, runStandardTest, STANDARD_PASSES } from './synthetic'

export type ProbePhase = 'off' | 'recording' | 'picking' | 'running' | 'done'

interface ProbeState {
  phase: ProbePhase
  segments: number
  /** 自动测试正在跑第几轮（1 起） */
  pass: number
  notice: 'not_draggable' | 'no_data' | 'save_failed' | null
  result: { file: string; dir: string | null; fps: number | null; jankPct: number | null } | null
  /** 桌面版后端没接住的那份报告：留着给「重试」，绝不因为保存失败就丢掉录好的数据 */
  unsaved: PerfReport | null
  /** 在文件管理器里定位失败时的完整路径（失败绝不静默，与导出对话框同一条） */
  revealFailedPath: string | null
  start: () => boolean
  beginPick: () => void
  cancelPick: () => void
  runAt: (x: number, y: number) => Promise<void>
  stopTest: () => void
  finish: () => Promise<void>
  /** 保存失败后再存一次同一份报告 */
  retrySave: () => Promise<void>
  /** 桌面：在访达 / 资源管理器里显示报告 */
  reveal: () => void
  discard: () => void
  close: () => void
}

let unsub: (() => void) | null = null
/**
 * 保存请求的代数。保存是异步的：在它回来之前用户可能已经关掉面板、放弃、重新开始，
 * 或又点了一次「重试保存」。只有**最新一次、且期间面板没被关掉**的结果才许落到界面——
 * 否则关掉的面板会被 `phase: 'done'` 重新打开，过时的失败也会盖掉后来的成功
 */
let saveGen = 0

export const usePerfProbeStore = create<ProbeState>((set, get) => ({
  phase: 'off',
  segments: 0,
  pass: 0,
  notice: null,
  result: null,
  unsaved: null,
  revealFailedPath: null,

  start: () => {
    if (get().phase !== 'off' && get().phase !== 'done') return false
    if (!startProbe()) return false
    unsub?.()
    unsub = perfSubscribe(() => set({ segments: perfSegmentCount() }))
    saveGen++
    set({ phase: 'recording', segments: 0, pass: 0, notice: null, result: null, unsaved: null })
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
    const gen = ++saveGen
    const report = await finishProbe()
    if (gen !== saveGen) return
    if (!report || report.segments.length === 0) {
      set({ phase: 'done', notice: 'no_data', result: null })
      return
    }
    const saved = await saveReport(report)
    if (gen === saveGen) landSaved(report, saved)
  },
  retrySave: async () => {
    const report = get().unsaved
    if (!report || get().phase !== 'done') return
    const gen = ++saveGen
    const saved = await saveReport(report)
    if (gen === saveGen) landSaved(report, saved)
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
    saveGen++
    abortSynthetic()
    unsub?.()
    unsub = null
    cancelProbe()
    set({ phase: 'off', segments: 0, pass: 0, notice: null, result: null, unsaved: null })
  },
  close: () => {
    saveGen++
    set({ phase: 'off', notice: null, result: null, unsaved: null, revealFailedPath: null })
  },
}))

/** 保存的结果落到界面：存上了给文件名与一句摘要；没存上说失败、留着报告 */
function landSaved(report: PerfReport, saved: SavedReport | null): void {
  const store = usePerfProbeStore
  if (!saved) {
    store.setState({ phase: 'done', notice: 'save_failed', result: null, unsaved: report })
    return
  }
  const s = quickSummary(report)
  store.setState({
    phase: 'done',
    notice: null,
    unsaved: null,
    revealFailedPath: null,
    result: { file: saved.name, dir: saved.dir, fps: s.fps, jankPct: s.jankPct },
  })
  store.getState().reveal()
}

export const STANDARD_PASS_COUNT = STANDARD_PASSES.length
