/**
 * 改图助手的**未发送草稿**与**待确认提交账本**（ADR 0132）：存**后端**（`data_dir` 里的 ai_history 库，
 * 与会话历史同一边界），不存浏览器。
 *
 * 为什么不是 localStorage：桌面端口被占会换 origin，localStorage 随之整个清空——用户写了一半的指令不该
 * 因此消失（用户裁决 2026-10-15：不得以 localStorage 的便利代替持久化设计）。这个模块**不碰任何浏览器存储**。
 *
 * 语义（后端为准，前端只是它的客户端）：
 * - 主语 (项目, 面板 fileId[, 请求 id])；项目取调用方传入的认领 pj，null 一律不读不写。
 * - 草稿全文覆盖、最后写入为准；**删除必须带条件**（`deleteDraftIf` 的原文 / `removeAccepted` 的账本原文）：
 *   另一个标签页后写的新稿不会被这一边的确认抹掉。
 * - 账本按 rid 一条：两个标签页同时发时各自的 rid 都在；确认 / 拒绝只清自己那条。
 * - 单稿 ≤ 20 KB（后端 413 `ai_draft_too_large`，不截断）、每项目 30 份、30 天过期——都在后端执行。
 * - 所有请求失败都**不抛**：回结果枚举，调用方（`useAiDraft`）在内存里保留并重试。
 *
 * 只在桌面 / 浏览器模式用：Codex 内嵌画布与 playground 没有助手面板，不 import 本模块
 * （`aiDraft.hosts.test.ts`）。**隐私**：草稿全文与请求 id 不进日志、遥测、诊断包 frontend-state、
 * interaction trace（`aiDraft.privacy.test.ts` + 后端 `tests/test_ai_drafts.py`）。
 */
import {
  acceptAiPending,
  ApiError,
  deleteAiDraftIf,
  fetchAiDraft,
  fetchAiPendings,
  putAiDraft,
  putAiPending,
} from '@/lib/api'

/** 账本 accept / 草稿被服务端清掉之后广播，`useAiDraft` 听到就对账一次（输入框以后端为准） */
export const AI_DRAFT_CHANGED_EVENT = 'mm:ai-draft-changed'

/**
 * **指令最大长度（UTF-8 字节）：草稿与待确认账本用同一个上限，发送也受它约束**。↔ 后端 `ai_history.INSTRUCTION_MAX_BYTES`
 * 严格同源（`tests/test_ai_drafts.py` 结构性读这一行对拍）。两个上限不一致正是「快照写草稿 413、发送却照样继续、
 * 草稿清不掉」的根。前端据此在输入框超限时直接提示并禁用发送，后端是权威。必须是十进制整数字面量（同源对读它）。
 */
export const INSTRUCTION_MAX_BYTES = 20_480
export const DRAFT_MAX_BYTES = INSTRUCTION_MAX_BYTES

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
  /**
   * **草稿文本，永远只是草稿**（输入框显示的就是它）。待确认提交的原文在 `pendings[].text` 里，是另一份状态：
   * 两个标签页（A 发出待确认、B 之后存了更新的草稿）时，不能让提交原文冒充草稿盖掉输入框，
   * 也不能让提交被确认时的清理动到草稿（Codex #1098）。没有草稿时为空串。
   */
  text: string
  /** 服务器上这一行（含墓碑）的写入戳；`fetchDraftState` 读回来时有 */
  stamp?: number
  /** 全部未落定的提交（原文 + 目标）；每条各自还原成「已发出未确认」行，可查询，不进输入框 */
  pendings?: { rid: string; text: string; sent: DraftTarget }[]
}

/** `saved` 已存；`removed` 空文本 = 已删；`too_large` 后端 413（旧稿也已删）；`unavailable` 没存成（网络 / 后端错），调用方重试 */
export type SaveResult = 'saved' | 'removed' | 'too_large' | 'unavailable' | 'stale'

/**
 * 写入戳：单调递增的整数（墙钟微秒；同一页内再 +1 保证严格递增），**跨标签页可比**（同一台机器的墙钟）。
 * 服务端 `ai_drafts.stamp` 只接受比已存戳更新的写入——pagehide 的 keepalive 拿不到响应、前端排队救不了，
 * 所以「旧请求赢」必须在服务端就不可能发生。收到服务端的戳（含被判 stale 时回的那个）就抬高本页的下限，
 * 之后的写入一定比它新。
 */
let lastStamp = 0
/** 低 10 位是本页的随机 client 段：不同页面（标签页）生成的戳在这一段上几乎总是不同（两页碰巧同段的概率 1/1024，
 * 那种情况下还有发件箱的 attempts 判定兜底），同时仍保持单调、可比较 */
const CLIENT_BITS = 1024
const randomClientId = (): number => {
  try {
    // 取低 10 位用位掩码（1024 整除 65536，本来就没有偏差；掩码写法让这一点一眼可见）
    return crypto.getRandomValues(new Uint16Array(1))[0] & (CLIENT_BITS - 1)
  } catch {
    return Math.floor(Math.random() * CLIENT_BITS)
  }
}
let clientId = randomClientId()
/**
 * 写入戳 = （按 1024 µs 对齐的时间部分）+ 本页的随机 client 段。量级仍是墙钟微秒，保持单调（页内每次至少 +1024）、跨标签页可比较，
 * 后端只比较数字大小。两个页面即使在同一毫秒内各发一次写入，戳也不会相等——相等的戳只可能来自**同一页同一版本**的重复尝试
 * （普通 PUT 与 pagehide 的 keepalive 重叠）。
 */
export function nextWriteStamp(): number {
  const t = Math.max(lastStamp + 1, Date.now() * 1000)
  let s = Math.floor(t / CLIENT_BITS) * CLIENT_BITS + clientId
  if (s < t) s += CLIENT_BITS
  lastStamp = s
  return s
}
/** 仅测试：写入戳下限是模块级状态（等于「新开一个页面」），用例之间要能回到从 0 起步；可指定 client 段来模拟另一个页面 */
export const __resetWriteStampForTests = (client?: number): void => {
  lastStamp = 0
  clientId = client ?? randomClientId()
}
export function observeStamp(serverStamp: unknown): void {
  if (typeof serverStamp === 'number' && Number.isFinite(serverStamp) && serverStamp > lastStamp) lastStamp = serverStamp
}

/**
 * 文本的 64 位 FNV-1a 哈希（UTF-8 字节，16 位十六进制），与后端 `ai_history.text_hash` 逐字节同算法。
 * 只用来在「stale 且戳相等」时确认服务器那一行的文本是不是本版自己的文本；`aiDraft.test.ts` 与 `test_ai_drafts.py` 用同一组固定文本钉住一致。
 */
export function textHash(text: string): string {
  let h = 0xcbf29ce484222325n
  for (const b of new TextEncoder().encode(text)) h = ((h ^ BigInt(b)) * 0x100000001b3n) & 0xffffffffffffffffn
  return h.toString(16).padStart(16, '0')
}

const byteLength = (text: string): number => {
  try {
    return new TextEncoder().encode(text).length
  } catch {
    return text.length * 3
  }
}

/** 现在就能判：写之前先给界面一个「过长」的答案，不等防抖与往返 */
export const isDraftTooLarge = (text: string): boolean => byteLength(text) > DRAFT_MAX_BYTES

/**
 * 读后端此刻的状态：草稿文本与未落定的提交是**两份独立的状态**，各自原样回来。
 * **没有草稿也没有提交 = `{ text: '' }`（成功）；读失败 / 没有项目认领 = `null`（不知道）**——
 * 调用方（`useAiDraft.reconcile`）靠这个区分「服务器说空」与「没读到」，后者绝不能当成空去清输入框。
 */
export async function fetchDraftState(
  project: string | null,
  fileId: string | null,
  signal?: AbortSignal,
): Promise<Draft | null> {
  if (!project || !fileId) return null
  let doc
  try {
    doc = await fetchAiDraft(fileId, project, signal)
  } catch {
    return null
  }
  const pendings = (doc.pendings ?? []).map((p) => ({ rid: p.rid, text: p.text, sent: p.sent as DraftTarget }))
  // 读到的戳（含墓碑的）立刻抬高本页的下限：墙钟回拨 / 新开页面从 0 起步，之后发出的写入也一定比库里的新
  observeStamp(doc.stamp)
  return {
    project,
    fileId,
    text: doc.draft?.text ?? '',
    stamp: doc.stamp ?? 0,
    ...(pendings.length ? { pendings } : {}),
  }
}

/** `fetchDraftState` 的简化形：什么都没有 / 读失败都回 null（只关心「有没有东西」的调用方用） */
export async function loadDraft(project: string | null, fileId: string | null): Promise<Draft | null> {
  const d = await fetchDraftState(project, fileId)
  return d && (d.text !== '' || d.pendings?.length) ? d : null
}

/** 写草稿**文本**（全文覆盖）。`keepalive`：pagehide / 卸载时的冲刷 */
export async function saveDraft(
  project: string | null,
  fileId: string | null,
  text: string,
  keepalive = false,
): Promise<SaveResult> {
  return (await saveDraftStamped(project, fileId, text, keepalive)).result
}

/** 同 `saveDraft`，另回这次写入被服务端接受时的写入戳（`stamp`；没被接受时无）——发送前要拿它记进账本 */
export async function saveDraftStamped(
  project: string | null,
  fileId: string | null,
  text: string,
  keepalive = false,
  /**
   * 这一版内容的写入戳。**戳代表「内容版本的时刻」（编辑发生时分配），不是「发送尝试的时刻」**：同一版本的所有 PUT、
   * 重试、keepalive 都带同一个戳，旧版本无论何时重试，戳都更小、一定被服务器拒绝。不传才在此刻现取（一次性写入用）。
   */
  stamp: number = nextWriteStamp(),
): Promise<{ result: SaveResult; stamp?: number; textHash?: string }> {
  if (!project || !fileId) return { result: 'unavailable' }
  try {
    // 服务端只接受比已存戳更新的写入，旧版本不管何时落地都不会覆盖新的
    const res = await putAiDraft(fileId, text, project, keepalive && byteLength(text) < 60 * 1024, stamp)
    observeStamp(res.stamp)
    if (res.stale) return { result: 'stale', stamp: res.stamp, textHash: res.text_hash } // stale 时回的是服务器当前那一行的戳与文本哈希
    return { result: text === '' ? 'removed' : 'saved', stamp: res.stamp }
  } catch (e) {
    if (e instanceof ApiError && e.status === 413) {
      // 超限写入服务端也留了墓碑（带戳）：把戳读出来，客户端据此抬高下限、发件箱退出「基线未知」
      const st = (e.body as { stamp?: unknown } | undefined)?.stamp
      if (typeof st === 'number' && Number.isFinite(st)) {
        observeStamp(st)
        return { result: 'too_large', stamp: st }
      }
      return { result: 'too_large' }
    }
    return { result: 'unavailable' }
  }
}

/** 条件删除：后端的文本仍等于 `ifText` 才删。回是否删了 */
export async function deleteDraftIf(project: string | null, fileId: string | null, ifText: string): Promise<boolean> {
  if (!project || !fileId) return false
  try {
    return (await deleteAiDraftIf(fileId, ifText, project)).deleted
  } catch {
    return false
  }
}

/**
 * 点发送：在 spawn 之前把这条提交（原文 + 目标 + 脚本 + 提交时草稿的写入戳 `draftStamp`）记进后端账本。
 * `draftStamp` 让确认之后的清理按「草稿自提交后没被改过」判（后端 `accept`），不比文本。**回是否写成功**：
 * 没写成（网络 / 后端错 / 超限）就不该发——账本是「响应丢了之后还能查到这条」的唯一线索，
 * 「尽力写、照样发」会让丢响应后的刷新没有 rid 可查、同一条指令换新 id 再执行一遍（Codex #1098）。
 * 没有项目认领时无从记账，回 true 由调用方照旧处理（那时本来也发不出去）。
 */
export async function markSubmitted(
  project: string | null,
  fileId: string | null,
  text: string,
  rid: string,
  sent: DraftTarget,
  script: string | null = null,
  draftStamp: number | null = null,
  supersedes: string[] = [],
): Promise<LedgerWrite> {
  if (!project || !fileId) return 'ok'
  try {
    await putAiPending(rid, fileId, text, sent, project, script, draftStamp, supersedes)
    return 'ok'
  } catch (e) {
    if (e instanceof ApiError && e.status < 500) {
      // 明确的拒绝原因，抛出去让界面说一句话（不是「记账失败」）：这个面板已发出未确认的提交满了 / 要取代的旧请求其实已登记 /
      // 同面板或同脚本上还有没落定的提交（后端在写账本的事务里判，界面据此还原并查询它们）
      const code = e.body?.code
      if (
        e.status === 409 &&
        (code === 'ai_pending_limit' || code === 'ai_supersede_conflict' || code === 'ai_pending_unresolved')
      )
        throw e
      return 'rejected' // 明确的 4xx（400 参数错 / 413 超限）：后端确定没写
    }
    // 网络错误 / 超时 / 5xx / 读不出响应：**不知道后端写没写**——可能已经写入、只是响应丢了。
    // 不能当成「没有写入」（那会换新 rid 重发，旧行随后被还原、同一条指令执行两次）：由调用方保持同一个 rid 幂等重试
    return 'unknown'
  }
}

/** 账本写入的结局：`ok` 写成了；`rejected` 后端明确拒绝（4xx，确定没写）；`unknown` 不知道（响应丢了 / 5xx，可能已经写入） */
export type LedgerWrite = 'ok' | 'rejected' | 'unknown'

/**
 * 本项目全部未落定的提交（跨面板，带 script）；发起它的面板已被删 / 换图时靠它找回。
 * **读失败 / 没有项目认领回 `null`（不知道），不是空数组（确认没有）**——调用方据此决定能不能放开发送。
 */
export async function loadProjectPendings(
  project: string | null,
  signal?: AbortSignal,
): Promise<{ file: string; rid: string; text: string; sent: DraftTarget; script: string | null }[] | null> {
  if (!project) return null
  try {
    return (await fetchAiPendings(project, signal)).pendings.map((p) => ({ ...p, sent: p.sent as DraftTarget }))
  } catch {
    return null
  }
}

/** **只在后端给出会话 id（请求已被接受）之后**调用：清这条账本；草稿文本仍等于提交时的原文才一并删 */
export async function removeAccepted(project: string | null, fileId: string | null, rid: string): Promise<void> {
  if (!project || !fileId) return
  try {
    await acceptAiPending(rid, fileId, project)
  } catch {
    /* 同上 */
  }
}
