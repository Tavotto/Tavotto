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
  type AiDataOverview,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { candidatesFromResponse, type Candidate } from '@/lib/dataFigureAdapter'
import { cellModeOf, fileRows, hasNumericColumn, isStaleProposeCode, mergeAnswers, sameTable, tableKey, usableOptions, type CellMode, type TableRef } from '@/lib/dataFigure'
import type { UiMessage } from '@/i18n'

/**
 * 「从数据到图」的页面状态（ADR 0178，实验开关 `ai.taf.enabled` 后面）。
 *
 * 结构：左栏「数据」抽屉里点一个文件 = 在画布标签栏开一个**临时标签**（`draftOpen`，激活时 `draftActive`）。它不是画布：
 * 不在 `documentStore.openTabs` 里、不进项目文件、不进导出、撤销栈不记——整个状态就在这个 store 里，关掉即没。
 * 点文件那一刻就读表并出候选（没有「开始」按钮）；后端要问的选择题一律先用默认答案（「不确定」/ 推荐列、非数字格不画、猜到的表头行），
 * 不再打断。点一张候选 = 出图，出完临时标签原地变成正式的图（走现有「打开并运行」），再给一枚「换个画法」的胶囊。
 *
 * 规矩同其它会在项目之间存活的 store：
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
  | { status: 'error'; entries: AiDataFileEntry[] | null; error: UiMessage | null }

export type OverviewState =
  | { status: 'loading'; data: AiDataOverview | null }
  | { status: 'ready'; data: AiDataOverview }
  | { status: 'error'; data: AiDataOverview | null; error: UiMessage | null }

export type { Candidate }

/** 提交之后要让用户知道的几件事（不是失败：图已经画出来了） */
export interface DrawNotes {
  /** 没画出来的行（文件里的行号，最多 50 条） */
  unplotted: { count: number; rows: number[] } | null
  /** 没有「已核对」：`unverifiable` = 这类图没法逐项核对；`unknown` = 核对没能完成 / 没给结论。只有明确的 verified 才是 null——绝不把缺失写成已核对 */
  check: 'unverifiable' | 'unknown' | null
}

export type ProblemKind =
  /** 提交没成、图没画出来：一句话 + 「再试一次」 */
  | { kind: 'commit'; error: UiMessage | null; retryable: boolean }
  /** 画出来的数据与文件对不上：脚本已经落盘 */
  | { kind: 'mismatch'; script: string }
  /** 画是画了，但项目文件夹写不进（只读等）：说清并给能走的路 */
  | { kind: 'unwritable'; error: UiMessage }

/**
 * 临时标签里这份数据此刻在哪一步：
 *  - `reading` 读表中；`unreadable` 不是表格；`text` 全是文字、没有能画的数字；`pickrow` 表头行没认出来、要用户点一行
 *  - `loading` 候选还在出；`ready` 候选好了；`nothing` 后端说这张表画不出；`failed` 出候选这一步没成
 */
export type DraftPhase = 'idle' | 'reading' | 'unreadable' | 'text' | 'pickrow' | 'loading' | 'ready' | 'nothing' | 'failed'

/** 这次画图用到的、数值列里混着文字的列：默认「不画这几行」，右栏可改成「只用里面的数字」 */
export interface MixedInfo {
  columns: AiDataMixedColumn[]
  mode: CellMode
}

/** 刚画出来的那张图：画布上的「换个画法」胶囊认它（只在这一次会话里，不持久化） */
export interface FreshFigure {
  file: TableRef
  /** 打开新图那一刻激活的画布（新图还没放上去时它还是旧的那张） */
  from: string | null
  /** 新图所在的画布；新图放上去、激活的画布变了之后才认下（`adoptFresh`），胶囊只在这张画布上出现；还没认下是 null */
  canvasId: string | null
}

/**
 * 这个 store 要借用的「项目 / 编辑器」那一头的几件事。**不在这里 import 它们**：`projectStore` 在换项目时要调本 store 的 `clear()`，
 * 本 store 再反过来 import `liveSync` / `projectStore` 就是一个环（`importArchitecture` 门禁）。由 `lib/dataFigureHost.ts` 在数据页挂上时注册
 * （和 `prepEnterEdit` 的持有者同一个办法）。没注册 = 什么都不做、项目可写性未知。
 */
export interface DataFigureHost {
  /** 打开左栏「数据」抽屉（首页左卡进项目后用） */
  openDrawer: () => void
  /** 把项目刷新到新脚本已经在的状态，然后用「打开并运行」那条现有路径进编辑器（准备会话检查 → 运行 → 放进画布进单图快速编辑） */
  openScript: (script: string) => Promise<void>
  /** 项目文件夹可不可写（后端给的 `writable`）；不知道回 undefined */
  projectWritable: () => boolean | undefined
  /** 此刻激活的画布 id（打开新图之后记下来，给「换个画法」胶囊认） */
  activeCanvasId: () => string | null
}
let host: DataFigureHost = { openDrawer: () => {}, openScript: async () => {}, projectWritable: () => undefined, activeCanvasId: () => null }
export const configureDataFigureHost = (h: DataFigureHost): void => {
  host = h
}

interface DataFigureStore {
  /** 画布标签栏里有这个临时标签 */
  draftOpen: boolean
  /** 临时标签此刻是激活的（画布内容区显示的是这份数据，不是画布） */
  draftActive: boolean
  files: FilesState
  /** 用户点中的那一张表 */
  selected: TableRef | null
  /** 每张表（含重读后的）概览；键 = `tableKey` + 表头行 */
  overviews: Record<string, OverviewState>
  /** 用户指了「哪一行是每列的名字」的文件：路径 → 物理行号；出图时原样带上 `header_rows` */
  headerRows: Record<string, number>
  /** 累积的回答（后端的选择题都由默认答案自动答，这里只存它们） */
  answers: Record<string, unknown>
  /** 有非数字格时的处理方式（右栏的下拉） */
  mixed: MixedInfo | null
  phase: DraftPhase
  /** 候选（同一个会话的一组） */
  session: string | null
  candidates: Candidate[]
  /** 后端说画不出的原因码 */
  nothingReasons: string[]
  /** 出候选失败时后端说过的话（没有就只有标题那一句） */
  error: UiMessage | null
  /** 正在出图的那一张候选 */
  drawingId: string | null
  /** 出图没成 / 要说话的结局（出图成功且没有要说的就直接进编辑器） */
  problem: ProblemKind | null
  drawn: { script: string; notes: DrawNotes } | null
  fresh: FreshFigure | null

  /** 首页左卡进项目之后：开「数据」抽屉；只有一个可读文件就直接开它的临时标签 */
  enter: () => void
  loadFiles: () => Promise<void>
  /** 点一个数据文件：开（或复用）临时标签，读表并出候选 */
  select: (ref: TableRef) => void
  /** 关临时标签（出图中只是收起，画完照样打开） */
  closeDraft: () => void
  activateDraft: () => void
  /** 切到真画布标签：临时标签留在标签栏里，只是不再激活 */
  deactivateDraft: () => void
  setHeaderRow: (path: string, sheet: string | null, line: number) => void
  setMixedMode: (mode: CellMode) => void
  draw: (id: string) => Promise<void>
  /** 出图中 / 出候选失败之后「再试一次」 */
  retry: () => Promise<void>
  /** 带着说明的那一步：打开这张图 */
  openDrawn: () => Promise<void>
  /** 出图失败之后回到候选 */
  dismissProblem: () => void
  /** 画布上的「换个画法」：回到这份数据的临时标签 */
  redraw: () => void
  /** 新图放上去、激活的画布换成了新的那张：认下它（给「换个画法」胶囊） */
  adoptFresh: (canvasId: string) => void
  clear: () => void
}

let epoch = 0
let filesSeq = 0
let proposeSeq = 0
let runSeq = 0
let commitSeq = 0
const overviewSeq = new Map<string, number>()
/** 同一个 (会话, 备选) 的提交请求号：一次点击一个，不确定的重试复用它（换号 = 后端当成新的一次、会建 `_2`） */
let commitRid: { for: string; rid: string } | null = null
let ridCounter = 0
const newRid = (): string => `df2-${Date.now().toString(36)}-${(ridCounter++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`

const overviewKey = (ref: TableRef, headerRow: number | null): string => `${tableKey(ref)}#${headerRow ?? ''}`

/** 这张表此刻（按用户指过的表头行）的概览在 `overviews` 里的键 */
export const overviewKeyOf = (ref: TableRef, headerRows: Record<string, number>): string => overviewKey(ref, headerRows[ref.path] ?? null)

/**
 * 失败的原因只说后端明确说过的（`ApiError` 带稳定码 / 后端那句话）；网络断了、浏览器自己的 `Failed to fetch` 之类的原文不显示——
 * 不猜原因、也不把英文报错摆给用户（错题本 L-143）：没有原因就只有标题那一句。
 */
const reasonOf = (e: unknown): UiMessage | null => (e instanceof ApiError ? backendErrorMsg(e) : null)

function codeOf(e: unknown): string {
  return e instanceof ApiError && typeof e.body?.code === 'string' ? e.body.code : ''
}


const initial = {
  draftOpen: false,
  draftActive: false,
  files: { status: 'idle' } as FilesState,
  selected: null as TableRef | null,
  overviews: {} as Record<string, OverviewState>,
  headerRows: {} as Record<string, number>,
  answers: {} as Record<string, unknown>,
  mixed: null as MixedInfo | null,
  phase: 'idle' as DraftPhase,
  session: null as string | null,
  candidates: [] as Candidate[],
  nothingReasons: [] as string[],
  error: null as UiMessage | null,
  drawingId: null as string | null,
  problem: null as ProblemKind | null,
  drawn: null as { script: string; notes: DrawNotes } | null,
  fresh: null as FreshFigure | null,
}
/** 换一份数据 / 回到起点时清掉的那些（文件清单、概览、表头行、画好的那张不在内） */
const idleDraft = {
  answers: {} as Record<string, unknown>,
  mixed: null as MixedInfo | null,
  phase: 'idle' as DraftPhase,
  session: null as string | null,
  candidates: [] as Candidate[],
  nothingReasons: [] as string[],
  error: null as UiMessage | null,
  drawingId: null as string | null,
  problem: null as ProblemKind | null,
  drawn: null as { script: string; notes: DrawNotes } | null,
}
/** 选择题的默认答案轮数上限：后端不会连着问这么多道，防的是死循环 */
const MAX_ASKS = 8

export const useDataFigureStore = create<DataFigureStore>((set, get) => {
  /** 发起时的快照；`still()` 为假 = 换过项目 / 被更新的同类请求取代，回来的结果作废 */
  const capture = (seq: { current: () => number }) => {
    const ep = epoch
    const mine = seq.current()
    const pj = currentProjectId()
    return { pj, still: () => ep === epoch && mine === seq.current() }
  }

  const discardSession = () => {
    const sid = get().session
    if (sid) void aiDataFigureDiscard(sid, currentProjectId()).catch(() => undefined)
  }

  /** 这一列 / 这几列混着文字时的回答：每列按 `mode` 取对应的选项（没有就不答，后端会再问） */
  const mixedAnswers = (cols: AiDataMixedColumn[], mode: CellMode): Record<string, unknown> =>
    cols.reduce<Record<string, unknown>>((acc, c) => {
      const o = c.options.find((x) => !x.free_text && cellModeOf(x) === mode)
      return o ? mergeAnswers(acc, o.answer) : acc
    }, {})

  /** 出候选（`answers` 此刻的累积 + 选中的表 + 用户指过的表头行）。后端的选择题一律用默认答案自动答，不打断 */
  async function propose(still: () => boolean, pj: string | null): Promise<void> {
    const sel = get().selected
    if (!sel) return
    set({ phase: 'loading', error: null })
    for (let asks = 0; asks < MAX_ASKS; asks++) {
      const answers = get().answers
      const hasHeaders = Object.keys(get().headerRows).length > 0
      let res
      try {
        res = await aiDataFigurePropose(
          {
            ...('table' in answers ? {} : { table: sel.path }),
            ...('sheet' in answers || sel.sheet == null ? {} : { sheet: sel.sheet }),
            ...(Object.keys(answers).length ? { answers } : {}),
            ...(hasHeaders ? { header_rows: get().headerRows } : {}),
          },
          pj,
        )
      } catch (e) {
        if (!still()) return
        set({ phase: 'failed', error: reasonOf(e) })
        return
      }
      if (!still()) return
      if (res.kind === 'ask') {
        if (res.question_code === 'mixed_cells' && Array.isArray(res.columns) && res.columns.length > 0) {
          const mode = get().mixed?.mode ?? 'skip'
          set((s) => ({ mixed: { columns: res.columns!, mode }, answers: { ...s.answers, ...mergeAnswers(s.answers, mixedAnswers(res.columns!, mode)) } }))
          continue
        }
        // 「不确定」永远有安全的默认；没有就取后端排第一的选项
        const pick = res.unsure ?? usableOptions(res.options)[0]
        if (!pick) {
          set({ phase: 'failed', error: null })
          return
        }
        set((s) => ({ answers: mergeAnswers(s.answers, pick.answer) }))
        continue
      }
      if (res.kind === 'candidates' && Array.isArray(res.items) && res.items.length > 0) {
        set({ phase: 'ready', session: res.session, candidates: candidatesFromResponse(res.items, pj), nothingReasons: [] })
      } else if (res.kind === 'nothing') {
        set({ phase: 'nothing', nothingReasons: [...(res.reasons ?? [])], candidates: [], session: null })
      } else {
        // 形状不对按失败处理（不当成拿到了空的）
        set({ phase: 'failed', error: null })
      }
      return
    }
    set({ phase: 'failed', error: null })
  }

  async function loadOverview(ref: TableRef): Promise<AiDataOverview | null> {
    const header = get().headerRows[ref.path] ?? null
    const key = overviewKey(ref, header)
    const prev = get().overviews[key]
    if (prev && prev.status === 'ready') return prev.data
    const mine = (overviewSeq.get(key) ?? 0) + 1
    overviewSeq.set(key, mine)
    const ep = epoch
    const pj = currentProjectId()
    set((s) => ({ overviews: { ...s.overviews, [key]: { status: 'loading', data: prev?.data ?? null } } }))
    try {
      const data = await aiDataOverview({ path: ref.path, sheet: ref.sheet, headerRow: header }, pj)
      if (ep !== epoch || overviewSeq.get(key) !== mine) return null
      set((s) => ({ overviews: { ...s.overviews, [key]: { status: 'ready', data } } }))
      return data
    } catch (e) {
      if (ep !== epoch || overviewSeq.get(key) !== mine) return null
      set((s) => ({ overviews: { ...s.overviews, [key]: { status: 'error', data: prev?.data ?? null, error: reasonOf(e) } } }))
      return null
    }
  }

  /** 点文件 / 改表头行之后：读表 → 判断（读不了 / 全是文字 / 要点表头行）→ 出候选 */
  async function run(): Promise<void> {
    const sel = get().selected
    if (!sel) return
    const seq = { current: () => runSeq }
    runSeq += 1
    proposeSeq += 1
    const { pj, still } = capture(seq)
    set({ ...idleDraft, answers: {}, phase: 'reading' })
    // 清单里已经说「读不了」（没有可看的原文）：不发请求
    const entries = get().files.status === 'idle' ? null : (get().files as { entries: AiDataFileEntry[] | null }).entries
    const row = entries ? fileRows(entries).flatMap((r) => r.rows).find((r) => sameTable(r.ref, sel)) : undefined
    if (row?.status.kind === 'unreadable' && get().headerRows[sel.path] == null) {
      set({ phase: 'unreadable' })
      return
    }
    const ov = await loadOverview(sel)
    if (!still()) return
    if (!ov) {
      set({ phase: 'failed', error: null })
      return
    }
    if (ov.status === 'unreadable') {
      set({ phase: (ov.raw_lines?.length ?? 0) > 0 ? 'pickrow' : 'unreadable' })
      return
    }
    if (ov.status === 'ok' && !hasNumericColumn(ov)) {
      set({ phase: 'text' })
      return
    }
    await propose(still, pj)
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

  /** 进编辑器（宿主那一头做刷新与「打开并运行」）：临时标签先收掉，新图在真画布里打开，再记下这张图好给「换个画法」认 */
  async function goEditor(script: string): Promise<void> {
    const ep = epoch
    const file = get().selected
    set({ draftOpen: false, draftActive: false, drawingId: null, problem: null, drawn: null, phase: 'idle', session: null, candidates: [] })
    if (ep !== epoch) return
    await host.openScript(script).catch(() => undefined)
    if (ep !== epoch) return
    if (file) set({ fresh: { file, from: host.activeCanvasId(), canvasId: null } })
  }

  /** 没成 / 要说话的结局：把临时标签翻出来（出图期间用户可能已经把它收起了） */
  const showOutcome = (patch: Partial<typeof initial>) => set({ draftOpen: true, draftActive: true, drawingId: null, ...patch })

  async function commit(sessionId: string, items: Candidate[], itemId: string, retried: boolean): Promise<void> {
    const seq = { current: () => commitSeq }
    commitSeq += 1
    const { pj, still } = capture(seq)
    const forKey = `${sessionId}/${itemId}`
    if (!commitRid || commitRid.for !== forKey) commitRid = { for: forKey, rid: newRid() }
    const rid = commitRid.rid
    set({ drawingId: itemId, problem: null, drawn: null })
    try {
      const res = await aiDataFigureCommit({ session: sessionId, id: itemId, client_request_id: rid }, pj)
      if (!still()) return
      commitRid = null
      const outcome = res.task?.outcome
      if (!res.script || !res.stem) {
        // 没落盘：任务没成（渲染没过 / 核对不过 / 写不进）。写不进是后端早就告诉过的事实（项目 `writable` 为假、或 `figures_dir_unusable`）：
        // 这时「再试一次」没有用，给的是换个文件夹；其余一句话 + 再试一次
        const unwritable = res.reason_code === 'figures_dir_unusable' || host.projectWritable() === false
        showOutcome({
          problem: unwritable ? { kind: 'unwritable', error: { key: 'dataFigureV2.problem.unwritable', ns: 'ai' } } : { kind: 'commit', error: null, retryable: true },
        })
        return
      }
      if (res.data_check === 'mismatch' || outcome === 'failed') {
        showOutcome({ problem: { kind: 'mismatch', script: res.script } })
        return
      }
      const notes = notesOf(res)
      if (notes.unplotted || notes.check) {
        showOutcome({ drawn: { script: res.script, notes } })
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
        set({ drawingId: null })
        const rs = { current: () => runSeq }
        runSeq += 1
        const cap = capture(rs)
        await propose(cap.still, cap.pj)
        if (!cap.still()) return
        const sid = get().session
        const same = get().candidates.find((i) => wanted && i.title_code === wanted.title_code)
        if (sid && same) await commit(sid, get().candidates, same.id, true)
        return
      }
      if (e instanceof ApiError && (code === 'figures_dir_unusable' || code === 'common_module_conflict')) {
        commitRid = null
        showOutcome({ problem: { kind: 'unwritable', error: reasonOf(e) ?? { key: 'dataFigureV2.problem.unwritable', ns: 'ai' } } })
        return
      }
      // 后端明确拒绝了：这次请求号作废；没收到结果（断网 / 网关）：保留请求号，「再试一次」复用，不会画出第二份
      if (isDefiniteRunFailure(e)) commitRid = null
      showOutcome({ problem: { kind: 'commit', error: reasonOf(e), retryable: true } })
    }
  }

  return {
    ...initial,

    enter: () => {
      set({ ...initial })
      host.openDrawer()
      void get()
        .loadFiles()
        .then(() => {
          const f = get().files
          if (f.status !== 'ready') return
          const tables = fileRows(f.entries).flatMap((r) => r.rows).filter((r) => r.status.kind === 'table')
          if (tables.length === 1) get().select(tables[0]!.ref)
        })
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
        set({ files: { status: 'error', entries: keep, error: reasonOf(e) } })
      }
    },

    select: (ref) => {
      // 出图中：画完再换，别让在飞的提交没人认领；标签被收起了就翻出来给他看进度
      if (get().drawingId) {
        set({ draftOpen: true, draftActive: true })
        return
      }
      if (sameTable(get().selected, ref) && get().draftOpen) {
        set({ draftActive: true })
        return
      }
      discardSession()
      commitRid = null
      set({ selected: ref, draftOpen: true, draftActive: true, fresh: null })
      void run()
    },

    closeDraft: () => {
      // 正在画：只收起标签，不取消；画完照样打开（成功进编辑器，要说话的结局把标签再翻出来）
      if (get().drawingId) {
        set({ draftOpen: false, draftActive: false })
        return
      }
      discardSession()
      proposeSeq += 1
      runSeq += 1
      commitRid = null
      set({ draftOpen: false, draftActive: false, selected: null, ...idleDraft })
    },

    activateDraft: () => {
      if (get().draftOpen) set({ draftActive: true })
    },
    deactivateDraft: () => {
      if (get().draftActive) set({ draftActive: false })
    },

    setHeaderRow: (path, _sheet, line) => {
      if (get().drawingId) return
      discardSession()
      commitRid = null
      set((s) => ({ headerRows: { ...s.headerRows, [path]: line } }))
      void run()
    },

    setMixedMode: (mode) => {
      const m = get().mixed
      if (!m || m.mode === mode || get().drawingId) return
      discardSession()
      commitRid = null
      const seq = { current: () => runSeq }
      runSeq += 1
      const { pj, still } = capture(seq)
      set((s) => ({ mixed: { ...m, mode }, answers: mergeAnswers(s.answers, mixedAnswers(m.columns, mode)), session: null, candidates: [] }))
      void propose(still, pj)
    },

    draw: async (id) => {
      const sid = get().session
      if (!sid || get().drawingId || get().phase !== 'ready') return // 没有候选 / 正在画：不重复提交
      await commit(sid, get().candidates, id, false)
    },

    retry: async () => {
      const p = get().problem
      if (p?.kind === 'commit') {
        // 同一次点击的重试：请求号还在就复用；没有候选可提交（会话没了）就重新出候选
        const last = commitRid
        if (last) {
          const [session, id] = last.for.split('/')
          await commit(session!, [], id!, false)
          return
        }
      }
      set({ problem: null })
      await run()
    },

    dismissProblem: () => set({ problem: null, drawn: null }),

    openDrawn: async () => {
      const d = get().drawn
      if (d) await goEditor(d.script)
    },

    redraw: () => {
      const f = get().fresh
      set({ fresh: null })
      if (f) get().select(f.file)
    },

    adoptFresh: (canvasId) => {
      const f = get().fresh
      if (f && f.canvasId == null && canvasId !== f.from) set({ fresh: { ...f, canvasId } })
    },

    clear: () => {
      epoch += 1
      commitRid = null
      overviewSeq.clear()
      set({ ...initial })
    },
  }
})
