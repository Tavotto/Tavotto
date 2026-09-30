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
 */
import { ApiError, fetchLayoutSession, putLayoutSessionLast } from '@/lib/api'
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
 * 「上次开着的」改回旧的那份。读之前也先等它排空（`loadProjectDocument`）。
 */
let remoteTail: Promise<void> = Promise.resolve()

function pushRemote(projectId: string, ref: ProjectDocumentRef, gen: string | null): void {
  if (remoteMissing) return
  remoteTail = remoteTail.then(() =>
    putLayoutSessionLast({ doc_id: ref.id, name: ref.name }, projectId).then(
      (res) => {
        // 后端确认了：记下它给的时刻（后端的钟）；本机这条还是这次推的那条就摘掉「待确认」
        // （期间又记了新的就只更新时刻、不动它的待确认）
        const at = finite(res?.last?.at) ? Math.max(res.last.at, readSeen(projectId)) : readSeen(projectId)
        const cur = readProjectDocument(projectId)
        const pending = readPending(projectId)
        if (gen !== null && pending?.gen === gen) writeCache(projectId, ref, undefined, at)
        // 确认的是更早的一次（本标签页的写入串行，后记的那条还在排队 / 失败）：后端此刻那条就是
        // 我们自己更早的写，不比待确认的新——把待确认的基准抬到它，免得下次读时被自己的旧写盖掉
        else if (cur) writeCache(projectId, cur, pending ? { base: Math.max(pending.base, at), gen: pending.gen } : undefined, at)
      },
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 404) remoteMissing = true
        /* 其余失败：本机缓存带着 pendingAt 记着，下次读的时候与后端比新旧、需要就重推 */
      },
    ),
  )
}

/** 记「这个项目现在开着这份排版」：本机缓存（先标「待确认」）+ 后端（权威）。 */
export function rememberProjectDocument(projectId: string, ref: ProjectDocumentRef): void {
  const pending = { base: readSeen(projectId), gen: newGen() }
  writeCache(projectId, ref, pending)
  pushRemote(projectId, ref, pending.gen)
}

/**
 * 只问后端：`ProjectDocumentRef` = 记着的那份；`null` = 后端有这组端点、这个项目还没记过；
 * `undefined` = 后端没有这组端点 / 此刻不可达（调用方退回本机的旧逻辑）。
 * 读到了就顺手更新本机缓存。
 */
export async function fetchRemoteProjectDocument(
  projectId: string,
): Promise<ProjectDocumentRef | null | undefined> {
  await remoteTail
  // GET 在路上时本机缓存被改过（这个或同 origin 的别的标签页又记了一次，它的 PUT 甚至可能
  // 已经确认、摘掉了待确认标记）：那次写比这份回包新，回包作废、不许覆盖（#719 Codex P2）。
  // 判据是缓存原文本身——localStorage 同 origin 共享，别的标签页写的也看得见
  const before = rawCache(projectId)
  const remote = await fetchLayoutSession(projectId)
  if (remote === undefined) return undefined
  if (rawCache(projectId) !== before) return readProjectDocument(projectId)
  // 本机有一条后端还没确认的记录，而且写下它之后后端没收过别的写（后端那条的 `at` 不晚于写下时
  // 见过的 `base`；或后端还没记过）：本机为准，重推一次。两边比的都是后端的钟
  const pending = readPending(projectId)
  const local = pending === null ? null : readProjectDocument(projectId)
  if (pending !== null && local && (!remote.last || remote.last.at <= pending.base)) {
    pushRemote(projectId, local, pending.gen)
    return local
  }
  if (!remote.last) return null
  const ref = { id: remote.last.doc_id, name: remote.last.name }
  writeCache(projectId, ref, undefined, Math.max(remote.last.at, readSeen(projectId)))
  return ref
}

/**
 * 这个项目上次开着哪份排版：后端优先；后端没有这组端点时读本机缓存（旧逻辑）；后端有、
 * 但还没记过时，本机缓存是同一个 origin 升级上来的旧记录——用它，并推一份上去。
 */
export async function loadProjectDocument(projectId: string): Promise<ProjectDocumentRef | null> {
  const remote = await fetchRemoteProjectDocument(projectId)
  if (remote) return remote
  const local = readProjectDocument(projectId)
  if (remote === null && local) {
    // 迁移也是一次写：先标待确认再推。推失败时这条仍带着待确认，下次读（同一个 origin）会重推；
    // 不标的话失败即丢，后端一直是 null（#719 Codex P2）
    const pending = readPending(projectId) ?? { base: readSeen(projectId), gen: newGen() }
    writeCache(projectId, local, pending)
    pushRemote(projectId, local, pending.gen)
  }
  return local
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
