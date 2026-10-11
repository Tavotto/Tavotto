/**
 * 「这个项目上次开着哪份排版」——按项目记（T02），**以后端为准**（#715 PR-B）。
 *
 * 切项目时 `resetForNewProject()` 会换上一份空白文档；改造前没有任何人再把
 * 用户上次在那个项目里排的版换回来，于是「返回 Tutorial 后先出现空白
 * fig_layout」（审计 T02）。这里记的只是一个 documentId + 名字：**不进
 * `.tavotto`、不进撤销、不是自动保存**——文档本身仍在自动保存槽位
 * （`layouts/_autosave/`）里，切回去时按 id 读回来。
 *
 * **权威在后端**（`GET /api/layout-session`、`PUT /api/layout-session/last`，数据目录
 * `state/layout-sessions.json`，`engine/layoutsession.py`）：桌面版每次启动 sidecar 可能换端口，
 * 换了端口就是换了 origin、一份空的 localStorage（#715）。本机的
 * `tavotto.projectDoc.<pj>` 只当缓存：后端没有这组端点（404：playground、嵌入画布、旧后端）
 * 或此刻不可达时退回它——那正是改造前的行为；后端有、但这个项目还没记过时，本机缓存是同一个
 * origin 升级上来的旧记录，拿它当迁移源并推一份上去。稳定端口与这份文件的关系见 #715 方案与
 * ADR 0108（桌面 origin 稳定与会话状态的后端权威，§三）。
 *
 * **空白文档不记**：没有内容的文档从不落盘（`flushAutosave` 回 `'empty'`），
 * 记下它的 id 只会在下次切回来时读到一个 404，然后向用户报一份根本不存在
 * 的「找不到上次文档」。
 *
 * **读与落账分开**（文档恢复可靠性修复）：
 *  - `planRestore` 只读——收集线索、核对归属、排出首选与后备，**不推迁移、不改缓存**（作废确知属于别的
 *    项目的缓存条目除外，那是原有行为）；
 *  - `confirmRestored` 在候选被**接受**（正文读到、仍有提交权）之后才推迁移 / 补推待确认的写入 /
 *    校正缓存与本机索引。修复前 `loadProjectDocument` 在验证正文之前就把本机引用推成后端 last，
 *    缺失的槽位因此成了唯一的「上次开着的」。
 *  - 后端的 `last` 只指向可恢复的正文（durable）；新文档还没有首次正文时后端只记 `pending`（打开意图）。
 *    前端同样不把「还没落盘的新文档」当唯一事实：缓存条目照记，但 `PUT` 回 `state:'pending'` 时保留
 *    「待确认」，正文真的落盘后由 `notifyDocumentPersisted` 补推（不被同值去重挡住）。
 */
import {
  ApiError,
  fetchLayoutOwnerEvidence,
  type OwnerEvidence,
  fetchLayoutSessionResult,
  probeAutosave,
  putLayoutSessionLast,
  type SessionFetch,
} from '@/lib/api'
import {
  correctRecordedProject,
  hasLocalBody,
  isForeignDocument,
  recordedProjectOf,
} from '@/lib/docOwnership'
import type { CanvasData, FigureDocument } from '@/types/document'

const PREFIX = 'tavotto.projectDoc.'

export interface ProjectDocumentRef {
  id: string
  /** 文档名（用户内容），只用来在「找不到上次文档」那句话里指名 */
  name: string
}

export function readProjectDocument(projectId: string): ProjectDocumentRef | null {
  try {
    const raw = localStorage.getItem(PREFIX + projectId)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<ProjectDocumentRef>
    return typeof v.id === 'string' && v.id ? { id: v.id, name: typeof v.name === 'string' ? v.name : '' } : null
  } catch {
    return null
  }
}

/**
 * 本机缓存里这条记录**还没被后端确认**：`base` 是写下它那一刻本机见过的**后端**最新记录时刻
 * （`seenAt`，后端给的 `last.at`），`gen` 是这一次写入的唯一标识（只用来认「后端确认的是不是这一次」，
 * 同一毫秒记两次也不撞，#719 Codex P2）。推送失败（非 404）时它留着；下一次读「上次开着哪份」时，
 * 后端那条的 `at` 不晚于 `base`（=写下之后后端没收过别的写）就以本机为准并重推，否则后端为准。
 *
 * **只比后端自己的时钟**：连着远程实例时浏览器与服务器是两台机器，拿本机 `Date.now()` 去比服务器的
 * `at`，服务器钟快就会把一次确知没推上去的写入判成旧的丢掉（#719 Codex P2）。
 */
interface Pending {
  base: number
  gen: string
}

let genSeq = 0
function newGen(): string {
  genSeq += 1
  return `${Date.now().toString(36)}-${genSeq.toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function rawCache(projectId: string): string | null {
  try {
    return localStorage.getItem(PREFIX + projectId)
  } catch {
    return null
  }
}

function readRaw(projectId: string): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(PREFIX + projectId)
    const v = raw ? (JSON.parse(raw) as unknown) : null
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** 本机见过的后端最新记录时刻（后端的钟）；没见过是 0 */
function readSeen(projectId: string): number {
  const v = readRaw(projectId)?.seenAt
  return finite(v) ? v : 0
}

function readPending(projectId: string): Pending | null {
  const v = readRaw(projectId)
  if (!v) return null
  return finite(v.pendingBase) && typeof v.pendingGen === 'string'
    ? { base: v.pendingBase, gen: v.pendingGen }
    : null
}

function writeCache(
  projectId: string,
  ref: ProjectDocumentRef,
  pending?: Pending,
  seenAt: number = readSeen(projectId),
): void {
  try {
    const v: Record<string, unknown> = { id: ref.id, name: ref.name }
    if (seenAt) v.seenAt = seenAt
    if (pending) {
      v.pendingBase = pending.base
      v.pendingGen = pending.gen
    }
    localStorage.setItem(PREFIX + projectId, JSON.stringify(v))
  } catch {
    /* 存不下只影响「下次切回这个项目落在哪份文档上」 */
  }
}

/**
 * 后端回过 404 = 这个后端没有这组端点（旧后端 / playground / 嵌入画布）：本模块实例里不再
 * 往上推。模块级而不是持久化——换一次后端（重启）自然重新探测。
 */
let remoteMissing = false

/**
 * 往后端推的写入**串行**：两次记录（A 然后 B）若并发发出、B 先到，后到的 A 会把
 * 「上次开着的」改回旧的那份。读之前也先等它排空（`planRestore`）。
 *
 * **只放已具备发送资格的请求**：「等正文保存之后才能发」的等待者不进这条链——否则
 * 读 last 等 remoteTail、保存又等恢复完成，就是一个环。需要等正文的补记走
 * `notifyDocumentPersisted`（正文落盘成功之后才进来）。
 */
let remoteTail: Promise<void> = Promise.resolve()

/**
 * 后端拒绝：这份排版的槽位记在**别的项目**名下（`409 layout_foreign`，`engine/layoutsession.py`
 * 的 `owner_conflict`）。与前端 `isForeignDocument` 是同一条判据的两侧，这里是后端那一道。
 */
export const LAYOUT_FOREIGN_CODE = 'layout_foreign'

/** 一次推送的结局：`refused` = 后端确知它属于别的项目（不重推、本机缓存里这条作废） */
type PushOutcome = 'ok' | 'refused' | 'failed' | 'missing' | 'pending'

/**
 * 每个项目最近一次推送的结局（模块级，不持久化）。`notifyDocumentPersisted` 靠它判断「正文落盘了，
 * 但上次推给后端的那条没有成为 durable」——要补推。`ok` = 后端回了 `durable`（或旧后端的 200）。
 */
const lastPush = new Map<string, { id: string; outcome: PushOutcome }>()

function pushRemote(projectId: string, ref: ProjectDocumentRef, gen: string | null): Promise<PushOutcome> {
  if (remoteMissing) return Promise.resolve('missing')
  const record = (outcome: PushOutcome): PushOutcome => {
    lastPush.set(projectId, { id: ref.id, outcome })
    return outcome
  }
  const done = remoteTail.then(() =>
    putLayoutSessionLast({ doc_id: ref.id, name: ref.name }, projectId).then(
      (res): PushOutcome => {
        // 后端确认了：记下它给的时刻（后端的钟）；本机这条还是这次推的那条就摘掉「待确认」
        // （期间又记了新的就只更新时刻、不动它的待确认）。
        // `state:'pending'` = 后端只记下了打开意图（槽位还没有有效正文），**不是**确认：待确认标记留着，
        // `res.last` 是原来的 durable 那条，它的时刻只用来抬高 base
        const at = finite(res?.last?.at) ? Math.max(res.last.at, readSeen(projectId)) : readSeen(projectId)
        const cur = readProjectDocument(projectId)
        const pending = readPending(projectId)
        if (res?.state === 'pending') {
          if (cur?.id === ref.id) {
            writeCache(projectId, cur, { base: Math.max(pending?.base ?? 0, at), gen: pending?.gen ?? gen ?? newGen() }, at)
          }
          return record('pending')
        }
        if (gen !== null && pending?.gen === gen) writeCache(projectId, ref, undefined, at)
        // 确认的是更早的一次（本标签页的写入串行，后记的那条还在排队 / 失败）：后端此刻那条就是
        // 我们自己更早的写，不比待确认的新——把待确认的基准抬到它，免得下次读时被自己的旧写盖掉
        else if (cur) writeCache(projectId, cur, pending ? { base: Math.max(pending.base, at), gen: pending.gen } : undefined, at)
        return record('ok')
      },
      (e: unknown): PushOutcome => {
        if (e instanceof ApiError && e.status === 404) {
          remoteMissing = true
          return record('missing')
        }
        if (e instanceof ApiError && e.status === 409 && e.body?.code === LAYOUT_FOREIGN_CODE) {
          // 后端确知这份属于别的项目：本机缓存里还是这一条就作废——留着「待确认」的话，下次读会
          // 以本机为准重推、再把别的项目的排版换进来
          if (readProjectDocument(projectId)?.id === ref.id) forgetProjectDocument(projectId)
          return record('refused')
        }
        /* 其余失败：本机缓存带着 pendingAt 记着，下次读的时候与后端比新旧、需要就重推 */
        return record('failed')
      },
    ),
  )
  remoteTail = done.then(() => undefined)
  return done
}

/** 记「这个项目现在开着这份排版」：本机缓存（先标「待确认」）+ 后端（权威）。 */
export function rememberProjectDocument(projectId: string, ref: ProjectDocumentRef): void {
  const pending = { base: readSeen(projectId), gen: newGen() }
  writeCache(projectId, ref, pending)
  void pushRemote(projectId, ref, pending.gen)
}

/**
 * 正文真的落盘成功了（`documentStore` 的 `onDocumentPersisted` 广播，带的是**保存载荷自己的** id 与 pj）：
 * 如果缓存里的意图就是这份、而上次推给后端的那条没有成为 durable（`pending` / 失败）或这个 origin 上次
 * 留下了待确认标记，就**重推一次**。它不经 `rememberProjectDocument` 的「同 id 同名不再记」去重——
 * 那一道正是让「早期推送被后端记成 pending、首次保存成功后永不补记」的原因。
 *
 * 旧文档 A 的迟到保存不会把 last 拉回去：缓存里的意图是 B 时 A 的广播直接忽略。
 * 接线在 `projectStore`（它同时认识两边；`projectDocs` 反过来 import `documentStore` 会成环）。
 */
export function notifyDocumentPersisted(id: string, pj: string | null): void {
  if (!pj || remoteMissing) return
  const cur = readProjectDocument(pj)
  if (!cur || cur.id !== id) return
  const last = lastPush.get(pj)
  const marker = readPending(pj)
  const needs = last && last.id === id ? last.outcome !== 'ok' : marker !== null
  if (!needs) return
  void pushRemote(pj, cur, marker?.gen ?? null)
}

/* ------------------------------- 只读候选计划 ------------------------------- */

/** 候选的来源。同一 docId 的多个来源合并在一条候选上 */
export type CandidateSource =
  | 'backend_last'
  | 'backend_prev'
  /** 后端记下的最新打开意图（槽位当时还没有有效正文；之后可能已经落盘） */
  | 'backend_pending'
  | 'local_cache'
  | 'global_current'
  | 'discovered'

/** 归属证据的来源：`local_index` = 本机最近文档索引；其余是后端 `/owner` 给的证据等级 */
export type OwnershipEvidence = 'local_index' | 'owners' | 'versions' | 'assets'

export interface RestoreCandidate {
  id: string
  name: string
  /** 去重后合并的来源 */
  sources: CandidateSource[]
  /**
   * 这条只是**打开意图**，后端还没有它的 durable 记录（本机缓存带着待确认标记、或后端 `pending`）。
   * 它的正文可能从没落过盘——所以它在磁盘上 `missing` 时**不是故障**，静默退到 durable 的后备；
   * 带 `backend_last` / `backend_prev` 来源的不算（那些是后端确认过可恢复的，缺了就是故障）。
   */
  pendingIntent?: boolean
  ownership: 'this' | 'unknown' | 'other'
  ownershipEvidence?: OwnershipEvidence
  /**
   * 后端记下 `backend_last` / `backend_prev` 指针时槽位正文的 revision；调用方拿它对照探测到的正文
   * （对不上 = 指针之后正文变过）。没有（旧后端 / 其它来源）就缺席。
   */
  expectedRevision?: string
  /**
   * 后端指针明说 `revision: null`（`backend_last` / `backend_prev` / `backend_pending` 任一，且没有别的来源带 revision）：
   * 指针校验那一刻槽位上没有有效正文，没有 revision 可对。字段缺席（旧后端）不算。
   * 之后别的项目可能用同一个 doc id 存了正文——所以调用方探测时若正文已经在，不能直接装，要 `/owner`
   * 给出 `this` 且 `verified` 且 revision 等于探测到的正文 revision 才行。没有这个字段 = 不是「预期不存在」。
   */
  expectedAbsent?: true
  /**
   * 本机索引陈旧地记着别的项目，而后端有 `owners` 证据（已核对 revision）证明它属于当前项目：
   * `confirmRestored` 在候选被接受之后才把本机索引纠正过来。
   */
  correctsIndex?: boolean
}

/** 没有进入 preferred / fallbacks 的线索——FE-R 据此知道「有线索但没认领」，不把它说成空项目 */
export interface WithheldCandidate {
  candidate: RestoreCandidate
  /** `foreign` = 确知属于别的项目；`owner_unknown` = 后端也没有证据；`owner_unavailable` = 此刻问不到归属 */
  reason: 'foreign' | 'owner_unknown' | 'owner_unavailable'
}

export interface CandidatePlan {
  pj: string | null
  /** 原样保留，含失败原因 */
  session: SessionFetch
  /**
   * 首选。优先级：本机待确认且后端没更晚写 → 本机；后端比 durable last 更晚的 pending 意图；后端 durable last；
   * 后端 null / unsupported → 本机缓存；再无 → 有归属证据的 global currentDoc。
   * **`session` 是 `unavailable` 时为 `null`**：不知道后端记着什么，不替它挑——本机线索都在 `fallbacks`，
   * 由调用方先有界重试再决定。
   */
  preferred: RestoreCandidate | null
  /** 其余可认领的候选（含 backend_prev、preferred 是本机 pendingIntent 时的 durable last） */
  fallbacks: RestoreCandidate[]
  /** preferred 来自本机缓存且后端 ok + last=null：**接受之后**才推迁移 */
  migrate: boolean
  withheld: WithheldCandidate[]
  /** 后端 durable last 的时刻（后端的钟），校正缓存时用；没有是 0 */
  remoteAt: number
  /** 规划那一刻的缓存原文：`confirmRestored` 发现它变了（别的标签页又记了）就不动缓存 */
  cacheSnapshot: string | null
  /** 本机缓存的意图比后端 durable last 新（待确认且后端没更晚写，或 GET 在途时缓存被改）：接受别的候选时也不拿后端那条覆盖它 */
  localWon: boolean
}

interface Entry {
  id: string
  name: string
  sources: CandidateSource[]
  pendingIntent: boolean
  expectedRevision?: string
  /** 有后端指针明说 `revision: null`（校验时槽位不存在 / 读不出）；字段缺席（旧后端）不算 */
  pointerNull?: boolean
}

type Verdict =
  | {
      ok: true
      ownership: 'this'
      evidence?: OwnershipEvidence
      correctsIndex?: boolean
      /** `/owner` 下结论时槽位正文的 revision：结论只对那一版成立，带去让探测时对一对 */
      revision?: string
      /** `/owner` 说下结论时槽位上没有正文（revision:null）：预期不存在，之后正文出现要走严格复核 */
      absent?: true
    }
  | { ok: false; reason: WithheldCandidate['reason']; keepCache?: true }

/**
 * 这份文档能不能认领给 `pj`。**判据的主语：本机索引（写下那一刻记的项目）→ 否则后端证据**：
 *  - 本机索引说属于 `pj`：认；
 *  - 本机索引说属于别的项目（C1）：只有后端 `/owner` 回 `owners` 证据且 `verified` 才推翻——仅 assets / versions
 *    不翻案（同名相对路径不是归属转移），后端说 other / unknown 或不支持 → 拒，问不到 → 拒但不当 foreign；
 *  - 本机不知道：带后端来源的认（后端已按 owners 过滤）；只有本机缓存的，后端明确说 other 才拒
 *    （其余认——缓存本身就是这个项目的记录）；只有全局 currentDoc 的，要后端正面证据。
 */
async function judge(id: string, pj: string | null, e: Entry, backendOk: boolean): Promise<Verdict> {
  const recorded = recordedProjectOf(id)
  if (pj === null) {
    return isForeignDocument(id, null)
      ? { ok: false, reason: 'foreign' }
      : { ok: true, ownership: 'this', evidence: 'local_index' }
  }
  const backendSourced = e.sources.some((s) => s === 'backend_last' || s === 'backend_prev' || s === 'backend_pending')
  const cached = e.sources.includes('local_cache')
  /** 把 `/owner` 的结论绑到下结论时的那一版正文上 */
  const bind = (ev: OwnerEvidence): { revision: string } | { absent: true } | Record<string, never> =>
    ev.revision ? { revision: ev.revision } : ev.revision === null ? { absent: true } : {} // 键缺席（旧后端）：没有东西可绑
  if (recorded === pj) {
    // 本机索引说是这个项目——但它可能是别的 origin / profile 留下的陈旧记录（那边已把这个 doc id 认领给了别的项目）。
    // 后端可达且这条没有后端指针背书时，一律向后端核对；旧后端（不支持）退回本机记录
    if (backendSourced || !backendOk) return { ok: true, ownership: 'this', evidence: 'local_index' }
    const ev = await fetchLayoutOwnerEvidence(id, pj)
    if (ev === 'unavailable') return { ok: false, reason: 'owner_unavailable' }
    if (ev === 'unsupported') return { ok: true, ownership: 'this', evidence: 'local_index' }
    // 后端明确说是别的项目：扣下，但**不**在规划里改本机记录（规划只读）
    if (ev.owner === 'other') return { ok: false, reason: 'foreign', keepCache: true }
    // this / 后端没有归属记录（旧数据，按「本机记着是它」的迁移规则认）：都绑上结论的 revision
    return { ok: true, ownership: 'this', evidence: 'local_index', ...bind(ev) }
  }
  if (typeof recorded === 'string') {
    const ev = await fetchLayoutOwnerEvidence(id, pj)
    if (ev === 'unavailable') {
      // 切项目时遗留的全局 currentDoc 若已在本机索引明确属于旧项目，问不到后端
      // 也不能让新项目卡在恢复问题上；带后端指针或本项目缓存的线索仍须等待复核。
      return {
        ok: false,
        reason: e.sources.length === 1 && e.sources[0] === 'global_current' ? 'foreign' : 'owner_unavailable',
      }
    }
    if (ev !== 'unsupported' && ev.owner === 'this' && ev.evidence === 'owners' && ev.verified) {
      // revision 与别的 /owner 分支同一套绑定：明说 null（槽位此刻不在）要记成 expectedAbsent，之后冒出来的正文必须再核（Codex #936）
      return { ok: true, ownership: 'this', evidence: 'owners', correctsIndex: true, ...bind(ev) }
    }
    return { ok: false, reason: 'foreign' }
  }
  if (backendSourced) return { ok: true, ownership: 'this' }
  if (cached && !backendOk) return { ok: true, ownership: 'this' }
  const ev = await fetchLayoutOwnerEvidence(id, pj)
  if (ev === 'unsupported') return { ok: true, ownership: 'this' }
  if (ev === 'unavailable') return { ok: false, reason: 'owner_unavailable' }
  if (ev.owner === 'other') return { ok: false, reason: 'foreign' }
  if (ev.owner === 'this') {
    return { ok: true, ownership: 'this', ...(ev.evidence ? { evidence: ev.evidence } : {}), ...bind(ev) }
  }
  return cached ? { ok: true, ownership: 'this', ...bind(ev) } : { ok: false, reason: 'owner_unknown' }
}

/**
 * 只读：这个项目启动 / 打开时该恢复哪份排版。**不推迁移、不写 currentDoc / owners、不改缓存**——
 * 唯一的例外是作废缓存里确知属于别的项目的那一条（原有行为）。候选的正文有没有、读不读得出来，
 * 不是这里的事（`documentStore.probeDocument` 逐个只读探测）。
 */
export async function planRestore(
  pj: string | null,
  clues: { globalCurrent: string | null },
): Promise<CandidatePlan> {
  await remoteTail
  // GET 在路上时本机缓存被改过（这个或同 origin 的别的标签页又记了一次，它的 PUT 甚至可能已经确认、
  // 摘掉了待确认标记）：那次写比这份回包新，回包不许压过它（#719 Codex P2）。判据是缓存原文本身——
  // localStorage 同 origin 共享，别的标签页写的也看得见
  const before = pj ? rawCache(pj) : null
  const session = await fetchLayoutSessionResult(pj)
  const cacheSnapshot = pj ? rawCache(pj) : null
  const cacheChanged = cacheSnapshot !== before
  const cache = pj ? readProjectDocument(pj) : null
  const marker = pj ? readPending(pj) : null

  const ok = session.kind === 'ok' ? session : null
  const last = ok?.last ?? null
  const prev = ok?.prev ?? null
  const bpending = ok?.pending ?? null

  const entries = new Map<string, Entry>()
  const add = (id: string, name: string, source: CandidateSource, pendingIntent = false, revision?: string | null) => {
    const hit = entries.get(id)
    if (hit) {
      if (revision && !hit.expectedRevision) hit.expectedRevision = revision
      if (revision === null) hit.pointerNull = true
      if (!hit.sources.includes(source)) hit.sources.push(source)
      if (!hit.name && name) hit.name = name
      hit.pendingIntent ||= pendingIntent
    } else {
      entries.set(id, {
        id,
        name,
        sources: [source],
        pendingIntent,
        ...(revision ? { expectedRevision: revision } : {}),
        ...(revision === null ? { pointerNull: true } : {}),
      })
    }
  }
  if (last) add(last.doc_id, last.name, 'backend_last', false, last.revision)
  if (prev) add(prev.doc_id, prev.name, 'backend_prev', false, prev.revision)
  if (bpending) add(bpending.doc_id, bpending.name, 'backend_pending', true, bpending.revision)
  if (cache) add(cache.id, cache.name, 'local_cache', marker !== null)
  if (clues.globalCurrent) add(clues.globalCurrent, '', 'global_current')

  // 排序
  const order: string[] = []
  const push = (id: string | undefined) => {
    if (id && !order.includes(id)) order.push(id)
  }
  const localWins =
    !!cache && !!ok && (cacheChanged || (marker !== null && (!last || last.at <= marker.base)))
  if (ok) {
    if (localWins) push(cache?.id)
    if (bpending && (!last || bpending.at > last.at)) push(bpending.doc_id)
    push(last?.doc_id)
    push(bpending?.doc_id)
    push(prev?.doc_id)
    push(cache?.id)
    push(clues.globalCurrent ?? undefined)
  } else {
    push(cache?.id)
    push(clues.globalCurrent ?? undefined)
  }

  const verdicts = await Promise.all(order.map((id) => judge(id, pj, entries.get(id)!, ok !== null)))
  const claimable: RestoreCandidate[] = []
  const withheld: WithheldCandidate[] = []
  order.forEach((id, i) => {
    const e = entries.get(id)!
    const v = verdicts[i]
    // 「只是打开意图」：没有后端 durable 记录（带 backend_last / backend_prev 的是后端确认过的）
    const intentOnly = e.pendingIntent && !e.sources.includes('backend_last') && !e.sources.includes('backend_prev')
    const base: RestoreCandidate = {
      id,
      name: e.name,
      sources: e.sources,
      ownership: v.ok ? 'this' : v.reason === 'foreign' ? 'other' : 'unknown',
      ...(intentOnly ? { pendingIntent: true } : {}),
      ...(e.expectedRevision ? { expectedRevision: e.expectedRevision } : {}),
      // 有指针明说 revision:null（校验时槽位不存在 / 读不出）且没有别的来源带 revision：预期不存在。
      // 字段缺席（旧后端）不算
      ...(!e.expectedRevision && e.pointerNull ? { expectedAbsent: true as const } : {}),
    }
    if (v.ok) {
      claimable.push({
        ...base,
        ...(v.evidence ? { ownershipEvidence: v.evidence } : {}),
        ...(v.correctsIndex ? { correctsIndex: true } : {}),
        // 归属是 /owner 给的结论、而指针没带 revision：结论只对下结论时的那一版正文成立，带上它让探测时对一对
        ...(!base.expectedRevision && v.revision ? { expectedRevision: v.revision } : {}),
        ...(!base.expectedRevision && !v.revision && v.absent ? { expectedAbsent: true as const } : {}),
      })
      return
    }
    withheld.push({ candidate: base, reason: v.reason })
    // 确知属于别的项目的缓存条目作废（原有行为）；问不到归属时不作废——那只是此刻不知道
    if (v.reason === 'foreign' && !v.keepCache && pj && cache?.id === id) forgetProjectDocument(pj)
  })

  const preferred = session.kind === 'unavailable' ? null : (claimable[0] ?? null)
  const fallbacks = preferred ? claimable.slice(1) : claimable
  const migrate =
    !!preferred && !!ok && ok.last === null && preferred.sources.includes('local_cache') && !preferred.sources.includes('backend_pending')
  return {
    pj,
    session,
    preferred,
    fallbacks,
    migrate,
    withheld,
    remoteAt: last?.at ?? 0,
    cacheSnapshot: pj ? rawCache(pj) : null,
    localWon: localWins,
  }
}

/**
 * 候选被**接受**之后落账（正文读到、仍有提交权）：
 *  - 缓存里就是这份、而它还没被后端确认（迁移 / 待确认标记）→ 标待确认并推一次；
 *  - 接受的是后端 durable last → 把缓存校正成它（与后端一致）；
 *  - 本机索引陈旧地记着别的项目（`correctsIndex`）→ 纠正成当前项目。
 * 没被选中的候选不会走到这里。缓存在规划之后又被改过（别的标签页又记了）就不动它——那次写更新。
 */
export async function confirmRestored(
  pj: string,
  ref: ProjectDocumentRef,
  plan: CandidatePlan,
): Promise<void> {
  if (plan.pj !== pj) return
  const cand = [plan.preferred, ...plan.fallbacks].find((c) => c?.id === ref.id)
  if (!cand) return
  if (cand.correctsIndex) correctRecordedProject(ref.id, pj)
  if (plan.session.kind !== 'ok' || remoteMissing) return
  if (rawCache(pj) !== plan.cacheSnapshot) return
  const cur = readProjectDocument(pj)
  const marker = readPending(pj)
  if (cur?.id === ref.id) {
    if (plan.migrate || marker) {
      const pending = marker ?? { base: readSeen(pj), gen: newGen() }
      writeCache(pj, cur, pending)
      await pushRemote(pj, cur, pending.gen)
    } else if (cand.sources.includes('backend_last')) {
      writeCache(pj, cur, undefined, Math.max(plan.remoteAt, readSeen(pj)))
    }
    return
  }
  if (cand.sources.includes('backend_last') && !plan.localWon) {
    writeCache(pj, { id: ref.id, name: ref.name }, undefined, Math.max(plan.remoteAt, readSeen(pj)))
  }
}

/**
 * 旧形状的薄适配：这个项目上次开着哪份排版，`null` = 没有。**新代码用 `planRestore` + `confirmRestored`**。
 * 与修复前的区别：迁移 / 补推只在**正文验证过**（磁盘上读得到，或本机有副本）之后才做——
 * 修复前它先推再说，缺失的槽位因此成了后端唯一的 last。
 */
export async function loadProjectDocument(projectId: string): Promise<ProjectDocumentRef | null> {
  const plan = await planRestore(projectId, { globalCurrent: null })
  // 旧语义：后端不可用时退回本机缓存（新语义里 preferred 为 null，缓存在 fallbacks）
  const pick =
    plan.preferred ?? (plan.session.kind === 'unavailable' ? plan.fallbacks.find((c) => c.sources.includes('local_cache')) : undefined)
  if (!pick) return null
  const ref = { id: pick.id, name: pick.name }
  const needsPush = plan.migrate || pick.pendingIntent
  if (needsPush) {
    const body = await probeAutosave(pick.id, projectId)
    if (body.kind !== 'found' && !hasLocalBody(pick.id)) return ref
  }
  if (plan.preferred) await confirmRestored(projectId, ref, plan)
  return ref
}

export function forgetProjectDocument(projectId: string): void {
  try {
    localStorage.removeItem(PREFIX + projectId)
  } catch {
    /* 同上 */
  }
}

/**
 * 文档有没有值得回来的东西。判据与 `documentStore.hasContent` 一致
 * （任一画布有对象 / 参考线，或不止一张画布）；`doc` 是激活画布的热态，
 * 可能比 `canvases` 里那份新，所以两处都看。
 */
export function documentHasContent(s: {
  doc: Pick<FigureDocument, 'objects' | 'guides'>
  canvases: readonly Pick<CanvasData, 'objects' | 'guides'>[]
}): boolean {
  if (s.doc.objects.length > 0 || s.doc.guides.length > 0) return true
  if (s.canvases.length > 1) return true
  return s.canvases.some((c) => c.objects.length > 0 || c.guides.length > 0)
}
