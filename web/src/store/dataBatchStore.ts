import { create } from 'zustand'
import {
  ApiError,
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  backendErrorMsg,
  isDefiniteRunFailure,
  type AiDataCommitResult,
  type AiDataProposeResponse,
} from '@/lib/api'
import { runExclusive } from '@/lib/dataCommitQueue'
import { isCommitInProgress, pollCommit } from '@/lib/dataCommitPoll'
import { KNOWN_NOTHING_REASONS, MAX_MERGE_TABLES, isStaleProposeCode, sameTable, tableKey, type TableRef } from '@/lib/dataFigure'
import { captureProjectEpoch } from '@/lib/projectEpoch'
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
  /** 这一轮要画、还没轮到 */
  | { state: 'queued' }
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
  /** 全部画成之后 / 「看已画的」：左栏切到素材抽屉（图卡在那里） */
  showAssets: () => void
  /** 一轮开画之前：别让「发现绘图脚本」的准备卡把我们自己刚生成、自己会运行的脚本当成外来的新脚本弹出来（项目刷新会顺带重扫） */
  quietGuideCard: () => void
  /** 合画列名不一致的出路：回到数据抽屉（窄屏临时标签激活时抽屉是收起的） */
  showData: () => void
  /** 项目文件夹可不可写；不知道回 undefined */
  projectWritable: () => boolean | undefined
  /** 此刻激活画布的绑定解析出的出版规范（随每份的出候选请求发给后端，各画一张都按同一份栏宽与字号）；画布没有绑定 / 没注册 = 不带 */
  canvasSpec?: () => Record<string, unknown> | null
}
let host: BatchHost = { refreshProject: async () => {}, runScript: async () => {}, showAssets: () => {}, showData: () => {}, quietGuideCard: () => {}, projectWritable: () => undefined }
export const configureDataBatchHost = (h: BatchHost): void => {
  host = h
}

interface BatchStore {
  /** 多选的表（按选的顺序；也是画的顺序）。进行中它是「还没画成的」：画成一份移出一份，没画成的留着 */
  selection: TableRef[]
  /** Shift+点选一段的起点：最近一次点的那一行 */
  anchor: TableRef | null
  /** 「选多份」多选态：抽屉里出现固定高度的动作区，普通单击 = 加选 / 取消。手势（⌘ 点 / Shift 点）选中第一份时自动进入，两条路是同一个状态 */
  pickMode: boolean
  rows: Record<string, RowState>
  running: boolean
  progress: { done: number; total: number }
  stop: BatchStop
  /** 点了「停下」：画完当前这一份就停，后面的不画、仍留在选择里；已画的留着 */
  stopRequested: boolean

  isSelected: (ref: TableRef) => boolean
  /** ⌘/Ctrl+点：加进 / 移出多选 */
  toggle: (ref: TableRef) => void
  /** Shift+点：从起点到这一行（按当前可见的表行顺序）全选上；没有起点就当 toggle */
  selectRange: (ref: TableRef, visible: TableRef[]) => void
  setAnchor: (ref: TableRef | null) => void
  /** 普通单击 / Esc：清掉多选（进行中不清） */
  clearSelection: () => void
  setPickMode: (on: boolean) => void
  /** `all` = 各画一张（整个选择，含写不进文件夹时没轮到的）；`failed` = 再试一次（只重做没画成的） */
  run: (mode?: 'all' | 'failed') => Promise<void>
  /** 停下：画完当前这一份就停 */
  stopRun: () => void
  /** 合画一张：退出多选态，把选中的几份交给临时标签 */
  merge: () => void
  /** 合画列名不一致的出路：恢复刚才的选择、回数据抽屉、直接各画一张 */
  drawEach: (refs: TableRef[]) => void
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
  pickMode: false,
  rows: {} as Record<string, RowState>,
  running: false,
  progress: { done: 0, total: 0 },
  stop: null as BatchStop,
  stopRequested: false,
}

/**
 * 提交结果不确定（断网 / 网关错：后端可能已经写出了脚本）的那一行：只留着请求号（和重发要带的会话 / 备选 id 两个字符串，按 `tableKey` 存），
 * 会话照常放弃——后端同时至多留 6 个候选会话，批量不能为每个不确定的行占一个、把用户正开着的临时标签的会话挤掉。
 * 「重试」用**同一个**请求号重发这一次提交：后端（`/api/ai/data-figure/commit`）**先按请求号去重、后取会话**，首次提交已落盘而响应丢了时，
 * 会话已不在也照样回原来那份结果（只回既有任务的结局），不会多画出一份（同单份流程的 `commitRid`）。
 * 首次请求根本没到后端（没有记录）时，重发会因会话不在回 404 `propose_session_expired`：什么都没写过，就重新出候选、换新请求号。
 * 其余明确拒绝（`isDefiniteRunFailure`）同样作废请求号重新出；仍没拿到结果就继续留着。
 */
const pendingCommits = new Map<string, { session: string; id: string; rid: string; pj: string | null }>()

/** 一次提交的结果 → 这一份的结局 */
function outcomeOf(done: AiDataCommitResult): One {
  if (!done.script || !done.stem) {
    // 没落盘：写不进文件夹是项目级的事实（停整批），其余算这一份没画成
    const unwritable = done.reason_code === 'figures_dir_unusable' || host.projectWritable() === false
    return { ok: false, reason: null, unwritable }
  }
  // 脚本已经落盘（普通脚本，留着）但数据与图对不上：这一份算没画成，原因照单份流程那句
  if (done.data_check === 'mismatch' || done.task?.outcome === 'failed') return { ok: false, reason: msg('dataFigureV2.result.mismatch', undefined, 'ai') }
  return { ok: true, script: done.script }
}

/** 一份表：（上次提交结果不确定就先用同一个请求号重发）出候选 → 提交第一张（它渲染不出来就依次试后面的）→ 放弃会话。不打开。 */
async function drawOne(ref: TableRef, still: () => boolean, pj: string | null, spec: Record<string, unknown> | null): Promise<One> {
  // 排队的回调轮到时先核一遍：这期间换过项目就什么都不发（A 的相对路径不能拿到 B 里去）
  if (!still()) return { ok: false, reason: null }
  const key = tableKey(ref)
  const headers = useDataFigureStore.getState().headerRows
  const pend = pendingCommits.get(key)
  if (pend && pend.pj === pj) {
    try {
      const body = { session: pend.session, id: pend.id, client_request_id: pend.rid }
      let done = await aiDataFigureCommit(body, pj)
      if (!still()) return { ok: false, reason: null }
      // 原任务还在处理中（去重回的是当前快照）：既不算失败也不算画成，请求号留着，按有界间隔重问同一个直到 final
      if (isCommitInProgress(done)) {
        const waited = await pollCommit(() => aiDataFigureCommit(body, pj), still)
        if (!still()) return { ok: false, reason: null }
        if (!waited) return { ok: false, reason: null } // 超时仍未完成：这一份标失败，请求号仍保留（下次重试继续问同一个）
        done = waited
      }
      pendingCommits.delete(key)
      return outcomeOf(done)
    } catch (e) {
      if (!still()) return { ok: false, reason: null }
      // 还是没拿到结果：原样留着，下次重试再用这个请求号
      if (!isDefiniteRunFailure(e)) return { ok: false, reason: reasonOf(e) }
      // 后端明确拒绝（含会话 / 备选已过期、去重表也不在了）：请求号作废，下面重新出候选；写不进文件夹是项目级的，不再重来
      pendingCommits.delete(key)
      const code = codeOf(e)
      if (code === 'figures_dir_unusable' || code === 'common_module_conflict') return { ok: false, reason: reasonOf(e), unwritable: true }
    }
  } else if (pend) {
    pendingCommits.delete(key)
  }
  let retried = false
  for (;;) {
    let res: AiDataProposeResponse
    try {
      res = await aiDataFigurePropose(
        { table: ref.path, ...(ref.sheet == null ? {} : { sheet: ref.sheet }), render: 'lazy', ...(headers[ref.path] != null ? { header_rows: { [ref.path]: headers[ref.path]! } } : {}), ...(spec ? { spec } : {}) },
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
        const rid = newRid()
        try {
          const done = await aiDataFigureCommit({ session, id: item.id, client_request_id: rid }, pj)
          if (!still()) return { ok: false, reason: null }
          return outcomeOf(done)
        } catch (e) {
          // 懒渲染：这一张提交时当场渲染 / 核对没过（404）→ 试下一张；一张都不行就是没画成
          if (codeOf(e) === 'propose_candidate_unknown') continue
          // 没收到结果（断网 / 网关）：后端可能已写出脚本。留着请求号，不试下一张（会多画一份），重试时同一个请求号重发
          if (!isDefiniteRunFailure(e)) {
            if (still()) pendingCommits.set(key, { session, id: item.id, rid, pj })
            return { ok: false, reason: reasonOf(e) }
          }
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
      void aiDataFigureDiscard(session, pj).catch(() => undefined)
    }
  }
}

export const useDataBatchStore = create<BatchStore>((set, get) => ({
  ...initial,

  isSelected: (ref) => get().selection.some((r) => sameTable(r, ref)),

  toggle: (ref) => {
    set((s) => ({
      anchor: ref,
      pickMode: true,
      selection: s.selection.some((r) => sameTable(r, ref)) ? s.selection.filter((r) => !sameTable(r, ref)) : [...s.selection, ref],
      // 取消选一份没画成的 = 把它的「没画出来」也撤掉（不留无主的标记）
      rows: s.rows[tableKey(ref)]?.state === 'failed' ? Object.fromEntries(Object.entries(s.rows).filter(([k]) => k !== tableKey(ref))) : s.rows,
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
      return { pickMode: true, selection: [...keep, ...visible.slice(lo, hi + 1)] }
    })
  },

  setAnchor: (ref) => set({ anchor: ref }),

  /** 进入 / 退出多选态；退出 = 清掉选择（进行中不动） */
  setPickMode: (on) => {
    if (get().running) return
    if (on) set({ pickMode: true })
    else get().clearSelection()
  },

  clearSelection: () => {
    if (get().running) return
    set({ pickMode: false, selection: [], rows: Object.fromEntries(Object.entries(get().rows).filter(([, v]) => v.state === 'done')), stop: null })
  },

  run: async (mode = 'all') => {
    if (get().running) return
    const failedKeys = new Set(Object.entries(get().rows).filter(([, v]) => v.state === 'failed').map(([k]) => k))
    const todo = get().selection.filter((r) => mode === 'all' || failedKeys.has(tableKey(r)))
    if (todo.length === 0) return
    host.quietGuideCard()
    // 一轮开始时解析一次：这一批各画一张用同一份规范，中途用户切了画布 / 换了期刊也不让同一批的图宽窄不一
    const spec = host.canvasSpec?.() ?? null
    const ep = epoch
    const guard = captureProjectEpoch(() => epoch)
    // 清掉这几份上一轮的标记（没画成的重做）；还没轮到的没有记录
    const keys = new Set(todo.map(tableKey))
    // 全新一轮（没有没画成的在等重试）：上一批留下的「已画」标记不再属于这一批，免得「N 张已画」把它们算进来
    const fresh = failedKeys.size === 0
    set((s) => ({
      running: true,
      stop: null,
      stopRequested: false,
      progress: { done: 0, total: todo.length },
      rows: {
        ...Object.fromEntries(Object.entries(s.rows).filter(([k, v]) => !keys.has(k) && !(fresh && v.state === 'done'))),
        ...Object.fromEntries([...keys].map((k) => [k, { state: 'queued' } as RowState])),
      },
    }))
    let stopped: BatchStop = null
    for (let i = 0; i < todo.length; i++) {
      const ref = todo[i]!
      if (!guard.still() || ep !== epoch) return // 换了项目：静默中止，已写出的就是普通脚本
      if (get().stopRequested) break // 「停下」：后面的不画
      const key = tableKey(ref)
      set((s) => ({ rows: { ...s.rows, [key]: { state: 'running' } } }))
      const r = await runExclusive(() => drawOne(ref, guard.still, guard.pj, spec))
      if (!guard.still() || ep !== epoch) return
      if (r.ok) {
        await host.refreshProject().catch(() => undefined) // 图卡出现；不打开
        if (!guard.still() || ep !== epoch) return
        // 只运行、不打开：素材卡才有预览。排进同一条队列，三份不会同时渲染；跑不成不算没画出来、不加提示
        // 排队期间换过项目：回调轮到时什么都不做（`runScript` 取的是此刻的项目，A 的相对路径会在 B 里被运行）
        await runExclusive(async () => {
          if (!guard.still()) return
          await host.runScript(r.script).catch(() => undefined)
        })
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
    // 没轮到的（停下 / 写不进文件夹）：不标成功也不标失败，撤掉「等着」，原样留在选择里
    set((s) => ({ running: false, stop: stopped, stopRequested: false, rows: Object.fromEntries(Object.entries(s.rows).filter(([, v]) => v.state !== 'queued')) }))
    const failed = Object.values(get().rows).some((v) => v.state === 'failed')
    if (!failed && !stopped && get().selection.length === 0) {
      // 全部画成：清多选，左栏切到素材抽屉（图卡在那里）；不弹任何通知
      set({ selection: [], rows: {}, anchor: null, pickMode: false })
      host.showAssets()
    }
  },

  stopRun: () => {
    if (get().running) set({ stopRequested: true })
  },

  merge: () => {
    const refs = [...get().selection]
    if (get().running || refs.length < 2 || refs.length > MAX_MERGE_TABLES) return
    get().clearSelection()
    useDataFigureStore.getState().selectGroup(refs)
  },

  drawEach: (refs) => {
    if (get().running || refs.length === 0) return
    useDataFigureStore.getState().closeDraft()
    set({ pickMode: true, selection: [...refs], anchor: null, rows: {}, stop: null })
    host.showData()
    void get().run('all')
  },

  clear: () => {
    epoch += 1
    pendingCommits.clear()
    set({ ...initial })
  },
}))
