/**
 * 改图助手的**未发送草稿**（ADR 0132）：按「项目 × 面板文件」存在 `localStorage`。
 *
 * **这是缓存，不是账本**（与 ADR 0108 一致）：
 * - 桌面端口被占会换 origin，`localStorage` 随之清空——草稿会丢。这是**已知并接受的降级**，
 *   界面与文档都不宣称「一定恢复」。
 * - 任何存储失败（隐私模式、配额、被禁）都静默退回纯内存；永远不抛。
 * - 「这条指令有没有被执行」的权威在后端（`client_request_id` + `GET /api/ai/requests/<id>`），
 *   草稿记录里的 `rid` 只是让刷新后还能回到「已发出未确认」，不是判据。
 *
 * **只在桌面 / 浏览器模式用**：Codex 内嵌画布与 playground 没有助手面板，不 import 本模块
 * （`aiDraft.hosts.test.ts` 用 import 图钉住）。
 *
 * **隐私**：草稿全文与请求 id 只在 `localStorage` 与助手面板内存里，不进日志、遥测、
 * 诊断包的 frontend-state、interaction trace（`aiDraft.privacy.test.ts`）。
 *
 * 键 `tavotto.ai.draft.v1.<项目>.<面板 fileId>`；项目与 fileId 同时写进值里，键只用于寻址，
 * 清扫时不靠切键名。
 */

export const DRAFT_PREFIX = 'tavotto.ai.draft.v1.'
/** 单份草稿上限（UTF-8 字节）：超出不持久化、不截断（截断会让恢复出来的是一句被砍掉的指令） */
export const DRAFT_MAX_BYTES = 20 * 1024
/** 全局最多保留几份：超出按最近写入 / 读取时间淘汰最老的 */
export const DRAFT_MAX_COUNT = 30
/** 过期：30 天没写也没读过 */
export const DRAFT_TTL_MS = 30 * 24 * 60 * 60 * 1000

/** 点发送时钉住的目标（与 `AiPanel` 的 `SendTarget` 同形；这里不 import 组件） */
export interface DraftTarget {
  scope: 'element' | 'axes' | 'figure'
  gid: string | null
  label: string | null
  target: string
}

export interface Draft {
  project: string
  fileId: string
  text: string
  /** 最近一次写入或读取（ms）：过期与 LRU 都看它 */
  at: number
  /**
   * 已经发出、但后端还没确认接受时的请求 id；后端给出会话 id 之后随草稿一起删。
   * 有它的草稿在刷新后恢复成「已发出未确认」，普通重试被禁用（不得静默重发）。
   */
  rid?: string
  /** 与 `rid` 一起存：重发要钉在**发出那一刻**的目标上，不跟着此刻的选择走 */
  sent?: DraftTarget
}

export type SaveResult = 'saved' | 'removed' | 'too_large' | 'unavailable'

const keyOf = (project: string, fileId: string) => `${DRAFT_PREFIX}${project}.${fileId}`

const store = (): Storage | null => {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

const byteLength = (text: string): number => {
  try {
    return new TextEncoder().encode(text).length
  } catch {
    return text.length * 3
  }
}

function parse(raw: string | null, project: string, fileId: string): Draft | null {
  if (!raw) return null
  try {
    const v = JSON.parse(raw) as Partial<Draft> | null
    if (
      !v ||
      typeof v.text !== 'string' ||
      typeof v.at !== 'number' ||
      v.project !== project ||
      v.fileId !== fileId
    ) {
      return null
    }
    const out: Draft = { project, fileId, text: v.text, at: v.at }
    if (typeof v.rid === 'string' && v.rid && v.sent && typeof v.sent === 'object') {
      out.rid = v.rid
      out.sent = v.sent as DraftTarget
    }
    return out
  } catch {
    return null
  }
}

function write(ls: Storage, d: Draft): boolean {
  try {
    ls.setItem(keyOf(d.project, d.fileId), JSON.stringify(d))
    return true
  } catch {
    return false
  }
}

function drop(ls: Storage, project: string, fileId: string): void {
  try {
    ls.removeItem(keyOf(project, fileId))
  } catch {
    /* 同上：缓存而已 */
  }
}

/** 读一份草稿。过期 / 损坏的当场删掉并回 null；读到的算一次「用过」（刷新 LRU 时间） */
export function loadDraft(project: string | null, fileId: string | null, now = Date.now()): Draft | null {
  if (!project || !fileId) return null
  const ls = store()
  if (!ls) return null
  let raw: string | null = null
  try {
    raw = ls.getItem(keyOf(project, fileId))
  } catch {
    return null
  }
  const d = parse(raw, project, fileId)
  if (!d || now - d.at > DRAFT_TTL_MS) {
    if (raw) drop(ls, project, fileId)
    return null
  }
  write(ls, { ...d, at: now })
  return d
}

/**
 * 写（或删）一份草稿的**文本**。已有的 `rid` / `sent` 原样保留——正在等后端确认的那条不能被
 * 文本防抖冲掉。空文本且没有待确认的请求 = 删键。
 */
export function saveDraft(
  project: string | null,
  fileId: string | null,
  text: string,
  now = Date.now(),
): SaveResult {
  if (!project || !fileId) return 'unavailable'
  const ls = store()
  if (!ls) return 'unavailable'
  const prev = (() => {
    try {
      return parse(ls.getItem(keyOf(project, fileId)), project, fileId)
    } catch {
      return null
    }
  })()
  if (text === '' && !prev?.rid) {
    drop(ls, project, fileId)
    return 'removed'
  }
  if (byteLength(text) > DRAFT_MAX_BYTES) {
    // 旧的较短版本也删：留着它，刷新后恢复出来的会是一句过期的话
    drop(ls, project, fileId)
    return 'too_large'
  }
  const d: Draft = { project, fileId, text, at: now, ...(prev?.rid ? { rid: prev.rid, sent: prev.sent } : {}) }
  if (!write(ls, d)) return 'unavailable'
  evictOverflow(ls, `${project}\u0000${fileId}`)
  return 'saved'
}

/** 点发送：把「这条已发出、等后端确认」记进草稿（同步落盘，刷新也认得）。文本超限时也要记下 rid */
export function markSubmitted(
  project: string | null,
  fileId: string | null,
  text: string,
  rid: string,
  sent: DraftTarget,
  now = Date.now(),
): void {
  if (!project || !fileId) return
  const ls = store()
  if (!ls) return
  write(ls, { project, fileId, text, at: now, rid, sent })
}

/**
 * 后端明确拒绝：请求 id 作废、文本留着让用户改。`rid` 对不上就不动——那是另一次发送的记录。
 */
export function clearSubmitted(project: string | null, fileId: string | null, rid: string): void {
  if (!project || !fileId) return
  const ls = store()
  if (!ls) return
  const cur = parse(safeGet(ls, keyOf(project, fileId)), project, fileId)
  if (!cur || cur.rid !== rid) return
  write(ls, { project, fileId, text: cur.text, at: cur.at })
}

/**
 * **只在后端给出会话 id（请求已被接受）之后**调用。`rid` 对不上不删：
 * 另一个标签页 / 之后重新写下的草稿不是这条，不能被这条的确认抹掉。
 */
export function removeAccepted(project: string | null, fileId: string | null, rid: string): void {
  if (!project || !fileId) return
  const ls = store()
  if (!ls) return
  const cur = parse(safeGet(ls, keyOf(project, fileId)), project, fileId)
  if (cur?.rid === rid) drop(ls, project, fileId)
}

const safeGet = (ls: Storage, key: string): string | null => {
  try {
    return ls.getItem(key)
  } catch {
    return null
  }
}

function allDrafts(ls: Storage): { key: string; d: Draft | null; raw: string | null }[] {
  const keys: string[] = []
  try {
    for (let i = 0; i < ls.length; i++) {
      const k = ls.key(i)
      if (k && k.startsWith(DRAFT_PREFIX)) keys.push(k)
    }
  } catch {
    return []
  }
  return keys.map((key) => {
    const raw = safeGet(ls, key)
    let d: Draft | null = null
    try {
      const v = JSON.parse(raw ?? 'null') as Partial<Draft> | null
      if (v && typeof v.project === 'string' && typeof v.fileId === 'string') d = parse(raw, v.project, v.fileId)
    } catch {
      /* 损坏的当垃圾清掉 */
    }
    return { key, d, raw }
  })
}

function evictOverflow(ls: Storage, keepId: string): void {
  const live = allDrafts(ls).filter((x) => x.d)
  if (live.length <= DRAFT_MAX_COUNT) return
  live.sort((a, b) => (a.d as Draft).at - (b.d as Draft).at)
  for (const x of live.slice(0, live.length - DRAFT_MAX_COUNT)) {
    const d = x.d as Draft
    if (`${d.project}\u0000${d.fileId}` === keepId) continue
    try {
      ls.removeItem(x.key)
    } catch {
      /* 同上 */
    }
  }
}

/**
 * 清扫：过期的、损坏的、超出份数的，以及（给了 `exists` 时）目标面板已经不存在的。
 * 有待确认请求（`rid`）的草稿**不因目标消失而删**——请求可能已被后端接受，那条记录是用户
 * 找回「到底发出去没有」的唯一线索，留给过期去收。
 */
export function sweepDrafts(
  opts: { now?: number; exists?: (project: string, fileId: string) => boolean } = {},
): number {
  const ls = store()
  if (!ls) return 0
  const now = opts.now ?? Date.now()
  let removed = 0
  for (const { key, d } of allDrafts(ls)) {
    const dead = !d || now - d.at > DRAFT_TTL_MS || (!d.rid && opts.exists && !opts.exists(d.project, d.fileId))
    if (!dead) continue
    try {
      ls.removeItem(key)
      removed++
    } catch {
      /* 同上 */
    }
  }
  evictOverflow(ls, '')
  return removed
}

/** 现在就能判：写盘之前先给界面一个「过长」的答案，不等防抖 */
export const isDraftTooLarge = (text: string): boolean => byteLength(text) > DRAFT_MAX_BYTES
