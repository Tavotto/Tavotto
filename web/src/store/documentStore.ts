import { create } from 'zustand'
import { applyPatches, enablePatches, produceWithPatches, type Patch } from 'immer'
import { ApiError, deleteAutosave, fetchAutosave, putAutosave } from '@/lib/api'
import { announceDocOpen } from '@/lib/docPresence'
import { msg, t, type UiMessage } from '@/i18n'
import { newId } from '@/lib/id'
import type { CanvasData, FigureDocument, ProjectDocument } from '@/types/document'
import {
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
 * 这是运行时状态，**不进 .magplot 文档**——文档 schema 一个字节没动。
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
  return { past, future: [] }
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
      return
    }
    set({ doc: next, ...pushHistory(state, { label, patches, inverse }) })
  },

  beginTxn: (label) => {
    if (get().txn) get().endTxn()
    set({ txn: { label, patches: [], inverse: [] } })
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
      return
    }
    const [patches, inverse] = compress(txn.patches, txn.inverse)
    set({ txn: null, ...pushHistory(state, { label: txn.label, patches, inverse }) })
  },

  undo: () => {
    const state = get()
    if (state.txn) state.endTxn()
    const entry = get().past.at(-1)
    if (!entry) return null
    // 补丁按路径应用：万一与当前文档对不上（历史损坏），扔掉这一条并保持
    // 文档不动，绝不能让栈和文档进入半应用的错位状态
    let next: FigureDocument
    try {
      next = applyPatches(get().doc, entry.inverse)
    } catch (err) {
      console.error('撤销补丁应用失败，该条历史已丢弃', err)
      set({ past: get().past.slice(0, -1) })
      return null
    }
    set({
      doc: next,
      past: get().past.slice(0, -1),
      future: [entry, ...get().future],
    })
    return entry.label
  },

  redo: () => {
    const state = get()
    const entry = state.future[0]
    if (!entry) return null
    let next: FigureDocument
    try {
      next = applyPatches(state.doc, entry.patches)
    } catch (err) {
      console.error('重做补丁应用失败，该条历史已丢弃', err)
      set({ future: state.future.slice(1) })
      return null
    }
    set({
      doc: next,
      past: [...state.past, entry],
      future: state.future.slice(1),
    })
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
 * 自动保存按文档隔离：一个文档一个槽位 `mm2.autosave.<documentId>`，
 * 另有一份轻量索引 `mm2.docIndex` 供「最近文档」列表使用（列菜单时不必反序列化
 * 每个完整文档）。`mm2.currentDoc` 记住刷新后该恢复哪一个。
 *
 * 注意：本机自动保存与「布局文件」是两件事——前者是浏览器里的工作副本，
 * 后者是写到服务器 layouts 的命名文件。保存布局文件不会改变文档身份。
 */
const SLOT_PREFIX = 'magplot.autosave.'
const INDEX_KEY = 'magplot.docIndex'
const CURRENT_KEY = 'magplot.currentDoc'
/** 更老的单槽自动保存（Magic Matplot 时代），只读不写 */
const LEGACY_KEY = 'mm2.autosave'
const MAX_SLOTS = 12
const DEBOUNCE_MS = 1000

export type FlushResult = 'saved' | 'empty' | 'error'

const slotKey = (id: string) => SLOT_PREFIX + id
const TABS_PREFIX = 'magplot.tabs.'

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

/* 磁盘为主、localStorage 为崩溃兜底：
 * flush 时同步写一份本机副本（快、抗崩溃），随即异步 PUT 到后端原子落盘，
 * 成功后删掉本机副本——稳态下 localStorage 不保存文档主体。 */

let diskBusy = false
/** 按 documentId 排队：同一个 id 天然合并成最新一份，不同 id 依次串行 PUT。
 *  用单槽变量的话，切文档时后来者会顶掉前一个文档排队的那份——那份连
 *  localStorage 兜底副本都还在等写盘成功后才清，顶掉即永久丢失。 */
const diskQueue = new Map<string, ProjectDocument>()

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

const isStaleWrite = (err: unknown) =>
  err instanceof ApiError && err.status === 409 && err.body.code === 'stale_write'

function scheduleDiskWrite(id: string, pd: ProjectDocument): void {
  if (diskBusy) {
    diskQueue.set(id, pd)
    return
  }
  diskBusy = true
  void putAutosave(id, pd, diskBaseline.get(id))
    .then(() => {
      diskBaseline.set(id, pd.updatedAt)
      try {
        localStorage.removeItem(slotKey(id))
      } catch {
        /* 副本删不掉不影响正确性（读取时按 updatedAt 取新） */
      }
    })
    .catch((err: unknown) => {
      // 磁盘写失败（含被 409 挡下的过期写）：本机副本仍在（flush 时已写，
      // 这里绝不清），提示由监听方处理。stale 与 io 的文案不一样，带上原因。
      window.dispatchEvent(
        new CustomEvent('magplot:autosave-error', {
          detail: { id, reason: isStaleWrite(err) ? 'stale' : 'io' },
        }),
      )
    })
    .finally(() => {
      diskBusy = false
      // 先出队再递归，队列里不会留下已经在写的那一份（不然同一 id 自己排自己）
      const next = diskQueue.entries().next()
      if (!next.done) {
        const [qid, qpd] = next.value
        diskQueue.delete(qid)
        scheduleDiskWrite(qid, qpd)
      }
    })
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
  try {
    localStorage.setItem(slotKey(state.documentId), JSON.stringify(pd))
  } catch {
    // 本机副本写不进去也照样走磁盘；只有两个都不可用才真会丢
    scheduleDiskWrite(state.documentId, pd)
    return 'error'
  }
  scheduleDiskWrite(state.documentId, pd)
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
  useDocumentStore.setState({ dirty: false, lastPersisted: savedAt, recentDocs: kept })
  return 'saved'
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

/**
 * 读文档快照：磁盘为主，本机兜底副本更新（崩溃窗口留下的）则用副本，
 * 并把胜出的一份推回磁盘。schema 2 旧数据统一迁移。
 */
export async function readAutosaveDoc(id: string): Promise<ProjectDocument | null> {
  let disk: ProjectDocument | null = null
  try {
    const raw = await fetchAutosave(id)
    disk = raw ? migrateToProject(raw) : null
  } catch {
    disk = null // 后端不可达：退回本机副本
  }
  // 读到什么就以什么为基线：之后本标签页的写盘都从这一版往前推进
  if (disk && typeof disk.updatedAt === 'number') diskBaseline.set(id, disk.updatedAt)
  const local = readLocalSlot(id)
  if (!disk && !local) return null
  const winner = !disk || (local && local.updatedAt >= disk.updatedAt) ? local! : disk
  if (winner !== disk) scheduleDiskWrite(id, winner) // 兜底副本转正 → 落盘并清理
  return winner
}

/** 老版本只有一个固定槽位 mm2.autosave；搬进新结构后删掉，只做一次。 */
function migrateLegacySlot(): void {
  try {
    const raw = localStorage.getItem(LEGACY_KEY)
    if (!raw) return
    const doc = JSON.parse(raw)
    if (!readIndex().length && doc?.schema === 2 && Array.isArray(doc.objects)) {
      const id = newId('d')
      localStorage.setItem(slotKey(id), raw)
      writeIndex([
        {
          id,
          name: typeof doc.name === 'string' ? doc.name : 'fig_layout',
          savedAt: Date.now(),
          objects: doc.objects.length,
        },
      ])
      writeCurrentId(id)
    }
    localStorage.removeItem(LEGACY_KEY)
  } catch {
    /* 迁移失败就当没有历史自动保存 */
  }
}

/** 启动时恢复上次的当前文档（含旧单槽自动保存的一次性迁移） */
/**
 * 崩溃逃生开关：界面因某个文档反复崩溃时，「刷新」只会把同一份文档再读回来，
 * 用户就此卡死。ErrorBoundary 置上这个一次性标记后刷新 = 这次开空白文档；
 * 磁盘/localStorage 上的文档一个都不删，仍可从「最近文档」取回。
 */
const SKIP_RESTORE_KEY = 'magplot:skip-restore'

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
  migrateLegacySlot()
  const index = readIndex()
  useDocumentStore.setState({ recentDocs: index })
  if (consumeSkipRestore()) return false
  const id = readCurrentId()
  const pd = id ? await readAutosaveDoc(id) : null
  if (!id || !pd) return false
  const active = pd.canvases.find((c) => c.id === pd.activeCanvasId) ?? pd.canvases[0]
  useDocumentStore.setState({
    doc: canvasToDoc(active),
    documentId: id,
    projectMeta: { id: pd.project.id, name: pd.project.name, createdAt: pd.createdAt },
    canvases: pd.canvases,
    activeCanvasId: active.id,
    canvasSessions: {},
    openTabs: restoreTabs(id, pd.canvases, active.id),
    dirty: false,
    lastPersisted: index.find((e) => e.id === id)?.savedAt ?? null,
    past: [],
    future: [],
    txn: null,
  })
  announceDocOpen(id)
  return true
}

export function startAutosave(): () => void {
  let timer: number | undefined
  const onLeave = () => {
    window.clearTimeout(timer)
    flushAutosave()
  }
  const unsubscribe = useDocumentStore.subscribe((state, prev) => {
    // `doc` 只是**激活画布**的编辑态。画布列表本身的结构性改动——重命名
    // 非激活画布、删除非激活画布、复制画布、调整画布顺序——只动 `canvases`，
    // 一个字节都不碰 `doc`。只盯 doc 的话这几类改动既不置 dirty 也不排队
    // 落盘，用户做完不再编辑激活画布就关掉应用（非优雅退出时连 beforeunload
    // 的兜底也没有），改动直接没了。
    // `openTabs` 不在此列：它按机器存 localStorage，走 persistTabs()。
    if (state.doc === prev.doc && state.canvases === prev.canvases) return
    if (!state.dirty) useDocumentStore.setState({ dirty: true })
    window.clearTimeout(timer)
    timer = window.setTimeout(flushAutosave, DEBOUNCE_MS)
  })
  // 防抖窗口内刷新/关页也不丢最后一次改动
  window.addEventListener('beforeunload', onLeave)
  return () => {
    window.clearTimeout(timer)
    window.removeEventListener('beforeunload', onLeave)
    unsubscribe()
  }
}
