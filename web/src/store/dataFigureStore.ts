import { create } from 'zustand'
import {
  ApiError,
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataProgressCancel,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataUnderstanding,
  aiDataOverview,
  backendErrorMsg,
  isDefiniteRunFailure,
  type AiDataCommitResult,
  type AiDataFigureStyle,
  type AiDataFileEntry,
  type AiDataOverview,
  type AiDataUnderstanding,
} from '@/lib/api'
import { setCanvasCovered } from '@/store/canvasCover'
import { runExclusive } from '@/lib/dataCommitQueue'
import { isCommitInProgress, pollCommit } from '@/lib/dataCommitPoll'
import { currentProjectId } from '@/lib/session'
import { candidateIdentity, candidatesFromResponse, headerRowFromResponse, mixedFromResponse, rolesFromResponse, sourcesFromResponse, unitsRowFromResponse, type Candidate, type GroupSource, type ColumnRoles, type MixedColumn, type ThumbState } from '@/lib/dataFigureAdapter'
import { MERGE_FAILURE_CODES, fileRows, hasNumericColumn, isStaleProposeCode, sameTable, tableKey, type CellMode, type TableRef } from '@/lib/dataFigure'
import { SUGGEST_KEYS, diffSuggestion, suggestionFrom, type Suggestion, type SuggestionDiff } from '@/lib/dataUnderstanding'
import { fontRangeOf, sameDraftBinding } from '@/lib/dataFigureSpec'
import type { DocumentProfile } from '@/types/document'
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
 *  - `cancelled` 大文件读到一半用户点了「取消」：后端真的停了读表（协作式取消），回到可以重读 / 换文件的状态（见 `cancelRead`）
 */
export type DraftPhase = 'idle' | 'reading' | 'cancelled' | 'unreadable' | 'text' | 'pickrow' | 'loading' | 'ready' | 'nothing' | 'failed' | 'mergeFailed'

/** 这次画图用到的、数值列里混着文字的列（后端 `mixed_cells`）：默认「不画这几行」，右栏可改成「只用里面的数字」；多列时 `mode` 是它们共用的处理方式 */
export interface MixedInfo {
  columns: MixedColumn[]
  mode: CellMode
}

/** 右栏三个下拉的角色 */
export type RoleKey = 'x' | 'y' | 'group'

/** 「需要你确认」两项对应的 `answers` 键：只有用户点击才写；模型的建议（`SUGGEST_KEYS`）不含它们 */
export type ConfirmKey = 'error' | 'design'

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
  /** 此刻激活画布的规范绑定（`doc.profile` 的深拷贝；没绑定 = null）。草稿第一次出候选时取一次，之后草稿自己持有。没注册 = null */
  canvasBinding?: () => DocumentProfile | null
  /** 绑定 → 随请求发给后端的那份已解析规范（`resolveDocumentSpec(binding, 清单).profile`）。没注册 = 不带规范 */
  resolveSpec?: (binding: DocumentProfile) => Record<string, unknown> | null
  /** 图画好、要进编辑器那一刻：让新图落的那张画布带上同一份绑定（见 `lib/dataFigureInherit`）；返回收手函数。没注册 = 什么都不做 */
  armInherit?: (binding: DocumentProfile, still: () => boolean) => () => void
}
const defaultHost: DataFigureHost = { openDrawer: () => {}, openScript: async (_script, _still) => {}, projectWritable: () => undefined, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} }
let host: DataFigureHost = defaultHost
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
  /** 「读懂数据」后台任务还在跑（右栏标题行一行灰字）；`failed` / `off` / 超时 / 出结果都回到 false，界面与没有这个功能时一样 */
  understanding: boolean
  /** 模型建议与此刻的映射不同、但用户已经动过下拉（或正在出图）没有自动套用：右栏标题行的胶囊；点一下才套用，不点就是忽略 */
  suggestion: Suggestion | null
  /** 「读懂数据」自动套用写进 `answers` 的那些键（含 `x_measure`）和值：不算用户动过下拉；用户自己改了那一项就从这里去掉 */
  autoPicks: Record<string, unknown>

  /** 后端说「读懂数据」此刻有没有可用的连接（`/api/ai/data-files` 的 `understanding_available`，随清单取）；null = 还没取到 / 旧后端没有这个字段（前端退回 `hasUsableAi`） */
  understandingAvailable: boolean | null
  /** 观察到过「读懂数据不可用」且连上之后还没因此重跑过：宿主（`useConnectAiWatch`，生命周期覆盖抽屉与草稿）重新挂上时据此补跑；记在 store 里而不是组件 ref */
  sawUnavailable: boolean
  /** 宿主汇报此刻「读懂数据」是否不可用：不可用 → 记下；从不可用变为可用 → 对当前数据重跑一次（`rerunForConnection` 自己拦「动过下拉 / 出图中」） */
  noteUnavailable: (unavailable: boolean) => void
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
  /** 用户刚连上 AI：对当前这份数据重新出一次候选，让「读懂数据」跑起来。用户已动过横轴 / 纵轴 / 分组下拉、正在出图、候选还没出完时什么都不做 */
  rerunForConnection: () => void
  /** 点胶囊：套用模型的建议（等同用户自己改了映射后重新出候选） */
  applySuggestion: () => void
  /** 主图此刻预览的那一张（候选的身份串 `candidateIdentity`，不是 id：换了规范 / 答案重新出候选后 id 全换、画法还在就还是它）；null = 第一张。只是预览，不创建 */
  previewKey: string | null
  /** 响应说用户点选过误差类型（`answers.error` 被后端认了）；没有 = 误差线画的是哪一种还没确认 */
  errorConfirmed: boolean
  /** 「是不是同一对象重复测量」这个问题出现过（`defaults` 里有 `pick_design`）：确认之后后端不再问，但那一行要留着（可再改），所以只增不减，直到换文件 */
  designAsked: boolean
  /** 只换预览，不发请求、不创建 */
  selectPreview: (id: string) => void
  /** 「需要你确认」里用户点了某一项：只由右栏那几行调用（建议 / autoPicks 永远不写这两个键）。带着新答案重新出候选，不算动了列角色下拉，不重起「读懂数据」 */
  setConfirm: (key: ConfirmKey, value: string) => void
  /** 草稿自己的规范绑定（`DocumentProfile` 形状；null = 默认规范）：第一次出候选时取自激活画布，之后只由右栏「期刊」一行改，不写任何画布 */
  specBinding: DocumentProfile | null
  /** `specBinding` 已经取过了（取一次，换项目 / 重新进入才重取） */
  bindingCaptured: boolean
  /** 这批候选实际按的栏宽与字号（响应的 `figure_style`；没带规范 / 规范不合法 / 还没出 = null） */
  figureStyle: AiDataFigureStyle | null
  /** 请求带的规范不合法、后端退回了默认规范（界面不为此多说话，留给诊断与测试） */
  specFallback: boolean
  /** 换草稿的绑定：不算用户动了下拉，按当前这份数据重新出候选；出图中不接受 */
  setSpecBinding: (binding: DocumentProfile | null) => void
  /** 此刻读表 / 出候选那个请求的请求号（进度条轮询、取消都认它）；没有在读 = null */
  readRid: string | null
  /** 大文件读表 / 出候选到一半不想等了：调后端取消端点（真的停），原请求随后回 409 `propose_cancelled`，按它收尾到 `cancelled`（可重读）；
   *  赛跑时以原请求的响应为准（已经读完就照常往下走）；取消端点不可用 / 还没登记上时退回「前端不再等这次请求」（后端会自己跑完、结果丢掉） */
  cancelRead: () => Promise<void>
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
/** 项目代际（`clear()` 加一）：右栏「期刊」选择器等下载回来时，它变了就丢掉这次结果 */
export const dataFigureEpoch = (): number => epoch
let filesSeq = 0
/** 清单在途时又来了磁盘变化：只记一个标记，在途那次结束后恰好补一次尾随重读（保证最后一个事件之后的磁盘状态一定被读到） */
let filesDirty = false
let proposeSeq = 0
let runSeq = 0
let commitSeq = 0
const overviewSeq = new Map<string, number>()
/** 此刻这一轮「读表 + 出候选」的请求共用一个中止信号：`cancelRead` 用它让浏览器不再等（`run` / `reask` 各开新的一个，不中止上一轮） */
let readCtl: AbortController | null = null
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
  understanding: false,
  suggestion: null as Suggestion | null,
  autoPicks: {} as Record<string, unknown>,
  understandingAvailable: null as boolean | null,
  sawUnavailable: false,
  readRid: null as string | null,
  previewKey: null as string | null,
  errorConfirmed: false,
  designAsked: false,
  // 草稿自己的规范绑定只在 `initial`：换文件 / 关标签（`idleDraft`）不清它，项目代际（`clear()`）与重新进入（`enter()`）才清
  specBinding: null as DocumentProfile | null,
  bindingCaptured: false,
  figureStyle: null as AiDataFigureStyle | null,
  specFallback: false,
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
  understanding: false,
  suggestion: null as Suggestion | null,
  autoPicks: {} as Record<string, unknown>,
  readRid: null as string | null,
  previewKey: null as string | null,
  errorConfirmed: false,
  designAsked: false,
  figureStyle: null as AiDataFigureStyle | null,
  specFallback: false,
}

/**
 * ## 按表记住（切走再切回来直接还原）
 *
 * 同一时刻只有一份草稿；切到别的表时，把这份**已完整**的草稿（候选出齐、缩略图都定了、没在出图）连同后端候选会话一起留在 `kept` 里，
 * 切回同一张表（同一组表、同一表头行、同一份期刊绑定）就原样放回来：不读表、不出候选、缩略图不重画。留的东西以「切回来看到的和切走前一模一样」为准。
 * 在读 / 在出候选 / 缩略图还在画的半成品**不存**（和从前一样直接丢，切回来重跑）。
 *
 * 什么时候不能用旧的（`pruneKept` / `takeKept`）：
 *  1. 清单里该文件的 `size` 变了（清单重读后比；还原那一刻也比）；**同大小但内容变了的文件比不出来**——这个缺口已知、先接受：
 *     还原出来的是旧图，直到提交时后端回 `propose_data_changed`（`isStaleProposeCode`）才静默重出；
 *  2. 会话没了：还原后缩略图取不到（`restoredSession` 标记）→ 静默带着现有答案重出；提交回 stale 码走原来的静默重出。浏览器图片缓存可能直接给出旧缩略图，那种情况只能靠提交时的 stale 码兜底；
 *  3. 超过会话 TTL（`KEEP_TTL_MS`，从出候选响应到达算，比后端「最后一次读」起算保守）；
 *  4. 换项目（`clear()` / 发起时的项目对不上）全丢；
 *  5. 条数上限 `KEEP_MAX`。
 * 丢的条目一律放弃它的后端会话（best-effort）。关临时标签（`closeDraft`）= 这份草稿不要了，照旧丢并放弃会话；别的表的条目留着。
 */
/** 后端 `ai_data_propose.SESSION_TTL_S`（30 分钟） */
export const KEEP_TTL_MS = 30 * 60 * 1000
/** 后端 `ai_data_propose.MAX_SESSIONS`（6）：所有候选会话共用；当前草稿占一个，所以留着的最多 6 − 1 */
export const KEEP_MAX = 6 - 1
let keepClock: () => number = () => Date.now()
/** 测试用：换时钟（TTL 不用真等） */
export const setKeepClock = (f: () => number): void => {
  keepClock = f
}

const KEEP_FIELDS = ['answers', 'mixed', 'roles', 'headerRowUsed', 'unitsRow', 'thumbs', 'phase', 'session', 'candidates', 'nothingReasons', 'previewKey', 'errorConfirmed', 'designAsked', 'figureStyle', 'specFallback', 'suggestion', 'autoPicks', 'sources'] as const
type KeepSnap = Pick<typeof initial, (typeof KEEP_FIELDS)[number]>
interface Kept {
  key: string
  refs: TableRef[]
  group: TableRef[] | null
  snap: KeepSnap
  binding: DocumentProfile | null
  sizes: Record<string, number | undefined>
  /** 出候选响应到达的时刻（会话大约是那时起算的） */
  liveAt: number
  pj: string | null
  /** 切走时「读懂数据」还在跑的任务 id：还原后接着轮询 */
  job: string | null
}
let kept: Kept[] = []
/** 当前会话出候选**那一刻**（请求发出时）涉及文件的大小，跟着候选走：留存时用它，不用切走时才读的清单（那时清单可能已经是新的，候选却是按旧内容出的）。有一个读不到 = 没法验证，这份不留 */
let sessionSizes: Record<string, number | undefined> = {}
/** 这份草稿是还原出来的、会话没确认过还活着：它的缩略图取不到就静默重出（只一次） */
let restoredSession: string | null = null
/** 当前会话的出候选时刻（还原的沿用原来的，再切走时存回去） */
let sessionLiveAt = 0
/** 「读懂数据」此刻在轮询的任务 id（只有 pending 时才有） */
let understandingJob: string | null = null

/** 键：这张表（合画 = 有序的一组）+ 各自当前的表头行；期刊绑定在命中判断里比（取得晚，不进字符串） */
const keepKeyOf = (refs: TableRef[], group: boolean, headerRows: Record<string, number>): string =>
  `${group ? 'g' : 't'}:${refs.map((r) => `${tableKey(r)}#${headerRows[r.path] ?? ''}`).join('|')}`

/** 两个 answers 取值是否相同（列名或列名数组，数组按顺序比） */
const sameValue = (a: unknown, b: unknown): boolean =>
  Array.isArray(a) || Array.isArray(b) ? Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v === b[i]) : a === b

/**
 * 「读懂数据」轮询：直到终态为止，按时间封顶——后端总时限订阅登录（CLI）60 s、Key 25 s，再留余量；1 s 一次，单次失败不算结局，
 * 到点静默放弃。（不按次数：请求本身慢的时候次数会比时间少得多。）
 */
const UNDERSTANDING_POLL_MS = 1000
const UNDERSTANDING_POLL_MAX_MS = 75_000
let understandingSeq = 0
let understandingTimer: ReturnType<typeof setTimeout> | null = null
/** 这一轮（同一份数据）已经起过「读懂数据」跟踪：换规范重新出候选时不再为它再起一轮（模型读的是数据，不是规范）；任何 `stopUnderstanding` 都清掉 */
let understandingTracked = false
/** 停掉在跑的轮询（换文件 / 重新读表 / 关标签 / 换项目 / 出图进编辑器）：旧结果回来对不上序号就丢，绝不套到别的文件上 */
const stopUnderstanding = (): void => {
  understandingSeq += 1
  understandingTracked = false
  understandingJob = null
  if (understandingTimer) clearTimeout(understandingTimer)
  understandingTimer = null
}
/** 草稿图画好、新画布还没出现之前的「带绑定过去」收手函数（`lib/dataFigureInherit`）：下一次画图 / 换项目时收掉 */
let inheritOff: (() => void) | null = null

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

  /* ---- 按表记住（说明见模块级 `kept`） ---- */
  const discardKept = (e: Kept) => void aiDataFigureDiscard(e.snap.session!, e.pj).catch(() => undefined)
  const dropKept = (e: Kept) => {
    kept = kept.filter((x) => x !== e)
    discardKept(e)
  }
  const dropAllKept = () => {
    for (const e of kept) discardKept(e)
    kept = []
    restoredSession = null
  }
  /** 清单里这个文件此刻的大小；清单还没读过 / 没有这个文件 = undefined（比不出来就当没变） */
  const sizeOf = (path: string): number | undefined => {
    const f = get().files
    return f.status === 'idle' ? undefined : f.entries?.find((e) => e.path === path)?.size
  }
  const pruneKept = () => {
    const now = keepClock()
    const pj = currentProjectId()
    for (const e of [...kept]) {
      const changed = Object.entries(e.sizes).some(([path, was]) => {
        const cur = sizeOf(path)
        return was !== undefined && cur !== undefined && was !== cur
      })
      if (now - e.liveAt > KEEP_TTL_MS || e.pj !== pj || changed) dropKept(e)
    }
  }
  /** 把此刻这份已完整的草稿留下（会话不放弃）；半成品 / 出过错 / 正在出图的不存，返回 false（调用方照旧放弃会话） */
  const stashCurrent = (): boolean => {
    const s = get()
    // 出候选时就没读到大小：之后清单怎么变都没法判，这份不留（退回从前：丢并放弃会话）
    if (Object.values(sessionSizes).some((n) => n === undefined) || Object.keys(sessionSizes).length === 0) return false
    // 出候选之后清单刷新、发现这份草稿用的文件变了：候选是按旧内容出的，不能留
    if (Object.entries(sessionSizes).some(([path, was]) => sizeOf(path) !== undefined && sizeOf(path) !== was)) return false
    if (!s.selected || s.phase !== 'ready' || !s.session || s.drawingId || s.problem || s.candidates.length === 0) return false
    if (!s.candidates.every((c) => s.thumbs[c.id] === 'ready' || s.thumbs[c.id] === 'failed')) return false
    const refs = s.group ?? [s.selected]
    const key = keepKeyOf(refs, !!s.group, s.headerRows)
    const old = kept.find((e) => e.key === key && sameDraftBinding(e.binding, s.specBinding))
    if (old) dropKept(old)
    pruneKept()
    kept.push({
      key,
      refs: [...refs],
      group: s.group ? [...s.group] : null,
      snap: Object.fromEntries(KEEP_FIELDS.map((k) => [k, s[k]])) as KeepSnap,
      binding: s.specBinding ? structuredClone(s.specBinding) : null,
      sizes: { ...sessionSizes },
      liveAt: sessionLiveAt,
      pj: currentProjectId(),
      job: understandingJob,
    })
    while (kept.length > KEEP_MAX) dropKept(kept[0]!)
    return true
  }
  /** 取出（并移出）这张表 / 这组表的留存；键、期刊绑定都对得上、没过期、文件没变才算 */
  const takeKept = (refs: TableRef[], group: boolean): Kept | null => {
    ensureBinding()
    pruneKept()
    const key = keepKeyOf(refs, group, get().headerRows)
    const hit = kept.find((e) => e.key === key && sameDraftBinding(e.binding, get().specBinding))
    if (!hit) return null
    kept = kept.filter((e) => e !== hit)
    return hit
  }
  /** 放回去：不读表、不出候选；旧的在飞响应靠序号作废 */
  const restoreKept = (e: Kept) => {
    runSeq += 1
    proposeSeq += 1
    stopUnderstanding()
    commitRid = null
    set({ selected: e.refs[0]!, group: e.group, draftOpen: true, draftActive: true, fresh: null, ...e.snap, drawingId: null, problem: null, error: null, understanding: false, readRid: null })
    restoredSession = e.snap.session
    sessionLiveAt = e.liveAt
    sessionSizes = { ...e.sizes }
    if (e.group) for (const r of e.group) if (get().overviews[overviewKeyOf(r, get().headerRows)]) groupFresh.add(overviewKeyOf(r, get().headerRows))
    if (e.job) trackUnderstanding({ id: e.job, state: 'pending' }, currentProjectId())
  }

  const headerFixed = (): boolean => {
    const sel = get().selected
    return sel ? get().headerRows[sel.path] != null : false
  }

  /** 套用一份（已核过与此刻不同的）建议：等同用户自己改了映射 / 指了表头行，再出候选 */
  function applyDiff(d: SuggestionDiff): void {
    stopUnderstanding()
    set({ understanding: false, suggestion: null })
    const sel = get().selected
    // 套进 `answers` 的补丁（含 `x_measure`，它不对应任何下拉）；记下来：这些键不算用户动过，也不会因同一份建议再套一次
    const patch: Record<string, unknown> = { ...d.picks, ...(d.xMeasure !== undefined ? { x_measure: d.xMeasure } : {}) }
    set((s) => ({ autoPicks: { ...s.autoPicks, ...patch } }))
    if (d.headerRow !== null && sel && !get().group) {
      // 换了表头行：列也跟着变，列名建议是按重读后的表给的——和 `setHeaderRow` 同一条路，但把建议的列一起带上
      discardSession()
      commitRid = null
      set((s) => ({ headerRows: { ...s.headerRows, [sel.path]: d.headerRow! }, answers: { ...s.answers, ...patch } }))
      void run({ keepChoices: true })
      return
    }
    if (Object.keys(patch).length > 0) reask((a) => ({ ...a, ...patch }))
  }

  /** 「读懂数据」出结果了：用户还没动过横轴 / 纵轴 / 分组就直接套用，动过了（或正在出图）留给胶囊，没差别什么都不做 */
  function onUnderstood(sug: Suggestion): void {
    const d = diffSuggestion(sug, get().roles, get().headerRowUsed, headerFixed(), get().answers)
    if (!d) return
    const { answers, autoPicks } = get()
    // 自动套用写进去的键不算「动过」（值还是套用时的那个）；用户自己改过的才算
    const touched = SUGGEST_KEYS.some((k) => k in answers && !(k in autoPicks && sameValue(answers[k], autoPicks[k])))
    // 同一份补丁已经自动套用过一次、这里仍不同（后端没采纳）：不再套第二遍，也不弹胶囊——点了也是同一个结果
    const again = d.headerRow === null && Object.keys(d.picks).length + (d.xMeasure !== undefined ? 1 : 0) > 0 &&
      Object.entries({ ...d.picks, ...(d.xMeasure !== undefined ? { x_measure: d.xMeasure } : {}) }).every(([k, v]) => k in autoPicks && sameValue(autoPicks[k], v))
    if (again) return
    if (!touched && !get().drawingId) applyDiff(d)
    else set({ suggestion: sug })
  }

  /** 起轮询（响应里 `understanding` 是 `pending`）或直接用内嵌的结果（`ready`）；`failed` / `off` / 超时全部静默 */
  function trackUnderstanding(u: AiDataUnderstanding, pj: string | null): void {
    stopUnderstanding()
    understandingTracked = true
    understandingJob = u.state === 'pending' ? u.id : null
    set({ understanding: false, suggestion: null })
    const mine = understandingSeq
    const ep = epoch
    const alive = () => ep === epoch && mine === understandingSeq
    /** 终态返回 true；`pending` 返回 false */
    const settle = (v: AiDataUnderstanding): boolean => {
      if (v.state === 'pending') return false
      understandingJob = null
      set({ understanding: false })
      if (v.state === 'ready') {
        const sug = suggestionFrom(v, get().selected?.path ?? null, !get().group)
        if (sug) onUnderstood(sug)
      }
      return true
    }
    if (u.state === 'pending') set({ understanding: true })
    else {
      settle(u)
      return
    }
    const t0 = Date.now()
    const tick = (): void => {
      understandingTimer = setTimeout(async () => {
        understandingTimer = null
        if (!alive()) return
        let v: AiDataUnderstanding | null = null
        try {
          v = await aiDataUnderstanding(u.id, pj)
        } catch {
          v = null // 偶发失败不当结局：下一次再问，到次数为止
        }
        if (!alive()) return
        if (v && settle(v)) return
        if (Date.now() - t0 >= UNDERSTANDING_POLL_MAX_MS) {
          understandingJob = null
          set({ understanding: false })
          return
        }
        tick()
      }, UNDERSTANDING_POLL_MS)
    }
    tick()
  }

  /**
   * 出候选（懒模式）：文字先到——名字 / 说明 / 推荐 / 列角色 / 表头行 / 非数字格一次回来，缩略图由 `<img>` 各自取（后端当场画）。
   * 不再有选择题：后端自己用默认答案，说清用了什么；用户在右栏改了就把改动放进 `answers` 重来。
   */
  /** 草稿的规范绑定第一次取自此刻激活的画布（取一次；之后草稿自己持有，换画布 / 换期刊都不再跟） */
  function ensureBinding(): void {
    if (get().bindingCaptured) return
    set({ specBinding: host.canvasBinding?.() ?? null, bindingCaptured: true })
  }

  /**
   * `fromSpec`：因为换了规范重新出候选——「读懂数据」读的是数据不是规范，这一轮已经起过跟踪（在跑 / 已出结论）就不再为这份响应再起一轮、
   * 也不清掉已有的建议；这一轮还没起过（第一次响应被这次取代了）才照常起。
   */
  async function propose(still: () => boolean, pj: string | null, signal?: AbortSignal, fromSpec = false): Promise<void> {
    const sel = get().selected
    if (!sel) return
    ensureBinding()
    // 这次出候选读的是什么样的文件：发请求这一刻的大小（响应到达时清单可能已经变了），成功落地时跟着候选记下
    const sizesAtAsk = Object.fromEntries((get().group ?? [sel]).map((r) => [r.path, sizeOf(r.path)]))
    const binding = get().specBinding
    const spec = binding ? (host.resolveSpec?.(binding) ?? null) : null
    const fontRange = fontRangeOf(binding)
    const group = get().group
    set({ phase: 'loading', error: null, candidates: [], session: null, thumbs: {} })
    const answers = get().answers
    const hasHeaders = Object.keys(get().headerRows).length > 0
    let res
    const rid = newRid()
    set({ readRid: rid })
    try {
      res = await aiDataFigurePropose(
        {
          ...(group
            ? { tables: group.map((r) => ({ path: r.path, ...(r.sheet == null ? {} : { sheet: r.sheet }) })) }
            : { table: sel.path, ...(sel.sheet == null ? {} : { sheet: sel.sheet }) }),
          render: 'lazy',
          ...(group ? {} : { client_request_id: rid }), // 合画永远是小表：不跟踪
          ...(Object.keys(answers).length ? { answers } : {}),
          ...(hasHeaders ? { header_rows: get().headerRows } : {}),
          ...(spec ? { spec } : {}),
          ...(fontRange ? { font_range: fontRange } : {}),
        },
        pj,
        signal,
      )
    } catch (e) {
      if (!still()) return
      if (get().readRid === rid) set({ readRid: null })
      // 后端按「取消」中止了这次出候选：回到可重读的状态（不是失败）
      if (codeOf(e) === 'propose_cancelled') {
        markCancelled()
        return
      }
      // 合画失败的稳定码（`MERGE_FAILURE_CODES`）各有出路：临时标签里显示后端那一句 + 「分别作图」，不当成「再试一次就行」的失败
      const code = e instanceof ApiError && typeof e.body?.code === 'string' ? e.body.code : ''
      if (group && MERGE_FAILURE_CODES.includes(code)) set({ phase: 'mergeFailed', error: reasonOf(e) })
      else set({ phase: 'failed', error: reasonOf(e) })
      return
    }
    if (!still()) return
    if (get().readRid === rid) set({ readRid: null })
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
      figureStyle: res.figure_style ?? null,
      specFallback: res.spec_fallback === true,
      errorConfirmed: res.error_confirmed === true,
      designAsked: get().designAsked || (res.defaults ?? []).some((d) => d.question_code === 'pick_design'),
    }
    if (res.kind === 'candidates' && Array.isArray(res.items) && res.items.length > 0) {
      restoredSession = null
      sessionLiveAt = keepClock()
      sessionSizes = sizesAtAsk
      set({
        ...common,
        phase: 'ready',
        session: res.session,
        candidates: candidatesFromResponse(res.items, pj),
        thumbs: Object.fromEntries(res.items.map((i) => [i.id, 'pending' as ThumbState])),
        nothingReasons: [],
      })
      if (res.understanding && !(fromSpec && understandingTracked)) trackUnderstanding(res.understanding, pj)
    } else if (res.kind === 'nothing') {
      set({ ...common, phase: 'nothing', nothingReasons: [...(res.reasons ?? [])], candidates: [], session: null })
      if (res.understanding && !(fromSpec && understandingTracked)) trackUnderstanding(res.understanding, pj)
    } else {
      // 形状不对按失败处理（不当成拿到了空的）
      set({ phase: 'failed', error: null })
    }
  }

  /** 读表 / 出候选被取消之后的收尾：概览回到取消前的样子（有旧内容还它，没有就当没读过），临时标签回到可重读的 `cancelled` */
  function markCancelled(): void {
    stopUnderstanding() // 取消读取：读懂数据的轮询一并停，旧结果不再套用
    const { selected } = get()
    if (selected) {
      const key = overviewKeyOf(selected, get().headerRows)
      const prev = get().overviews[key]
      set((s) => {
        const next = { ...s.overviews }
        if (prev?.data) next[key] = { status: 'ready', data: prev.data }
        else delete next[key]
        return { overviews: next }
      })
    }
    set({ phase: 'cancelled', candidates: [], session: null, thumbs: {}, error: null, readRid: null, understanding: false, suggestion: null })
  }

  async function loadOverview(ref: TableRef, signal?: AbortSignal, rid?: string): Promise<AiDataOverview | null | 'cancelled'> {
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
      const data = await aiDataOverview({ path: ref.path, sheet: ref.sheet, headerRow: header }, pj, signal, rid)
      if (ep !== epoch || overviewSeq.get(key) !== mine) return null
      set((s) => ({ overviews: { ...s.overviews, [key]: { status: 'ready', data } } }))
      return data
    } catch (e) {
      if (ep !== epoch || overviewSeq.get(key) !== mine) return null
      if (codeOf(e) === 'propose_cancelled') return 'cancelled'
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
    restoredSession = null
    ensureBinding() // 读表 / 判断阶段（全是文字、要点表头行）也要让右栏「期刊」一行显示的是真绑定，不等到出候选
    const seq = { current: () => runSeq }
    runSeq += 1
    proposeSeq += 1
    stopUnderstanding()
    const { pj, still } = capture(seq)
    const kept = opts.keepChoices ? { answers: get().answers, autoPicks: get().autoPicks, mixed: get().mixed, roles: get().roles } : { answers: {} as Record<string, unknown> }
    const ctl = new AbortController()
    readCtl = ctl
    set({ ...idleDraft, ...kept, phase: 'reading' })
    // 合画：几份表一起交给后端判断（第一份的「全是文字 / 要点表头行」不代表整组），只读第一份的概览给数据条
    if (get().group) {
      groupFresh.add(overviewKeyOf(sel, get().headerRows))
      void loadOverview(sel)
      await propose(still, pj, ctl.signal)
      return
    }
    // 清单里已经说「读不了」（没有可看的原文）：不发请求
    const entries = get().files.status === 'idle' ? null : (get().files as { entries: AiDataFileEntry[] | null }).entries
    const row = entries ? fileRows(entries).flatMap((r) => r.rows).find((r) => sameTable(r.ref, sel)) : undefined
    if (row?.status.kind === 'unreadable' && get().headerRows[sel.path] == null) {
      set({ phase: 'unreadable' })
      return
    }
    const ovRid = newRid()
    set({ readRid: ovRid })
    const ov = await loadOverview(sel, ctl.signal, ovRid)
    if (!still()) return
    if (ov === 'cancelled') {
      markCancelled()
      return
    }
    if (get().readRid === ovRid) set({ readRid: null })
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
    await propose(still, pj, ctl.signal)
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
    stopUnderstanding()
    // 变成正式的图了：这份草稿不再存（它自己的留存条目若有也删掉）
    restoredSession = null
    if (file) {
      const key = keepKeyOf(get().group ?? [file], !!get().group, get().headerRows)
      for (const e of kept.filter((x) => x.key === key)) dropKept(e)
    }
    set({ understanding: false, suggestion: null, draftOpen: false, draftActive: false, drawingId: null, problem: null, phase: 'idle', session: null, candidates: [], fresh: file && !get().group ? { file, from: host.activeCanvasId(), canvasId: null } : null })
    if (ep !== epoch) return
    // 新图落的那张画布要带上同一份绑定（编辑器据此检查；不进撤销栈，见 `lib/dataFigureInherit`）
    inheritOff?.()
    const binding = get().specBinding
    inheritOff = binding && host.armInherit ? host.armInherit(structuredClone(binding), () => ep === epoch) : null
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
        // 身份完全相同（画法 + 合并作图的 variant）才接着画；找不到就不自动提交，让用户看新的候选重选
        const same = wanted ? get().candidates.find((i) => candidateIdentity(i) === candidateIdentity(wanted)) : undefined
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
  function reask(fn: (a: Record<string, unknown>) => Record<string, unknown>, extra: Partial<typeof initial> = {}, fromSpec = false): void {
    discardSession()
    commitRid = null
    const seq = { current: () => runSeq }
    runSeq += 1
    const { pj, still } = capture(seq)
    set({ answers: fn(get().answers), ...extra })
    const ctl = new AbortController()
    readCtl = ctl
    void propose(still, pj, ctl.signal, fromSpec)
  }

  return {
    ...initial,

    enter: () => {
      dropAllKept()
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
        set({
          files: { status: 'ready', entries: r.files, truncated: r.truncated === true },
          understandingAvailable: typeof r.understanding_available === 'boolean' ? r.understanding_available : null,
        })
        pruneKept() // 文件的大小变了：对应的留存作废
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
      if (!get().group && sameTable(get().selected, ref) && get().draftOpen && get().phase !== 'cancelled') {
        set({ draftActive: true })
        return
      }
      // 切走：已完整的草稿留下（会话不放弃）；切回留过的就原样放回，不读表不出候选
      ensureBinding()
      // 先取目标再存当前：存当前可能挤掉最旧的一条，不能挤掉要还原的那条
      const hit = takeKept([ref], false)
      const stashed = stashCurrent()
      // 存不了的当前草稿（半成品 / 没法验证大小等）：无论目标命不命中，会话都要放弃，不能既不留也不放弃
      if (!stashed) discardSession()
      if (hit) {
        restoreKept(hit)
        return
      }
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
      ensureBinding()
      const hit = takeKept(refs, true)
      // 重新合同一组 = 本来就要重出，不把当前这份当成「切走」存起来
      const stashed = keepKeyOf(refs, true, get().headerRows) === keepKeyOf(get().group ?? [], true, get().headerRows) ? false : stashCurrent()
      if (!stashed) discardSession()
      if (hit) {
        restoreKept(hit)
        return
      }
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
      restoredSession = null
      proposeSeq += 1
      runSeq += 1
      stopUnderstanding()
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
      // 用户改了哪一项，就清掉与那一项绑定的自动键：改横轴 → 清 `x_measure`（它说的就是「原来那个横轴」）；原来是「不要横轴 + 多列纵轴」
      // （把列当分组）而用户选了真横轴 → 纵轴收回第一列（数组只在没有横轴时才是分组）；用户选单列纵轴 → 放掉那个「不要横轴」的绑定。
      const a = { ...get().answers, [key]: value }
      const auto = { ...get().autoPicks }
      delete auto[key]
      const was = get().answers
      if (key === 'x') {
        delete a.x_measure
        delete auto.x_measure
        if (was.x === 'none' && Array.isArray(was.y) && value !== 'none') {
          a.y = was.y[0]
          delete auto.y
        }
      }
      if (key === 'y' && was.x === 'none' && Array.isArray(was.y)) {
        delete a.x
        delete auto.x
      }
      reask(() => a, { autoPicks: auto })
    },

    thumbSettled: (session, id, ok) => {
      // 还原出来的草稿：原来到了的缩略图现在取不到（会话已没 / 文件变了）= 旧会话不能用，静默带着现有答案重新出候选（只一次）
      if (!ok && session === restoredSession && get().session === session && get().thumbs[id] === 'ready') {
        restoredSession = null
        if (!get().drawingId) reask((a) => a, {}, true)
        return
      }
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
      discardSession() // `run` 会把会话 id 清掉：旧会话不放弃就成了没人认领的
      await run({ keepChoices: true })
    },

    noteUnavailable: (unavailable) => {
      if (unavailable) {
        if (!get().sawUnavailable) set({ sawUnavailable: true })
        return
      }
      if (!get().sawUnavailable) return
      set({ sawUnavailable: false })
      get().rerunForConnection()
    },

    rerunForConnection: () => {
      const { selected, phase, drawingId, answers, autoPicks } = get()
      if (!selected || drawingId || (phase !== 'ready' && phase !== 'nothing')) return
      // 「用户动过下拉」与 `onUnderstood` 同一口径：自动套用写进 `answers` 的键（`autoPicks`）不算
      if (SUGGEST_KEYS.some((k) => k in answers && !(k in autoPicks && sameValue(answers[k], autoPicks[k])))) return
      reask((a) => a)
    },

    applySuggestion: () => {
      const sug = get().suggestion
      if (!sug || get().drawingId) return
      const d = diffSuggestion(sug, get().roles, get().headerRowUsed, headerFixed(), get().answers)
      if (!d) {
        set({ suggestion: null })
        return
      }
      applyDiff(d)
    },

    selectPreview: (id) => {
      const c = get().candidates.find((x) => x.id === id)
      if (c && get().thumbs[id] === 'ready') set({ previewKey: candidateIdentity(c) })
    },

    setConfirm: (key, value) => {
      if (get().drawingId) return
      const was = get().answers[key]
      // 「不确定」是没人答过时的默认：从没答过又选「不确定」= 什么都没变
      if (key === 'design' && value === 'unsure' && was === undefined) return
      if (was === value && key !== 'error') return
      // 与换规范同一条路：重出候选、不动 `autoPicks`、这一轮「读懂数据」已起过就不再起（模型读的是数据，不是这两个答案）
      reask((a) => ({ ...a, [key]: value }), {}, true)
    },

    setSpecBinding: (binding) => {
      // 出图中：提交用的是已经出好的那一批（同一份规范），这时换了会让「画出来的」与「选的」对不上——不接受（界面也是禁用的）
      if (get().drawingId) return
      const was = get().specBinding
      if (get().bindingCaptured && sameDraftBinding(was, binding)) return
      // 新的一份：旧期刊的栏宽 / 字号不能挂在新名字下面，先清；不碰 `answers` / `autoPicks`——这不是用户动了下拉
      set({ specBinding: binding ? structuredClone(binding) : null, bindingCaptured: true, figureStyle: null, specFallback: false })
      if (!get().selected) return
      const ph = get().phase
      // 有候选（或正在出 / 刚失败 / 没画出来）才有东西可重出；读表中的那一轮出候选时自己会读到新的绑定，其余阶段只存着
      if (ph === 'ready' || ph === 'loading' || ph === 'nothing' || ph === 'failed') reask((a) => a, {}, true)
    },

    cancelRead: async () => {
      const { phase, selected, readRid } = get()
      if (phase !== 'reading' && phase !== 'loading') return
      const ep = epoch
      const mine = runSeq
      /** 退路：取消端点不可用 / 请求还没登记上——前端不再等这次请求（后端会自己跑完，结果被丢掉） */
      const walkAway = () => {
        if (ep !== epoch || mine !== runSeq) return
        readCtl?.abort()
        readCtl = null
        runSeq += 1
        proposeSeq += 1
        commitRid = null
        discardSession()
        if (selected) overviewSeq.set(overviewKeyOf(selected, get().headerRows), (overviewSeq.get(overviewKeyOf(selected, get().headerRows)) ?? 0) + 1)
        markCancelled()
      }
      /** 取消请求回来得晚（或失败）时原请求可能已经读完、候选都出来了：那就以原请求为准，不能再把好结果换成「已取消」 */
      const stillWaiting = () => get().phase === 'reading' || get().phase === 'loading'
      if (!readRid) return walkAway()
      try {
        const r = await aiDataProgressCancel(readRid, currentProjectId())
        // 真的标成取消了：原请求随后回 409，由它收尾（见 `propose` / `run`）；已经读完（赛跑）就让原请求照常往下走
        if (r.cancelled) return
        if (r.state === 'none' && stillWaiting()) walkAway()
      } catch {
        if (stillWaiting()) walkAway()
      }
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
      dropAllKept()
      stopUnderstanding()
      inheritOff?.()
      inheritOff = null
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
