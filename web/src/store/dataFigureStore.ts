import { create } from 'zustand'
import {
  ApiError,
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  backendErrorMsg,
  isDefiniteRunFailure,
  type AiDataCommitResult,
  type AiDataFileEntry,
  type AiDataMixedColumn,
  type AiDataOption,
  type AiDataOverview,
  type AiDataProposeItem,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { isStaleProposeCode, mergeAnswers, tableKey, type TableRef } from '@/lib/dataFigure'
import type { UiMessage } from '@/i18n'

/**
 * 「从数据到图」的页面状态（ADR 0178，实验开关 `ai.taf.enabled` 后面）。一个 store，规矩同其它会在项目之间存活的 store：
 *
 *  - **项目代际**：`clear()`（换项目，`projectStore.resetForNewProject` 调）把 `epoch` 加一，在飞的请求回来时对不上就丢；
 *  - **发起那一刻的项目**：每个请求都带 `currentProjectId()` 的快照（`pj`），不跟着此刻的项目走；缩略图地址同样带它；
 *  - **请求序号**：清单、每张表的概览、出备选、提交各一个序号，回来时不是最新一次就丢；
 *  - 失败不清空已有内容；事实在后端：这里不编造读取进度 / 步骤，只把后端的话原样放进状态。
 *
 * 键一律是 `tableKey({path, sheet})`（项目相对路径 + 工作表名的结构化键），不用文件名。
 */

export type FilesState =
  | { status: 'idle' }
  | { status: 'loading'; entries: AiDataFileEntry[] | null }
  | { status: 'ready'; entries: AiDataFileEntry[]; truncated: boolean }
  | { status: 'error'; entries: AiDataFileEntry[] | null; error: UiMessage }

export type OverviewState =
  | { status: 'loading'; data: AiDataOverview | null }
  | { status: 'ready'; data: AiDataOverview }
  | { status: 'error'; data: AiDataOverview | null; error: UiMessage }

export interface PickItem extends AiDataProposeItem {
  /** 带项目标识的缩略图地址（`<img>` 发不了项目请求头） */
  thumb: string
}

/** 提交之后要让用户知道的几件事（不是失败：图已经画出来了） */
export interface DrawNotes {
  /** 没画出来的行（文件里的行号，最多 50 条） */
  unplotted: { count: number; rows: number[] } | null
  /** 没有「已核对」：`unverifiable` = 这类图没法逐项核对；`unknown` = 核对没能完成 / 没给结论。只有明确的 verified 才是 null——绝不把缺失写成已核对 */
  check: 'unverifiable' | 'unknown' | null
}

export type ProblemKind =
  /** 出备选这一步没成（数据读不了、网络……）：一句话 + 「再试一次」 */
  | { kind: 'propose'; error: UiMessage | null }
  /** 提交没成、图没画出来：一句话 + 「再试一次」 */
  | { kind: 'commit'; error: UiMessage | null; retryable: boolean }
  /** 画出来的数据与文件对不上：脚本已经落盘 */
  | { kind: 'mismatch'; script: string }
  /** 画是画了，但项目文件夹写不进（只读等）：说清并给能走的路 */
  | { kind: 'unwritable'; error: UiMessage }

export type Scene =
  | { kind: 'browse' }
  | { kind: 'proposing' }
  | { kind: 'ask'; code: string; options: AiDataOption[]; unsure: AiDataOption | null }
  | { kind: 'mixed'; columns: AiDataMixedColumn[] }
  | { kind: 'pick'; session: string; items: PickItem[]; selectedId: string; sourcePath: string; fromRetry: boolean }
  | { kind: 'nothing'; reasons: string[] }
  | { kind: 'drawing' }
  | { kind: 'drawn'; script: string; notes: DrawNotes }
  | { kind: 'problem'; problem: ProblemKind }

type View = 'none' | 'entering' | 'data'

interface DataFigureStore {
  view: View
  files: FilesState
  /** 用户点中的那一张表 */
  selected: TableRef | null
  /** 每张表（含重读后的）概览；键 = `tableKey` + 表头行 */
  overviews: Record<string, OverviewState>
  /** 用户指了「哪一行是每列的名字」的文件：路径 → 物理行号；出图时原样带上 `header_rows` */
  headerRows: Record<string, number>
  /** 累积的选择题回答 */
  answers: Record<string, unknown>
  scene: Scene

  /** 点「只有数据」之后、项目还没打开完：先把数据页的位置占住，不闪一帧画布 */
  beginEntering: () => void
  cancelEntering: () => void
  enter: () => void
  leave: () => void
  loadFiles: () => Promise<void>
  select: (ref: TableRef) => void
  setHeaderRow: (path: string, sheet: string | null, line: number) => void
  start: () => Promise<void>
  answer: (option: AiDataOption) => Promise<void>
  answerCells: (picks: { column: string; option: AiDataOption }[]) => Promise<void>
  choose: (id: string) => void
  draw: () => Promise<void>
  /** 回到浏览（放弃这一轮的备选） */
  back: () => void
  /** 出图后带着说明的那一屏：打开这张图 */
  openDrawn: () => Promise<void>
  retry: () => Promise<void>
  clear: () => void
}

let epoch = 0
let filesSeq = 0
let proposeSeq = 0
let commitSeq = 0
const overviewSeq = new Map<string, number>()
/** 同一个 (会话, 备选) 的提交请求号：一次点击一个，不确定的重试复用它（换号 = 后端当成新的一次、会建 `_2`） */
let commitRid: { for: string; rid: string } | null = null
let ridCounter = 0
const newRid = (): string => `df2-${Date.now().toString(36)}-${(ridCounter++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`

const overviewKey = (ref: TableRef, headerRow: number | null): string => `${tableKey(ref)}#${headerRow ?? ''}`

/** 这张表此刻（按用户指过的表头行）的概览在 `overviews` 里的键 */
export const overviewKeyOf = (ref: TableRef, headerRows: Record<string, number>): string => overviewKey(ref, headerRows[ref.path] ?? null)

function codeOf(e: unknown): string {
  return e instanceof ApiError && typeof e.body?.code === 'string' ? e.body.code : ''
}

const initial = {
  view: 'none' as View,
  files: { status: 'idle' } as FilesState,
  selected: null as TableRef | null,
  overviews: {} as Record<string, OverviewState>,
  headerRows: {} as Record<string, number>,
  answers: {} as Record<string, unknown>,
  scene: { kind: 'browse' } as Scene,
}

export const useDataFigureStore = create<DataFigureStore>((set, get) => {
  /** 发起时的快照；`still()` 为假 = 换过项目 / 被更新的同类请求取代，回来的结果作废 */
  const capture = (seq: { current: () => number }) => {
    const ep = epoch
    const mine = seq.current()
    const pj = currentProjectId()
    return { pj, still: () => ep === epoch && mine === seq.current() }
  }

  const toItems = (items: AiDataProposeItem[], pj: string | null, thumbOf: (u: string, pj: string | null) => string): PickItem[] =>
    items.map((it) => ({ ...it, thumb: thumbOf(it.thumbnail_url, pj) }))

  /** 出备选（`answers` 此刻的累积 + 选中的表 + 用户指过的表头行）。返回备选里推荐的那一个的 `title_code`（给静默重试对号用） */
  async function propose(opts: { fromRetry?: boolean } = {}): Promise<void> {
    const sel = get().selected
    if (!sel) return
    const seq = { current: () => proposeSeq }
    proposeSeq += 1
    const { pj, still } = capture(seq)
    set({ scene: { kind: 'proposing' } })
    const { aiDataThumbnailUrl } = await import('@/lib/api')
    const answers = get().answers
    const hasHeaders = Object.keys(get().headerRows).length > 0
    try {
      const res = await aiDataFigurePropose(
        {
          ...('table' in answers ? {} : { table: sel.path }),
          ...('sheet' in answers || sel.sheet == null ? {} : { sheet: sel.sheet }),
          ...(Object.keys(answers).length ? { answers } : {}),
          ...(hasHeaders ? { header_rows: get().headerRows } : {}),
        },
        pj,
      )
      if (!still()) return
      if (res.kind === 'ask') {
        if (res.question_code === 'mixed_cells' && Array.isArray(res.columns) && res.columns.length > 0) {
          set({ scene: { kind: 'mixed', columns: res.columns } })
        } else {
          set({
            scene: { kind: 'ask', code: res.question_code, options: res.options ?? [], unsure: res.unsure ?? null },
          })
        }
      } else if (res.kind === 'candidates' && Array.isArray(res.items) && res.items.length > 0) {
        const items = toItems(res.items, pj, aiDataThumbnailUrl)
        const first = items.find((i) => i.recommended) ?? items[0]!
        set({
          scene: {
            kind: 'pick',
            session: res.session,
            items,
            selectedId: first.id,
            sourcePath: sel.path,
            fromRetry: opts.fromRetry === true,
          },
        })
      } else if (res.kind === 'nothing') {
        const reasons = [...(res.reasons ?? [])]
        set({ scene: { kind: 'nothing', reasons } })
      } else {
        // 形状不对按失败处理（不当成拿到了空的）
        set({ scene: { kind: 'problem', problem: { kind: 'propose', error: null } } })
      }
    } catch (e) {
      if (!still()) return
      set({ scene: { kind: 'problem', problem: { kind: 'propose', error: backendErrorMsg(e) } } })
    }
  }

  async function loadOverview(ref: TableRef): Promise<void> {
    const header = get().headerRows[ref.path] ?? null
    const key = overviewKey(ref, header)
    const prev = get().overviews[key]
    if (prev && prev.status !== 'error') return
    const mine = (overviewSeq.get(key) ?? 0) + 1
    overviewSeq.set(key, mine)
    const ep = epoch
    const pj = currentProjectId()
    set((s) => ({ overviews: { ...s.overviews, [key]: { status: 'loading', data: prev?.data ?? null } } }))
    try {
      const data = await aiDataOverview({ path: ref.path, sheet: ref.sheet, headerRow: header }, pj)
      if (ep !== epoch || overviewSeq.get(key) !== mine) return
      set((s) => ({ overviews: { ...s.overviews, [key]: { status: 'ready', data } } }))
    } catch (e) {
      if (ep !== epoch || overviewSeq.get(key) !== mine) return
      set((s) => ({ overviews: { ...s.overviews, [key]: { status: 'error', data: prev?.data ?? null, error: backendErrorMsg(e) } } }))
    }
  }

  const notesOf = (res: AiDataCommitResult): DrawNotes => {
    const un = res.unplotted
    const check = res.data_check
    return {
      unplotted: un && un.count > 0 ? { count: un.count, rows: [...new Set((un.rows ?? []).map((r) => r.row))] } : null,
      // 只有明确说「核对过」才不提；缺失 / unknown / unverifiable 都不能写成已核对
      check: check === 'verified' ? null : check === 'unverifiable' ? 'unverifiable' : 'unknown',
    }
  }

  /** 进编辑器：就是「打开并运行」一个脚本的那条现有路径（准备会话检查 → 运行 → 把捕获的图放进画布并进单图快速编辑） */
  async function goEditor(script: string): Promise<void> {
    const ep = epoch
    const { refreshProjectNow } = await import('@/store/liveSync')
    await refreshProjectNow().catch(() => undefined)
    if (ep !== epoch) return
    const { useProjectPreparationStore, scriptTarget } = await import('@/store/projectPreparationStore')
    // 先收起数据页再开：准备卡与落图的提示都出在编辑器那一边
    set({ view: 'none', scene: { kind: 'browse' } })
    await useProjectPreparationStore.getState().open(scriptTarget(script, { reuse: true }), { run: true }).catch(() => undefined)
  }

  async function commit(sessionId: string, items: PickItem[], itemId: string, retried: boolean): Promise<void> {
    const seq = { current: () => commitSeq }
    commitSeq += 1
    const { pj, still } = capture(seq)
    const forKey = `${sessionId}/${itemId}`
    if (!commitRid || commitRid.for !== forKey) commitRid = { for: forKey, rid: newRid() }
    const rid = commitRid.rid
    set({ scene: { kind: 'drawing' } })
    try {
      const res = await aiDataFigureCommit({ session: sessionId, id: itemId, client_request_id: rid }, pj)
      if (!still()) return
      commitRid = null
      const outcome = res.task?.outcome
      if (!res.script || !res.stem) {
        // 没落盘：任务没成（渲染没过 / 核对不过 / 写不进）。写不进是后端早就告诉过的事实（项目 `writable` 为假、或 `figures_dir_unusable`）：
        // 这时「再试一次」没有用，给的是换个文件夹；其余一句话 + 再试一次
        const { useProjectStore } = await import('@/store/projectStore')
        if (!still()) return
        const unwritable = res.reason_code === 'figures_dir_unusable' || useProjectStore.getState().project?.writable === false
        set({
          scene: {
            kind: 'problem',
            problem: unwritable
              ? { kind: 'unwritable', error: { key: 'dataFigureV2.problem.unwritable', ns: 'ai' } }
              : { kind: 'commit', error: null, retryable: true },
          },
        })
        return
      }
      if (res.data_check === 'mismatch' || outcome === 'failed') {
        set({ scene: { kind: 'problem', problem: { kind: 'mismatch', script: res.script } } })
        return
      }
      const notes = notesOf(res)
      if (notes.unplotted || notes.check) {
        set({ scene: { kind: 'drawn', script: res.script, notes } })
        // 图已经在项目里：先把项目刷新到位，用户点「打开」时就是现成的
        void import('@/store/liveSync').then((m) => m.refreshProjectNow()).catch(() => undefined)
        return
      }
      await goEditor(res.script)
    } catch (e) {
      if (!still()) return
      const code = codeOf(e)
      if (isStaleProposeCode(code) && !retried) {
        // 备选过期 / 数据变了：不是用户的错，静默重新出备选；原来选的画法还在就接着画，没有就让用户看新的
        const wanted = items.find((i) => i.id === itemId)
        commitRid = null
        await propose({ fromRetry: true })
        if (!still()) return
        const sc = get().scene
        if (sc.kind === 'pick' && wanted) {
          const same = sc.items.find((i) => i.title_code === wanted.title_code)
          if (same) {
            set({ scene: { ...sc, selectedId: same.id } })
            await commit(sc.session, sc.items, same.id, true)
          }
        }
        return
      }
      if (e instanceof ApiError && (code === 'figures_dir_unusable' || code === 'common_module_conflict')) {
        commitRid = null
        set({ scene: { kind: 'problem', problem: { kind: 'unwritable', error: backendErrorMsg(e) } } })
        return
      }
      // 后端明确拒绝了：这次请求号作废；没收到结果（断网 / 网关）：保留请求号，「再试一次」复用，不会画出第二份
      const definite = isDefiniteRunFailure(e)
      if (definite) commitRid = null
      set({ scene: { kind: 'problem', problem: { kind: 'commit', error: backendErrorMsg(e), retryable: true } } })
    }
  }

  return {
    ...initial,

    beginEntering: () => set({ view: 'entering' }),
    cancelEntering: () => set((s) => (s.view === 'entering' ? { view: 'none' } : s)),
    enter: () => {
      set({ ...initial, view: 'data' })
      void get().loadFiles()
    },
    leave: () => {
      const sc = get().scene
      if (sc.kind === 'pick') void aiDataFigureDiscard(sc.session, currentProjectId()).catch(() => undefined)
      epoch += 1
      set({ ...initial })
    },

    loadFiles: async () => {
      const mine = ++filesSeq
      const ep = epoch
      const pj = currentProjectId()
      const prev = get().files
      const keep = prev.status === 'ready' || prev.status === 'error' || prev.status === 'loading' ? prev.entries : null
      set({ files: { status: 'loading', entries: keep } })
      try {
        const r = await aiDataFilesAll(pj)
        if (ep !== epoch || mine !== filesSeq) return
        if (!r || !Array.isArray(r.files)) throw new Error('bad shape')
        set({ files: { status: 'ready', entries: r.files, truncated: r.truncated === true } })
      } catch (e) {
        if (ep !== epoch || mine !== filesSeq) return
        set({ files: { status: 'error', entries: keep, error: backendErrorMsg(e) } })
      }
    },

    select: (ref) => {
      const sc = get().scene
      if (sc.kind === 'drawing') return // 正在画：画完再换，别让在飞的提交没人认领
      if (sc.kind === 'pick') void aiDataFigureDiscard(sc.session, currentProjectId()).catch(() => undefined)
      proposeSeq += 1 // 还在出备选的那一轮作废
      commitRid = null
      set({ selected: ref, scene: { kind: 'browse' }, answers: {} })
      void loadOverview(ref)
    },

    setHeaderRow: (path, sheet, line) => {
      const sc = get().scene
      if (sc.kind === 'drawing') return
      if (sc.kind === 'pick') void aiDataFigureDiscard(sc.session, currentProjectId()).catch(() => undefined)
      proposeSeq += 1
      commitRid = null
      set((s) => ({ headerRows: { ...s.headerRows, [path]: line }, answers: {}, scene: { kind: 'browse' } }))
      void loadOverview({ path, sheet })
    },

    start: async () => {
      set({ answers: {} })
      await propose()
    },

    answer: async (option) => {
      set((s) => ({ answers: mergeAnswers(s.answers, option.answer) }))
      await propose()
    },

    answerCells: async (picks) => {
      set((s) => ({ answers: picks.reduce((acc, p) => mergeAnswers(acc, p.option.answer), s.answers) }))
      await propose()
    },

    choose: (id) => {
      const sc = get().scene
      if (sc.kind === 'pick' && sc.items.some((i) => i.id === id)) set({ scene: { ...sc, selectedId: id } })
    },

    draw: async () => {
      const sc = get().scene
      if (sc.kind !== 'pick') return // 不是备选屏（含正在画）：不重复提交
      await commit(sc.session, sc.items, sc.selectedId, false)
    },

    back: () => {
      const sc = get().scene
      if (sc.kind === 'pick') void aiDataFigureDiscard(sc.session, currentProjectId()).catch(() => undefined)
      commitRid = null
      proposeSeq += 1
      commitSeq += 1
      set({ scene: { kind: 'browse' }, answers: {} })
    },

    openDrawn: async () => {
      const sc = get().scene
      if (sc.kind !== 'drawn') return
      await goEditor(sc.script)
    },

    retry: async () => {
      const sc = get().scene
      if (sc.kind !== 'problem') return
      const p = sc.problem
      if (p.kind === 'commit') {
        // 同一次点击的重试：请求号还在就复用；没有备选可提交（会话没了）就重新出备选
        const last = commitRid
        if (last) {
          const [session, id] = last.for.split('/')
          await commit(session!, [], id!, false)
          return
        }
      }
      await propose()
    },

    clear: () => {
      epoch += 1
      commitRid = null
      overviewSeq.clear()
      // 「正在进数据页」是跨项目换代的那一刻发出的，不属于被换掉的项目：留着，等打开完成后 `enter()`
      set((s) => ({ ...initial, view: s.view === 'entering' ? 'entering' : 'none' }))
    },
  }
})

