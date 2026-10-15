/**
 * 草稿发件箱（ADR 0132）：**未落定的草稿写入由它持有，与 `useAiDraft` 的实例、挂载的目标无关**。
 *
 * 为什么不放在 hook 里：用户打完字、防抖（600 ms）到期之前切了面板 / 项目 / 关了助手，hook 实例随即退役。
 * 旧做法是 cleanup 里发一次 keepalive PUT，这次暂时失败就没人再替旧目标重试，重开后显示服务器上的旧值——最新的编辑静默丢失。
 * 退役的 hook 又不能自己乱写（会拿旧文本去覆盖重新挂载后保存的新草稿）。两头的结构解法：
 *
 * - 每个 (项目, 面板文件) 一个槽，槽里只放「最新的本地版本：文本 + 这一版的写入戳」。**PUT、防抖、重试、stale 处置全由这里负责**，
 *   直到服务器接受了这个版本、或它被同一个目标上更新的版本取代、或被判定放弃（别的标签页写了更新稿），才清掉；
 * - hook 只做两件事：编辑时 `post` 一个版本、订阅槽的结果（推进 `draftStamp` / 触发对账）。退役实例不发任何写入；
 * - 同一目标重新挂载时，槽里有未落定的版本就先显示它（`peek`），对账也不覆盖它；
 * - 页面离开（pagehide / 转入后台）对所有有未落定版本的槽发 keepalive（尽力而为，见下面的已知限制）。
 *
 * **戳代表内容版本的时刻**：每次 `post` 分配一个戳，这一版的所有 PUT / 重试 / keepalive 都带它；旧版本无论何时重试，戳都更小、
 * 一定被服务器拒绝。被判 stale 时按「已见版本」处置（见 `land`）。
 *
 * **已知限制**：页面真的关掉（pagehide）时发出的 keepalive 也可能失败，那一刻内存里的发件箱随页面消失；
 * 不用 localStorage 兜底（规格禁止拿它代替持久化设计）。丢的只有「最后一次防抖窗口内、且最后一次 keepalive 也失败」的那一版。
 */
import { nextWriteStamp, saveDraftStamped, type SaveResult } from './aiDraft'

/** 打字停多久才写后端：每个键都发请求没必要，丢的也只是最后半秒（pagehide 会冲刷） */
export const WRITE_DEBOUNCE_MS = 600
/** 写失败（离线 / 后端暂时不可用）后多久重试：文本一直留在发件箱里，不丢 */
export const RETRY_MS = 3000
/** 连续失败重试的上限（约 1 分钟）；之后停下，等下一次冲刷（回到前台 / 再编辑）再试 */
const MAX_RETRIES = 20
/** 同一个版本连续被判 stale（已见）重写的上限 */
const MAX_STALE_RETRIES = 5

export type OutboxEvent =
  /** 槽里最新的版本被服务器接受（或因超限被删）；`stamp` 是服务器那一行现在的戳 */
  | { kind: 'accepted'; stamp?: number; result: 'saved' | 'removed' | 'too_large' }
  /** 服务器上有本页没见过的更新版本（别的标签页写的）：本版放弃，调用方对账采纳服务器值 */
  | { kind: 'abandoned' }

interface Entry {
  text: string
  /** 这一版内容的写入戳（post 时分配；只有「已见」的 stale 才会换更大的） */
  stamp: number
  version: number
  debounce?: ReturnType<typeof setTimeout>
  retry?: ReturnType<typeof setTimeout>
  retries: number
  staleRetries: number
}

interface Slot {
  project: string
  fileId: string
  entry: Entry | null
  inflight: Promise<'accepted' | 'unavailable' | 'abandoned'> | null
  /** 本页见过的服务器戳（GET 读到的、自己 PUT 被接受的、stale 回的） */
  seen: Set<number>
  /** 一次都没读成功过：编辑是在不知道服务器版本的情况下做的，stale 按「已见」处理（用户的输入不能丢） */
  unknown: boolean
  listeners: Set<(e: OutboxEvent) => void>
}

const slots = new Map<string, Slot>()
let versionCounter = 0

const keyOf = (project: string, fileId: string) => `${project}\u0000${fileId}`
function slotOf(project: string, fileId: string): Slot {
  const k = keyOf(project, fileId)
  let s = slots.get(k)
  if (!s) {
    s = { project, fileId, entry: null, inflight: null, seen: new Set(), unknown: false, listeners: new Set() }
    slots.set(k, s)
  }
  return s
}

const emit = (slot: Slot, e: OutboxEvent): void => {
  for (const l of [...slot.listeners]) l(e)
}

function clearTimers(entry: Entry): void {
  clearTimeout(entry.debounce)
  clearTimeout(entry.retry)
  entry.debounce = undefined
  entry.retry = undefined
}

/** 投递一个新版本（用户编辑）：取代槽里的旧版本（旧版本不再重试），防抖到点后发出 */
export function post(project: string, fileId: string, text: string): void {
  const slot = slotOf(project, fileId)
  if (slot.entry) clearTimers(slot.entry)
  const entry: Entry = {
    text,
    stamp: nextWriteStamp(),
    version: ++versionCounter,
    retries: 0,
    staleRetries: 0,
  }
  slot.entry = entry
  entry.debounce = setTimeout(() => void send(slot, false), WRITE_DEBOUNCE_MS)
}

/** 槽里有没有未落定的版本 */
export const has = (project: string, fileId: string): boolean => !!slots.get(keyOf(project, fileId))?.entry

/** 未落定的版本的文本（重新挂载同一目标时先显示它）；没有 = null */
export function peek(project: string, fileId: string): string | null {
  return slots.get(keyOf(project, fileId))?.entry?.text ?? null
}

/** 订阅一个目标的结果；返回取消订阅。槽与未落定的版本不随订阅者消失 */
export function subscribe(project: string, fileId: string, listener: (e: OutboxEvent) => void): () => void {
  const slot = slotOf(project, fileId)
  slot.listeners.add(listener)
  return () => void slot.listeners.delete(listener)
}

/** 记下本页读到的服务器戳（对账的 GET）；读成功 = 基线已知 */
export function observe(project: string, fileId: string, stamp: number | undefined): void {
  const slot = slotOf(project, fileId)
  if (stamp !== undefined) slot.seen.add(stamp)
  slot.unknown = false
}

/** 第一次读失败 / 超时：不知道服务器上有没有更新的版本（只在一次都没读成功过时成立） */
export function markUnknown(project: string, fileId: string): void {
  const slot = slotOf(project, fileId)
  if (slot.seen.size === 0) slot.unknown = true
}

/** 本页见过这个目标的服务器戳吗（hook 判「第一次读成功过没有」） */
export const hasSeen = (project: string, fileId: string): boolean => (slots.get(keyOf(project, fileId))?.seen.size ?? 0) > 0

/**
 * 现在就发（不等防抖）：直到槽空了、或遇到暂时失败（交给重试）为止，最多 4 轮（每轮落地时若又有更新的版本就接着发）。
 * `keepalive`：页面正在关。回 `pending: true` = 还有没落定的版本。
 */
export async function flush(project: string, fileId: string, keepalive = false): Promise<{ pending: boolean }> {
  const slot = slots.get(keyOf(project, fileId))
  if (!slot) return { pending: false }
  for (let i = 0; i < 4 && slot.entry; i++) {
    const r = await send(slot, keepalive)
    if (r !== 'accepted') break
  }
  return { pending: !!slot.entry }
}

/** 页面离开：所有有未落定版本的槽各发一次 keepalive（尽力而为） */
export function flushAll(keepalive = true): void {
  for (const slot of slots.values()) if (slot.entry) void send(slot, keepalive)
}

function send(slot: Slot, keepalive: boolean): Promise<'accepted' | 'unavailable' | 'abandoned'> {
  const entry = slot.entry
  if (!entry) return Promise.resolve('accepted')
  clearTimers(entry)
  if (slot.inflight && !keepalive) return slot.inflight // 在途的落地时会检查并补发更新的版本
  const v = entry.version
  const p = saveDraftStamped(slot.project, slot.fileId, entry.text, keepalive, entry.stamp).then(({ result, stamp }) => {
    if (slot.inflight === p) slot.inflight = null
    return land(slot, v, result, stamp)
  })
  slot.inflight = p
  return p
}

async function land(
  slot: Slot,
  v: number,
  result: SaveResult,
  serverStamp: number | undefined,
): Promise<'accepted' | 'unavailable' | 'abandoned'> {
  const entry = slot.entry
  const latest = !!entry && entry.version === v

  if (result === 'unavailable') {
    // 没写成：文本还在槽里。只有最新版本替自己重试（旧版本已被取代，新版本有自己的防抖 / 冲刷）
    if (entry && latest && entry.retries < MAX_RETRIES) {
      entry.retries++
      clearTimeout(entry.retry)
      entry.retry = setTimeout(() => void send(slot, false), RETRY_MS)
    }
    return 'unavailable'
  }

  if (result === 'stale') {
    // 服务器说：它那里有比这一版**更新的戳**，这一版没被采纳。只看「这个服务器戳本页见过没有」：
    // 见过 = 没有别处在我们不知情时写过，本版是用户在已知最新状态上的编辑——仍是最新版本时换更大的戳重写（有界）；
    // 没见过 = 别的标签页写了我们不知道的更新版本：不提升、不重写，放弃这一版（调用方对账采纳服务器值），A 晚到不盖掉 B。
    // 首读失败（`unknown`）时一律按「已见」：用户的输入不能丢。
    const seen = serverStamp === undefined || slot.unknown || slot.seen.has(serverStamp)
    if (serverStamp !== undefined) slot.seen.add(serverStamp)
    if (!entry) return 'accepted'
    if (!latest) return send(slot, false) // 更新的版本带着自己的戳去写；旧版本不换戳、不重写
    if (!seen) {
      clearTimers(entry)
      slot.entry = null
      emit(slot, { kind: 'abandoned' })
      return 'abandoned'
    }
    if (++entry.staleRetries > MAX_STALE_RETRIES) {
      // 有界：两端互相压戳时别没完没了，隔一会儿再试
      clearTimeout(entry.retry)
      entry.retry = setTimeout(() => void send(slot, false), RETRY_MS)
      return 'unavailable'
    }
    entry.stamp = nextWriteStamp()
    return send(slot, false)
  }

  // saved / removed / too_large：服务器处理了这一版
  if (serverStamp !== undefined) slot.seen.add(serverStamp)
  if (entry && latest) {
    clearTimers(entry)
    slot.entry = null
    emit(slot, { kind: 'accepted', result, ...(serverStamp !== undefined ? { stamp: serverStamp } : {}) })
    return 'accepted'
  }
  if (entry) return send(slot, false) // 落地时本地已有更新的版本：以本地为准再写一次
  return 'accepted'
}

/** 仅测试：发件箱是模块级状态，用例之间要能回到空 */
export function __resetOutboxForTests(): void {
  for (const slot of slots.values()) if (slot.entry) clearTimers(slot.entry)
  slots.clear()
  versionCounter = 0
}

// 页面离开 / 转入后台：对所有未落定的槽发 keepalive（在模块级监听，与任何 hook 实例的生命周期无关）
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => flushAll(true))
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushAll(true)
  })
}
