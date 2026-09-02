import { create } from 'zustand'
import { applyPatches, enablePatches, produceWithPatches, type Patch } from 'immer'
import {
  ApiError,
  REVISION_ABSENT,
  deleteAutosave,
  fetchAutosave,
  fetchAutosaveSummary,
  putAutosave,
  type DiskDocumentSummary,
} from '@/lib/api'
import { emitActivity } from '@/lib/activity'
import { announceDocOpen } from '@/lib/docPresence'
import { currentProjectId } from '@/lib/session'
import { msg, t, type UiMessage } from '@/i18n'
import { newId } from '@/lib/id'
import { boundedCount, captureTelemetry, classifyEditKind } from '@/lib/telemetry'
import { documentDigest, recordDiagnosticEvent } from '@/diagnostics'
import { patchRefs } from '@/diagnostics/patches'
import type { CanvasData, FigureDocument, ProjectDocument } from '@/types/document'
import {
  SCHEMA_CURRENT,
  canvasToDoc,
  docToCanvas,
  emptyProject,
  migrateToProject,
} from '@/types/document'

enablePatches()

const HISTORY_LIMIT = 200

/** 「最近文档」条目：只存列表要显示的字段 */
export interface RecentDoc {
  id: string
  name: string
  savedAt: number
  objects: number
  /** 画布数；旧条目无此字段 = 1 */
  canvases?: number
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
}

/** 非激活画布的撤销栈存放处（切换画布时换入换出） */
export interface CanvasSession {
  past: HistoryEntry[]
  future: HistoryEntry[]
}

type Recipe = (draft: FigureDocument) => void

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
  /** 上次写入本机自动保存的时间戳 */
  lastPersisted: number | null
  /** 本机自动保存过的文档（含当前文档），按最近保存时间倒序 */
  recentDocs: RecentDoc[]
  past: HistoryEntry[]
  future: HistoryEntry[]
  /** 进行中的拖动事务：pointerdown 开启，pointerup 合并成一条历史 */
  txn: { label: UiMessage; patches: Patch[]; inverse: Patch[] } | null

  /** 一次用户操作 = 一条历史记录 */
  commit: (label: UiMessage, recipe: Recipe) => void
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
  ) => Promise<boolean>
}

/**
 * 拖动过程中同一路径会被反复 replace，提交时压缩成一条：
 * 正向保留最后一次，反向保留最早一次。含增删的批次不压缩以保证正确性。
 *
 * **两个数组的时间序是相反的**：事务里正向补丁追加累积（末尾最新），反向补丁
 * 前插累积（`[...inverse, ...txn.inverse]`），所以**末尾才是最早的那条**——
 * 不压缩时按序全量 applyPatches，最早的最后落地，正好回到事务开始前。
 * 因此两边都取「数组里最后出现的那条」：正向拿到最新，反向拿到最早。
 * 反向若按数组顺序取第一条（= 最新），撤销就只退到倒数第二次更新。
 */
function compress(patches: Patch[], inverse: Patch[]): [Patch[], Patch[]] {
  const replaceOnly = (list: Patch[]) => list.every((p) => p.op === 'replace')
  if (!replaceOnly(patches) || !replaceOnly(inverse)) return [patches, inverse]

  const key = (p: Patch) => p.path.join('\u0000')
  const fwd = new Map<string, Patch>()
  for (const p of patches) fwd.set(key(p), p)
  const inv = new Map<string, Patch>()
  for (const p of inverse) inv.set(key(p), p)
  return [[...fwd.values()], [...inv.values()]]
}

function pushHistory(state: DocumentState, entry: HistoryEntry): Partial<DocumentState> {
  const past = [...state.past, entry]
  if (past.length > HISTORY_LIMIT) past.splice(0, past.length - HISTORY_LIMIT)
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
  return { past, future: [] }
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
  loadSeq: 0,
  derivedSeq: 0,
  lastPersisted: null,
  recentDocs: [],
  past: [],
  future: [],
  txn: null,

  commit: (label, recipe) => {
    const state = get()
    const [next, patches, inverse] = produceWithPatches(state.doc, recipe)
    if (!patches.length) return
    if (state.txn) {
      // 事务进行中的结构性操作也并入当前事务
      set({
        doc: next,
        txn: {
          label: state.txn.label,
          patches: [...state.txn.patches, ...patches],
          inverse: [...inverse, ...state.txn.inverse],
        },
      })
      noteCommit(label, state, next, patches, true)
      return
    }
    set({ doc: next, ...pushHistory(state, { label, patches, inverse }) })
    noteCommit(label, state, next, patches, false)
  },

  beginTxn: (label) => {
    // 上一个事务还开着 = 手势与离散操作混在了一起。诊断要看得见这件事：
    // 它正是「撤销一次回退了两件事」那一类现象的成因
    const replaced = get().txn != null
    if (replaced) get().endTxn()
    set({ txn: { label, patches: [], inverse: [] } })
    recordDiagnosticEvent({
      type: 'transaction.begin',
      label_key: label.key,
      replaced_open_txn: replaced,
    })
  },

  txnUpdate: (recipe) => {
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
    const [next, patches, inverse] = produceWithPatches(state.doc, recipe)
    if (!patches.length) return
    set({
      doc: next,
      txn: {
        label: state.txn.label,
        patches: [...state.txn.patches, ...patches],
        inverse: [...inverse, ...state.txn.inverse],
      },
    })
  },

  endTxn: (opts) => {
    const state = get()
    const txn = state.txn
    if (!txn) return
    if (opts?.discard || !txn.patches.length) {
      // 丢弃：把反向补丁打回去，恢复到事务开始前
      set({
        doc: txn.inverse.length ? applyPatches(state.doc, txn.inverse) : state.doc,
        txn: null,
      })
      recordDiagnosticEvent({
        type: 'transaction.cancel',
        label_key: txn.label.key,
        patch_count: txn.patches.length,
      })
      return
    }
    const [patches, inverse] = compress(txn.patches, txn.inverse)
    set({ txn: null, ...pushHistory(state, { label: txn.label, patches, inverse }) })
    recordDiagnosticEvent({
      type: 'transaction.end',
      label_key: txn.label.key,
      patch_count: patches.length,
      past_count: get().past.length,
      document_hash_after: documentDigest(get().doc),
    })
  },

  undo: () => {
    const state = get()
    recordDiagnosticEvent({
      type: 'undo.request',
      past_count: state.past.length,
      future_count: state.future.length,
      txn_open: state.txn != null,
    })
    if (state.txn) state.endTxn()
    const before = documentDigest(get().doc)
    const entry = get().past.at(-1)
    if (!entry) {
      noteUndoRedo('undo.complete', false, null, before, get())
      return null
    }
    // 补丁按路径应用：万一与当前文档对不上（历史损坏），扔掉这一条并保持
    // 文档不动，绝不能让栈和文档进入半应用的错位状态
    let next: FigureDocument
    try {
      next = applyPatches(get().doc, entry.inverse)
    } catch (err) {
      console.error('撤销补丁应用失败，该条历史已丢弃', err)
      set({ past: get().past.slice(0, -1) })
      noteUndoRedo('undo.complete', false, entry.label, before, get())
      return null
    }
    set({
      doc: next,
      past: get().past.slice(0, -1),
      future: [entry, ...get().future],
    })
    noteUndoRedo('undo.complete', true, entry.label, before, get())
    return entry.label
  },

  redo: () => {
    const state = get()
    recordDiagnosticEvent({
      type: 'redo.request',
      past_count: state.past.length,
      future_count: state.future.length,
      txn_open: state.txn != null,
    })
    const before = documentDigest(state.doc)
    const entry = state.future[0]
    if (!entry) {
      noteUndoRedo('redo.complete', false, null, before, get())
      return null
    }
    let next: FigureDocument
    try {
      next = applyPatches(state.doc, entry.patches)
    } catch (err) {
      console.error('重做补丁应用失败，该条历史已丢弃', err)
      set({ future: state.future.slice(1) })
      noteUndoRedo('redo.complete', false, entry.label, before, get())
      return null
    }
    set({
      doc: next,
      past: [...state.past, entry],
      future: state.future.slice(1),
    })
    noteUndoRedo('redo.complete', true, entry.label, before, get())
    return entry.label
  },

  canUndo: () => get().past.length > 0,
  canRedo: () => get().future.length > 0,

  silent: (recipe) => {
    const [next, patches] = produceWithPatches(get().doc, recipe)
    if (patches.length) set({ doc: next })
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
    const s = get()
    if (!s.canvases.some((c) => c.id === id)) return
    if (!s.openTabs.includes(id)) {
      set({ openTabs: [...s.openTabs, id] })
    }
    get().switchCanvas(id)
    persistTabs()
  },

  closeCanvasTab: (id) => {
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
    const s = get()
    if (from === to || from < 0 || from >= s.openTabs.length) return
    const openTabs = [...s.openTabs]
    const [moved] = openTabs.splice(from, 1)
    openTabs.splice(Math.max(0, Math.min(to, openTabs.length)), 0, moved)
    set({ openTabs })
    persistTabs()
  },

  addCanvas: (name) => {
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
    const s = get()
    if (from === to || from < 0 || from >= s.canvases.length) return
    const canvases = [...s.canvases]
    const [moved] = canvases.splice(from, 1)
    canvases.splice(Math.max(0, Math.min(to, canvases.length)), 0, moved)
    set({ canvases })
  },

  renameProject: (name) => {
    const clean = name.trim()
    if (!clean) return
    set((s) => ({ projectMeta: { ...s.projectMeta, name: clean } }))
    // 项目名不进撤销历史，但要立即落快照（最近文档列表显示它）
    flushAutosave()
  },

  switchDocument: async (next, nextId, confirmLoss) => {
    const hadContent = hasContent(get().buildProject())
    if (flushAutosave() === 'error' && hadContent && confirmLoss && !(await confirmLoss())) {
      return false
    }
    const pd = migrateToProject(next)
    if (!pd) return false
    const active = pd.canvases.find((c) => c.id === pd.activeCanvasId) ?? pd.canvases[0]
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
      loadSeq: get().loadSeq + 1,
      lastPersisted: null,
      past: [],
      future: [],
      txn: null,
    })
    writeCurrentId(nextId)
    // 广播「我端着这份了」：别的标签页也开着同一个 documentId 时会回音报警
    announceDocOpen(nextId)
    // 新文档立刻落一次快照，这样它马上出现在「最近文档」里
    flushAutosave()
    return true
  },
}))

let canvasSeq = 0

/** 默认画布名：Fig N，跳过已占用的名字 */
function nextCanvasName(canvases: CanvasData[]): string {
  const used = new Set(canvases.map((c) => c.name))
  for (let n = canvases.length + 1; ; n++) {
    const name = `Fig ${n}`
    if (!used.has(name)) return name
    if (n > canvases.length + 1000) return `Fig ${++canvasSeq}`
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
const INDEX_KEY = 'tavotto.docIndex'
const CURRENT_KEY = 'tavotto.currentDoc'
const MAX_SLOTS = 12
const DEBOUNCE_MS = 1000

export type FlushResult = 'saved' | 'empty' | 'error'

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

/**
 * 当前文档这一次保存走到了哪一步。
 *
 * ```text
 * clean ──编辑──▶ dirty ──flush──▶ saving ──成功且期间没再编辑──▶ saved ──▶ clean
 *                   ▲                 │                └──期间又编辑了──▶ dirty
 *                   │                 ├──写盘失败──▶ save_error ──重试──▶ saving
 *                   └────编辑─────────┴──409────────▶ conflict ──裁决──▶ dirty/clean
 * ```
 *
 * `saved` 是一个**短暂反馈态**，1.6 秒后自己回到 `clean`；它存在的理由是
 * 「刚刚存好了」和「一直是干净的」对用户是两件事。
 *
 * **「未决的恢复副本」与「那份文档读不了」不在这个枚举里**（Prompt 03 §二
 * 把 `recovery_available` / `read_only` 与它们并列）。理由是它们与保存进度
 * 是**两根互不相干的轴**，塞进同一个枚举两者就会互相顶掉：恢复副本还在本机
 * 躺着，一次成功的自动保存把状态推成 `saved`，横幅就没了，而那份副本一直到
 * 下次启动才再被想起来。它们走 `DocNotice`。
 */
export type SaveState = 'clean' | 'dirty' | 'saving' | 'saved' | 'save_error' | 'conflict'

/** 保存卡住的原因；`saveState` 说卡在哪一步，它说卡的是什么 */
export interface SaveIssue {
  /**
   * - `io`：写盘本身失败（磁盘满、权限、后端不可达）
   * - `stale`：另一个标签页存了更新的版本（后端 `stale_write`）
   * - `external`：磁盘上那份不是我上次读到/写出的那份（后端 `external_change`）
   */
  kind: 'io' | 'stale' | 'external'
  docId: string
  /** 磁盘上那份的摘要（`external` / `stale`）；拿不到时为 null */
  disk?: DiskDocumentSummary | null
}

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

/**
 * 这个状态下**不许碰磁盘**：磁盘上那份不是我以为的那份，得先由用户裁决，
 * 否则每一次防抖自动保存都是一次静默覆盖的尝试。
 */
const blocksDiskWrite = (state: SaveState) => state === 'conflict'

/**
 * 有没有「还没安全落盘」的工作？关闭保护、切文档提示都问它。
 *
 * `clean` / `saved` 是安全的：磁盘上就是内存里这份。**未决的恢复副本不在
 * 此列**——那份副本本身就在本机磁盘上，关掉窗口它还在，下次打开照样问。
 */
export const hasUnsavedWork = (state: SaveState): boolean =>
  state === 'dirty' || state === 'saving' || state === 'save_error' || state === 'conflict'

/* 磁盘为主、localStorage 为崩溃兜底：
 * flush 时同步写一份本机副本（快、抗崩溃），随即异步 PUT 到后端原子落盘，
 * 成功后删掉本机副本——稳态下 localStorage 不保存文档主体。 */

let diskBusy = false
/** 按 documentId 排队：同一个 id 天然合并成最新一份，不同 id 依次串行 PUT。
 *  用单槽变量的话，切文档时后来者会顶掉前一个文档排队的那份——那份连
 *  localStorage 兜底副本都还在等写盘成功后才清，顶掉即永久丢失。
 *
 *  **项目 id 跟着载荷一起排队**：一次写入属于排队那一刻的项目，不属于
 *  "socket 打开那一刻碰巧是哪个"。`dropProject()` 正是先冲刷再忘掉 pj，
 *  而排出去的那次 PUT 要过几个 await 才真的发出（写之前要先确认磁盘状况）
 *  ——读全局的话它就落进了后端的默认项目，在原项目里表现为"没保存"。 */
const diskQueue = new Map<string, { pd: ProjectDocument; pj: string | null }>()

/**
 * 乐观并发基线：本标签页最后一次**成功落盘**时那份文档的 updatedAt。
 * 读档时按磁盘上的值初始化，每次 PUT 成功后推进。PUT 带上它，后端发现磁盘
 * 更新（另一个标签页存过）就回 409 stale_write，不整份覆盖。
 *
 * 没有基线（首次写、从没读过盘）时不带，后端也就不校验——那时磁盘上本来就
 * 没有别人的东西可覆盖。收到 409 后基线**故意不推进**：本窗口后续的写盘会
 * 继续 409，而不是转头把对方的版本盖掉。
 */
const diskBaseline = new Map<string, number>()

/**
 * 外部修改基线（R-08）。三种取值**含义各不相同，不许合并**：
 *
 * | 值 | 含义 | 写入时带什么 |
 * | --- | --- | --- |
 * | 内容 hash | 我最后读到/写出的就是这一份 | 那个 hash |
 * | `REVISION_ABSENT` | 我读过，磁盘上没有这份文件 | `absent` 哨兵 |
 * | `null` | 我读过，但**拿不到**内容 hash（旧后端、代理吃了响应头） | 什么都不带 |
 *
 * **条目缺席**是第四种，与上面三种都不同：本会话从没确认过这份文档的磁盘
 * 状况，写之前得先去问一次（`ensureDiskKnown`）。
 *
 * `null` 这一档不是多余的：把它并进「缺席」，`ensureDiskKnown` 每次都会去探，
 * 探到一份「我从没读过的文档」（其实读过，只是没 hash）→ 判成冲突 → 这份文档
 * **永远存不上**；把它并进 `REVISION_ABSENT`，一个明明存在的文件被说成不存在
 * → 后端 409 → 同样永远存不上。两条捷径都通向同一个死结。
 *
 * 与 `diskBaseline` 是**两个维度**而不是同一件事的两种精度：updatedAt 由
 * 文档自己声明，编辑器外的工具改完 `tavottofile/*.json` 往往一动不动；
 * 内容 hash 由字节决定，谁改都瞒不过。所以带得了 hash 就以 hash 为准，
 * 带不了就退回 updatedAt 那条判据——弱一档，但不会把用户锁死。
 */
const diskRevision = new Map<string, string | null>()

const isStaleWrite = (err: unknown) =>
  err instanceof ApiError && err.status === 409 && err.body.code === 'stale_write'
const isExternalChange = (err: unknown) =>
  err instanceof ApiError && err.status === 409 && err.body.code === 'external_change'

/** 落盘队列空了就通知等待方（saveNow 靠它知道"这次真的写完了"） */
const idleWaiters: (() => void)[] = []

function settleIdle(): void {
  if (diskBusy || diskQueue.size) return
  for (const w of idleWaiters.splice(0)) w()
}

/** 队列排空（含在途那一次）后 resolve。已经空了就立刻 resolve。 */
function whenDiskIdle(): Promise<void> {
  if (!diskBusy && !diskQueue.size) return Promise.resolve()
  return new Promise<void>((resolve) => idleWaiters.push(resolve))
}

/** 记下这次读到的磁盘状况；三档的含义见 `diskRevision` 上的表。 */
function rememberRevision(id: string, fetched: { revision: string | null } | null): void {
  if (!fetched) diskRevision.set(id, REVISION_ABSENT)
  else diskRevision.set(id, fetched.revision || null)
}

/** 迟到的写入结果不该去改**别的文档**的状态（切文档、恢复都会换 id） */
const isCurrentDoc = (id: string) => useDocumentStore.getState().documentId === id

/** 写盘成功后：期间又编辑过就还是 dirty，没编辑过才是"存好了" */
function afterWriteOk(id: string, savedAt: number): void {
  if (!isCurrentDoc(id)) return
  useDocumentStore.setState({ lastPersisted: savedAt })
  setSaveState(useDocumentStore.getState().dirty ? 'dirty' : 'saved')
  emitActivity({ kind: 'document.saved' })
}

function conflictIssue(id: string, err: unknown): SaveIssue {
  const body = err instanceof ApiError ? err.body : {}
  const disk = (body.summary as DiskDocumentSummary | undefined) ?? null
  // 后端在 409 体里回了磁盘当下的 hash：显式覆盖拿它当基线，
  // 覆盖前如果又被改了一次，那次仍然会 409。
  const revision = typeof body.revision === 'string' ? body.revision : null
  return {
    kind: isExternalChange(err) ? 'external' : 'stale',
    docId: id,
    disk: disk ?? (revision ? ({ revision } as DiskDocumentSummary) : null),
  }
}

function scheduleDiskWrite(id: string, pd: ProjectDocument, pj = currentProjectId()): void {
  if (diskBusy) {
    diskQueue.set(id, { pd, pj })
    return
  }
  diskBusy = true
  if (isCurrentDoc(id)) setSaveState('saving')
  void ensureDiskKnown(id, pj)
    .then((issue) => {
      if (issue) throw new PendingConflict(issue)
      // `null`（确认过但拿不到 hash）与「缺席」在这里都变成 undefined = 不带，
      // 但两者在 `ensureDiskKnown` 那里是两回事：前者不再探，后者要探。
      return putAutosave(id, pd, diskBaseline.get(id), diskRevision.get(id) ?? undefined, pj)
    })
    .then((res) => {
      diskBaseline.set(id, pd.updatedAt)
      if (res.revision) diskRevision.set(id, res.revision)
      try {
        localStorage.removeItem(slotKey(id))
      } catch {
        /* 副本删不掉不影响正确性（读取时按 updatedAt 取新） */
      }
      afterWriteOk(id, res.saved_at ?? Date.now())
    })
    .catch((err: unknown) => {
      // 磁盘写失败（含被 409 挡下的过期写）：本机副本仍在（flush 时已写，
      // 这里绝不清）。**基线一个都不推进**——推进等于承认对方那版是我的起点，
      // 下一次写就会把它盖掉。
      const conflict =
        err instanceof PendingConflict
          ? err.issue
          : isStaleWrite(err) || isExternalChange(err)
            ? conflictIssue(id, err)
            : null
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
    })
    .finally(() => {
      diskBusy = false
      // 先出队再递归，队列里不会留下已经在写的那一份（不然同一 id 自己排自己）
      const next = diskQueue.entries().next()
      if (!next.done) {
        const [qid, queued] = next.value
        diskQueue.delete(qid)
        // 冲突挡住之后，队列里排着的那份不能继续往磁盘上撞：它的内容已经
        // 在本机副本里，等用户裁决完再写。
        if (blocksDiskWrite(useDocumentStore.getState().saveState) && isCurrentDoc(qid)) {
          settleIdle()
          return
        }
        scheduleDiskWrite(qid, queued.pd, queued.pj)
        return
      }
      settleIdle()
    })
}

/** 「先别写，这是个待裁决的冲突」——走 catch 那条路，不占用 ApiError 的语义 */
class PendingConflict extends Error {
  issue: SaveIssue
  constructor(issue: SaveIssue) {
    super('pending_conflict')
    this.issue = issue
  }
}

/**
 * 写之前确认磁盘状况**确实是我以为的那样**。
 *
 * 触发条件是「`diskRevision` 里没有这个 id 的条目」——也就是本会话从没
 * 确认过它的磁盘状况。这**没有例外**：新建文档、载入画布文件、读盘那次
 * 抛了异常、以及应用启动时那份还没被切换过的初始文档，全都落在这条上。
 * 写一条「只有读盘失败时才确认」的规则更省一次 GET，但那三种情况一样是
 * 手里两个基线都没有，而不带基线的 PUT 后端一律放行——磁盘上要是有一份
 * 我从没读过的文档，这一次 PUT 就把它整份盖掉了。判据留了例外就会从例外
 * 那一侧漏。
 *
 * 代价是每份文档第一次落盘前多一个 GET，之后一次都不多。
 */
async function ensureDiskKnown(id: string, pj: string | null): Promise<SaveIssue | null> {
  if (diskRevision.has(id)) return null
  let probe: Awaited<ReturnType<typeof fetchAutosave>>
  try {
    probe = await fetchAutosave(id, pj)
  } catch {
    // 还是问不到：不猜，也不记。这次照常尝试写（后端多半同样不可达，
    // 那就走 save_error），下一次写之前还会再确认一遍。
    return null
  }
  rememberRevision(id, probe)
  if (!probe) return null
  // 磁盘上有一份我从没读过的：这就是冲突，不是「首次写」
  return {
    kind: 'external',
    docId: id,
    disk: await fetchAutosaveSummary(id, pj),
  }
}

/** 立刻把当前项目文档写入自动保存（本机副本同步 + 磁盘异步）。 */
export function flushAutosave(): FlushResult {
  const state = useDocumentStore.getState()
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
  scheduleDiskWrite(state.documentId, pd)
  if (!localOk) return 'error'
  const entry: RecentDoc = {
    id: state.documentId,
    name: pd.project.name,
    savedAt,
    objects: countObjects(pd),
    canvases: pd.canvases.length,
  }
  const before = readIndex()
  const kept = writeIndex([entry, ...before.filter((e) => e.id !== state.documentId)])
  // 被索引挤掉的文档：磁盘槽位一并清理
  const keptIds = new Set(kept.map((e) => e.id))
  for (const e of before) {
    if (!keptIds.has(e.id) && e.id !== state.documentId) {
      void deleteAutosave(e.id).catch(() => {})
    }
  }
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
  cancelPendingAutosave()
  const result = flushAutosave()
  if (result === 'empty') {
    setSaveState('clean')
    return 'clean'
  }
  await whenDiskIdle()
  return useDocumentStore.getState().saveState
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
  return true
}

/** 保留磁盘上的主版本：扔掉恢复副本（**只删自己那一个键**） */
export function discardLocalCopy(): void {
  const notice = useDocumentStore.getState().docNotice
  if (notice?.kind !== 'recovery') return
  dropRecovery(notice.docId)
  setDocNotice(null)
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
  rememberRevision(id, fetched)
  diskBaseline.set(id, pd.updatedAt)
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
  if (revision) diskRevision.set(documentId, revision)
  else diskRevision.delete(documentId)
  diskBaseline.delete(documentId)
  setSaveState('dirty')
  return saveNow()
}

/** 本机兜底副本（若有）；schema 2 旧槽位自动迁移 */
function readLocalSlot(id: string): ProjectDocument | null {
  try {
    const raw = localStorage.getItem(slotKey(id))
    if (!raw) return null
    return migrateToProject(JSON.parse(raw))
  } catch {
    return null
  }
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
 * 读文档快照：磁盘是主文档，本机副本是**待裁决的恢复候选**，不再静默取胜。
 *
 * 三条规则：
 * 1. 磁盘上有 → 打开磁盘那份。本机副本比它新 = 上次写盘没成，挪进恢复槽位
 *    交给用户；不比它新 = 陈旧残留，直接删（"旧 autosave 不提示"）。
 * 2. 磁盘上没有（404）→ 本机那份就是这个文档本身，直接用，没什么可裁决的。
 * 3. 磁盘 schema 比本构建新 → **不打开**，也不许写。旧构建对新字段的语义一无
 *    所知，"尽力打开"等于用旧规则重写用户的新数据。
 */
export async function readAutosaveDoc(id: string): Promise<LoadedDoc> {
  let fetched: Awaited<ReturnType<typeof fetchAutosave>> = null
  let reachable = true
  try {
    fetched = await fetchAutosave(id)
  } catch {
    reachable = false // 后端不可达：退回本机副本，但磁盘状况**未确认**
  }
  if (reachable && fetched) {
    const future = futureSchemaOf(fetched.doc)
    if (future !== null) {
      return { doc: null, notice: { kind: 'schema_too_new', docId: id, schema: future } }
    }
  }
  const disk = fetched ? migrateToProject(fetched.doc) : null
  // 读盘失败时**删掉**条目而不是留个旧的：条目缺席的含义就是「这份文档的
  // 磁盘状况我还没确认过」，写之前 ensureDiskKnown 会去确认一次。
  if (!reachable) diskRevision.delete(id)
  else rememberRevision(id, fetched)
  // 读到什么就以什么为基线：之后本标签页的写盘都从这一版往前推进
  if (disk && typeof disk.updatedAt === 'number') diskBaseline.set(id, disk.updatedAt)

  const local = readLocalSlot(id)
  const pending = readRecovery(id)
  if (!disk) {
    // 磁盘上没有（或读不到）：本机那份就是文档本身
    const doc = local ?? pending
    if (!doc) return { doc: null, notice: null }
    if (!local) dropRecovery(id) // 恢复槽位转正，别再问一遍
    // 确认过磁盘上真的没有（404）才推上去。读盘失败时**不推**：那时我们
    // 不知道磁盘上有什么，一次整份 PUT 就可能盖掉一份从没读过的文档。
    if (reachable) scheduleDiskWrite(id, doc)
    return { doc, notice: null }
  }
  // 上一轮没裁决完的恢复副本优先：它一直有效，直到用户处置它
  const candidate = pending ?? (local && local.updatedAt > disk.updatedAt ? local : null)
  if (local && !pending && local.updatedAt <= disk.updatedAt) {
    // 陈旧残留：磁盘上那份更新或一样，本机这份没有任何可恢复的东西
    try {
      localStorage.removeItem(slotKey(id))
    } catch {
      /* 删不掉只是留个垃圾键，下次索引清理还会再试 */
    }
  }
  if (!candidate) return { doc: disk, notice: null }
  const kept = pending ?? promoteToRecovery(id)
  if (!kept) return { doc: disk, notice: null }
  return {
    doc: disk,
    notice: { kind: 'recovery', docId: id, summary: summarizeRecovery(kept) },
  }
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
    loadSeq: useDocumentStore.getState().loadSeq + 1,
    past: [],
    future: [],
    txn: null,
  })
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
  useDocumentStore.setState((s) => ({
    ...(next.doc ? { doc: next.doc } : {}),
    ...(next.canvases ? { canvases: next.canvases } : {}),
    derivedSeq: s.derivedSeq + 1,
  }))
}

/** 启动时恢复上次的当前文档（含旧单槽自动保存的一次性迁移） */
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

function consumeSkipRestore(): boolean {
  try {
    const skip = window.sessionStorage.getItem(SKIP_RESTORE_KEY) === '1'
    if (skip) window.sessionStorage.removeItem(SKIP_RESTORE_KEY)
    return skip
  } catch {
    return false
  }
}

export async function restoreSession(): Promise<boolean> {
  const index = readIndex()
  useDocumentStore.setState({ recentDocs: index })
  if (consumeSkipRestore()) return false
  const id = readCurrentId()
  if (!id) return false
  const { doc: pd, notice } = await readAutosaveDoc(id)
  if (!pd) {
    // schema 太新是**载入失败里唯一需要说话的那一种**：文件好好的，是这个
    // 构建读不了它。默不作声地开一份空白，用户会以为自己的文档没了。
    setDocNotice(notice)
    return false
  }
  applyProject(pd, id, { dirty: false })
  useDocumentStore.setState({ lastPersisted: index.find((e) => e.id === id)?.savedAt ?? null })
  setSaveState('clean')
  setDocNotice(notice)
  announceDocOpen(id)
  return true
}

/** 防抖中的那次自动保存（手动保存要先把它取消，否则会多写一遍） */
let autosaveTimer: number | undefined

function cancelPendingAutosave(): void {
  window.clearTimeout(autosaveTimer)
  autosaveTimer = undefined
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
    if (state.loadSeq !== prev.loadSeq) return
    if (state.doc === prev.doc && state.canvases === prev.canvases) return
    // 外部派生元数据同步（`applyDerivedUpdate`）：内容确实变了、必须落盘，
    // 但它不是用户的编辑。**只有 `saveState` 这一档不推**——`dirty` 照置
    // （字面含义就是"有改动还没写进自动保存"），落盘照排队，写盘失败照报。
    const derived = state.derivedSeq !== prev.derivedSeq
    if (!state.dirty) useDocumentStore.setState({ dirty: true })
    // 冲突未决时不覆盖状态：它要用户裁决，被一次编辑顶掉就等于替用户按了
    // 「算了」，而下一次防抖写盘又会去撞同一堵墙。编辑照常进本机副本。
    if (!derived && !blocksDiskWrite(state.saveState)) setSaveState('dirty')
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
