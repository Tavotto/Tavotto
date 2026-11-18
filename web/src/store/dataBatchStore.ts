import { create } from 'zustand'
import {
  ApiError,
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  backendErrorMsg,
  type AiDataProposeResponse,
} from '@/lib/api'
import { runExclusive } from '@/lib/dataCommitQueue'
import { KNOWN_NOTHING_REASONS, isStaleProposeCode, sameTable, tableKey, type TableRef } from '@/lib/dataFigure'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId } from '@/lib/session'
import { msg, type UiMessage } from '@/i18n'
import { useDataFigureStore } from '@/store/dataFigureStore'

/**
 * 「一次选多份数据、各画一张」（ADR 0178 多选一节）。和单份流程（`dataFigureStore`，临时标签里点一张）互不借状态：
 * 这里有自己的一组（选了哪几份、每份的进行态、整批的进度），只共用一把串行锁（`lib/dataCommitQueue`：任何时刻只有一个提交在飞）。
 *
 * 一份的做法：`propose`（懒模式，不带 answers，用后端的默认）→ 取第一张候选（后端合同：推荐永远在第一项）→ `commit` → 放弃这个会话（后端同时至多 6 个）。
 * 不取缩略图（提交没渲染完的候选时后端自己渲染 + 核对）；写出的脚本只刷新素材清单，**不打开**——图卡出现在素材库里，由用户自己选改哪一张。
 * 逐份顺序做，不并发。状态按 `tableKey` 存（清单重读时行对象会换，状态不能丢），文件名不进 `data-*`。
 * 项目代际：切项目 / 关项目（`clear()`、`captureProjectEpoch`）静默中止，已写出的脚本就是普通脚本，留着。
 */

export type RowState =
  | { state: 'running' }
  | { state: 'done' }
  /** `reason` = 后端给的（或这份表画不出的）原因句，放行的 title 里；没有就只有「没画出来」 */
  | { state: 'failed'; reason: UiMessage | null }

/** 整批怎么停的：`unwritable` = 项目文件夹写不进，立刻停整批、按钮换成「换个文件夹」 */
export type BatchStop = 'unwritable' | null

export interface BatchHost {
  /** 刷新项目（新脚本进素材清单、图卡陆续出现）；不打开任何东西 */
  refreshProject: () => Promise<void>
  /** 只运行这份新脚本（不打开、不放进画布），好让素材卡有预览；跑不成不算没画出来，卡片自己会显示脚本的状态 */
  runScript: (script: string) => Promise<void>
  /** 全部画成之后：左栏切到素材抽屉（图卡在那里） */
  showAssets: () => void
  /** 项目文件夹可不可写；不知道回 undefined */
  projectWritable: () => boolean | undefined
}
let host: BatchHost = { refreshProject: async () => {}, runScript: async () => {}, showAssets: () => {}, projectWritable: () => undefined }
export const configureDataBatchHost = (h: BatchHost): void => {
  host = h
}

interface BatchStore {
  /** 多选的表（按选的顺序；也是画的顺序）。进行中它是「还没画成的」：画成一份移出一份，没画成的留着 */
  selection: TableRef[]
  /** Shift+点选一段的起点：最近一次点的那一行 */
  anchor: TableRef | null
  rows: Record<string, RowState>
  running: boolean
  progress: { done: number; total: number }
  stop: BatchStop

  isSelected: (ref: TableRef) => boolean
  /** ⌘/Ctrl+点：加进 / 移出多选 */
  toggle: (ref: TableRef) => void
  /** Shift+点：从起点到这一行（按当前可见的表行顺序）全选上；没有起点就当 toggle */
  selectRange: (ref: TableRef, visible: TableRef[]) => void
  setAnchor: (ref: TableRef | null) => void
  /** 普通单击 / Esc：清掉多选（进行中不清） */
  clearSelection: () => void
  /** 整批进行中 / 有没画成的 / 多选 ≥ 2：底部那一条要不要出现 */
  barVisible: () => boolean
  run: () => Promise<void>
  clear: () => void
}

let epoch = 0
let ridCounter = 0
const newRid = (): string => `df2b-${Date.now().toString(36)}-${(ridCounter++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`

const reasonOf = (e: unknown): UiMessage | null => (e instanceof ApiError ? backendErrorMsg(e) : null)
const codeOf = (e: unknown): string => (e instanceof ApiError && typeof e.body?.code === 'string' ? e.body.code : '')

type One = { ok: true; script: string } | { ok: false; reason: UiMessage | null; unwritable?: boolean }

const initial = {
  selection: [] as TableRef[],
  anchor: null as TableRef | null,
  rows: {} as Record<string, RowState>,
  running: false,
  progress: { done: 0, total: 0 },
  stop: null as BatchStop,
}

/** 一份表：出候选 → 提交第一张（它渲染不出来就依次试后面的）→ 放弃会话。不打开。 */
async function drawOne(ref: TableRef, still: () => boolean, pj: string | null): Promise<One> {
  const headers = useDataFigureStore.getState().headerRows
  let retried = false
  for (;;) {
    let res: AiDataProposeResponse
    try {
      res = await aiDataFigurePropose(
        { table: ref.path, ...(ref.sheet == null ? {} : { sheet: ref.sheet }), render: 'lazy', ...(headers[ref.path] != null ? { header_rows: { [ref.path]: headers[ref.path]! } } : {}) },
        pj,
      )
    } catch (e) {
      return { ok: false, reason: reasonOf(e) }
    }
    if (!still()) return { ok: false, reason: null }
    if (res.kind === 'nothing') {
      const first = res.reasons.find((r) => KNOWN_NOTHING_REASONS.includes(r))
      return { ok: false, reason: msg(first ? `dataFigureV2.reason.${first}` : 'dataFigureV2.nothing.generic', undefined, 'ai') }
    }
    if (res.kind !== 'candidates' || res.items.length === 0) return { ok: false, reason: null }
    const session = res.session
    try {
      for (const item of res.items) {
        if (!still()) return { ok: false, reason: null }
        try {
          const done = await aiDataFigureCommit({ session, id: item.id, client_request_id: newRid() }, pj)
          if (!still()) return { ok: false, reason: null }
          if (!done.script || !done.stem) {
            // 没落盘：写不进文件夹是项目级的事实（停整批），其余算这一份没画成
            const unwritable = done.reason_code === 'figures_dir_unusable' || host.projectWritable() === false
            return { ok: false, reason: null, unwritable }
          }
          // 脚本已经落盘（普通脚本，留着）但数据与图对不上：这一份算没画成，原因照单份流程那句
          if (done.data_check === 'mismatch' || done.task?.outcome === 'failed') return { ok: false, reason: msg('dataFigureV2.result.mismatch', undefined, 'ai') }
          return { ok: true, script: done.script }
        } catch (e) {
          // 懒渲染：这一张提交时当场渲染 / 核对没过（404）→ 试下一张；一张都不行就是没画成
          if (codeOf(e) === 'propose_candidate_unknown') continue
          throw e
        }
      }
      return { ok: false, reason: null }
    } catch (e) {
      const code = codeOf(e)
      if (code === 'figures_dir_unusable' || code === 'common_module_conflict') return { ok: false, reason: reasonOf(e), unwritable: true }
      if (isStaleProposeCode(code) && !retried) {
        retried = true // 会话过期 / 数据变了：不是用户的错，静默重新出一次
        continue
      }
      return { ok: false, reason: reasonOf(e) }
    } finally {
      void aiDataFigureDiscard(session, currentProjectId()).catch(() => undefined)
    }
  }
}

export const useDataBatchStore = create<BatchStore>((set, get) => ({
  ...initial,

  isSelected: (ref) => get().selection.some((r) => sameTable(r, ref)),

  toggle: (ref) => {
    set((s) => ({
      anchor: ref,
      selection: s.selection.some((r) => sameTable(r, ref)) ? s.selection.filter((r) => !sameTable(r, ref)) : [...s.selection, ref],
    }))
  },

  selectRange: (ref, visible) => {
    const a = get().anchor
    const from = a ? visible.findIndex((r) => sameTable(r, a)) : -1
    const to = visible.findIndex((r) => sameTable(r, ref))
    if (from < 0 || to < 0) {
      get().toggle(ref)
      return
    }
    const [lo, hi] = from <= to ? [from, to] : [to, from]
    set((s) => {
      const keep = s.selection.filter((r) => !visible.slice(lo, hi + 1).some((v) => sameTable(v, r)))
      return { selection: [...keep, ...visible.slice(lo, hi + 1)] }
    })
  },

  setAnchor: (ref) => set({ anchor: ref }),

  clearSelection: () => {
    if (get().running) return
    set({ selection: [], rows: Object.fromEntries(Object.entries(get().rows).filter(([, v]) => v.state === 'done')), stop: null })
  },

  barVisible: () => {
    const s = get()
    return s.running || s.selection.length >= 2 || Object.values(s.rows).some((r) => r.state === 'failed') || s.stop != null
  },

  run: async () => {
    if (get().running) return
    // 写不进的整批停下之后，按钮是「换个文件夹」，不是再来一遍
    const todo = [...get().selection]
    if (todo.length === 0) return
    const ep = epoch
    const guard = captureProjectEpoch(() => epoch)
    // 清掉这几份上一轮的标记（没画成的重做）；还没轮到的没有记录
    const keys = new Set(todo.map(tableKey))
    set((s) => ({
      running: true,
      stop: null,
      progress: { done: 0, total: todo.length },
      rows: Object.fromEntries(Object.entries(s.rows).filter(([k]) => !keys.has(k))),
    }))
    let stopped: BatchStop = null
    for (let i = 0; i < todo.length; i++) {
      const ref = todo[i]!
      if (!guard.still() || ep !== epoch) return // 换了项目：静默中止，已写出的就是普通脚本
      const key = tableKey(ref)
      set((s) => ({ rows: { ...s.rows, [key]: { state: 'running' } } }))
      const r = await runExclusive(() => drawOne(ref, guard.still, guard.pj))
      if (!guard.still() || ep !== epoch) return
      if (r.ok) {
        await host.refreshProject().catch(() => undefined) // 图卡出现；不打开
        if (!guard.still() || ep !== epoch) return
        // 只运行、不打开：素材卡才有预览。排进同一条队列，三份不会同时渲染；跑不成不算没画出来、不加提示
        await runExclusive(() => host.runScript(r.script).catch(() => undefined))
        if (!guard.still() || ep !== epoch) return
        set((s) => ({
          rows: { ...s.rows, [key]: { state: 'done' } },
          selection: s.selection.filter((x) => !sameTable(x, ref)),
          progress: { done: i + 1, total: todo.length },
        }))
      } else {
        set((s) => ({ rows: { ...s.rows, [key]: { state: 'failed', reason: r.reason } }, progress: { done: i + 1, total: todo.length } }))
        if (r.unwritable) {
          stopped = 'unwritable'
          // 还没轮到的：不标成功也不标失败，原样留在多选里
          break
        }
      }
    }
    set({ running: false, stop: stopped })
    const failed = Object.values(get().rows).some((v) => v.state === 'failed')
    if (!failed && !stopped) {
      // 全部画成：清多选，左栏切到素材抽屉（图卡在那里）；不弹任何通知
      set({ selection: [], rows: {}, anchor: null })
      host.showAssets()
    }
  },

  clear: () => {
    epoch += 1
    set({ ...initial })
  },
}))
