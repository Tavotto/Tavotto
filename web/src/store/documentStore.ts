import { create } from 'zustand'
import { sameFigureInputs, validFigureSnapshot, type FigureSnapshotSaveContext } from '@/lib/figureSnapshot'
import { figureExecutionOwnerKey } from '@/lib/capturedFigureSources'
import { perfSpan } from '@/perf/core'
import { enablePatches, produceWithPatches, type Patch } from 'immer'
import * as history from '@/lib/history'
import { rebaseToCurrentNative, sizeBasisOf, type SizeBasis } from '@/lib/panelNativeSize'
import { fetchAutosave, fetchAutosaveSummary, probeAutosave, putAutosave, type AutosaveProbe } from '@/lib/api'
import { DOC_INDEX_KEY, isForeignDocument, recordedProjectOf } from '@/lib/docOwnership'
import type { RestoreIssue } from '@/store/documentRestore'
import {
  blocksDiskWrite,
  createDiskWriter,
  hasUnsavedWork,
  type SaveIssue,
  type SaveState,
} from '@/lib/autosave/diskWriter'
import { emitActivity } from '@/lib/activity'
import { announceDocOpen } from '@/lib/docPresence'
import { currentProjectLabel } from '@/lib/projectLabel'
import {
  bindingForProject,
  forgetProjectFile,
  readProjectFile,
  writeProjectFile,
  type ProjectFileBinding,
} from '@/lib/projectFile'
import { currentProjectId } from '@/lib/session'
import { sameRules } from '@/lib/specBinding'
import { msg, t, type UiMessage } from '@/i18n'
import { newId } from '@/lib/id'
import { boundedCount, captureTelemetry, classifyEditKind } from '@/lib/telemetry'
import { documentDigest, recordDiagnosticEvent } from '@/diagnostics'
import { patchRefs } from '@/diagnostics/patches'
import { defaultCanvasName, type CanvasData, type FigureDocument, type FigureExecutionOwner, type FigureSnapshot, type PanelObject, type ProjectDocument } from '@/types/document'
import {
  SCHEMA_CURRENT,
  canvasToDoc,
  docToCanvas,
  emptyProject,
  migrateToProject,
} from '@/types/document'

enablePatches()


/** 「最近文档」条目：只存列表要显示的字段 */
export interface RecentDoc {
  id: string
  name: string
  savedAt: number
  objects: number
  /** 画布数；旧条目无此字段 = 1 */
  canvases?: number
  /**
   * 写下这一条时开着的是哪个项目（审计 T04）。索引是**跨项目共用一份**的
   * （localStorage 一个键），不记的话别的项目的文档混在列表里没人认得出。
   *
   * **旧条目没有这两个字段，而缺席就是缺席**：那是「不知道属于哪个项目」，
   * 既不能当成「属于当前项目」，也不能当成「属于别的项目」——补一个默认值
   * 就是替它编一个归属出来。
   */
  projectId?: string
  /** 写下那一刻这个项目叫什么（项目后来改名不追认） */
  projectName?: string
}

/**
 * 一条撤销历史。
 *
 * label 是**描述符**而不是翻译好的字符串：撤销栈活得比一次渲染长，存
 * "删除 3 个对象" 之后用户切到英文，历史面板与撤销 toast 里还是中文，而且
 * 再也换不回来（参数已经被拼死在字符串里）。
 * 这是运行时状态，**不进 .tavotto 文档**——文档 schema 一个字节没动。
 */
export interface HistoryEntry {
  label: UiMessage
  patches: Patch[]
  inverse: Patch[]
  /**
   * 这条改过哪些面板的 w/h、那时按哪个原生图幅量的（`lib/panelNativeSize`）。
   * 原生图幅会在历史之外被渲染同步静默改掉；撤销 / 重做打回来的 w/h 要换算到
   * 此刻的图幅上，否则缩放比错了。没改面板尺寸的条目不带。
   */
  sizeBasis?: SizeBasis[]
}

/** 非激活画布的撤销栈存放处（切换画布时换入换出） */
export interface CanvasSession {
  past: HistoryEntry[]
  future: HistoryEntry[]
}

type Recipe = (draft: FigureDocument) => void

/**
 * 事务收尾前的派生修正：`endTxn` 在压缩成一条历史**之前**依次跑一遍，改动并进这条
 * 事务。给「手势进行中不许写、手势结束时必须和手势一起进历史」的派生值用——
 * 典型是渲染回来的原生图幅（`useEngineSync`）：手势中途写进去，缩放 / 裁剪的下一帧
 * 按按下时抓的旧几何写绝对尺寸，会把它盖掉；手势结束后再 silent 写，又不在这条
 * 历史里，撤销 / 重做就不自洽。丢弃的事务不跑（回滚后由派生方自己 silent 补）。
 */
const txnFinalizers = new Set<Recipe>()

/** 登记一个事务收尾修正，返回注销函数。 */
export function registerTxnFinalizer(fn: Recipe): () => void {
  txnFinalizers.add(fn)
  return () => {
    txnFinalizers.delete(fn)
  }
}

/**
 * 提交后的**override 目标身份**抄写（ADR 0083）：给这一次新写 / 改了值的 override
 * 抄上写它时那一版 manifest 的 `identity`。挂在 commit / txnUpdate 这两个唯一的
 * 文档写入口上，而不是散在二十来个 override 写入点里——新增一个写 override 的动作
 * 不会因为忘了抄而静默退回「按位置匹配」。抄写本身在 `lib/overrideIdentity`（纯函数），
 * 它要读渲染态，所以由渲染同步方登记（`useEngineSync`），这里不 import 渲染态。
 * 抄写的补丁并进同一条历史 / 同一个事务：撤销一次连身份一起打回。
 */
type OverrideStamper = (draft: FigureDocument, base: FigureDocument, next: FigureDocument) => void
let overrideStamper: OverrideStamper | null = null

/** 登记 override 身份抄写，返回注销函数（只注销自己登记的那一个）。 */
export function registerOverrideStamper(fn: OverrideStamper): () => void {
  overrideStamper = fn
  return () => {
    if (overrideStamper === fn) overrideStamper = null
  }
}

/**
 * 一次提交里的 override **从哪来**（ADR 0083）——由调用方显式说，不从数据里推断：
 *
 * - `'edit'`（默认）：用户此刻的编辑，新写 / 改了值的条目抄身份；
 * - `'restored'`：从存储原样读回来的一整份（布局版本、写回历史、写回基线）。这些条目的
 *   身份是**它们写下那一刻**的事实，按此刻的 manifest 重抄等于把旧编辑按位置绑到新对象上
 *   （#602 评审 P1：同一 (gid, prop) 在当前文档与要恢复的版本里记着同一个身份、只是值不同
 *   ——最常见的恢复——看起来和「原地改值」一模一样，推断不出来）。整次提交不抄，
 *   存的是什么就是什么，没带身份的也照旧不带（按位置，与引入身份之前一致）。
 */
export type CommitOptions = { overrides?: 'edit' | 'restored'; undoAlso?: Recipe }

/** `produceWithPatches` + 身份抄写：两段补丁按时序拼成一份（反向补丁倒序）。 */
function produceStamped(
  base: FigureDocument,
  recipe: Recipe,
  opts?: CommitOptions,
): [FigureDocument, Patch[], Patch[]] {
  const [next, patches, inverse] = produceWithPatches(base, recipe)
  if (!patches.length) return [next, patches, inverse]
  const stamp = opts?.overrides === 'restored' ? null : overrideStamper
  const [stamped, more, moreInverse] = produceWithPatches(next, (d) => {
    stamp?.(d, base, next)
    clearChangedFigureSnapshots(d, base)
  })
  return [stamped, [...patches, ...more], [...moreInverse, ...inverse]]
}

/** 页面坐标里的一个点（mm）。 */
export type TxnAnchor = { x: number; y: number }

/**
 * 进行中手势登记的**锚点**：收尾修正改对象尺寸时，按它反推 x/y，让手势刻意
 * 固定住的那一点在页面上不动（缩放 = 被拖手柄的对边，裁剪 = 整图锚点）。
 * 由手势在开事务之后登记，只在这一个事务里有效：开新事务、事务结束（含丢弃）都清空。
 * 没登记的对象，收尾修正按默认锚点（包围盒左上角）换算。
 */
const txnAnchors = new Map<string, TxnAnchor>()

export function setTxnAnchor(objectId: string, anchor: TxnAnchor): void {
  if (useDocumentStore.getState().txn) txnAnchors.set(objectId, anchor)
}

/** 收尾修正读：当前事务里这个对象登记的锚点（没有 = undefined）。 */
export function txnAnchorOf(objectId: string): TxnAnchor | undefined {
  return txnAnchors.get(objectId)
}

/* -------------------------------------------------------------------------- */
/*  恢复门（文档恢复可靠性，CONTRACT 2.4）                                       */
/*                                                                            */
/*  「项目 ready」与「文档 ready」是两件事：工作台挂载、项目认领完成时，上次的     */
/*  排版往往还在路上（last / 自动保存 GET、重试、等用户选）。这段时间内存里的     */
/*  是一份**占位文档**——不是用户的文档，它不许写 currentDoc / 最近文档 / 归属 /    */
/*  last / 时间线，也不许排队自动保存；所有能写文档的入口（commit、事务、撤销、    */
/*  画布增删改……）一律看这扇门。                                                */
/*                                                                            */
/*  门是模块级的一个值（再镜像到 store 上一个只读字段给界面用）：                  */
/*    restoring  某次恢复在路上（持当前代次令牌的安装才能写）                      */
/*    needs_user 恢复没成、等用户处理（占位文档仍不许写；出口是重试 / 打开别的 /   */
/*               继续新建）                                                      */
/*    ready      文档已定（恢复成功 / 显式准备 / 确认没有历史 / 用户明确新建）      */
/*                                                                            */
/*  **离开旧文档的冲刷在设门之前做**（`resetForNewProject` 第一步），DiskWriter 已   */
/*  排的队不清；门只管「还没准备好的目标文档」。                                   */
/* -------------------------------------------------------------------------- */

export type RestoreReason =
  | 'restored'
  | 'prepared'
  | 'explicit'
  | 'empty'
  | 'blank_requested'
  | 'continued_new'
  | 'failed'
  | 'superseded'
  | 'initial'

export interface RestoreGate {
  state: 'restoring' | 'ready' | 'needs_user'
  /** 恢复代次：每开始一次恢复 / 每次被显式意图取代都前进 */
  gen: number
  /** 这扇门说的是哪个项目（没开项目 = null） */
  pj: string | null
  /** 落定时开着的文档（恢复中为 null） */
  docId: string | null
  reason: RestoreReason
}

/** 提交权：只有持**当前代次**令牌的安装才能在门关着时写 */
export interface RestoreToken {
  gen: number
  pj: string | null
}

export type DocumentReadiness = 'ready' | 'superseded' | 'timeout'

/** 初值 `ready`：playground / 嵌入画布 / 单测不经启动链，不该被一扇没人开的门卡住 */
let gate: RestoreGate = { state: 'ready', gen: 0, pj: null, docId: null, reason: 'initial' }
let gateSeq = 0
/** 正在用令牌安装文档（那一刻订阅方看到的是真文档，不是占位） */
let installingUnderToken = false
/** 令牌发出时占位文档的身份（`persistedIdentity`）：门关着期间它被绕过门改过就让位 */
let tokenIdentity: { gen: number; identity: readonly unknown[] } | null = null

export const getRestoreGate = (): RestoreGate => gate

/** 当前文档是不是还没准备好的占位（门没开，且不是令牌安装的那一刻） */
export const isDocumentPlaceholder = (): boolean => gate.state !== 'ready' && !installingUnderToken

interface ReadyWaiter {
  pj: string | null
  resolve: (r: DocumentReadiness) => void
  timer: ReturnType<typeof setTimeout> | undefined
}
const readyWaiters = new Set<ReadyWaiter>()

function releaseWaiters(): void {
  for (const w of [...readyWaiters]) {
    let verdict: DocumentReadiness | null = null
    if (gate.state === 'ready') {
      // `initial` 的门不属于任何项目：没有恢复在路上，谁来问都是 ready
      verdict = gate.reason === 'initial' || gate.pj === w.pj ? 'ready' : 'superseded'
    } else if (gate.pj !== w.pj) {
      verdict = 'superseded'
    }
    if (!verdict) continue
    readyWaiters.delete(w)
    clearTimeout(w.timer)
    w.resolve(verdict)
  }
}

function setGate(next: RestoreGate): void {
  gate = next
  useDocumentStore.setState({ restoreGate: next })
  releaseWaiters()
}

/**
 * 仅给用例：把门放回「还没有任何恢复发生过」的初始态。一个用例要模拟「又一次全新启动」时调
 * （真实的全新启动是模块重新求值，门天然是初值）。清掉等待者与令牌身份，不碰文档本身。
 */
export function resetRestoreGateForTests(): void {
  for (const w of readyWaiters) clearTimeout(w.timer)
  readyWaiters.clear()
  tokenIdentity = null
  installingUnderToken = false
  useDocumentStore.setState({ restoreIssue: null, restoreMetaIssue: null })
  setGate({ state: 'ready', gen: ++gateSeq, pj: null, docId: null, reason: 'initial' })
}

/** 门等待的上限：`needs_user` 期间一直等用户，最长这么久（交接据此给终局） */
export const DOCUMENT_READY_TIMEOUT_MS = 120_000

/**
 * 等「`pj` 这个项目的文档」准备好。门 ready 且 pj 相同 → `'ready'`；被取代 / 切了项目 → `'superseded'`；
 * `needs_user` 期间继续等，最长 `timeoutMs` → `'timeout'`。
 */
export function whenDocumentReady(
  pj: string | null,
  opts: { timeoutMs?: number } = {},
): Promise<DocumentReadiness> {
  if (gate.state === 'ready') {
    return Promise.resolve(gate.reason === 'initial' || gate.pj === pj ? 'ready' : 'superseded')
  }
  if (gate.pj !== pj) return Promise.resolve('superseded')
  return new Promise<DocumentReadiness>((resolve) => {
    const w: ReadyWaiter = { pj, resolve, timer: undefined }
    w.timer = setTimeout(() => {
      readyWaiters.delete(w)
      resolve('timeout')
    }, opts.timeoutMs ?? DOCUMENT_READY_TIMEOUT_MS)
    readyWaiters.add(w)
  })
}

/** 令牌还是当前代次、门也还在「恢复中」（别的意图取代了 / 已落定 = 不再有提交权） */
export const isRestoreTokenCurrent = (token: RestoreToken): boolean =>
  gate.state === 'restoring' && gate.gen === token.gen && gate.pj === token.pj

/**
 * 开始一次恢复：门关上，代次前进（旧令牌从此无提交权）。冷启动 / 切项目 / 重试都走这一个入口。
 * 旧文档的冲刷必须在调它**之前**做完（见本节说明）。
 */
export function beginRestore(pj: string | null): RestoreToken {
  const token: RestoreToken = { gen: ++gateSeq, pj }
  tokenIdentity = null
  useDocumentStore.setState({ restoreIssue: null })
  setGate({ state: 'restoring', gen: token.gen, pj, docId: null, reason: 'initial' })
  return token
}

/** 把占位文档此刻的身份记给这个令牌：之后若被绕过门改过（身份变了），这次安装让位 */
export function armRestoreToken(token: RestoreToken): void {
  if (!isRestoreTokenCurrent(token)) return
  tokenIdentity = { gen: token.gen, identity: persistedIdentity(useDocumentStore.getState()) }
}

/** 令牌有提交权，且占位文档没有被绕过门改过 */
export function canCommitRestore(token: RestoreToken): boolean {
  if (!isRestoreTokenCurrent(token)) return false
  if (tokenIdentity?.gen === token.gen) {
    const now = persistedIdentity(useDocumentStore.getState())
    if (now.some((v, i) => v !== tokenIdentity!.identity[i])) return false
  }
  return true
}

export type SettleOutcome =
  | { state: 'ready'; reason: Exclude<RestoreReason, 'initial'> }
  | { state: 'needs_user'; issue: RestoreIssue }

/**
 * 恢复落定。令牌过期（被显式意图 / 新一轮恢复取代）返回 false，什么都不改。
 * `ready` 且文档仍是占位（确认没有历史 / 用户要求空白启动）：占位在这一刻转正——此前它没有归属、
 * 没有身份广播，转正才补上（同 `switchDocument` 换进来的那一刻）。
 */
export function settleRestore(token: RestoreToken, outcome: SettleOutcome): boolean {
  if (!isRestoreTokenCurrent(token)) return false
  const docId = useDocumentStore.getState().documentId
  if (outcome.state === 'ready') {
    // 占位转正：确认没有历史 / 开空白 / 让位给绕过门写进来的那份内存文档（后者此前同样没有归属）
    if (outcome.reason === 'empty' || outcome.reason === 'blank_requested' || outcome.reason === 'superseded') {
      adoptDocumentOwner(docId)
      announceDocOpen(docId)
    }
    tokenIdentity = null
    setGate({ state: 'ready', gen: token.gen, pj: token.pj, docId, reason: outcome.reason })
    return true
  }
  useDocumentStore.setState({ restoreIssue: outcome.issue })
  setGate({ state: 'needs_user', gen: token.gen, pj: token.pj, docId: null, reason: 'failed' })
  return true
}

/** 落定之后给同一代次的门改个更准的理由（教程 prepare：普通切换先取代成 `explicit`，确认后改记 `prepared`） */
export function relabelReadyGate(gen: number, reason: RestoreReason): void {
  if (gate.state !== 'ready' || gate.gen !== gen) return
  setGate({ ...gate, reason })
}

/**
 * 显式意图（新建 / 打开排版 / 打开最近 / 教程 prepare / 继续新建 / 选中候选）取代旧恢复：
 * 代次前进、门置 ready。门本来就开着时什么都不动——已经定了的恢复记录不被后来的普通切换改写。
 */
export function supersedeRestore(docId: string, reason: RestoreReason): void {
  if (gate.state === 'ready') return
  tokenIdentity = null
  useDocumentStore.setState({ restoreIssue: null })
  setGate({ state: 'ready', gen: ++gateSeq, pj: currentProjectId(), docId, reason })
}

/**
 * 项目没了 / 回到 Picker 且不再等这次恢复（`dropProject`）：门放成 ready（占位已经没有用户可达的界面），
 * 等待者拿 `superseded`。
 */
export function releaseRestoreGate(): void {
  if (gate.state === 'ready') return
  tokenIdentity = null
  useDocumentStore.setState({ restoreIssue: null })
  setGate({ state: 'ready', gen: ++gateSeq, pj: null, docId: null, reason: 'superseded' })
}

/** 门关着时被拒的写入：`needs_user` 时由 `documentRestore` 登记的通知器说一句（自带节流） */
let blockedWriteNotifier: (() => void) | null = null
export function onBlockedWrite(fn: (() => void) | null): void {
  blockedWriteNotifier = fn
}

/** 用户动作被拒时说一句（`needs_user`；自带节流，登记在 `documentRestore`）。用户级入口在门前自己拦时用 */
export function noteBlockedWrite(): void {
  blockedWriteNotifier?.()
}

/** 写入口的门：占位文档不许写。`notify` = 用户动作被拒，要说一句；派生写入静默 */
function writesBlocked(notify = true): boolean {
  if (!isDocumentPlaceholder()) return false
  if (notify && gate.state === 'needs_user') blockedWriteNotifier?.()
  return true
}

/** 保存元数据（下次自动恢复用的 last / owner）没写成：与 `saveState` 正交 */
export interface RestoreMetaIssue {
  docId: string
  pj: string | null
}

type PersistedListener = (docId: string, pj: string | null, metadata: PersistedMetadata) => void
export type PersistedMetadata = 'ok' | 'failed' | 'owner_unknown' | undefined
const persistedListeners = new Set<PersistedListener>()

/**
 * 某份文档的**正文**真的写盘成功了（不看是不是当前文档，pj 是排队那一刻的）。
 * 给「保存成功后补记 last」这类元数据落账用；返回注销函数。UI 那一半仍按 `isCurrentDoc` 守。
 */
export function onDocumentPersisted(fn: PersistedListener): () => void {
  persistedListeners.add(fn)
  return () => {
    persistedListeners.delete(fn)
  }
}

interface DocumentState {
  /** 当前激活画布的活跃编辑态（schema 2 形状；画布编辑代码只认它） */
  doc: FigureDocument
  /** 本次编辑会话的项目文档身份；自动保存槽位、最近文档、版本都按它索引 */
  documentId: string
  /** 项目级元数据（schema 3 顶层）；画布名在 doc.name / canvases[].name */
  projectMeta: { id: string; name: string; createdAt: number }
  /** 全部画布（数组序 = 显示顺序）；激活画布的内容以 doc 为准，此处为最后同步的快照 */
  canvases: CanvasData[]
  activeCanvasId: string
  canvasSessions: Record<string, CanvasSession>
  /**
   * 打开的画布标签（Tab ≠ 画布列表：关标签不删画布）。
   * 按 documentId 持久化在本机，刷新后恢复；激活画布必然在其中。
   */
  openTabs: string[]
  /** 有改动尚未写入本机自动保存 */
  dirty: boolean
  /**
   * 保存生命周期（R-06）。**它是文档的一个字段，不是一次通知**——改造前
   * `saving` / `save_error` / `conflict` 都只是一个 4.5 秒后自动消失的状态条，
   * 刷新即丢，而磁盘上那份可能已经落后半小时。见文件末尾 `SaveState`。
   */
  saveState: SaveState
  /** `saveState` 说卡在哪一步，它说卡的是什么；保存没卡住时为 null */
  saveIssue: SaveIssue | null
  /**
   * 与保存进度**正交**的一件待裁决的事（未决的恢复副本 / 那份文档读不了）。
   * 单独一根轴，不进 `saveState`——见 `DocNotice` 的说明。
   */
  docNotice: DocNotice | null
  /** 恢复门的镜像（只读，给界面用；写入口只在本文件的 `setGate`） */
  restoreGate: RestoreGate
  /**
   * 上次的排版没能恢复、等用户处理的那件事（一句话 + 一个主按钮，见顶栏芯片）。与 `saveState` 正交：
   * 一次 `saved → clean` 清不掉它；只有恢复成功 / 用户选了出口才清。
   */
  restoreIssue: RestoreIssue | null
  /** 正文存下了、但「下次自动恢复」要用的元数据没更新（与 `saveState` 正交，下次保存成功清掉） */
  restoreMetaIssue: RestoreMetaIssue | null
  /**
   * 「整体换文档」的代次，每次载入 +1（磁盘恢复 / 载入布局 / 切项目）。
   *
   * 自动保存的订阅靠它把**载入**与**编辑**区分开。两者都会换掉 `doc` 与
   * `canvases`，光看引用变化分不出来；而把一次载入当成编辑的后果是：刚打开
   * 一个文档，1 秒后它就被原样重写一遍并带上新的 `updatedAt`——另一个标签页
   * 开着同一份时，这一下足以撞出 `stale_write`。
   * 载入路径自己会在同一次 set 里写 `dirty: false`，那正是「这不是一次编辑」
   * 的声明，订阅不该把它翻回来。
   */
  loadSeq: number
  /**
   * 「外部派生元数据同步」的代次，每次 `applyDerivedUpdate()` +1。
   *
   * 与 `loadSeq` 是同一类东西——都是给自动保存的订阅回答「刚才那次 doc 变化
   * 是什么性质」。三种性质各有各的处置：
   *
   * | 性质 | dirty | saveState | 历史 | 落盘 |
   * | --- | --- | --- | --- | --- |
   * | 载入（loadSeq+1） | 由载入方声明 | 由载入方声明 | 清空 | 不排队 |
   * | 用户编辑 | 置位 | 推成 dirty | 进 | 排队 |
   * | 派生同步（derivedSeq+1） | 置位 | **不动** | 不进 | 排队 |
   *
   * 派生同步那一行的两个「不」是有代价地选出来的：`script` 是**存进文档的
   * 字段**，只改内存不落盘的话，用户下次打开这份文档，面板又回到不可编辑
   * ——所以必须排队落盘。但外部编辑器改了一个脚本不是用户在 Tavotto 里的
   * 编辑，把状态推成「未保存」会让关闭保护无端拦人，而那句拦人的话说的是
   * 一件用户没做过的事。写盘本身照常走状态机（`saving` / `save_error` 一个
   * 不吞）——那是"这次写成了没有"，用户必须看得见。
   */
  derivedSeq: number
  /** Already-persisted snapshot receipts merged from a save response; do not autosave them again. */
  snapshotSeq: number
  /** 上次写入本机自动保存的时间戳 */
  lastPersisted: number | null
  /**
   * 这份排版绑定的项目文件（ADR 0096，`lib/projectFile.ts`）：⌘S 写回的目标。
   * 与 `saveState` 是**两根轴**——`saveState` 说本机自动保存走到哪一步，这里的
   * `dirty` 说项目里那份文件是不是落后于工作副本。存的是这份排版的原始绑定，
   * 属于别的项目时由 `activeProjectFile()` 滤掉（不抹掉）。
   */
  projectFile: ProjectFileBinding | null
  /** 本机自动保存过的文档（含当前文档），按最近保存时间倒序 */
  recentDocs: RecentDoc[]
  past: HistoryEntry[]
  future: HistoryEntry[]
  /** 进行中的拖动事务：pointerdown 开启，pointerup 合并成一条历史 */
  txn: { label: UiMessage; patches: Patch[]; inverse: Patch[] } | null

  /**
   * 一次用户操作 = 一条历史记录。`opts.overrides === 'restored'`：这次写进去的 override
   * 是从存储原样读回来的，身份不按此刻重抄（`CommitOptions`）。
   *
   * `opts.undoAlso`：撤销这一条时，除了回到操作之前，**再**落下这几处改动（重做时先把它们撤掉、
   * 再重放操作）——两者都在这一条历史的补丁里，撤销 / 重做 / 再撤销都一致。用于「撤销之后
   * 的状态不等于操作之前」的写入：撤销一次样式修改，画布回到旧值并标成「已脱离样式」
   * （ADR 0081 §十二）。只用于独立的一条历史：事务开着时先收尾
   */
  commit: (label: UiMessage, recipe: Recipe, opts?: CommitOptions) => void
  /** 不进历史的即时修改（仅在事务中使用） */
  beginTxn: (label: UiMessage) => void
  txnUpdate: (recipe: Recipe) => void
  endTxn: (opts?: { discard?: boolean }) => void

  undo: () => UiMessage | null
  redo: () => UiMessage | null
  canUndo: () => boolean
  canRedo: () => boolean

  /** 不进历史的写入：用于文字自适应高度这类由渲染反推的派生值 */
  silent: (recipe: Recipe) => void

  /**
   * 把一笔改动**补进 `entry` 那条历史**（撤销时两笔一起退）。只在 `entry` 仍是最后
   * 一条、且没有进行中的事务时成立，否则什么都不做、返回 false——用户在那之后又
   * 做了别的（或撤销了它），补进去就会让一次撤销退掉两件不相干的事。
   * 用处：松手后按权威渲染的实测补正一次落点（图例整体缩放钉对角）。
   */
  amendLast: (entry: HistoryEntry, recipe: Recipe) => boolean

  /* ---------------- 画布（Canvas）操作 ---------------- */
  /** 当前完整项目文档快照（激活画布从 doc 同步） */
  buildProject: () => ProjectDocument
  /** 切换激活画布：撤销栈随画布换入换出 */
  switchCanvas: (id: string) => void
  /** 新建空白画布（页面尺寸沿用当前画布）并切换过去；返回新画布 id */
  addCanvas: (name?: string) => string
  /** 重命名画布；激活画布走可撤销 commit，非激活直接写快照 */
  renameCanvas: (id: string, name: string) => void
  /** 复制画布（对象/成组/布局组的 id 全部换新）；返回新画布 id */
  duplicateCanvas: (id: string) => string | null
  /** 删除画布（最后一张不可删；删除不可撤销，UI 层必须确认） */
  deleteCanvas: (id: string) => boolean
  reorderCanvases: (from: number, to: number) => void
  renameProject: (name: string) => void
  /** 打开画布标签（不存在则加入）并切换过去 */
  openCanvasTab: (id: string) => void
  /** 关闭标签（画布保留）；关掉激活标签切到邻居；最后一个标签不可关 */
  closeCanvasTab: (id: string) => boolean
  reorderTabs: (from: number, to: number) => void

  /**
   * 整体替换文档的**唯一入口**（新建 / 载入布局文件 / 切回最近文档都经过它）。
   * 接受 schema 2（自动迁移为单画布项目）或 schema 3。
   * 换文档前先把当前文档冲刷成本机快照，所以切走的文档总能从「最近文档」取回；
   * 只有快照确实失败、且当前文档有内容时才回调 confirmLoss 征求同意。
   * 返回 false = 用户取消或载荷无法识别，文档未切换。
   */
  switchDocument: (
    next: FigureDocument | ProjectDocument,
    nextId: string,
    confirmLoss?: () => Promise<boolean>,
    opts?: SwitchOptions,
  ) => Promise<boolean>
}

/**
 * 换文档的三种性质：
 *  - 普通（默认）：显式意图，**取代**还在路上的恢复（门置 ready，旧恢复回来没有提交权）；
 *  - `placeholder`：切项目时换上的占位空白——不写 currentDoc / 最近文档 / 归属 / 身份广播，
 *    不冲刷（旧文档的冲刷在设门之前已经做了）；门关着，它不是用户的文档；
 *  - `restoreToken`：恢复协调自己的安装（持当前代次令牌才成立，过期回 false）。
 * `reason` 只在「普通」取代一次未落定的恢复时用（默认 `explicit`）。
 */
export interface SwitchOptions {
  placeholder?: boolean
  restoreToken?: RestoreToken
  reason?: Exclude<RestoreReason, 'initial'>
  /** 显式找回在取代恢复门、确定新文档归属前核实并纠正本机索引；拒绝时保留旧文档。 */
  beforeInstall?: () => Promise<boolean>
}

function pushHistory(state: DocumentState, entry: HistoryEntry): Partial<DocumentState> {
  const stacks = history.push(state.past, entry)
  // 匿名用量统计**唯一**的编辑埋点。挂在这里而不是散落在各个控件上：
  // 一次拖动 = 一条事务 = 一条历史 = **一个事件**，而不是 120 次 pointermove；
  // 而且 commit 与 endTxn 都汇到这一个函数，新增编辑动作自动被覆盖。
  // 发出去的只有「哪一类」和「几条补丁」——分类查的是开发者写死的标签 key
  // （闭表，落不到就是 other），补丁内容一个字节都不参与。
  captureTelemetry('figure_edit_completed', {
    edit_kind: classifyEditKind(entry.label?.key),
    patch_count: boundedCount(entry.patches.length),
  })
  // 本地活动信号（不是遥测）：教程要知道「一条真实的编辑事务落进了历史」。
  // 只带开发者写死的历史 key，不带补丁、不带对象。
  emitActivity({ kind: 'history.pushed', label: entry.label?.key ?? '' })
  return stacks
}

/* -------------------------------------------------------------------------- */
/*  诊断（ADR 0016）：历史平面的三个状态边界都从这里出事件                        */
/*                                                                            */
/*  挂在 store 里而不是各个调用点上，理由与上面那条埋点一致：commit / endTxn /   */
/*  undo / redo 是**唯一**能改历史的四个入口，挂在这里就不存在「新增了一个动作、 */
/*  忘了记诊断」。记的全是结构与计数——补丁的 value、历史标签的插值参数         */
/*  （里面是用户的文件名与属性值）一个字都不进去。                               */
/* -------------------------------------------------------------------------- */

/** 一次 commit 的诊断记录。`next` 是**应用补丁之后**的文档：override 的
 *  gid/prop 要在新文档里才查得到（新增一条时旧文档里那个下标还不存在）。 */
function noteCommit(
  label: UiMessage,
  before: DocumentState,
  next: FigureDocument,
  patches: Patch[],
  intoTxn: boolean,
): void {
  recordDiagnosticEvent({
    type: 'document.commit',
    label_key: label.key,
    patch_count: patches.length,
    past_count: before.past.length,
    future_count: before.future.length,
    txn_open: intoTxn,
    document_hash_before: documentDigest(before.doc),
    document_hash_after: documentDigest(next),
    patches: patchRefs(before.doc, next, patches),
  })
}

/** undo / redo 的收尾。`ok=false` 覆盖两种情况：栈空，以及补丁应用失败被丢弃 */
function noteUndoRedo(
  type: 'undo.complete' | 'redo.complete',
  ok: boolean,
  label: UiMessage | null,
  hashBefore: string,
  after: DocumentState,
): void {
  recordDiagnosticEvent({
    type,
    ok,
    label_key: label?.key ?? '',
    past_count: after.past.length,
    future_count: after.future.length,
    document_hash_before: hashBefore,
    document_hash_after: documentDigest(after.doc),
  })
}

const INITIAL = emptyProject()

export const useDocumentStore = create<DocumentState>((set, get) => ({
  doc: canvasToDoc(INITIAL.canvases[0]),
  documentId: newId('d'),
  projectMeta: {
    id: INITIAL.project.id,
    name: INITIAL.project.name,
    createdAt: INITIAL.createdAt,
  },
  canvases: INITIAL.canvases,
  activeCanvasId: INITIAL.activeCanvasId,
  canvasSessions: {},
  openTabs: [INITIAL.activeCanvasId],
  dirty: false,
  saveState: 'clean',
  saveIssue: null,
  docNotice: null,
  restoreGate: gate,
  restoreIssue: null,
  restoreMetaIssue: null,
  loadSeq: 0,
  derivedSeq: 0,
  snapshotSeq: 0,
  lastPersisted: null,
  projectFile: null,
  recentDocs: [],
  past: [],
  future: [],
  txn: null,

  commit: (label, recipe, opts) => {
    if (writesBlocked()) return
    if (opts?.undoAlso && get().txn) get().endTxn()
    const state = get()
    const [next, patches, inverse] = produceStamped(state.doc, recipe, opts)
    if (!patches.length) return
    if (opts?.undoAlso) {
      // 撤销落在「操作之前 + undoAlso」上：inverse 之后接 extra；重做先 extraInverse 回到操作之前
      const [, extra, extraInverse] = produceWithPatches(state.doc, opts.undoAlso)
      const entry = { label, patches: [...extraInverse, ...patches], inverse: [...inverse, ...extra] }
      set({ doc: next, ...pushHistory(state, entry) })
      noteCommit(label, state, next, patches, false)
      return
    }
    if (state.txn) {
      // 事务进行中的结构性操作也并入当前事务
      set({ doc: next, txn: history.accumulate(state.txn, patches, inverse) })
      noteCommit(label, state, next, patches, true)
      return
    }
    const sizeBasis = sizeBasisOf(state.doc, next, patches)
    set({
      doc: next,
      ...pushHistory(state, { label, patches, inverse, ...(sizeBasis.length ? { sizeBasis } : {}) }),
    })
    noteCommit(label, state, next, patches, false)
  },

  amendLast: (entry, recipe) => {
    if (writesBlocked(false)) return false
    const state = get()
    if (state.txn || state.past.at(-1) !== entry) return false
    const [next, patches, inverse] = produceStamped(state.doc, recipe)
    if (!patches.length) return true
    // 撤销按 inverse 的顺序打回：后补的那笔先退，再退原来那条
    const merged: HistoryEntry = {
      ...entry,
      patches: [...entry.patches, ...patches],
      inverse: [...inverse, ...entry.inverse],
    }
    set({ doc: next, past: [...state.past.slice(0, -1), merged] })
    noteCommit(entry.label, state, next, patches, false)
    return true
  },

  beginTxn: (label) => {
    if (writesBlocked()) return
    // 上一个事务还开着 = 手势与离散操作混在了一起。诊断要看得见这件事：
    // 它正是「撤销一次回退了两件事」那一类现象的成因
    const replaced = get().txn != null
    if (replaced) get().endTxn()
    txnAnchors.clear()
    set({ txn: { label, patches: [], inverse: [] } })
    recordDiagnosticEvent({
      type: 'transaction.begin',
      label_key: label.key,
      replaced_open_txn: replaced,
    })
  },

  txnUpdate: (recipe) => {
    if (writesBlocked(false)) return
    const state = get()
    if (!state.txn) {
      // 没有进行中的事务 = 这次更新没人记账。以前这里会直接 set(doc)：
      // 拖动途中事务被外部 endTxn/undo 结束后（桌面菜单的 ⌘Z 加速键就能做到），
      // 后续 pointermove 全部变成**不进历史的静默位移**——用户撤销旧条目时
      // 只有部分对象回退、且那段位移永远撤不回来（真实用户撞见过：成组的
      // 文字回到原位、图片留在新位，历史里找不到任何痕迹）。
      // 丢掉一帧拖动画面是无害的；绕过历史改文档是数据损坏。
      return
    }
    const txn = state.txn
    // 性能探针（ADR 0075）：拖动中每个 pointermove 一次，是热路径上最可疑的一段
    perfSpan('doc.txn_update', () => {
      const [next, patches, inverse] = produceStamped(state.doc, recipe)
      if (!patches.length) return
      set({ doc: next, txn: history.accumulate(txn, patches, inverse) })
    })
  },

  endTxn: (opts) => {
    const state = get()
    let txn = state.txn
    if (!txn) return
    // 收尾修正算在本地，与「事务结束」**同一次** set 落地：订阅者看到的是一次
    // 「事务已关、文档变了」的更新（事务之后的变化）。若先在事务还开着时 set 文档、
    // 再单独 set `txn: null`，只在事务外看文档的订阅者（布局组自动重排）会在前一次
    // 因事务开着跳过、后一次因文档引用没变跳过，这次尺寸变化就永远漏掉
    let doc = state.doc
    if (!opts?.discard && txn.patches.length && txnFinalizers.size) {
      for (const fn of txnFinalizers) {
        const [next, patches, inverse] = produceWithPatches(doc, fn)
        if (!patches.length) continue
        txn = history.accumulate(txn, patches, inverse)
        doc = next
      }
    }
    txnAnchors.clear()
    if (opts?.discard || !txn.patches.length) {
      // 丢弃：把反向补丁打回去，恢复到事务开始前
      const rolledBack = history.rollback(doc, txn)
      const [clean] = produceWithPatches(rolledBack, (d) => clearChangedFigureSnapshots(d, doc))
      set({ doc: clean, txn: null })
      recordDiagnosticEvent({
        type: 'transaction.cancel',
        label_key: txn.label.key,
        patch_count: txn.patches.length,
      })
      return
    }
    const [patches, inverse] = history.compress(txn.patches, txn.inverse)
    // 原生图幅基准的两侧：before = 事务开始前，after = **收尾修正跑完之后**的文档——
    // 条目记下的就是它真正打回的那个状态（包围盒值、原生图幅、维度集合同一时刻）。
    // 注：在收尾之前取 after 目前是等价的（收尾修正本身就是一次保持比例的换基，
    // 条目又连图幅一起打回，同步器随后补齐），变异反证里它不红；按「同一时刻」取
    // 是为了不依赖这个巧合。事务开始前的文档只在这里现算一次（松手时一次，
    // 不在 pointermove 的热路径上）
    const before = history.rollback(doc, { label: txn.label, patches, inverse })
    const sizeBasis = sizeBasisOf(before, doc, patches)
    // 基准与收尾修正、事务结束、压入历史同一次 set 落地
    set({
      doc,
      txn: null,
      ...pushHistory(state, {
        label: txn.label,
        patches,
        inverse,
        ...(sizeBasis.length ? { sizeBasis } : {}),
      }),
    })
    recordDiagnosticEvent({
      type: 'transaction.end',
      label_key: txn.label.key,
      patch_count: patches.length,
      past_count: get().past.length,
      document_hash_after: documentDigest(get().doc),
    })
  },

  undo: () => {
    if (writesBlocked()) return null
    const state = get()
    recordDiagnosticEvent({
      type: 'undo.request',
      past_count: state.past.length,
      future_count: state.future.length,
      txn_open: state.txn != null,
    })
    if (state.txn) state.endTxn()
    const before = documentDigest(get().doc)
    // 补丁按路径应用：万一与当前文档对不上（历史损坏），扔掉这一条并保持
    // 文档不动，绝不能让栈和文档进入半应用的错位状态（算法在 lib/history）
    const step = history.undoStep(get().doc, get().past, get().future)
    if (!step.ok && step.entry) console.error('撤销补丁应用失败，该条历史已丢弃')
    // 栈空时什么都不写：空撤销不该换掉 past / future 的引用去惊动订阅者
    // 打回来的 w/h 按条目之前的原生图幅量；图幅在历史之外变过的话换算到此刻的图幅
    const candidate = step.ok ? rebaseToCurrentNative(step.doc, step.entry?.sizeBasis, 'before') : step.doc
    const [doc] = produceWithPatches(candidate, (d) => clearChangedFigureSnapshots(d, get().doc))
    if (step.ok || step.entry) set({ doc, past: step.past, future: step.future })
    noteUndoRedo('undo.complete', step.ok, step.entry?.label ?? null, before, get())
    return step.ok && step.entry ? step.entry.label : null
  },

  redo: () => {
    if (writesBlocked()) return null
    const state = get()
    recordDiagnosticEvent({
      type: 'redo.request',
      past_count: state.past.length,
      future_count: state.future.length,
      txn_open: state.txn != null,
    })
    const before = documentDigest(state.doc)
    const step = history.redoStep(state.doc, state.past, state.future)
    if (!step.ok && step.entry) console.error('重做补丁应用失败，该条历史已丢弃')
    const candidate = step.ok ? rebaseToCurrentNative(step.doc, step.entry?.sizeBasis, 'after') : step.doc
    const [doc] = produceWithPatches(candidate, (d) => clearChangedFigureSnapshots(d, state.doc))
    if (step.ok || step.entry) set({ doc, past: step.past, future: step.future })
    noteUndoRedo('redo.complete', step.ok, step.entry?.label ?? null, before, get())
    return step.ok && step.entry ? step.entry.label : null
  },

  canUndo: () => get().past.length > 0,
  canRedo: () => get().future.length > 0,

  silent: (recipe) => {
    if (writesBlocked(false)) return
    const base = get().doc
    const [next, patches] = produceWithPatches(base, recipe)
    const [clean] = produceWithPatches(next, (d) => clearChangedFigureSnapshots(d, base))
    if (patches.length) set({ doc: clean })
  },

  /* ---------------- 画布（Canvas）操作 ---------------- */

  buildProject: () => {
    const s = get()
    return {
      schema: 3,
      project: { id: s.projectMeta.id, name: s.projectMeta.name },
      canvases: s.canvases.map((c) =>
        c.id === s.activeCanvasId ? docToCanvas(s.doc, c.id) : c,
      ),
      activeCanvasId: s.activeCanvasId,
      createdAt: s.projectMeta.createdAt,
      updatedAt: Date.now(),
    }
  },

  switchCanvas: (id) => {
    if (writesBlocked()) return
    const s0 = get()
    if (id === s0.activeCanvasId || !s0.canvases.some((c) => c.id === id)) return
    if (s0.txn) s0.endTxn()
    const s = get()
    const target = s.canvases.find((c) => c.id === id)!
    set({
      canvases: s.canvases.map((c) =>
        c.id === s.activeCanvasId ? docToCanvas(s.doc, c.id) : c,
      ),
      canvasSessions: {
        ...s.canvasSessions,
        [s.activeCanvasId]: { past: s.past, future: s.future },
      },
      doc: canvasToDoc(target),
      activeCanvasId: id,
      openTabs: s.openTabs.includes(id) ? s.openTabs : [...s.openTabs, id],
      past: s.canvasSessions[id]?.past ?? [],
      future: s.canvasSessions[id]?.future ?? [],
      txn: null,
    })
    persistTabs()
  },

  openCanvasTab: (id) => {
    if (writesBlocked()) return
    const s = get()
    if (!s.canvases.some((c) => c.id === id)) return
    if (!s.openTabs.includes(id)) {
      set({ openTabs: [...s.openTabs, id] })
    }
    get().switchCanvas(id)
    persistTabs()
  },

  closeCanvasTab: (id) => {
    if (writesBlocked()) return false
    const s = get()
    const idx = s.openTabs.indexOf(id)
    if (idx < 0 || s.openTabs.length <= 1) return false
    if (id === s.activeCanvasId) {
      const neighbor = s.openTabs[idx + 1] ?? s.openTabs[idx - 1]
      get().switchCanvas(neighbor)
    }
    set((st) => ({ openTabs: st.openTabs.filter((t) => t !== id) }))
    persistTabs()
    return true
  },

  reorderTabs: (from, to) => {
    if (writesBlocked()) return
    const s = get()
    if (from === to || from < 0 || from >= s.openTabs.length) return
    const openTabs = [...s.openTabs]
    const [moved] = openTabs.splice(from, 1)
    openTabs.splice(Math.max(0, Math.min(to, openTabs.length)), 0, moved)
    set({ openTabs })
    persistTabs()
  },

  addCanvas: (name) => {
    // 占位文档不许加画布；回当前激活画布 id，调用方（切换 / 取景）落在原地，不会拿到一个不存在的 id
    if (writesBlocked()) return get().activeCanvasId
    const s = get()
    const id = newId('c')
    const canvas: CanvasData = {
      id,
      name: name?.trim() || nextCanvasName(s.canvases),
      page: { ...s.doc.page },
      objects: [],
      guides: [],
    }
    set({ canvases: [...s.canvases, canvas] })
    get().switchCanvas(id)
    captureTelemetry('canvas_created', { creation_kind: 'blank' })
    return id
  },

  renameCanvas: (id, name) => {
    if (writesBlocked()) return
    const clean = name.trim()
    if (!clean) return
    const s = get()
    if (id === s.activeCanvasId) {
      get().commit(msg('history.renameCanvas', undefined, 'workspace'), (d) => {
        d.name = clean
      })
      return
    }
    set({
      canvases: s.canvases.map((c) => (c.id === id ? { ...c, name: clean } : c)),
    })
  },

  duplicateCanvas: (id) => {
    if (writesBlocked()) return null
    const s = get()
    const src = s.canvases.find((c) => c.id === id)
    if (!src) return null
    const synced = id === s.activeCanvasId ? docToCanvas(s.doc, id) : src
    const nid = newId('c')
    // 对象 / 成组 / 布局组的 id 全部换新：id 在项目内必须唯一
    const idMap = new Map<string, string>()
    const groupMap = new Map<string, string>()
    const objects = synced.objects.map((o) => {
      const copy = structuredClone(o)
      copy.id = newId(o.type[0])
      idMap.set(o.id, copy.id)
      if (copy.groupId) {
        if (!groupMap.has(copy.groupId)) groupMap.set(copy.groupId, newId('g'))
        copy.groupId = groupMap.get(copy.groupId)
      }
      return copy
    })
    const layoutGroups = synced.layoutGroups
      ?.filter((g) => groupMap.has(g.id))
      .map((g) => ({
        ...structuredClone(g),
        id: groupMap.get(g.id)!,
        order: g.order.map((x) => idMap.get(x)).filter((x): x is string => !!x),
      }))
    const copy: CanvasData = {
      id: nid,
      name: t('history.duplicateCanvasSuffix', { ns: 'workspace', name: synced.name }),
      page: { ...synced.page },
      objects,
      guides: structuredClone(synced.guides),
      ...(layoutGroups?.length ? { layoutGroups } : {}),
    }
    const idx = s.canvases.findIndex((c) => c.id === id)
    const canvases = [...s.canvases]
    canvases.splice(idx + 1, 0, copy)
    set({ canvases })
    captureTelemetry('canvas_created', { creation_kind: 'duplicate' })
    return nid
  },

  deleteCanvas: (id) => {
    if (writesBlocked()) return false
    const s = get()
    if (s.canvases.length <= 1 || !s.canvases.some((c) => c.id === id)) return false
    if (id === s.activeCanvasId) {
      const idx = s.canvases.findIndex((c) => c.id === id)
      const neighbor = s.canvases[idx + 1] ?? s.canvases[idx - 1]
      get().switchCanvas(neighbor.id)
    }
    const st = get()
    const sessions = { ...st.canvasSessions }
    delete sessions[id]
    set({
      canvases: st.canvases.filter((c) => c.id !== id),
      canvasSessions: sessions,
      openTabs: st.openTabs.filter((t) => t !== id),
    })
    persistTabs()
    return true
  },

  reorderCanvases: (from, to) => {
    if (writesBlocked()) return
    const s = get()
    if (from === to || from < 0 || from >= s.canvases.length) return
    const canvases = [...s.canvases]
    const [moved] = canvases.splice(from, 1)
    canvases.splice(Math.max(0, Math.min(to, canvases.length)), 0, moved)
    set({ canvases })
  },

  renameProject: (name) => {
    if (writesBlocked()) return
    const clean = name.trim()
    if (!clean) return
    const changed = clean !== get().projectMeta.name
    set((s) => ({ projectMeta: { ...s.projectMeta, name: clean } }))
    // 排版名写在项目文件里：改名之后项目里那份就旧了
    if (changed) markProjectFileDirty()
    // 改名是用户的编辑：显式打开的别的项目的排版，改了名就归到这个项目（#773 Codex 复核 P2）
    if (changed) claimDocumentOnEdit()
    // 项目名不进撤销历史，但要立即落快照（最近文档列表显示它）
    flushAutosave()
  },

  switchDocument: async (next, nextId, confirmLoss, opts) => {
    const placeholder = opts?.placeholder === true
    const token = opts?.restoreToken
    if (!placeholder) {
      const hadContent = hasContent(get().buildProject())
      if (flushAutosave() === 'error' && hadContent && confirmLoss && !(await confirmLoss())) {
        return false
      }
    }
    const pd = migrateToProject(next)
    if (!pd) return false
    if (token) {
      // 恢复协调自己的安装：令牌过期（被显式意图 / 新一轮恢复取代）就不装——旧恢复回来没有提交权。
      // 确认框（上面）是 await：它期间门可能已经换代，所以提交权在这里、同步地判
      if (!isRestoreTokenCurrent(token)) return false
    } else if (!placeholder) {
      if (opts?.beforeInstall && !(await opts.beforeInstall())) return false
      // 显式意图取代未落定的恢复（门本来就开着时什么都不动）
      supersedeRestore(nextId, opts?.reason ?? 'explicit')
    }
    // 换文档 = 挂起结束（上面那次 flush 已经按挂起跳过了，旧文档的编辑随它一起放弃）
    autosaveSuspendedFor = null
    // 归属在**换进来那一刻**定，不在落盘那一刻现问（见 `docProject`）。
    // 必须排在上面那次 flush 之后：那一次写的是**旧**文档，它属于旧项目。
    // 占位文档没有归属：转正（`settleRestore`）或被真文档取代时才定
    if (!placeholder) adoptDocumentOwner(nextId)
    const active = pd.canvases.find((c) => c.id === pd.activeCanvasId) ?? pd.canvases[0]
    if (token) installingUnderToken = true
    try {
      set({
        doc: canvasToDoc(active),
        documentId: nextId,
        projectMeta: { id: pd.project.id, name: pd.project.name, createdAt: pd.createdAt },
        canvases: pd.canvases,
        activeCanvasId: active.id,
        canvasSessions: {},
        openTabs: restoreTabs(nextId, pd.canvases, active.id),
        dirty: false,
        // 换了文档，上一份的待裁决事项跟着走：冲突是**那一份**的冲突，
        // 挂在新文档头上会让用户对着一份根本没冲突的文档点「重新加载」。
        saveState: 'clean',
        saveIssue: null,
        docNotice: null,
        restoreMetaIssue: null,
        loadSeq: get().loadSeq + 1,
        lastPersisted: null,
        projectFile: readProjectFile(nextId),
        past: [],
        future: [],
        txn: null,
      })
      if (placeholder) return true
      writeCurrentId(nextId)
      // 广播「我端着这份了」：别的标签页也开着同一个 documentId 时会回音报警
      announceDocOpen(nextId)
      // 新文档立刻落一次快照，这样它马上出现在「最近文档」里
      flushAutosave()
    } finally {
      installingUnderToken = false
    }
    return true
  },
}))

let canvasSeq = 0

/** 默认画布名：`defaultCanvasName(N)`（与空文档的第一张同一个格式），跳过已占用的名字 */
function nextCanvasName(canvases: CanvasData[]): string {
  const used = new Set(canvases.map((c) => c.name))
  for (let n = canvases.length + 1; ; n++) {
    const name = defaultCanvasName(n)
    if (!used.has(name)) return name
    if (n > canvases.length + 1000) return defaultCanvasName(++canvasSeq)
  }
}

/* --------------------------- 本机自动保存 --------------------------------- */

/**
 * 自动保存按文档隔离：一个文档一个槽位 `tavotto.autosave.<documentId>`，
 * 另有一份轻量索引 `tavotto.docIndex` 供「最近文档」列表使用（列菜单时不必反序列化
 * 每个完整文档）。`tavotto.currentDoc` 记住刷新后该恢复哪一个。
 *
 * 注意：本机自动保存与「布局文件」是两件事——前者是浏览器里的工作副本，
 * 后者是写到服务器 layouts 的命名文件。保存布局文件不会改变文档身份。
 */
const SLOT_PREFIX = 'tavotto.autosave.'
const INDEX_KEY = DOC_INDEX_KEY
const CURRENT_KEY = 'tavotto.currentDoc'
const MAX_SLOTS = 12
const DEBOUNCE_MS = 1000

export type FlushResult = 'saved' | 'empty' | 'error' | 'skipped'

/**
 * **这份文档**属于哪个项目（审计 T04）——在它被换进来那一刻定下，不是落盘
 * 那一刻现问 `currentProjectId()`。
 *
 * 切项目的顺序是「先认领新项目 → 再换空白文档」，而换文档第一句就是把旧文档
 * 冲刷落盘。落盘那一刻现问的话，旧项目最后一份文档会被记成新项目的
 * （与 `lib/session.ts` 里那两个 `*For` 变体同一条纪律：一次写入属于**排队
 * 那一刻**的那个项目）。
 *
 * 名字记的是当时那个名字，项目后来改名不追认。
 */
let docProject: { id: string | null; name: string | null } | null = null

/**
 * `null` = 这份文档还没被 `switchDocument` 换进来过（应用刚起、还停在初始的
 * 空白文档上）。那一档回落到「现在开着哪个项目」——此刻还没发生过任何项目
 * 切换，所以现问是对的。
 *
 * **不在模块初始化时求值**：`currentProjectId()` 在 import 阶段就被调用的话，
 * 谁 mock 了 `@/lib/session` 谁就会撞上自己的 TDZ（模块图里 documentStore 先
 * 于测试文件的 `let` 求值）。实测过：`useServerEvents.test.ts` 会以
 * 「Cannot access 'project' before initialization」整文件失败。
 */
const projectOfDoc = () => docProject ?? { id: currentProjectId(), name: currentProjectLabel() }

/**
 * 从「最近文档」显式打开的**别的项目**的排版：打开时归属**不变**，用户在这个项目里真改了一笔
 * 才转到这里（#715 验收 P1 / #773 Codex P2）。`null` = 没有待转的归属。
 *
 * 打开那一下 `switchDocument` 会立刻冲刷一次（为了进「最近文档」）；那次冲刷若按当前项目记
 * 归属，只是看一眼就把排版从原项目拿走了——原项目的「上次开着的」随之被后端判成别人的、
 * 这个项目又没记它，两边都恢复不出来。转去的项目是**打开时**所在的那个，不是改的那一刻
 * 现问：切项目的窗口里（先认领新项目、再冲刷旧文档）改的一笔不该把它送进下一个项目。
 */
let pendingClaim: { id: string | null; name: string | null } | null = null

/** 换进一份排版时定它的归属：确知属于别的项目就保留那个项目，等真改了才转（见 `pendingClaim`） */
function adoptDocumentOwner(docId: string): void {
  const here = { id: currentProjectId(), name: currentProjectLabel() }
  const entry = isForeignDocument(docId, here.id) ? readIndex().find((e) => e.id === docId) : undefined
  docProject = entry?.projectId ? { id: entry.projectId, name: entry.projectName ?? null } : here
  pendingClaim = entry?.projectId ? here : null
}

/** 还没换进来的排版（读盘那一刻）该记在哪个项目名下：确知属于别的项目就是那个项目，否则是当前项目 */
function ownerProjectForWrite(docId: string): string | null {
  const owner = recordedProjectOf(docId)
  return typeof owner === 'string' ? owner : currentProjectId()
}

/** 用户真改了一笔：显式打开的别的项目的排版，从这一刻起属于打开它的那个项目 */
function claimDocumentOnEdit(): void {
  const claim = pendingClaim
  if (!claim) return
  docProject = claim
  pendingClaim = null
  // 本机索引当场改记（不等下一次冲刷）：「记上次开着哪份」的判据读的就是它。只改了一笔就离开的话
  // 后面不会再有变化去触发记录（#773 Codex 复核 P2）
  const id = useDocumentStore.getState().documentId
  const index = readIndex()
  if (claim.id && index.some((e) => e.id === id)) {
    const owner = claim.id
    const kept = writeIndex(
      index.map((e): RecentDoc => {
        if (e.id !== id) return e
        const { projectName: _previous, ...rest } = e
        return { ...rest, projectId: owner, ...(claim.name ? { projectName: claim.name } : {}) }
      }),
    )
    useDocumentStore.setState({ recentDocs: kept })
  }
  claimListener?.()
}

/** 归属刚转到当前项目时要做的事（`projectStore` 登记：把它记成这个项目「上次开着的」） */
let claimListener: (() => void) | null = null

export function onDocumentClaimed(fn: (() => void) | null): void {
  claimListener = fn
}

/**
 * 切项目**认领新项目之前**调：把内存里这份排版的归属钉在此刻的项目上。
 *
 * 应用刚起、从没换过文档时归属是 `null`（= 现问 `currentProjectId()`）；切项目先认领新项目、
 * 再冲刷旧文档，那次冲刷现问的话会把旧项目的排版按新项目写进后端的槽位归属——#715 验收 P1
 * 之后后端按归属拒认别的项目的排版，回到旧项目时它上次开着的那份就被判成别人的了。
 */
export function pinDocumentOwner(): void {
  docProject ??= { id: currentProjectId(), name: currentProjectLabel() }
}

const slotKey = (id: string) => SLOT_PREFIX + id
const TABS_PREFIX = 'tavotto.tabs.'

/** 打开标签的本机持久化：{ open: canvasId[], active }，按 documentId 一档 */
function persistTabs(): void {
  const s = useDocumentStore.getState()
  try {
    localStorage.setItem(
      TABS_PREFIX + s.documentId,
      JSON.stringify({ open: s.openTabs, active: s.activeCanvasId }),
    )
  } catch {
    /* 存不下只影响「恢复上次打开的标签」 */
  }
}

/** 恢复打开标签：过滤掉已不存在的画布；空则回退激活画布 */
function restoreTabs(
  documentId: string,
  canvases: CanvasData[],
  activeId: string,
): string[] {
  try {
    const raw = localStorage.getItem(TABS_PREFIX + documentId)
    if (raw) {
      const parsed = JSON.parse(raw) as { open?: string[] }
      const ids = new Set(canvases.map((c) => c.id))
      const open = (parsed.open ?? []).filter((t) => ids.has(t))
      if (open.length) return open.includes(activeId) ? open : [...open, activeId]
    }
  } catch {
    /* 损坏当作没有 */
  }
  return [activeId]
}

const hasContent = (pd: ProjectDocument) =>
  pd.canvases.some((c) => c.objects.length > 0 || c.guides.length > 0) ||
  pd.canvases.length > 1

const countObjects = (pd: ProjectDocument) =>
  pd.canvases.reduce((n, c) => n + c.objects.length, 0)

function readCurrentId(): string | null {
  try {
    return localStorage.getItem(CURRENT_KEY)
  } catch {
    return null
  }
}

function writeCurrentId(id: string): void {
  try {
    localStorage.setItem(CURRENT_KEY, id)
  } catch {
    /* 存储不可用时只影响「刷新后恢复」，不影响正常编辑 */
  }
}

function readIndex(): RecentDoc[] {
  try {
    const raw = localStorage.getItem(INDEX_KEY)
    const arr = raw ? JSON.parse(raw) : null
    if (!Array.isArray(arr)) return []
    return arr.filter(
      (e): e is RecentDoc =>
        !!e && typeof e.id === 'string' && typeof e.savedAt === 'number',
    )
  } catch {
    return []
  }
}

/** 索引里没有的槽位一并清掉，避免 localStorage 无限增长；返回实际保留的条目 */
function writeIndex(next: RecentDoc[]): RecentDoc[] {
  const kept = next.slice(0, MAX_SLOTS)
  const keptIds = new Set(kept.map((e) => e.id))
  try {
    localStorage.setItem(INDEX_KEY, JSON.stringify(kept))
    const stale: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key?.startsWith(SLOT_PREFIX) && !keptIds.has(key.slice(SLOT_PREFIX.length))) {
        stale.push(key)
      }
    }
    for (const key of stale) localStorage.removeItem(key)
  } catch {
    /* 索引写不进去时列表会退化，但不影响已存的槽位 */
  }
  return kept
}

/* -------------------------------------------------------------------------- */
/*  保存状态机（R-06）                                                          */
/*                                                                            */
/*  改造前，"保存"这件事在本仓库是没有状态的：只有一个 `dirty` 布尔和一个       */
/*  `window` 事件。写盘失败 → 派一个 CustomEvent → App.tsx 弹一句 4.5 秒后      */
/*  自动消失的状态条 → 刷新即丢。用户回到界面时看到的是"已自动保存 14:03"，      */
/*  而磁盘上那份还停在 13:58。                                                 */
/*                                                                            */
/*  所以状态必须是**文档的一个字段**，而不是一次通知。下面这八个状态是           */
/*  唯一的真相，TopBar / 横幅 / 关闭保护全都从它读。                            */
/* -------------------------------------------------------------------------- */

export type { SaveIssue, SaveState } from '@/lib/autosave/diskWriter'
export { hasUnsavedWork } from '@/lib/autosave/diskWriter'

/**
 * 与保存进度**正交**的一件待裁决的事，同一时刻至多一件。
 *
 * - `recovery`：本机还留着一份没人裁决的副本（上次写盘没成）。当前文档照常
 *   编辑照常保存，这份副本在旁边等着。
 * - `schema_too_new`：那份文档来自更新的 Tavotto，**根本没有打开**。所以它
 *   不是「当前文档只读」——当前文档是一份崭新的空白文档，能存能改；读不了的
 *   是磁盘上的**另一份**，而它一个字节都没被动过。
 */
export type DocNotice =
  | { kind: 'recovery'; docId: string; summary: RecoverySummary }
  | { kind: 'schema_too_new'; docId: string; schema: number }

/** 待恢复的本机副本长什么样：够用户判断「这份值不值得要」，不多不少 */
export interface RecoverySummary {
  savedAt: number
  objects: number
  canvases: number
  name: string
}

/** `saved` 反馈停留多久后回到 `clean` */
const SAVED_FEEDBACK_MS = 1600
let savedTimer: number | undefined

/**
 * 保存状态的**唯一**写入口。散在各处 `setState({ saveState })` 的话，
 * `saved → clean` 的定时器就会有好几个，互相把对方的状态拽回去。
 */
function setSaveState(next: SaveState, issue: SaveIssue | null = null): void {
  window.clearTimeout(savedTimer)
  useDocumentStore.setState({ saveState: next, saveIssue: issue })
  if (next !== 'saved') return
  savedTimer = window.setTimeout(() => {
    // 反馈期内又开始编辑或又存了一次，状态已经不是 saved 了：别拽回 clean
    if (useDocumentStore.getState().saveState === 'saved') {
      useDocumentStore.setState({ saveState: 'clean' })
    }
  }, SAVED_FEEDBACK_MS)
}

/** 待裁决事项的唯一写入口（与 `setSaveState` 分开，两根轴互不覆盖） */
function setDocNotice(notice: DocNotice | null): void {
  useDocumentStore.setState({ docNotice: notice })
}

/* 磁盘为主、localStorage 为崩溃兜底：
 * flush 时同步写一份本机副本（快、抗崩溃），随即异步 PUT 到后端原子落盘，
 * 成功后删掉本机副本——稳态下 localStorage 不保存文档主体。
 *
 * 排队 / 串行 PUT / 两个基线 / 写前确认 / 冲突判定都在 `lib/autosave/diskWriter`
 * 里（它不认识 store），这里只把 store 的读写包成回调递进去。 */

/** 迟到的写入结果不该去改**别的文档**的状态（切文档、恢复都会换 id） */
const isCurrentDoc = (id: string) => useDocumentStore.getState().documentId === id

/**
 * 每份文档本会话**成功落盘**的次数（不看是不是当前文档）。手动保存据此回答「这次写成了
 * 没有」——看最终的实时 `saveState` 回答不了：写的途中又改过的话它照实是 dirty（与 #674
 * 的圆点同源），而 ⌘S 那一刻的内容其实已经在盘上了（Codex #679）。
 */
const diskWrites = new Map<string, number>()

/**
 * 写盘成功后：期间又编辑过就还是 dirty，没编辑过才是"存好了"。
 *
 * 两半，**判据不同**：
 *  - 元数据那一半（`onDocumentPersisted`：补记 last 等）属于**这份保存自己**，不看现在开着哪份——
 *    A 的排队保存晚于 B 的打开才完成，A 的槽位照样该被登记；`pj` 是排队那一刻的，不现问；
 *  - UI 那一半（`lastPersisted` / 保存状态 / `restoreMetaIssue`）只在这份仍是当前文档时才动。
 */
function afterWriteOk(
  id: string,
  savedAt: number,
  meta?: { pj?: string | null; metadata?: PersistedMetadata; figureSnapshots?: Record<string, FigureSnapshot>; snapshotContext?: FigureSnapshotSaveContext },
): void {
  if (meta?.snapshotContext) mergeSavedFigureSnapshots(meta.snapshotContext, meta.figureSnapshots)
  diskWrites.set(id, (diskWrites.get(id) ?? 0) + 1)
  const pj = meta?.pj ?? null
  for (const fn of [...persistedListeners]) {
    try {
      fn(id, pj, meta?.metadata)
    } catch {
      /* 监听方的失败不能让保存的收尾半途而废 */
    }
  }
  if (!isCurrentDoc(id)) return
  // 元数据没落成 ≠ 正文没存下：另开一根轴，不碰 saveState；下次这份保存成功时清掉
  // 回包的 pj 是排队那一刻的：A 项目的排队保存在界面切到 B（恰好同一个 documentId）之后才完成时，
  // 它的元数据结局不属于现在开着的这份——只按 id 判会把 A 的失败挂到 B 头上。比的是这份排版**现在**的归属项目
  // （与排队时取 pj 的是同一个表达式）；不一致就既不挂也不清
  if (pj === (projectOfDoc().id ?? currentProjectId())) {
    const prevMeta = useDocumentStore.getState().restoreMetaIssue
    // `owner_unknown`（正文写下了、归属 / 晋升没记：项目在后端已经没了）与 `failed` 一样是「下次自动恢复信息没更新」
    if (meta?.metadata === 'failed' || meta?.metadata === 'owner_unknown') {
      useDocumentStore.setState({ restoreMetaIssue: { docId: id, pj } })
    } else if (prevMeta && prevMeta.docId === id && meta?.metadata !== undefined) {
      useDocumentStore.setState({ restoreMetaIssue: null })
    }
  }
  useDocumentStore.setState({ lastPersisted: savedAt })
  setSaveState(useDocumentStore.getState().dirty ? 'dirty' : 'saved')
  emitActivity({ kind: 'document.saved' })
}

const disk = createDiskWriter({
  // 经箭头函数转发而不是直接递函数值：几份用例对 `@/lib/api` 做的是**部分** mock
  // （没有 putAutosave），模块加载那一刻去取它就会撞上 vitest 的「export 未定义」——
  // 搬出去之前这些名字只在调用那一刻才被读到，这里保持同样的懒。
  api: {
    put: (id, pd, baseline, revision, pj) => putAutosave(id, pd, baseline, revision, pj),
    fetch: (id, pj) => fetchAutosave(id, pj),
    fetchSummary: (id, pj) => fetchAutosaveSummary(id, pj),
  },
  isCurrent: isCurrentDoc,
  onSaving: (id) => {
    if (isCurrentDoc(id)) setSaveState('saving')
  },
  onWritten: afterWriteOk,
  onFailed: (id, conflict) => {
    if (isCurrentDoc(id)) {
      setSaveState(conflict ? 'conflict' : 'save_error', conflict ?? { kind: 'io', docId: id })
    }
    // 事件保留：老的监听方（App.tsx 的状态条）与用例都还在用它。
    // 它是**通知**，不是状态——状态在 store 里。
    window.dispatchEvent(
      new CustomEvent('tavotto:autosave-error', {
        detail: { id, reason: conflict ? conflict.kind : 'io' },
      }),
    )
  },
  writeBlocked: () => blocksDiskWrite(useDocumentStore.getState().saveState),
  dropLocalCopy: (id, written) => {
    try {
      const key = slotKey(id)
      // 比的是刚落盘的整份快照，不只比 Date.now()：同毫秒编辑或时钟回拨
      // 仍可能产生内容更新、updatedAt 却相同或更小的恢复副本（#646）。
      if (localStorage.getItem(key) === JSON.stringify(written)) localStorage.removeItem(key)
    } catch {
      /* 副本删不掉不影响正确性（读取时按 updatedAt 取新） */
    }
  },
  /* 遥测：一次写盘的结局。只有 manual / autosave 两档与 ok / conflict / failed
   * 三种结局，没有文档名、路径、修订号。 */
  outcome: (trigger, outcome) => captureTelemetry('document_saved', { trigger, outcome }),
})

/** 立刻把当前项目文档写入自动保存（本机副本同步 + 磁盘异步）。 */
export function flushAutosave(): FlushResult {
  // 性能探针（ADR 0075）：同步那一段在主线程上，落在拖动途中就是一次卡顿
  return perfSpan('autosave.flush', flushAutosaveNow)
}

function flushAutosaveNow(): FlushResult {
  // 占位文档（恢复还没落定）一个字节都不写：它不是用户的文档，写了 last / 最近文档 / 本机副本就是假的
  if (isDocumentPlaceholder()) return 'skipped'
  const state = useDocumentStore.getState()
  // 挂起中的文档一个字节都不写（见 `suspendAutosaveFor`）：内存里的编辑还在，
  // 换文档时它们随旧文档一起被放弃——这正是「重新开始」要的
  if (autosaveSuspendedFor !== null && autosaveSuspendedFor === state.documentId) return 'skipped'
  const pd = state.buildProject()
  if (!hasContent(pd)) {
    useDocumentStore.setState({ dirty: false, lastPersisted: null })
    return 'empty'
  }
  const savedAt = Date.now()
  let localOk = true
  try {
    localStorage.setItem(slotKey(state.documentId), JSON.stringify(pd))
  } catch {
    // 本机副本写不进去也照样走磁盘；只有两个都不可用才真会丢
    localOk = false
  }
  // 冲突未决时**只写本机副本**：再往磁盘上撞只会继续 409，而每撞一次就把
  // 用户的编辑又晾在一次失败上。内容一个字节都没丢——它在本机副本里
  // 等着用户裁决。
  if (blocksDiskWrite(state.saveState)) {
    useDocumentStore.setState({ dirty: false })
    return localOk ? 'saved' : 'error'
  }
  // 写盘记在**这份排版所属的项目**名下（后端按它记槽位归属）：切项目时离开那一下的冲刷发生在认领
  // 新项目之后，现问 `currentProjectId()` 会把旧项目的排版记成新项目的（#715 验收 P1）
  const owner = projectOfDoc().id ?? currentProjectId()
  disk.schedule(state.documentId, pd, owner, captureFigureSnapshotSave(pd, owner))
  if (!localOk) return 'error'
  const pj = projectOfDoc()
  const entry: RecentDoc = {
    id: state.documentId,
    name: pd.project.name,
    savedAt,
    objects: countObjects(pd),
    canvases: pd.canvases.length,
    // 没打开项目时（纯排版）两个字段都不写：那是「不知道」，不是空字符串
    ...(pj.id ? { projectId: pj.id } : {}),
    ...(pj.id && pj.name ? { projectName: pj.name } : {}),
  }
  const before = readIndex()
  const kept = writeIndex([entry, ...before.filter((e) => e.id !== state.documentId)])
  // 被本机索引挤掉的文档**不再**发 DELETE 删它的磁盘槽位（#715 PR-B）：索引只是本机
  // 「最近文档」列表放不下了，不是用户不要了；换了 origin 的索引还是空的，按它删会删错。
  // 磁盘槽位的清理只由后端做（`app._prune_autosave_slots`，按槽位归属）。
  // 项目文件绑定（`projectFile`）因此也留着：槽位还在，它仍有工作副本可指。
  writeCurrentId(state.documentId)
  useDocumentStore.setState({ dirty: false, recentDocs: kept })
  return 'saved'
}

/**
 * 真实的手动保存（⌘S / Ctrl+S）—— 立刻冲刷并**等到磁盘真的写完**。
 *
 * 与 `flushAutosave()` 的区别就是这个"等"：flush 只把这一份排进队列，队列
 * 什么时候写完、写没写成，调用方无从知道。手动保存必须能回答"存好了吗"，
 * 否则 ⌘S 就只是一个"提醒保存"的手势。
 *
 * 同时按下多次自然合并：同一个 documentId 在队列里只留最新一份，几次调用
 * 都在同一个"队列排空"上醒来，不会并发覆盖。
 */
export async function saveNow(): Promise<SaveState> {
  return (await saveNowWithResult()).state
}

/**
 * 手动保存的两个答案：`state` 是写完之后**此刻**的保存状态（照实算：途中又改过就是
 * dirty）；`wrote` 是**按下那一刻的内容**写没写成——冲刷之后这份文档有没有一次成功落盘
 * （排队合并成更新的一份也算：盘上的内容只会比按下时更新），或者根本没有要写的（空文档）。
 * 「排版写成了」（时间线的「保存」点）跟 `wrote` 走，不跟 `state` 走（Codex #679）。
 */
export async function saveNowWithResult(): Promise<{ state: SaveState; wrote: boolean }> {
  // 恢复没落定时没有「要存的文档」：不举手动标志、不认领、不等写盘队列
  if (isDocumentPlaceholder()) {
    return { state: useDocumentStore.getState().saveState, wrote: false }
  }
  // 手动保存是用户的动作：显式打开的别的项目的排版，存一下就归到这个项目（与真改一笔同一档）
  claimDocumentOnEdit()
  const id = useDocumentStore.getState().documentId
  const before = diskWrites.get(id) ?? 0
  cancelPendingAutosave()
  disk.markManual()
  const result = flushAutosave()
  if (result === 'empty') {
    disk.clearManual()
    setSaveState('clean')
    return { state: 'clean', wrote: true }
  }
  await disk.whenIdle()
  return {
    state: useDocumentStore.getState().saveState,
    wrote: (diskWrites.get(id) ?? 0) > before,
  }
}

/* -------------------------------------------------------------------------- */
/*  崩溃恢复（本机副本）                                                        */
/*                                                                            */
/*  稳态下 localStorage 里没有文档主体：写盘成功就删。所以启动时还留着一份     */
/*  副本，含义只有一个——上次写盘没成（崩溃、掉电、后端挂了）。                 */
/*                                                                            */
/*  改造前这里是**静默**的：`readAutosaveDoc` 按 updatedAt 挑一个赢家，赢的    */
/*  那份立刻推回磁盘。用户从没被问过，而被推上去的可能正是他不想要的那份。      */
/*  现在的规则是：主文档照常打开，本机那份挪进一个专用槽位等用户裁决。          */
/* -------------------------------------------------------------------------- */

const RECOVERY_PREFIX = 'tavotto.recovery.'
const recoveryKey = (id: string) => RECOVERY_PREFIX + id

function summarizeRecovery(pd: ProjectDocument): RecoverySummary {
  return {
    savedAt: pd.updatedAt,
    objects: countObjects(pd),
    canvases: pd.canvases.length,
    name: pd.project.name,
  }
}

/**
 * 把本机兜底副本挪进恢复槽位（**挪**，不是拷）。
 *
 * 留在原槽位不行：那个键是"在途的兜底副本"，下一次 flush 就会把它按当前
 * 内存内容覆盖掉——用户还没做决定，待恢复的那份就没了。
 */
function promoteToRecovery(id: string): ProjectDocument | null {
  const pd = readLocalSlot(id)
  if (!pd) return null
  try {
    localStorage.setItem(recoveryKey(id), JSON.stringify(pd))
    localStorage.removeItem(slotKey(id))
  } catch {
    return null // 存不下就当没有：不能把唯一一份挪丢了
  }
  return pd
}

function readRecovery(id: string): ProjectDocument | null {
  try {
    const raw = localStorage.getItem(recoveryKey(id))
    return raw ? migrateToProject(JSON.parse(raw)) : null
  } catch {
    return null
  }
}

function dropRecovery(id: string): void {
  try {
    localStorage.removeItem(recoveryKey(id))
  } catch {
    /* 删不掉只影响下次还会再问一遍 */
  }
}

/**
 * 恢复这份副本：进入内存并**置 dirty** —— 用户确认保存后才覆盖主文档。
 *
 * 主文档此刻一个字节没动：恢复只发生在内存里，磁盘上仍是刚才打开的那份，
 * 用户改主意就撤销（恢复是一次普通的载入，`past` 清空但主文件还在）。
 */
export function recoverLocalCopy(): boolean {
  const notice = useDocumentStore.getState().docNotice
  if (notice?.kind !== 'recovery') return false
  const { documentId } = useDocumentStore.getState()
  const pd = readRecovery(notice.docId)
  if (!pd || notice.docId !== documentId) return false
  applyProject(pd, notice.docId, { dirty: true })
  dropRecovery(notice.docId)
  setDocNotice(null)
  setSaveState('dirty')
  captureTelemetry('recovery_action', { action: 'restore' })
  return true
}

/** 保留磁盘上的主版本：扔掉恢复副本（**只删自己那一个键**） */
export function discardLocalCopy(): void {
  const notice = useDocumentStore.getState().docNotice
  if (notice?.kind !== 'recovery') return
  dropRecovery(notice.docId)
  setDocNotice(null)
  captureTelemetry('recovery_action', { action: 'keep_main' })
}

/** 「这份读不了」的裁决：知道了。磁盘上那份文件一个字节没动。 */
export function dismissDocNotice(): void {
  setDocNotice(null)
}

/**
 * 忘掉本机关于某个 documentId 的一切：自动保存槽位、待恢复副本、最近文档
 * 条目。**只动本机、只动这一个 id**——磁盘上的文档不归这里管。
 *
 * 给「重新开始教程」用：后端把教程画布的磁盘槽位清了，本机这一份不跟着清的话
 * `readAutosaveDoc` 会把它当成"磁盘上没有、本机这份就是文档本身"推回磁盘，
 * 刚重置的教程当场被旧进度盖回去。
 */
export function forgetLocalDocument(id: string): void {
  try {
    localStorage.removeItem(slotKey(id))
    localStorage.removeItem(recoveryKey(id))
    localStorage.removeItem(TABS_PREFIX + id)
  } catch {
    /* 删不掉只是留几个垃圾键 */
  }
  forgetProjectFile(id)
  const kept = writeIndex(readIndex().filter((e) => e.id !== id))
  useDocumentStore.setState({ recentDocs: kept })
}

/* -------------------------------------------------------------------------- */
/*  冲突裁决                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 重新加载磁盘上那份。
 *
 * **加载前先保护当前内存版本**：本机副本此刻正好就是它（写盘失败时故意
 * 没清），把它挪进恢复槽位，加载完立刻以 `recovery_available` 提供出来。
 * 所以"重新加载"不是一个会丢东西的按钮。
 */
export async function reloadFromDisk(): Promise<boolean> {
  const id = useDocumentStore.getState().documentId
  // 先落一次本机副本：冲突可能发生在防抖窗口之前，副本未必是最新的内存内容
  try {
    localStorage.setItem(slotKey(id), JSON.stringify(useDocumentStore.getState().buildProject()))
  } catch {
    /* 存不下就用已有的那份副本（可能旧一点，总比没有强） */
  }
  const backup = promoteToRecovery(id)
  let fetched: Awaited<ReturnType<typeof fetchAutosave>>
  try {
    fetched = await fetchAutosave(id)
  } catch {
    return false // 读不回来就别动内存里那份
  }
  const pd = fetched ? migrateToProject(fetched.doc) : null
  if (!pd) return false
  disk.rememberRevision(id, fetched)
  disk.setBaseline(id, pd.updatedAt)
  applyProject(pd, id, { dirty: false })
  setSaveState('clean')
  // 刚才那份内存版本没有丢：它进了恢复槽位，横幅立刻把它提供出来。
  // 「重新加载」因此不是一个会丢东西的按钮。
  setDocNotice(
    backup ? { kind: 'recovery', docId: id, summary: summarizeRecovery(backup) } : null,
  )
  return true
}

/**
 * 明确覆盖磁盘上那份。
 *
 * 基线换成后端在 409 里回的那个 hash —— 也就是"我现在知道磁盘上是什么了，
 * 我要盖掉它"。**不是**清空基线：清空等于此后每一次写都不再校验，用户按一次
 * 「覆盖」就把这份文档的外部修改检测永久关掉了。
 *
 * 拿不到 hash 时**去补一次**（`stale_write` 那条 409 比的是 updatedAt，body
 * 里没有磁盘修订号；旧后端 / 剥掉响应头的代理也会走到这里）。不补的话这一下
 * 只是把 `diskRevision` 删掉，而下一次写之前的 `ensureDiskKnown` 会探到磁盘上
 * 那份「我从没读过的文档」→ 又是一次冲突：按钮写着「覆盖」，实际效果是**再
 * 弹一次同样的框**。补不到才退回删除——那时至少下一轮会重新确认，而不是拿
 * 一个猜出来的基线去盖。
 */
export async function overwriteDisk(): Promise<SaveState> {
  const { documentId, saveIssue } = useDocumentStore.getState()
  let revision = saveIssue?.disk?.revision
  if (!revision) {
    // `fetchAutosaveSummary` 自己把失败吞成 null（摘要读不出来 = 磁盘上没有
    // 可比较的东西），所以这里不用再包一层 catch。
    revision = (await fetchAutosaveSummary(documentId, currentProjectId()))?.revision ?? undefined
  }
  if (revision) disk.setRevision(documentId, revision)
  else disk.forgetRevision(documentId)
  disk.forgetBaseline(documentId)
  setSaveState('dirty')
  return saveNow()
}

interface LocalSnapshot {
  raw: unknown
  doc: ProjectDocument
}

/** 原始快照与可打开的文档一起读取；旧格式迁移生成的身份不能用于判断原稿是否相同。 */
function readLocalSnapshot(id: string): LocalSnapshot | null {
  try {
    const raw = localStorage.getItem(slotKey(id))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    const doc = migrateToProject(parsed)
    return doc ? { raw: parsed, doc } : null
  } catch {
    return null
  }
}

/** 本机兜底副本（若有）；schema 2 旧槽位自动迁移 */
function readLocalSlot(id: string): ProjectDocument | null {
  return readLocalSnapshot(id)?.doc ?? null
}

type LocalRecoveryKind = 'local_newer' | 'local_distinct'

/** 内容不同才有恢复价值；时间戳不能证明另一个快照已经安全落盘（#646）。 */
function localRecoveryKind(local: LocalSnapshot | null, diskDoc: ProjectDocument, diskRaw: unknown): LocalRecoveryKind | null {
  if (!local) return null
  const localRaw = local.raw as Record<string, unknown>
  const rawDisk = diskRaw as Record<string, unknown> | null
  if (localRaw.schema === 2 && rawDisk?.schema === 2) {
    // schema 2 两次迁移会生成不同 project/canvas ID 与时间；比较完整原稿，保留所有用户字段。
    return sameRules({ ...localRaw, updatedAt: 0 }, { ...rawDisk, updatedAt: 0 }) ? null : 'local_distinct'
  }
  if (sameRules({ ...local.doc, updatedAt: 0 }, { ...diskDoc, updatedAt: 0 })) return null
  return local.doc.updatedAt > diskDoc.updatedAt ? 'local_newer' : 'local_distinct'
}

/** 载入一份文档的结果；`notice` 是需要用户裁决的那件事（没有则 null） */
export interface LoadedDoc {
  doc: ProjectDocument | null
  notice: DocNotice | null
}

/** `migrateToProject` 认不出的载荷里，是不是一份"来自更新的 Tavotto"？ */
function futureSchemaOf(raw: unknown): number | null {
  const d = raw as { schema?: unknown } | null
  if (!d || typeof d !== 'object') return null
  const schema = d.schema
  return typeof schema === 'number' && schema > SCHEMA_CURRENT ? schema : null
}

/**
 * 一次只读探测的结果（零副作用：不记 revision / 基线，不挪恢复副本，不 `dropRecovery`，不排队写盘）。
 * 选中并且仍有提交权的那一份再交给 `acceptProbe` 落账。
 */
export interface ProbeResult {
  id: string
  kind: 'found' | 'missing' | 'unavailable' | 'invalid' | 'too_new'
  /** 磁盘那一侧的探测原样 */
  disk: AutosaveProbe
  /** 可以打开的那份：磁盘优先；磁盘 missing / unavailable / invalid 时是本机副本 */
  doc: ProjectDocument | null
  from: 'disk' | 'local' | null
  /** 待裁决（不在探测里挪键）：不同内容的本机副本 / 上一轮没裁决完的恢复副本 */
  pendingRecovery: LocalRecoveryKind | 'recovery_slot' | null
  schema?: number
  /**
   * 本机副本（崩溃兜底 / 恢复槽位），**与磁盘探测的结局分开放**：磁盘 `unavailable` / `invalid` 时 `kind` 照实是那个，
   * `doc` 为 null——不能因为手边有副本就把读失败说成「读到了」（那份副本可能比磁盘旧）。只有确认过 404（`missing`）
   * 才自动把它当文档本身。恢复协调把它作为用户明确选择的备选（「打开本机副本」）提供；显式打开最近文档
   * （`readAutosaveDoc`）保持老行为，直接用它。
   */
  local: ProjectDocument | null
}

/**
 * 读文档快照（**只读**）：磁盘是主文档，本机副本是**待裁决的恢复候选**，不再静默取胜。
 *
 * 三条规则（`acceptProbe` 落账时执行）：
 * 1. 磁盘上有 → 打开磁盘那份。本机副本内容不同则挪进恢复槽位交给用户，
 *    不用时间戳猜新旧；内容相同（忽略 updatedAt、JSON 键序）才清冗余副本。
 * 2. 磁盘上没有（404）→ 本机那份就是这个文档本身，直接用，没什么可裁决的。
 * 3. 磁盘 schema 比本构建新 → **不打开**，也不许写。旧构建对新字段的语义一无
 *    所知，"尽力打开"等于用旧规则重写用户的新数据。
 *
 * 探测不碰任何东西，所以恢复协调可以逐个候选地探：没被选中的候选不会产生写入、不会迁移恢复副本。
 */
export async function probeDocument(id: string, pj: string | null): Promise<ProbeResult> {
  const disk = await probeAutosave(id, pj)
  const localSnapshot = readLocalSnapshot(id)
  const local = localSnapshot?.doc ?? null
  const pending = readRecovery(id)
  if (disk.kind === 'found') {
    const future = futureSchemaOf(disk.doc)
    if (future !== null) {
      return { id, kind: 'too_new', disk, doc: null, from: null, pendingRecovery: null, schema: future, local: null }
    }
    let diskDoc: ProjectDocument | null = null
    try {
      diskDoc = migrateToProject(disk.doc)
    } catch {
      diskDoc = null // 坏正文：当成读不了（下面退本机副本或回 invalid）
    }
    if (diskDoc) {
      // 上一轮没裁决完的恢复副本优先：它一直有效，直到用户处置它
      const pendingRecovery = pending
        ? 'recovery_slot'
        : localRecoveryKind(localSnapshot, diskDoc, disk.doc)
      return { id, kind: 'found', disk, doc: diskDoc, from: 'disk', pendingRecovery, local: local ?? pending }
    }
    // 磁盘上有字节但本构建认不出：当成读不了，有本机副本就用它（与旧 `fetchAutosave` 抛错同一条路）
  }
  // 磁盘 missing / unavailable / invalid（或认不出）：本机那份就是文档本身，没有就是没有
  const fallback = local ?? pending
  // 只有确认过 404 才自动把本机副本当文档本身；读失败（不可用 / 坏）保持磁盘的结局，副本另放
  if (fallback && disk.kind === 'missing') {
    return { id, kind: 'found', disk, doc: fallback, from: 'local', pendingRecovery: null, local: fallback }
  }
  const kind = disk.kind === 'found' ? 'invalid' : disk.kind
  return { id, kind, disk, doc: null, from: null, pendingRecovery: null, local: fallback }
}

/**
 * 接受探测结果：**只在选中且仍有提交权时调**。revision / 基线 / 恢复副本挪动 / `dropRecovery` /
 * 本机副本推回磁盘都在这里。`token` 给了就要求它仍是当前恢复代次，过期什么都不做、回 null。
 * 返回读盘带回来的待裁决事项（调用方在文档换进来**之后**挂上）。
 */
export function acceptProbe(p: ProbeResult, _pj: string | null, token?: RestoreToken | null): DocNotice | null {
  if (token && !isRestoreTokenCurrent(token)) return null
  const id = p.id
  if (p.kind === 'too_new') {
    return { kind: 'schema_too_new', docId: id, schema: p.schema ?? 0 }
  }
  if (p.kind !== 'found' || !p.doc) return null
  const localSnapshot = readLocalSnapshot(id)
  const local = localSnapshot?.doc ?? null
  const pending = readRecovery(id)
  if (p.from === 'disk' && p.disk.kind === 'found') {
    const diskDoc = p.doc
    disk.rememberRevision(id, { revision: p.disk.revision })
    // 读到什么就以什么为基线：之后本标签页的写盘都从这一版往前推进
    if (typeof diskDoc.updatedAt === 'number') disk.setBaseline(id, diskDoc.updatedAt)
    const localKind = localRecoveryKind(localSnapshot, diskDoc, p.disk.doc)
    const candidate = pending ?? (localKind ? local : null)
    if (local && !pending && !localKind) {
      // 内容已经在磁盘中；仅 updatedAt 或 JSON 键序不同，没有要恢复的编辑。
      try {
        localStorage.removeItem(slotKey(id))
      } catch {
        /* 删不掉只是留个垃圾键，下次索引清理还会再试 */
      }
    }
    if (!candidate) return null
    const kept = pending ?? promoteToRecovery(id)
    if (!kept) return null
    return { kind: 'recovery', docId: id, summary: summarizeRecovery(kept) }
  }
  // 磁盘上没有 / 读不到：本机那份就是文档本身
  if (!local) dropRecovery(id) // 恢复槽位转正，别再问一遍
  if (p.disk.kind === 'missing') {
    // 确认过磁盘上真的没有（404）：记成「缺席」档，并把本机那份推上去。记在这份排版**原本**属于的项目名下：
    // 只是打开（显式从「最近文档」打开别的项目的）不算带进当前项目
    disk.rememberRevision(id, null)
    const owner = ownerProjectForWrite(id)
    disk.schedule(id, p.doc, owner, { ...captureFigureSnapshotSave(p.doc, owner), documentId: id })
  } else {
    // 读盘失败（不可达 / 5xx / 磁盘上的字节坏了）：**不推**，也不伪造「缺席」——那时我们不知道磁盘上有什么，
    // 一次整份 PUT 就可能盖掉一份从没读过的文档。删掉条目 = 这份文档的磁盘状况我还没确认过，
    // 写之前 ensureDiskKnown 会去确认一次
    disk.forgetRevision(id)
  }
  return null
}

/** 读文档并落账（`probeDocument` + `acceptProbe`）：显式打开最近文档 / 教程装文档用，现有调用方不变 */
export async function readAutosaveDoc(id: string): Promise<LoadedDoc> {
  const pj = currentProjectId()
  let probe = await probeDocument(id, pj)
  // 显式打开最近文档：读盘失败时本机副本就是能给用户的那份（老行为）；恢复协调不走这里
  if (!probe.doc && probe.local && (probe.kind === 'unavailable' || probe.kind === 'invalid')) {
    probe = { ...probe, kind: 'found', doc: probe.local, from: 'local' }
  }
  const notice = acceptProbe(probe, pj)
  return { doc: probe.doc, notice }
}

/**
 * 「这份文档此刻要落盘的是什么」的身份：`buildProject()` 读的每一片（`doc` / `canvases` /
 * `activeCanvasId` / `projectMeta`）加上文档身份（`documentId`）与载入代次（`loadSeq`）。
 * store 的更新都是不可变的，任何一次改动都会换掉其中至少一个引用——比较引用就知道有没有被改过。
 * **`buildProject` 多读一片，这里就要多加一片**（看护：documentStore.test 里重命名项目 / 挪非激活画布那两条）。
 */
const persistedIdentity = (s: DocumentState) =>
  [s.documentId, s.loadSeq, s.doc, s.canvases, s.activeCanvasId, s.projectMeta] as const

/* -------------------------------------------------------------------------- */
/*  项目文件绑定（ADR 0096）                                                     */
/* -------------------------------------------------------------------------- */

/** 此刻这个标签页能写回的项目文件；属于别的项目 / 没有绑定 = null */
export function activeProjectFile(): ProjectFileBinding | null {
  return bindingForProject(useDocumentStore.getState().projectFile, currentProjectId())
}

/**
 * 绑定的写入口（state 与本机记录一起改）；`null` = 解绑。`documentId` 给的是
 * **发起保存那一刻**的排版：写盘途中切走了的话，只改那一份的本机记录，不去碰
 * 此刻开着的另一份的 state。
 */
export function setProjectFile(
  binding: ProjectFileBinding | null,
  documentId = useDocumentStore.getState().documentId,
): void {
  if (binding) writeProjectFile(documentId, binding)
  else forgetProjectFile(documentId)
  if (useDocumentStore.getState().documentId === documentId) {
    useDocumentStore.setState({ projectFile: binding })
  }
}

/**
 * 每份文档在本会话里被**用户**编辑过几次（编辑代次）。与绑定里的 `dirty` 同源：只在
 * `markProjectFileDirty()` 里前进——也就是 `saveState` 推 `dirty` 的那一处（派生同步
 * `applyDerivedUpdate` 不算）外加改排版名。载入 / 换文档 / 重新载入都不是编辑。
 *
 * 项目文件写成之后判「途中改没改过」要比的是**被提交那份文档自己的**代次，不是此刻开着
 * 哪一份（#674 评审第 4 轮），也不能是「要落盘的内容变没变」——派生同步也会改内容，按那个
 * 算的话写的途中来一次元数据同步，圆点就亮了、要求一次多余的保存（评审第 6 轮）。
 */
const editGeneration = new Map<string, number>()
const editGenerationOf = (documentId: string) => editGeneration.get(documentId) ?? 0

/**
 * 用户编辑之后：项目里那份落后了。编辑代次一律前进；绑定没有 / 已经是 dirty 就不再写。
 * **项目文件那根轴的唯一入口**——判「是不是用户编辑」的只有调用它的那两处，这里不再判第二遍。
 */
function markProjectFileDirty(): void {
  const s = useDocumentStore.getState()
  editGeneration.set(s.documentId, editGenerationOf(s.documentId) + 1)
  const b = s.projectFile
  if (b && !b.dirty) setProjectFile({ ...b, dirty: true })
}

/**
 * 「这份排版此刻的样子」的快照令牌：写项目文件前取一个（与 `buildProject()` 同步，同一份），
 * 写成之后问一下——**这份**排版在写的途中又改过的话，项目里那份仍然落后，圆点不能灭；
 * 途中切到别的排版不算改过。
 */
export function projectFileSnapshot(): () => boolean {
  const documentId = useDocumentStore.getState().documentId
  const before = editGenerationOf(documentId)
  return () => editGenerationOf(documentId) !== before
}

/**
 * 读自动保存槽位并切到这份文档（同一个 documentId），**切过去之后**再把读盘带回来的待裁决事项
 * （上次没写进盘的恢复副本、schema 太新）挂到 `docNotice` 上——`switchDocument` 会清掉它，
 * 挂早了就没了。教程装文档、切项目接回上次的文档都走这里；工作台挂载时的 `restoreSession()`
 * 看到文档已经装好就不再读盘，所以这句提示每条路径上恰好出现一次。
 */
export async function loadAutosavedDocument(
  id: string,
): Promise<{ loaded: boolean; notice: DocNotice | null }> {
  const { doc, notice } = await readAutosaveDoc(id)
  if (!doc) return { loaded: false, notice }
  const loaded = await useDocumentStore.getState().switchDocument(doc, id)
  if (loaded && notice) setDocNotice(notice)
  return { loaded, notice }
}

/**
 * 把一份项目文档装进 store —— **不 flush、不写本机副本**。
 *
 * 与 `switchDocument` 的分工：那条是用户动作的入口（会先冲刷旧文档、会立刻
 * 落一次快照进「最近文档」）；这条是"内容换了但会话没换"的装载，恢复、重新
 * 加载、启动恢复都走它。恢复路径**必须**不写本机副本：那个槽位正是待恢复的
 * 那份，写一次就没了。
 */
function applyProject(pd: ProjectDocument, id: string, opts: { dirty: boolean }): void {
  const active = pd.canvases.find((c) => c.id === pd.activeCanvasId) ?? pd.canvases[0]
  useDocumentStore.setState({
    doc: canvasToDoc(active),
    documentId: id,
    projectMeta: { id: pd.project.id, name: pd.project.name, createdAt: pd.createdAt },
    canvases: pd.canvases,
    activeCanvasId: active.id,
    canvasSessions: {},
    openTabs: restoreTabs(id, pd.canvases, active.id),
    dirty: opts.dirty,
    projectFile: readProjectFile(id),
    loadSeq: useDocumentStore.getState().loadSeq + 1,
    past: [],
    future: [],
    txn: null,
  })
}

/** Source-run generations are runtime facts, scoped by project; never inferred from a render refresh. */
const figureRunGenerations = new Map<string, number>()
const figureOwnerGenerations = new Map<string, number>()
const figureOwnerRunKey = (pj: string | null, owner: FigureExecutionOwner) => JSON.stringify([pj, figureExecutionOwnerKey(owner)])
const figureRunKey = (pj: string | null, sourceId: string) => JSON.stringify([pj, sourceId])
const figureRunGeneration = (pj: string | null, sourceId: string) => figureRunGenerations.get(figureRunKey(pj, sourceId)) ?? 0
const invalidatedSnapshotReceipts = new Set<string>()
const invalidatedExecutionReceipts = new Set<string>()
let figureReceiptGeneration = 0
let snapshotLoadSeq = -1
const ensureSnapshotReceiptScope = () => {
  const loadSeq = useDocumentStore.getState().loadSeq
  if (snapshotLoadSeq !== loadSeq) {
    invalidatedSnapshotReceipts.clear()
    invalidatedExecutionReceipts.clear()
    figureReceiptGeneration = 0
    snapshotLoadSeq = loadSeq
  }
}
const invalidatedSnapshotKey = (sourceId: string, receiptId: string) => {
  ensureSnapshotReceiptScope()
  return JSON.stringify([currentProjectId(), sourceId, receiptId])
}
const snapshotReceiptInvalidated = (ref: FigureSnapshot) => {
  ensureSnapshotReceiptScope()
  return invalidatedExecutionReceipts.has(ref.receiptId) ||
    invalidatedSnapshotReceipts.has(invalidatedSnapshotKey(ref.sourceId, ref.receiptId))
}

/** Editing source/patches invalidates the association; page geometry and exact copies preserve it. */
function clearChangedFigureSnapshots(next: FigureDocument, before: FigureDocument): void {
  if (!next.objects.some((o) => o.type === 'panel' && o.figureSnapshot)) return
  const previous = new Map(before.objects.filter((o) => o.type === 'panel').map((o) => [o.id, o]))
  for (const panel of next.objects) {
    if (panel.type !== 'panel' || !panel.figureSnapshot) continue
    const old = previous.get(panel.id)
    if (!validFigureSnapshot(panel.figureSnapshot, panel.fileId) ||
        (old && !sameFigureInputs(old, panel)) ||
        snapshotReceiptInvalidated(panel.figureSnapshot)) {
      delete panel.figureSnapshot
    }
  }
}

export function captureFigureSnapshotSave(
  document: ProjectDocument,
  pj: string | null = currentProjectId(),
): FigureSnapshotSaveContext {
  const state = useDocumentStore.getState()
  ensureSnapshotReceiptScope()
  const panels = document.canvases.flatMap((c) => c.objects).filter((o) => o.type === 'panel')
  return {
    documentId: state.documentId, loadSeq: state.loadSeq, pj, document,
    sourceGenerations: new Map(panels.map((p) => [p.fileId, figureRunGeneration(pj, p.fileId)])),
    ownerGenerations: new Map(figureOwnerGenerations),
    receiptGeneration: figureReceiptGeneration,
  }
}

/** Merge only the exact sent panel version; a late reply cannot reattach an old execution. */
export function mergeSavedFigureSnapshots(
  context: FigureSnapshotSaveContext,
  refs: Record<string, FigureSnapshot> | undefined,
): void {
  if (refs === undefined || writesBlocked(false)) return
  const state = useDocumentStore.getState()
  if (state.documentId !== context.documentId || state.loadSeq !== context.loadSeq ||
      currentProjectId() !== context.pj || (projectOfDoc().id ?? currentProjectId()) !== context.pj) return
  const sent = new Map(context.document.canvases.flatMap((c) => c.objects)
    .filter((o) => o.type === 'panel').map((p) => [p.id, p]))
  const corrective = new Set<string>()
  const merge = (objects: FigureDocument['objects']) => {
    for (const panel of objects) {
      if (panel.type !== 'panel') continue
      const previous = sent.get(panel.id)
      if (!previous || !sameFigureInputs(panel, previous) ||
          figureRunGeneration(context.pj, panel.fileId) !== context.sourceGenerations.get(panel.fileId)) continue
      const ref = refs[panel.id]
      if (validFigureSnapshot(ref, panel.fileId)) {
        if (ref.executionOwner) {
          const key = figureOwnerRunKey(context.pj, ref.executionOwner)
          if ((figureOwnerGenerations.get(key) ?? 0) !== (context.ownerGenerations?.get(key) ?? 0)) {
            corrective.add(panel.id)
            continue
          }
        }
        if (snapshotReceiptInvalidated(ref)) {
          if ((context.receiptGeneration ?? 0) !== figureReceiptGeneration) corrective.add(panel.id)
          continue
        }
        if (JSON.stringify(panel.figureSnapshot) !== JSON.stringify(ref)) panel.figureSnapshot = { ...ref }
      } else if (panel.figureSnapshot) delete panel.figureSnapshot
    }
  }
  const [doc, docPatches] = produceWithPatches(state.doc, (d) => merge(d.objects))
  const [canvases, canvasPatches] = produceWithPatches(state.canvases, (cs) => {
    for (const canvas of cs) merge(canvas.objects)
  })
  if (docPatches.length || canvasPatches.length) {
    useDocumentStore.setState({ doc, canvases, snapshotSeq: state.snapshotSeq + 1 })
  }
  if (corrective.size) {
    // This reply just persisted an execution that was retired while the write was in flight.
    // Keep any newer live ref, but schedule a corrective write through the normal dirty path.
    // The next context carries the current generations, so rejection alone cannot form a loop.
    const current = useDocumentStore.getState()
    const touch = (objects: FigureDocument['objects']) => {
      for (let i = 0; i < objects.length; i++) if (corrective.has(objects[i].id)) objects[i] = { ...objects[i] }
    }
    useDocumentStore.setState({
      doc: produceWithPatches(current.doc, (d) => touch(d.objects))[0],
      canvases: produceWithPatches(current.canvases, (cs) => { for (const c of cs) touch(c.objects) })[0],
    })
  }
}

/** Witnessed execution changes the live document; separately saved disk documents retain their references. */
export function invalidateFigureSnapshots(
  sourceIds: readonly string[],
  pj: string | null = currentProjectId(),
  receiptIds: readonly string[] = [],
  owners: readonly FigureExecutionOwner[] = [],
): void {
  if (pj !== currentProjectId() || writesBlocked(false)) return
  const ids = new Set(sourceIds)
  const receipts = new Set(receiptIds)
  if (!ids.size && !receipts.size && !owners.length) return
  const ownerKeys = new Set(owners.map(figureExecutionOwnerKey))
  for (const owner of owners) {
    const key = figureOwnerRunKey(pj, owner)
    figureOwnerGenerations.set(key, (figureOwnerGenerations.get(key) ?? 0) + 1)
  }
  ensureSnapshotReceiptScope()
  figureReceiptGeneration += 1
  for (const id of ids) figureRunGenerations.set(figureRunKey(pj, id), figureRunGeneration(pj, id) + 1)
  const state = useDocumentStore.getState()
  const matchesSource = (sourceId: string, ref?: FigureSnapshot): boolean => ids.has(sourceId) &&
    (!ref?.executionOwner || !owners.length || ownerKeys.has(figureExecutionOwnerKey(ref.executionOwner)))
  // Undo may later restore a removed object carrying a reference: remember those receipt IDs too.
  const remember = (value: unknown): void => {
    if (!value || typeof value !== 'object') return
    const object = value as Record<string, unknown>
    const ref = (object.figureSnapshot ?? (typeof object.sourceId === 'string' ? object : undefined)) as FigureSnapshot | undefined
    if (ref && validFigureSnapshot(ref, ref.sourceId)) {
      if (matchesSource(ref.sourceId, ref)) invalidatedSnapshotReceipts.add(invalidatedSnapshotKey(ref.sourceId, ref.receiptId))
      if (ref.executionOwner && ownerKeys.has(figureExecutionOwnerKey(ref.executionOwner))) receipts.add(ref.receiptId)
    }
    for (const child of Object.values(object)) remember(child)
  }
  remember([state.doc, state.canvases, state.past, state.future, state.canvasSessions, state.txn])
  for (const receipt of receipts) invalidatedExecutionReceipts.add(receipt)
  const clear = (objects: FigureDocument['objects']) => {
    for (let i = 0; i < objects.length; i++) {
      const panel = objects[i]
      if (panel.type !== 'panel' || (!matchesSource(panel.fileId, panel.figureSnapshot) &&
          !(panel.figureSnapshot && receipts.has(panel.figureSnapshot.receiptId)))) continue
      // Even without a live ref, an in-flight save may have captured the preceding run.
      // Publish a persistent change so a corrective save follows that stale write.
      objects[i] = { ...panel }
      delete (objects[i] as PanelObject).figureSnapshot
    }
  }
  const [doc, docPatches] = produceWithPatches(state.doc, (d) => clear(d.objects))
  const [canvases, canvasPatches] = produceWithPatches(state.canvases, (cs) => {
    for (const canvas of cs) clear(canvas.objects)
  })
  if (docPatches.length || canvasPatches.length) {
    // A rerun affects what this document exports: normal dirty/save scheduling applies,
    // including the bound project file, but execution itself adds no undo entry.
    // Only already-persisted save responses may bypass this via snapshotSeq.
    useDocumentStore.setState({ doc, canvases })
  }
}

/**
 * 外部派生元数据的写入口（Prompt 06）。
 *
 * 「外部」= 磁盘 / registry 说了算的那几个字段（`script` / `cost` /
 * `fileKind` / `pxW`），不是用户在 Tavotto 里做的编辑。调用方（
 * `store/panelSourceSync.ts`）自己算好新的 `doc` 与 `canvases`——**没有算出
 * 差异就不该调这里**：无差异还调一次会白白置 dirty、白白排一次落盘，而那
 * 一次落盘会带上一个新的 `updatedAt` 去和别的标签页抢乐观并发的基线。
 *
 * 三个「不」：不进 `past` / `future`（外部事实不该占用户的撤销步数）、
 * 不清空历史（撤销栈对这个面板的几何仍然有效）、不推 `saveState`。
 * 一个「要」：`derivedSeq` 一定要 +1，自动保存的订阅靠它认出这次变化的性质。
 */
export function applyDerivedUpdate(next: {
  doc?: FigureDocument
  canvases?: CanvasData[]
}): void {
  if (!next.doc && !next.canvases) return
  // 派生同步也要看门：占位文档上落一次派生更新会排队写盘（照样把占位写成了「用户的文档」）
  if (writesBlocked(false)) return
  useDocumentStore.setState((s) => {
    const [doc] = produceWithPatches(next.doc ?? s.doc, (d) => clearChangedFigureSnapshots(d, s.doc))
    const [canvases] = produceWithPatches(next.canvases ?? s.canvases, (cs) => {
      for (const canvas of cs) {
        const before = s.canvases.find((c) => c.id === canvas.id)
        if (before) clearChangedFigureSnapshots(canvasToDoc(canvas), canvasToDoc(before))
      }
    })
    return { doc, canvases, derivedSeq: s.derivedSeq + 1 }
  })
}

/**
 * 崩溃逃生开关：界面因某个文档反复崩溃时，「刷新」只会把同一份文档再读回来，
 * 用户就此卡死。ErrorBoundary 置上这个一次性标记后刷新 = 这次开空白文档；
 * 磁盘/localStorage 上的文档一个都不删，仍可从「最近文档」取回。
 */
const SKIP_RESTORE_KEY = 'tavotto:skip-restore'

export function requestBlankStart(): void {
  try {
    window.sessionStorage.setItem(SKIP_RESTORE_KEY, '1')
  } catch {
    /* 隐私模式下 sessionStorage 不可用：拿不到逃生标记也不该拦住刷新 */
  }
}

/**
 * 启动恢复（工作台挂载时调）。逻辑在 `documentRestore`（冷启动 / Picker / 重试同一条 `resolveProjectDocument`）；
 * 这里只转发，让现有调用方与用例的入口（`documentStore.restoreSession`）不变。
 *
 * **不静态 / 动态 import `documentRestore`**：它依赖本模块，反过来会把它拉进本模块所在的那个 import 环
 * （`importArchitecture.test`：旧环不扩大）。改成它加载时把自己的实现登记进来——`projectStore` 在应用里
 * 总会加载它；只 import 本模块的用例要自己 `import '@/store/documentRestore'`。
 */
let restoreSessionImpl: (() => Promise<boolean>) | null = null

export function registerRestoreSession(fn: () => Promise<boolean>): void {
  restoreSessionImpl = fn
}

export function restoreSession(): Promise<boolean> {
  if (!restoreSessionImpl) {
    return Promise.reject(new Error('restoreSession: `@/store/documentRestore` 没有加载（它加载时登记实现）'))
  }
  return restoreSessionImpl()
}

/** 一次性的「这次启动开空白」标记：读到就消费掉（StrictMode 第二轮拿到的是同一次启动的共享决定，见 `documentRestore`） */
export function consumeSkipRestoreOnce(): boolean {
  try {
    const skip = window.sessionStorage.getItem(SKIP_RESTORE_KEY) === '1'
    if (skip) window.sessionStorage.removeItem(SKIP_RESTORE_KEY)
    return skip
  } catch {
    return false
  }
}

/** 本机 `currentDoc` 记着的文档 id（恢复前捕获线索用——重置会改写它） */
export const readCurrentDocId = readCurrentId

/** 本机「最近文档」索引刷进 store（启动恢复一开始就做，列表不等文档恢复） */
export function refreshRecentDocsFromIndex(): void {
  useDocumentStore.setState({ recentDocs: readIndex() })
}

/**
 * 恢复协调的安装：把探测选中的那份换进来（`applyProject`，不冲刷、不写本机副本——那个槽位正是待恢复的那份）。
 * 令牌过期 / 占位文档被绕过门改过 → 不装、回 false。装的当下订阅方看到的是真文档（`installingUnderToken`），
 * 落定（`settleRestore`）由调用方紧接着同步做。
 */
export function installRestoredDocument(
  token: RestoreToken,
  pd: ProjectDocument,
  id: string,
  notice: DocNotice | null,
): boolean {
  if (!canCommitRestore(token)) return false
  installingUnderToken = true
  try {
    applyProject(pd, id, { dirty: false })
    // 恢复出来的只会是这个项目自己的排版（候选已按归属验过）：归属钉在这个项目上，本机索引里不知道归属的
    // 那条（升级前的）当场补上（#773）
    docProject = { id: token.pj, name: currentProjectLabel() }
    pendingClaim = null
    if (token.pj) backfillIndexOwner(id, pd, token.pj)
    writeCurrentId(id)
    useDocumentStore.setState({
      recentDocs: readIndex(),
      lastPersisted: readIndex().find((e) => e.id === id)?.savedAt ?? null,
      saveState: 'clean',
      saveIssue: null,
      docNotice: notice,
      restoreMetaIssue: null,
    })
    announceDocOpen(id)
  } finally {
    installingUnderToken = false
  }
  return true
}

/** 本机索引里这份排版的归属补成 `pj`（没有这条就补一条）；记着别的项目的不动 */
function backfillIndexOwner(docId: string, pd: ProjectDocument, pj: string): void {
  const index = readIndex()
  const hit = index.find((e) => e.id === docId)
  if (hit && typeof hit.projectId === 'string') return
  const name = currentProjectLabel()
  const entry: RecentDoc = {
    ...(hit ?? { id: docId, name: pd.project.name, savedAt: Date.now() }),
    objects: hit?.objects ?? countObjects(pd),
    canvases: hit?.canvases ?? pd.canvases.length,
    projectId: pj,
    ...(name ? { projectName: name } : {}),
  }
  const kept = writeIndex(hit ? index.map((e) => (e.id === docId ? entry : e)) : [entry, ...index])
  useDocumentStore.setState({ recentDocs: kept })
}

/** 防抖中的那次自动保存（手动保存要先把它取消，否则会多写一遍） */
let autosaveTimer: number | undefined

function cancelPendingAutosave(): void {
  window.clearTimeout(autosaveTimer)
  autosaveTimer = undefined
}

/**
 * 自动保存被**挂起**的那份文档 id；`null` = 没有。
 *
 * 调用方只有教程（`lib/onboarding/tutorial.ts` 的重置与打开）：后端在重置里清掉磁盘
 * 槽位、换掉项目目录（打开也可能因资源升级换副本），而前端此刻手里还是旧文档——
 * 这个窗口里的任何一次防抖写盘 / 派生同步 / 认领新项目时的那次冲刷
 * （项目重开会推 `registry.changed` / `assets.changed`，它们照样进 `applyDerivedUpdate`）
 * 都会把**拖过 / 改过的旧文档**写回刚清掉的那一格，随后装回来的就是旧的
 * （Windows 桌面腿的 e2e 第一次跑就抓到，mac 上只是时序没撞上）。
 * 挂起期间：防抖不排、`flushAutosave` 不写；`switchDocument` 换到任何文档即恢复。
 */
let autosaveSuspendedFor: string | null = null

/** 把这份文档从自动保存链路上摘下来，直到下一次 `switchDocument`。 */
export function suspendAutosaveFor(id: string): void {
  autosaveSuspendedFor = id
  cancelPendingAutosave()
}

export const isAutosaveSuspendedFor = (id: string): boolean => autosaveSuspendedFor === id

/** 重置 / 打开没做成（锁住 / 失败）、或落地后没换文档时，把文档接回自动保存链路。 */
export function resumeAutosave(): void {
  autosaveSuspendedFor = null
}

export function startAutosave(): () => void {
  const onLeave = (e: BeforeUnloadEvent) => {
    // **先读状态再冲刷。** 反过来的话 `flushAutosave()` 会把状态推成
    // `saving`，而 `saving` 属于"有未落盘的工作"——于是一份干干净净的文档
    // 每次刷新都弹一次「确定离开吗」，用户学会的是无脑点确定，那时候真该
    // 拦的那一次也拦不住了。
    const unsaved = hasUnsavedWork(useDocumentStore.getState().saveState)
    // 拦不拦都要冲刷：防抖窗口内的最后一次改动不能丢
    cancelPendingAutosave()
    flushAutosave()
    // 文案由浏览器决定（自定义文案早已被各家忽略），这里只表态要不要拦。
    if (unsaved) {
      e.preventDefault()
      e.returnValue = ''
    }
  }
  const unsubscribe = useDocumentStore.subscribe((state, prev) => {
    // `doc` 只是**激活画布**的编辑态。画布列表本身的结构性改动——重命名
    // 非激活画布、删除非激活画布、复制画布、调整画布顺序——只动 `canvases`，
    // 一个字节都不碰 `doc`。只盯 doc 的话这几类改动既不置 dirty 也不排队
    // 落盘，用户做完不再编辑激活画布就关掉应用（非优雅退出时连 beforeunload
    // 的兜底也没有），改动直接没了。
    // `openTabs` 不在此列：它按机器存 localStorage，走 persistTabs()。
    // 整体换文档不是编辑：磁盘恢复根本不该回写（内容就是从那儿读的），
    // 载入 / 切项目那条自己已经显式 flush 过一次了（为了立刻进「最近文档」），
    // 再排一次防抖写只会多一个新的 updatedAt 去和别的标签页抢。
    if (state.loadSeq !== prev.loadSeq || state.snapshotSeq !== prev.snapshotSeq) return
    if (state.doc === prev.doc && state.canvases === prev.canvases) return
    // 占位文档的变化不是编辑：写入口都看门，这里再守一道（绕过门的写入也不排队落盘）
    if (isDocumentPlaceholder()) return
    if (autosaveSuspendedFor !== null && autosaveSuspendedFor === state.documentId) return
    // 外部派生元数据同步（`applyDerivedUpdate`）：内容确实变了、必须落盘，
    // 但它不是用户的编辑。**只有 `saveState` 这一档不推**——`dirty` 照置
    // （字面含义就是"有改动还没写进自动保存"），落盘照排队，写盘失败照报。
    const derived = state.derivedSeq !== prev.derivedSeq
    if (!state.dirty) useDocumentStore.setState({ dirty: true })
    // 冲突未决时不覆盖状态：它要用户裁决，被一次编辑顶掉就等于替用户按了
    // 「算了」，而下一次防抖写盘又会去撞同一堵墙。编辑照常进本机副本。
    if (!derived && !blocksDiskWrite(state.saveState)) setSaveState('dirty')
    // 项目文件那根轴只跟用户编辑：派生同步不是用户做的事，圆点不该因它亮起
    if (!derived) markProjectFileDirty()
    // 显式打开的别的项目的排版：真改了一笔才归到这个项目（派生同步不算）
    if (!derived) claimDocumentOnEdit()
    cancelPendingAutosave()
    autosaveTimer = window.setTimeout(flushAutosave, DEBOUNCE_MS)
  })
  // 防抖窗口内刷新/关页也不丢最后一次改动
  window.addEventListener('beforeunload', onLeave)
  return () => {
    cancelPendingAutosave()
    window.removeEventListener('beforeunload', onLeave)
    unsubscribe()
  }
}

/**
 * 文档里的素材实例：激活画布优先，其次别的画布，只有这一份查找规则。
 * 给定 panelId 时必须仍是同一素材的那个实例；删除 / 换素材后绝不退到另一个副本。
 */
export function findFigurePanel(
  figureId: string,
  panelId?: string,
): { panel: PanelObject; canvasId: string } | null {
  const state = useDocumentStore.getState()
  const matches = (o: FigureDocument['objects'][number]): o is PanelObject =>
    o.type === 'panel' && o.fileId === figureId && (panelId === undefined || o.id === panelId)
  const here = state.doc.objects.find(matches)
  if (here) return { panel: here, canvasId: state.activeCanvasId }
  for (const canvas of state.canvases) {
    if (canvas.id === state.activeCanvasId) continue
    const panel = canvas.objects.find(matches)
    if (panel) return { panel, canvasId: canvas.id }
  }
  return null
}
