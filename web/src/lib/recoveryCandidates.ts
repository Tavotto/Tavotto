/**
 * 「找回本机排版」的纯逻辑（ADR 0108 §四）：候选列表的分页 / 去重 / 行状态，和「恢复副本」。
 *
 * **只读 + 一次显式确认**：
 *  - 列表来自后端 `GET /api/layout-session/candidates`（只读，不为它运行脚本、不装包、不读脚本依赖数据）；
 *    草图（`sketch`）是后端给的结构化页面 + 对象，拿不到就没有——这里不编缩略图；
 *  - 归属明确属于当前项目的（`scope='project'`）原 id 打开，走 `openRecentDocument` 那条老路；
 *  - 归属待确认的（`scope='unknown'`）只能**恢复成副本**：固定目标 id `d_rc_<源id前缀>_<revision前8>`，
 *    正文以新 id 装进文档、走正常的自动保存链（`REVISION_ABSENT`）——归属由那条链建立。**源槽位与源
 *    归属一个字节都不动**；用户取消零写入；目标已存在就直接打开它（双击 / 网络重试不会生出第二份）。
 *  - 确认的那一刻再读一次源正文：revision 变了（预览之后被别处改过）→ `stale`，要求刷新，不拿旧预览去恢复。
 *
 * 不依赖 store / window：打开文档的动作经 `RecoverCopyDeps` 递进来，所以能用有状态的假 fetch 直接量序列。
 */
import {
  fetchLayoutOwnerEvidence,
  fetchRecoveryCandidates,
  probeAutosave,
  type RecoveryCandidateItem,
  type SessionIndexStatus,
} from '@/lib/api'
import { SCHEMA_CURRENT, migrateToProject, type ProjectDocument } from '@/types/document'

/** 每页读多少行（后端上限 100） */
export const CANDIDATE_PAGE = 50
/** 一个分组最多列多少行；到顶后不再给「加载更多」——更多的靠清理旧排版，不靠无限滚动 */
export const CANDIDATE_CAP = 200
/** 连续读到没有新行的页最多再翻几页 */
const MAX_EMPTY_HOPS = 5

export type RowIssue = 'invalid' | 'too_new' | 'too_large'

/** 这一行为什么不能恢复；可以恢复是 `null`。同时看后端给的 status 与 schema（前端构建可能比后端旧） */
export function rowIssue(item: RecoveryCandidateItem): RowIssue | null {
  if (item.status === 'too_new' || (item.schema !== null && item.schema > SCHEMA_CURRENT)) return 'too_new'
  if (item.status === 'too_large') return 'too_large'
  if (item.status === 'invalid') return 'invalid'
  return null
}

export interface CandidateList {
  rows: RecoveryCandidateItem[]
  /** 还有下一页（不透明游标）；到头是 null */
  next: string | null
  /** 上一批读到的最后一页里还没放进来的行（批量上限截掉的）；下一次「加载更多」先吃它们，再读 `next` */
  leftover: RecoveryCandidateItem[]
  /** 这一批读满了 `CANDIDATE_CAP` 行而后面还有：界面说"已显示 N 份，还有更多" */
  capped: boolean
  /** 后端扫描碰到上限：`next` 为 null 也不代表已列全（界面说"可能还有更多"，不说"没有"） */
  truncated: boolean
  index: SessionIndexStatus
}

/** 还有可以继续读的（下一页，或上一批被截掉的行）：「加载更多」是否可点 */
export const hasMore = (l: CandidateList): boolean => l.next !== null || l.leftover.length > 0

export type CandidateLoad =
  | { kind: 'ok'; list: CandidateList }
  | { kind: 'unsupported' }
  | { kind: 'unavailable' }

/** 一次调用最多翻多少页（页数有界；每页非空时 ≈ CAP / PAGE 页，其余留给空页续翻） */
const MAX_HOPS = 40

/**
 * 读一批：第一批，或接着 `prev` 继续（上一批被截掉的行先放进来，再读 `next`）。每批最多 `CANDIDATE_CAP` 行、
 * 页数有界；用户每点一次「加载更多」就是新的一批，**总数不封顶**但每次读取有界，行不会不可达。
 * 按 doc_id 去重（翻页之间槽位被改动，同一份可能出现两次）；`exclude` 是不该出现的 id（比如此刻开着的那份）。
 */
export async function loadCandidates(
  scope: 'project' | 'unknown',
  pj: string | null,
  prev?: CandidateList,
  exclude?: ReadonlySet<string>,
): Promise<CandidateLoad> {
  const seen = new Set(prev?.rows.map((r) => r.doc_id) ?? [])
  const rows = [...(prev?.rows ?? [])]
  const startLen = rows.length
  const room = () => CANDIDATE_CAP - (rows.length - startLen)
  const leftover: RecoveryCandidateItem[] = []
  const take = (items: readonly RecoveryCandidateItem[]) => {
    for (const it of items) {
      if (seen.has(it.doc_id) || exclude?.has(it.doc_id)) continue
      if (room() <= 0) {
        leftover.push(it)
        continue
      }
      seen.add(it.doc_id)
      rows.push(it)
    }
  }
  take(prev?.leftover ?? [])
  let next: string | null = prev?.next ?? null
  let index: SessionIndexStatus = prev?.index ?? 'ok'
  // 粘性：读过的任何一页截断过，结果就是截断的（后面不截断的页不能把它冲掉）；「加载更多」也继承上一次的
  let truncated = prev?.truncated ?? false
  let emptyStreak = 0
  let fetchedAny = !!prev // 接着上一批时只在还有下一页时才读；全新的第一批无条件读第一页
  // 这一批还有余量、上一批没有留下被截掉的行、后面还有页：接着读。后端按 scope 过滤之后一页可能为空而
  // next_cursor 仍在——连续空页最多再翻 MAX_EMPTY_HOPS 页
  for (let hops = 0; hops < MAX_HOPS && room() > 0 && leftover.length === 0 && (!fetchedAny || next !== null); hops++) {
    const res = await fetchRecoveryCandidates(scope, pj, next ?? undefined, CANDIDATE_PAGE)
    if (res === 'unsupported' || res === 'unavailable') {
      // 这一批一行都没读到就把故障原样交出去；读到一部分后失败则保留已读到的，`next` 仍是要重试的那页
      if (rows.length === startLen) return { kind: res }
      break
    }
    fetchedAny = true
    // 任一页曾因会话索引退化而漏掉归属行，这一批就不能在后页恢复正常时宣称完整。
    index = index === 'degraded' || res.index === 'degraded' ? 'degraded' : res.index
    next = res.next_cursor
    truncated ||= res.truncated
    const before = rows.length
    take(res.items)
    emptyStreak = rows.length === before ? emptyStreak + 1 : 0
    if (emptyStreak >= MAX_EMPTY_HOPS) break
  }
  const more = next !== null || leftover.length > 0
  return {
    kind: 'ok',
    list: { rows, next, leftover, capped: room() <= 0 && more, truncated, index },
  }
}

/**
 * 给启动恢复用的发现：本项目明确归属的、能恢复的候选（只读，第一页）。`more` = 第一页之外还有。
 * 「恰好一份就自动恢复、≥2 份让用户选、只有 unknown 归属的不阻塞」的产品决定在调用方
 * （`store/documentRestore.ts`），这里只给事实。
 */
export async function discoverProjectCandidates(
  pj: string | null,
): Promise<
  | { kind: 'ok'; items: RecoveryCandidateItem[]; more: boolean; truncated: boolean; index: SessionIndexStatus }
  | { kind: 'unsupported' }
  | { kind: 'unavailable' }
> {
  const res = await fetchRecoveryCandidates('project', pj, undefined, CANDIDATE_PAGE)
  if (res === 'unsupported' || res === 'unavailable') return { kind: res }
  return {
    kind: 'ok',
    items: res.items.filter((it) => it.owner === 'this' && rowIssue(it) === null),
    more: res.next_cursor !== null,
    truncated: res.truncated,
    index: res.index,
  }
}

/** 目标项目的短标记（FNV-1a，8 位十六进制）：同一份源在不同项目里恢复得到不同的副本 id */
function projectTag(pj: string | null): string {
  let h = 0x811c9dc5
  for (let i = 0; i < (pj ?? '').length; i++) h = Math.imul(h ^ (pj as string).charCodeAt(i), 0x01000193) >>> 0
  return h.toString(16).padStart(8, '0')
}

/**
 * 恢复副本的固定目标 id：`d_rc_<源 id 前缀>_<revision 前 8>_<目标项目标记>`。同一份源的同一个版本在**同一个项目**里
 * 永远得到同一个目标，所以双击 / 重试落在同一个槽位上；源改过（revision 变）或换了目标项目就是另一个目标——
 * 否则 A、B 两个项目恢复同一份源会撞同一个槽位，B 会"打开已有的"A 的副本。符合后端 doc_id 规则（`[\w-]{1,128}`）。
 */
export function recoveryCopyId(sourceId: string, revision: string | null, pj: string | null): string {
  const src = sourceId.replace(/[^\w-]/g, '').slice(0, 24) || 'x'
  const rev = (revision ?? '').replace(/[^\w]/g, '').slice(0, 8).padEnd(8, '0') || '00000000'
  return `d_rc_${src}_${rev}_${projectTag(pj)}`
}

/**
 * 已存在的目标槽位确实属于当前项目吗：**必须有绑定正文 revision 的后端证据**——`/owner` 回 `this`、`owners`、
 * 已核对，且它的 revision 等于刚探测到的正文 revision。本机索引**从不够**：跨 origin / profile 时它可能陈旧，
 * 而另一个 profile 已经把同一个槽位认领给了别的项目。旧后端（404）/ 问不到 / 读不到 revision 都拒绝
 * （恢复副本这套流程本来就要新后端）。
 */
async function ownsTarget(id: string, pj: string | null, probedRevision: string | null): Promise<boolean> {
  const ev = await fetchLayoutOwnerEvidence(id, pj)
  if (ev === 'unsupported' || ev === 'unavailable') return false
  if (!(ev.owner === 'this' && ev.evidence === 'owners' && ev.verified)) return false
  return ev.revision !== null && ev.revision === probedRevision
}

export type RecoverCopyResult =
  /** 副本已打开；`existed` = 目标早就在（上一次的恢复 / 重复点击），直接打开了它 */
  | { kind: 'opened'; id: string; existed: boolean }
  /** 预览之后源正文变了：没有恢复，调用方刷新列表让用户重新确认 */
  | { kind: 'stale' }
  | { kind: 'issue'; reason: 'missing' | 'invalid' | 'too_new' | 'unavailable' | 'foreign' | 'cancelled' }

export interface RecoverCopyDeps {
  /** 当前项目（目标槽位与源槽位都在它的数据目录里读） */
  pj: string | null
  /** 目标已存在：按原 id 打开它（`revision` 是刚才探测到的目标正文 revision，打开时再核对一次）。返回是否真的打开了 */
  openExisting: (id: string, revision: string | null) => Promise<boolean>
  /** 把副本正文以 `id` 装进文档（之后走正常自动保存链）。false = 用户取消 / 没装上 */
  install: (pd: ProjectDocument, id: string) => Promise<boolean>
  /** 确认开始之后项目 / 排版是否还是同一个；不是就什么都不装 */
  stillCurrent?: () => boolean
}

/** 同一目标的进行中请求：重复点击拿到同一个结果，不会并发装两次 */
const inflight = new Map<string, Promise<RecoverCopyResult>>()

/**
 * 用户在「恢复副本」上点了确认。顺序：目标已在 → 直接打开；否则重读源正文核对 revision → 装进文档。
 * 全程只读源；唯一的写是装进文档之后正常自动保存链写**目标**槽位。
 */
export function recoverCopy(item: RecoveryCandidateItem, deps: RecoverCopyDeps): Promise<RecoverCopyResult> {
  const target = recoveryCopyId(item.doc_id, item.revision, deps.pj)
  const key = `${deps.pj ?? ''}|${target}`
  const running = inflight.get(key)
  if (running) return running
  const p = doRecover(item, target, deps).finally(() => inflight.delete(key))
  inflight.set(key, p)
  return p
}

async function doRecover(
  item: RecoveryCandidateItem,
  target: string,
  deps: RecoverCopyDeps,
): Promise<RecoverCopyResult> {
  const stale = () => deps.stillCurrent?.() === false
  const there = await probeAutosave(target, deps.pj)
  if (there.kind === 'found') {
    // 已存在的目标必须是这个项目的：不重开别人的副本
    if (!(await ownsTarget(target, deps.pj, there.revision))) return { kind: 'issue', reason: 'foreign' }
    if (stale()) return { kind: 'issue', reason: 'cancelled' }
    return (await deps.openExisting(target, there.revision))
      ? { kind: 'opened', id: target, existed: true }
      : { kind: 'issue', reason: 'cancelled' }
  }
  // 目标读不出（不是「没有」）时不往下走：不知道它在不在，装下去可能再生出第二份
  if (there.kind === 'unavailable') return { kind: 'issue', reason: 'unavailable' }
  if (there.kind === 'invalid') return { kind: 'issue', reason: 'invalid' }

  const src = await probeAutosave(item.doc_id, deps.pj)
  if (src.kind === 'missing') return { kind: 'issue', reason: 'missing' }
  if (src.kind === 'unavailable') return { kind: 'issue', reason: 'unavailable' }
  if (src.kind === 'invalid') return { kind: 'issue', reason: 'invalid' }
  const raw = src.doc as { schema?: unknown; updatedAt?: unknown } | null
  if (raw && typeof raw.schema === 'number' && raw.schema > SCHEMA_CURRENT) {
    return { kind: 'issue', reason: 'too_new' }
  }
  // 预览之后源正文是不是变了：有 hash 比 hash；拿不到 hash（旧后端 / 代理吃了响应头）退回文档自报的 updatedAt
  const changed =
    item.revision !== null || src.revision !== null
      ? src.revision !== item.revision
      : (typeof raw?.updatedAt === 'number' ? raw.updatedAt : null) !== item.updatedAt
  if (changed) return { kind: 'stale' }
  // `migrateToProject` 对认得出 schema、字段却坏掉的正文会抛（不只是回 null）：都算这份读不了
  let pd: ProjectDocument | null = null
  try {
    pd = migrateToProject(src.doc)
  } catch {
    pd = null
  }
  if (!pd) return { kind: 'issue', reason: 'invalid' }
  if (stale()) return { kind: 'issue', reason: 'cancelled' }
  return (await deps.install(pd, target))
    ? { kind: 'opened', id: target, existed: false }
    : { kind: 'issue', reason: 'cancelled' }
}
