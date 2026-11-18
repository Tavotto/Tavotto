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
  type AiDataOverview,
} from '@/lib/api'
import { setCanvasCovered } from '@/store/canvasCover'
import { runExclusive } from '@/lib/dataCommitQueue'
import { isCommitInProgress, pollCommit } from '@/lib/dataCommitPoll'
import { currentProjectId } from '@/lib/session'
import { candidatesFromResponse, headerRowFromResponse, mixedFromResponse, rolesFromResponse, sourcesFromResponse, unitsRowFromResponse, type Candidate, type GroupSource, type ColumnRoles, type MixedColumn, type ThumbState } from '@/lib/dataFigureAdapter'
import { fileRows, hasNumericColumn, isStaleProposeCode, sameTable, tableKey, type CellMode, type TableRef } from '@/lib/dataFigure'
import { msg, type UiMessage } from '@/i18n'

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

/** 提交之后要让用户知道的一件事（不是失败：图已经画出来了）：核对没有「已核对」 */
export interface DrawNotes {
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
export type DraftPhase = 'idle' | 'reading' | 'unreadable' | 'text' | 'pickrow' | 'loading' | 'ready' | 'nothing' | 'failed' | 'columnsDiffer' | 'limitExceeded'

/** 这次画图用到的、数值列里混着文字的列（后端 `mixed_cells`）：默认「不画这几行」，右栏可改成「只用里面的数字」；多列时 `mode` 是它们共用的处理方式 */
export interface MixedInfo {
  columns: MixedColumn[]
  mode: CellMode
}

/** 右栏三个下拉的角色 */
export type RoleKey = 'x' | 'y' | 'group'

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
  openScript: (script: string, still: () => boolean) => Promise<void>
  /** 项目文件夹可不可写（后端给的 `writable`）；不知道回 undefined */
  projectWritable: () => boolean | undefined
  /** 此刻激活的画布 id（打开新图之后记下来，给「换个画法」胶囊认） */
  activeCanvasId: () => string | null
  /** 浮动通知（胶囊）里说一句话：没有按钮 */
  notify: (message: UiMessage) => void
  /** 「已在新画布里打开 · 撤销」那条说明不再有意义（用户回去换画法了）：清掉 */
  dismissAddedNote: () => void
}
let host: DataFigureHost = { openDrawer: () => {}, openScript: async (_script, _still) => {}, projectWritable: () => undefined, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} }
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
  /** 「合画一张」：几份表合进同一张图（`selected` 是其中第一份，数据条 / 概览认它）；单份流程是 null。只由 `selectGroup` 设，`select` / `closeDraft` / `clear` 复位 */
  group: TableRef[] | null
  /** 合画响应里每份文件的显示名与表头行 / 单位行（数据条的文件切换标签用）；单份是 null */
  sources: GroupSource[] | null
  /** 合并标签里的概览失效代数：磁盘事件（`invalidateFiles`）之后加一，数据条据此重读当前显示的文件 */
  overviewGen: number
  /** 每张表（含重读后的）概览；键 = `tableKey` + 表头行 */
  overviews: Record<string, OverviewState>
  /** 用户指了「哪一行是每列的名字」的文件：路径 → 物理行号；出图时原样带上 `header_rows` */
  headerRows: Record<string, number>
  /** 用户在右栏改过的东西（`y` / `x` / `group` 的列名或 `none`、`cells`），重新出候选时原样带上；后端不再提问，没改的用它自己的默认 */
  answers: Record<string, unknown>
  /** 有非数字格时的处理方式（右栏的下拉），来自响应的 `mixed_cells` */
  mixed: MixedInfo | null
  /** 这次用了哪几列、每个角色可换成哪些列（右栏的下拉），来自响应的 `roles`；重新出候选期间保持上一次的，下拉不卸载 */
  roles: ColumnRoles | null
  /** 这次实际用的表头物理行号（响应的 `header_row`；XLSX / 还没出 = null） */
  headerRowUsed: number | null
  /** 表头后紧跟的是单位行（`headerRowUsed + 1`），数据条里置灰 */
  unitsRow: boolean
  /** 每张候选缩略图各自的就绪状态；`failed` 的静默去掉 */
  thumbs: Record<string, ThumbState>
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
  /** 出图没成的结局（出图成功就直接进编辑器；没画出来的行 / 没法核对这类话走浮动通知，不留在临时标签里） */
  problem: ProblemKind | null
  fresh: FreshFigure | null

  /** 首页左卡进项目之后：开「数据」抽屉；只有一个可读文件就直接开它的临时标签 */
  enter: () => void
  loadFiles: () => Promise<void>
  /** 项目刷新 / 磁盘变化时：清单读过就重读（外部加了数据文件，入口要能出现）；没读过（开关关着）什么都不做 */
  invalidateFiles: () => void
  /** 点一个数据文件：开（或复用）临时标签，读表并出候选 */
  select: (ref: TableRef) => void
  /** 「合画一张」：开临时标签、把这几份（≥ 2）一起交给后端出候选 */
  selectGroup: (refs: TableRef[]) => void
  /** 合画数据条切到某个文件：这份的概览还没读（或表头行换了、键变了）才读，读过的不重读 */
  ensureOverview: (ref: TableRef) => void
  /** 关临时标签（出图中只是收起，画完照样打开） */
  closeDraft: () => void
  activateDraft: () => void
  /** 切到真画布标签：临时标签留在标签栏里，只是不再激活 */
  deactivateDraft: () => void
  setHeaderRow: (path: string, sheet: string | null, line: number) => void
  setMixedMode: (mode: CellMode) => void
  /** 右栏换了某个角色的列（`value` = 列名；`'none'` = 不要横轴 / 不分组）：带着它重新出候选 */
  setRole: (key: RoleKey, value: string) => void
  /** 一张缩略图取到了 / 取不到（422）：就绪状态 + 全部都画不出来时走「没画出来」 */
  thumbSettled: (session: string, id: string, ok: boolean) => void
  draw: (id: string) => Promise<void>
  /** 出图中 / 出候选失败之后「再试一次」 */
  retry: () => Promise<void>
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
/** 清单在途时又来了磁盘变化：只记一个标记，在途那次结束后恰好补一次尾随重读（保证最后一个事件之后的磁盘状态一定被读到） */
let filesDirty = false
let proposeSeq = 0
let runSeq = 0
let commitSeq = 0
const overviewSeq = new Map<string, number>()
/** 当前合并标签里「本标签内已读过 / 在读」的概览键：新开标签清空，磁盘事件清空；`ensureOverview` 不重复读 */
const groupFresh = new Set<string>()
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
  group: null as TableRef[] | null,
  overviewGen: 0,
  sources: null as GroupSource[] | null,
  overviews: {} as Record<string, OverviewState>,
  headerRows: {} as Record<string, number>,
  answers: {} as Record<string, unknown>,
  mixed: null as MixedInfo | null,
  roles: null as ColumnRoles | null,
  headerRowUsed: null as number | null,
  unitsRow: false,
  thumbs: {} as Record<string, ThumbState>,
  phase: 'idle' as DraftPhase,
  session: null as string | null,
  candidates: [] as Candidate[],
  nothingReasons: [] as string[],
  error: null as UiMessage | null,
  drawingId: null as string | null,
  problem: null as ProblemKind | null,
  fresh: null as FreshFigure | null,
}
/** 换一份数据 / 回到起点时清掉的那些（文件清单、概览、表头行、画好的那张不在内） */
const idleDraft = {
  answers: {} as Record<string, unknown>,
  mixed: null as MixedInfo | null,
  roles: null as ColumnRoles | null,
  headerRowUsed: null as number | null,
  unitsRow: false,
  thumbs: {} as Record<string, ThumbState>,
  phase: 'idle' as DraftPhase,
  session: null as string | null,
  candidates: [] as Candidate[],
  nothingReasons: [] as string[],
  error: null as UiMessage | null,
  drawingId: null as string | null,
  problem: null as ProblemKind | null,
}

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

  /**
   * 出候选（懒模式）：文字先到——名字 / 说明 / 推荐 / 列角色 / 表头行 / 非数字格一次回来，缩略图由 `<img>` 各自取（后端当场画）。
   * 不再有选择题：后端自己用默认答案，说清用了什么；用户在右栏改了就把改动放进 `answers` 重来。
   */
  async function propose(still: () => boolean, pj: string | null): Promise<void> {
    const sel = get().selected
    if (!sel) return
    const group = get().group
    set({ phase: 'loading', error: null, candidates: [], session: null, thumbs: {} })
    const answers = get().answers
    const hasHeaders = Object.keys(get().headerRows).length > 0
    let res
    try {
      res = await aiDataFigurePropose(
        {
          ...(group
            ? { tables: group.map((r) => ({ path: r.path, ...(r.sheet == null ? {} : { sheet: r.sheet }) })) }
            : { table: sel.path, ...(sel.sheet == null ? {} : { sheet: sel.sheet }) }),
          render: 'lazy',
          ...(Object.keys(answers).length ? { answers } : {}),
          ...(hasHeaders ? { header_rows: get().headerRows } : {}),
        },
        pj,
      )
    } catch (e) {
      if (!still()) return
      // 合画的两个稳定码各有出路（临时标签里一句话 + 「各画一张」），不当成「再试一次就行」的失败
      const code = e instanceof ApiError && typeof e.body?.code === 'string' ? e.body.code : ''
      if (group && code === 'tables_columns_differ') set({ phase: 'columnsDiffer', error: null })
      else if (group && code === 'tables_limit_exceeded') set({ phase: 'limitExceeded', error: reasonOf(e) }) // 后端那句「一次最多合画 {{max}} 份。」
      else set({ phase: 'failed', error: reasonOf(e) })
      return
    }
    if (!still()) return
    if (res.kind === 'ask') {
      // 懒模式不会问；万一问了按失败处理（不替用户答）
      set({ phase: 'failed', error: null })
      return
    }
    const mixedCols = mixedFromResponse(res)
    const common = {
      roles: rolesFromResponse(res) ?? get().roles,
      headerRowUsed: headerRowFromResponse(res),
      unitsRow: unitsRowFromResponse(res),
      sources: group ? sourcesFromResponse(res) : null,
      // 用户给过 `cells` 规则之后，后端不再把这些列列进 `mixed_cells`（已经不是「替用户做的默认决定」）：右栏那一行要留着，不然切过去就切不回来
      mixed: mixedCols.length > 0 ? { columns: mixedCols, mode: mixedCols[0]!.mode } : answers.cells !== undefined ? get().mixed : null,
    }
    if (res.kind === 'candidates' && Array.isArray(res.items) && res.items.length > 0) {
      set({
        ...common,
        phase: 'ready',
        session: res.session,
        candidates: candidatesFromResponse(res.items, pj),
        thumbs: Object.fromEntries(res.items.map((i) => [i.id, 'pending' as ThumbState])),
        nothingReasons: [],
      })
    } else if (res.kind === 'nothing') {
      set({ ...common, phase: 'nothing', nothingReasons: [...(res.reasons ?? [])], candidates: [], session: null })
    } else {
      // 形状不对按失败处理（不当成拿到了空的）
      set({ phase: 'failed', error: null })
    }
  }

  async function loadOverview(ref: TableRef): Promise<AiDataOverview | null> {
    const header = get().headerRows[ref.path] ?? null
    const key = overviewKey(ref, header)
    const prev = get().overviews[key]
    // 每次（重新）打开都重新请求：键里没有文件版本，外部改了文件之后缓存会是旧的（数据条旧内容、全是文字的文件一直「画不了」）。
    // 先显示缓存（下面 `loading` 带着 `prev.data`，不闪），新结果回来替换；序号保证只认最新一次
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
  /**
   * 读表 → 判断 → 出候选。`keepChoices`：重试当前临时标签（出候选失败 / 全部缩略图画不出之后）——保留用户在右栏改过的东西
   * （`answers` / 列角色 / 非数字格的处理），别回到后端默认；新选一张表 / 改表头行才清。
   */
  async function run(opts: { keepChoices?: boolean } = {}): Promise<void> {
    const sel = get().selected
    if (!sel) return
    const seq = { current: () => runSeq }
    runSeq += 1
    proposeSeq += 1
    const { pj, still } = capture(seq)
    const kept = opts.keepChoices ? { answers: get().answers, mixed: get().mixed, roles: get().roles } : { answers: {} as Record<string, unknown> }
    set({ ...idleDraft, ...kept, phase: 'reading' })
    // 合画：几份表一起交给后端判断（第一份的「全是文字 / 要点表头行」不代表整组），只读第一份的概览给数据条
    if (get().group) {
      groupFresh.add(overviewKeyOf(sel, get().headerRows))
      void loadOverview(sel)
      await propose(still, pj)
      return
    }
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
    const check = res.data_check
    // 只有明确说「核对过」才不提；缺失 / unknown / unverifiable 都不能写成已核对
    return { check: check === 'verified' ? null : check === 'unverifiable' ? 'unverifiable' : 'unknown' }
  }

  /** 进编辑器（宿主那一头做刷新与「打开并运行」）：临时标签先收掉，新图在真画布里打开，再记下这张图好给「换个画法」认 */
  async function goEditor(script: string): Promise<void> {
    const ep = epoch
    const file = get().selected
    set({ draftOpen: false, draftActive: false, drawingId: null, problem: null, phase: 'idle', session: null, candidates: [], fresh: file && !get().group ? { file, from: host.activeCanvasId(), canvasId: null } : null })
    if (ep !== epoch) return
    // `still`：换过项目 / 清过状态就不许再打开（A 项目的相对路径不能拿到 B 项目里去跑）
    await host.openScript(script, () => ep === epoch).catch(() => undefined)
    if (ep !== epoch) return
    // 一直没认下新图所在的画布（打开失败 / 画布没换）：不留着，免得之后不相干的画布切换被当成它
    const pending = get().fresh
    window.setTimeout(() => {
      const f = get().fresh
      if (f && f === pending && f.canvasId == null) set({ fresh: null })
    }, 20000)
  }

  /** 没成 / 要说话的结局：把临时标签翻出来（出图期间用户可能已经把它收起了） */
  const showOutcome = (patch: Partial<typeof initial>) => set({ draftOpen: true, draftActive: true, drawingId: null, ...patch })

  /**
   * `owner`：点「画这一张」/「再试一次」那一刻的项目与代际。提交排在共用串行队列里，轮到时可能已换了项目：
   * 此时 `capture` 抓到的是**现在**的项目（B），拿 A 的会话去发等于串了项目——所以快照必须在排队之前取，轮到时先核，对不上就什么都不发。
   */
  async function commit(sessionId: string, items: Candidate[], itemId: string, retried: boolean, owner?: { ep: number; pj: string | null }): Promise<void> {
    if (owner && (owner.ep !== epoch || owner.pj !== currentProjectId())) return
    const seq = { current: () => commitSeq }
    commitSeq += 1
    const cap = capture(seq)
    const pj = owner ? owner.pj : cap.pj
    const still = cap.still
    const forKey = `${sessionId}/${itemId}`
    if (!commitRid || commitRid.for !== forKey) commitRid = { for: forKey, rid: newRid() }
    const rid = commitRid.rid
    set({ drawingId: itemId, problem: null })
    try {
      const body = { session: sessionId, id: itemId, client_request_id: rid }
      let res = await aiDataFigureCommit(body, pj)
      if (!still()) return
      // 同一个请求号重发时原任务还在处理中：不是失败也不是画成，请求号留着，有界地重问同一个直到 final；超时仍未完成 = 这次没成，但请求号还在，「再试一次」继续问它
      if (isCommitInProgress(res)) {
        const waited = await pollCommit(() => aiDataFigureCommit(body, pj), still)
        if (!still()) return
        if (!waited) {
          showOutcome({ problem: { kind: 'commit', error: null, retryable: true } })
          return
        }
        res = waited
      }
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
      // 这类图没法逐项核对：照样打开图，说明走浮动通知（一句话，没有按钮）；缺 data_check 不写成已核对。
      // 「有 N 行没画出来」不再通知（用户拍板 11-17）
      const notes = notesOf(res)
      if (notes.check === 'unverifiable') host.notify(msg('dataFigureV2.result.unverifiable', undefined, 'ai'))
      else if (notes.check) host.notify(msg('dataFigureV2.result.unknown', undefined, 'ai'))
      await goEditor(res.script)
    } catch (e) {
      if (!still()) return
      const code = codeOf(e)
      if (code === 'propose_candidate_unknown' && get().session === sessionId && get().thumbs[itemId] !== undefined) {
        // 懒渲染：这一张提交时后端当场渲染 + 核对没过（取图本来就 422 的也一样）→ 这张不许落盘。
        // 把这张磁贴去掉、回到候选；一张都不剩就是「这次没画出来」。不重新出候选（同一张还是画不出）
        commitRid = null
        const thumbs = { ...get().thumbs, [itemId]: 'failed' as ThumbState }
        const allFailed = get().candidates.every((c) => thumbs[c.id] === 'failed')
        set(allFailed ? { thumbs, drawingId: null, phase: 'failed', error: null } : { thumbs, drawingId: null })
        return
      }
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
      // 同名的图占满了：后端的结论是确定的，再试一次永远一样，只留后端那句话
      showOutcome({ problem: { kind: 'commit', error: reasonOf(e), retryable: code !== 'figure_name_exhausted' } })
    }
  }

  /** 右栏改了东西：改 `answers`（可同时改别的字段），放弃上一份候选，带着重新出；右栏的下拉保持挂着（`roles` / `mixed` 不清） */
  function reask(fn: (a: Record<string, unknown>) => Record<string, unknown>, extra: Partial<typeof initial> = {}): void {
    discardSession()
    commitRid = null
    const seq = { current: () => runSeq }
    runSeq += 1
    const { pj, still } = capture(seq)
    set({ answers: fn(get().answers), ...extra })
    void propose(still, pj)
  }

  return {
    ...initial,

    enter: () => {
      filesDirty = false
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
      // 只有最新的那次在途读完才补尾随（旧的被取代时，取代它的那次读完会补）
      if (filesDirty) {
        filesDirty = false
        void get().loadFiles()
      }
    },

    invalidateFiles: () => {
      const st = get().files.status
      if (st === 'idle') return
      // 磁盘变了：合并标签里读过的概览都作废，当前显示的那个文件由数据条的 effect 随 `overviewGen` 重读
      if (get().group) {
        groupFresh.clear()
        set((s) => ({ overviewGen: s.overviewGen + 1 }))
      }
      if (st === 'loading') {
        filesDirty = true
        return
      }
      void get().loadFiles()
    },

    select: (ref) => {
      // 出图中：画完再换，别让在飞的提交没人认领；标签被收起了就翻出来给他看进度
      if (get().drawingId) {
        set({ draftOpen: true, draftActive: true })
        return
      }
      if (!get().group && sameTable(get().selected, ref) && get().draftOpen) {
        set({ draftActive: true })
        return
      }
      discardSession()
      commitRid = null
      set({ selected: ref, group: null, sources: null, draftOpen: true, draftActive: true, fresh: null })
      void run()
    },

    ensureOverview: (ref) => {
      const key = overviewKeyOf(ref, get().headerRows)
      // 这个标签里读过 / 在读的不重读（来回切不重复请求）；新开的合并标签里还没读过的，哪怕缓存里有也重读一次（先显示缓存）——外部改过文件，旧预览不能一直挂着
      if (groupFresh.has(key)) return
      groupFresh.add(key)
      void loadOverview(ref)
    },

    selectGroup: (refs) => {
      if (refs.length < 2) return
      groupFresh.clear() // 新开的合并标签：每个文件的概览至少重读一次
      if (get().drawingId) {
        set({ draftOpen: true, draftActive: true })
        return
      }
      discardSession()
      commitRid = null
      set({ selected: refs[0]!, group: refs, sources: null, draftOpen: true, draftActive: true, fresh: null })
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
      set({ draftOpen: false, draftActive: false, selected: null, group: null, sources: null, ...idleDraft })
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
      // 每一列都带上同一个 mode
      reask((a) => ({ ...a, cells: Object.fromEntries(m.columns.map((c) => [c.column, { mode }])) }), { mixed: { ...m, mode } })
    },

    setRole: (key, value) => {
      if (get().drawingId) return
      reask((a) => ({ ...a, [key]: value }))
    },

    thumbSettled: (session, id, ok) => {
      if (get().session !== session || get().thumbs[id] !== 'pending') return
      const thumbs = { ...get().thumbs, [id]: ok ? ('ready' as ThumbState) : ('failed' as ThumbState) }
      // 一张都没画出来：走「这次没画出来」那一屏
      const allFailed = get().candidates.length > 0 && get().candidates.every((c) => thumbs[c.id] === 'failed')
      set(allFailed ? { thumbs, phase: 'failed', error: null } : { thumbs })
    },

    draw: async (id) => {
      const sid = get().session
      if (!sid || get().drawingId || get().phase !== 'ready') return // 没有候选 / 正在画：不重复提交
      // 先把「正在画」摆出来（可能要排在批量出图的一份后面）；真正的提交在串行锁里做
      set({ drawingId: id, problem: null })
      const owner = { ep: epoch, pj: currentProjectId() }
      await runExclusive(() => commit(sid, get().candidates, id, false, owner))
    },

    retry: async () => {
      const p = get().problem
      if (p?.kind === 'commit') {
        // 同一次点击的重试：请求号还在就复用；没有候选可提交（会话没了）就重新出候选
        const last = commitRid
        if (last) {
          const [session, id] = last.for.split('/')
          set({ drawingId: id!, problem: null })
          const owner = { ep: epoch, pj: currentProjectId() }
          await runExclusive(() => commit(session!, [], id!, false, owner))
          return
        }
      }
      set({ problem: null })
      await run({ keepChoices: true })
    },

    dismissProblem: () => set({ problem: null }),

    redraw: () => {
      const f = get().fresh
      set({ fresh: null })
      host.dismissAddedNote()
      if (f) get().select(f.file)
    },

    adoptFresh: (canvasId) => {
      const f = get().fresh
      if (f && f.canvasId == null && canvasId !== f.from) set({ fresh: { ...f, canvasId } })
    },

    clear: () => {
      epoch += 1
      groupFresh.clear()
      filesDirty = false
      commitRid = null
      overviewSeq.clear()
      set({ ...initial })
    },
  }
})

// 「数据」临时标签激活 = 画布被盖住：同步给画布动作的让位判据（`store/canvasCover`），键盘 / 系统菜单 / 剪贴板 / 顶栏撤销都读它
useDataFigureStore.subscribe((s, prev) => {
  if (s.draftActive !== prev.draftActive) setCanvasCovered(s.draftActive)
})
