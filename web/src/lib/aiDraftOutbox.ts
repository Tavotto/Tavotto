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
import { nextWriteStamp, saveDraftStamped, textHash, type SaveResult } from './aiDraft'

/** 打字停多久才写后端：每个键都发请求没必要，丢的也只是最后半秒（pagehide 会冲刷） */
export const WRITE_DEBOUNCE_MS = 600
/** 写失败（离线 / 后端暂时不可用）后多久重试：文本一直留在发件箱里，不丢 */
export const RETRY_MS = 3000
/** 重试间隔的上限：前 3 次仍是 `RETRY_MS`，之后逐步加长到最多这么久。**槽不会因为时间放弃**——只要页面还在就一直试 */
export const RETRY_MAX_MS = 30_000
/** 连续失败超过这么久，才对界面说一句「还没保存到本机项目，正在重试」（短暂抖动不打扰用户） */
export const STALLED_AFTER_MS = 60_000
const retryDelay = (n: number): number => (n <= 3 ? RETRY_MS : Math.min(RETRY_MS * 2 ** (n - 3), RETRY_MAX_MS))
/** 同一个版本连续被判 stale（已见）重写的上限 */
const MAX_STALE_RETRIES = 5

export type OutboxEvent =
  /** 槽里最新的版本被服务器接受（或因超限被删）；`stamp` 是服务器那一行现在的戳 */
  | { kind: 'accepted'; stamp?: number; result: 'saved' | 'removed' | 'too_large' }
  /** 服务器上有本页没见过的更新版本（别的标签页写的）：本版放弃，调用方对账采纳服务器值 */
  | { kind: 'abandoned' }
  /** 连续失败超过 `STALLED_AFTER_MS`（`on: true`）/ 恢复（`on: false`）：界面据此显示一行安静的灰字 */
  | { kind: 'stalled'; on: boolean }

interface Entry {
  text: string
  /** 这一版内容的写入戳（post 时分配；只有「已见」的 stale 才会换更大的） */
  stamp: number
  version: number
  debounce?: ReturnType<typeof setTimeout>
  retry?: ReturnType<typeof setTimeout>
  retries: number
  staleRetries: number
  /**
   * **这一版编辑所基于的服务器戳**：编辑发生那一刻本页已知的最新服务器戳（`undefined` = 那时基线未知，首读失败）。
   * stale 回来时只拿**这一版自己的**基线和服务器戳比：≤ 基线 = 这一版是在已知的最新状态上编辑的，可以提升；
   * > 基线 = 有我们编辑时不知道的写入，放弃、交给对账采纳服务器值。之后对账读到的戳只影响**之后新编辑**的基线，不改已 post 的版本
   * （否则 A 在途时 B 被保存、对账把 B 的戳读进来，A 的 stale 就被误判成「已知」而盖掉 B）。
   */
  baseStamp?: number
}

interface Slot {
  project: string
  fileId: string
  entry: Entry | null
  inflight: Promise<'accepted' | 'unavailable' | 'abandoned'> | null
  /** 本页已知的最新服务器戳（GET 读到的、自己 PUT 被接受的、stale 回的）；-1 = 还没读到过任何戳。只用来给**新编辑**取 `baseStamp` */
  known: number
  /**
   * 被服务器以 413（超长）拒绝的那一版文本：服务端留了空墓碑、什么都没存，**不再重试、不再调度 PUT**，
   * 但文本要留在内存里——否则 hook 已卸载（切面板 / 关助手）时槽被清掉，重开面板读到的是空墓碑，用户写的超长文字就丢了。
   * 重新挂载时 hydrate 优先用它（并仍显示「太长」的提示）；对账不得用服务器的空文本覆盖它；用户 `post` 新版本（改短了）即清掉。
   */
  rejected: string | null
  /** 本页成功读到过一次（或写成功过）：基线不再是「未知」 */
  read: boolean
  /** 当前这一轮连续失败超过一分钟的计时器 / 是否已对界面宣布 */
  stallTimer?: ReturnType<typeof setTimeout>
  stalled: boolean
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
    s = { project, fileId, entry: null, inflight: null, known: -1, rejected: null, read: false, unknown: false, listeners: new Set(), stalled: false }
    slots.set(k, s)
  }
  return s
}

const emit = (slot: Slot, e: OutboxEvent): void => {
  for (const l of [...slot.listeners]) l(e)
}

/** 一轮失败开始：计时，超过一分钟还没好就宣布「卡住」 */
function beginFailing(slot: Slot): void {
  if (slot.stallTimer !== undefined || slot.stalled) return
  slot.stallTimer = setTimeout(() => {
    slot.stallTimer = undefined
    if (!slot.entry) return
    slot.stalled = true
    emit(slot, { kind: 'stalled', on: true })
  }, STALLED_AFTER_MS)
}
/** 这一轮失败结束（服务器处理了 / 放弃 / 槽空了） */
function endFailing(slot: Slot): void {
  clearTimeout(slot.stallTimer)
  slot.stallTimer = undefined
  if (slot.stalled) {
    slot.stalled = false
    emit(slot, { kind: 'stalled', on: false })
  }
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
  slot.rejected = null // 用户有了新版本（比如改短了）：被拒的旧文本不再需要保留
  const entry: Entry = {
    text,
    stamp: nextWriteStamp(),
    version: ++versionCounter,
    retries: 0,
    staleRetries: 0,
    // 这一版的基线：编辑发生此刻本页已知的最新服务器戳；基线未知（首读失败、还没有写成功过）就是 undefined
    baseStamp: slot.unknown ? undefined : slot.known,
  }
  slot.entry = entry
  entry.debounce = setTimeout(() => void send(slot, false), WRITE_DEBOUNCE_MS)
}

/** 槽里有没有未落定的版本 */
export const has = (project: string, fileId: string): boolean => !!slots.get(keyOf(project, fileId))?.entry

/** 这个目标的写入是否已连续失败超过一分钟（重新挂载时界面用它给初值） */
export const isStalled = (project: string, fileId: string): boolean => !!slots.get(keyOf(project, fileId))?.stalled

/** 未落定的版本的文本（重新挂载同一目标时先显示它）；没有 = null */
export function peek(project: string, fileId: string): string | null {
  return slots.get(keyOf(project, fileId))?.entry?.text ?? null
}

/** 被 413 拒绝、但仍需让用户看到的那一版文本（见 `Slot.rejected`）；没有 = null */
export const peekRejected = (project: string, fileId: string): string | null => slots.get(keyOf(project, fileId))?.rejected ?? null

/** 订阅一个目标的结果；返回取消订阅。槽与未落定的版本不随订阅者消失 */
export function subscribe(project: string, fileId: string, listener: (e: OutboxEvent) => void): () => void {
  const slot = slotOf(project, fileId)
  slot.listeners.add(listener)
  return () => void slot.listeners.delete(listener)
}

/** 对账的 GET 成功了（不管文本有没有被采用）：基线不再是「未知」。**不推进 `known`**——没被采用的读取不改变「正在显示的文本」对应哪个服务器戳 */
export function noteRead(project: string, fileId: string): void {
  const slot = slotOf(project, fileId)
  slot.read = true
  slot.unknown = false
}

/**
 * **推进基线**：只有两种情况调用——对账确实把服务器文本应用到了输入框（或输入框里本来就是同一份文本），
 * 以及本页自己的写入被接受（在发件箱内部）。基线 = 输入框当前显示的那份文本所对应的服务器戳；
 * 没被应用的读取（本地还有未落定的版本，服务器文本并没有采用）不能推进它，否则用户接着输入时，
 * 新版本会把别处刚保存的稿的戳当成自己的基线、在 stale 时盖掉它。
 */
export function advance(project: string, fileId: string, stamp: number | undefined): void {
  const slot = slotOf(project, fileId)
  if (stamp !== undefined) slot.known = Math.max(slot.known, stamp)
  slot.read = true
  slot.unknown = false
}

/** 第一次读失败 / 超时：不知道服务器上有没有更新的版本（只在一次都没读成功过时成立） */
export function markUnknown(project: string, fileId: string): void {
  const slot = slotOf(project, fileId)
  if (!slot.read) slot.unknown = true
}

/** 本页见过这个目标的服务器戳吗（hook 判「第一次读成功过没有」） */
export const hasSeen = (project: string, fileId: string): boolean => (slots.get(keyOf(project, fileId))?.read ?? false)

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

/** 普通 PUT 最多等这么久；超时按 `unavailable` 处理（走正常的退避重试）。半开连接上的请求不会自己结束 */
export const INFLIGHT_TIMEOUT_MS = 15_000

function send(slot: Slot, keepalive: boolean): Promise<'accepted' | 'unavailable' | 'abandoned'> {
  const entry = slot.entry
  if (!entry) return Promise.resolve('accepted')
  clearTimers(entry)
  if (slot.inflight && !keepalive) return slot.inflight // 在途的落地时会检查并补发更新的版本
  const v = entry.version
  const attempt = saveDraftStamped(slot.project, slot.fileId, entry.text, keepalive, entry.stamp)
  if (keepalive) {
    // keepalive 是「发出去就不管」的尽力尝试：**不替换被跟踪的普通请求**（它的连接半开挂住时，原来那次普通请求
    // 成功了也清不掉 inflight，之后的编辑就永远复用一个卡住的 promise）。它只在落地时参与「等戳同内容」判定与槽的收尾
    // **有界兜底**：keepalive 不进 `inflight`，连接半开时它的 promise 永远不落地——而上面的 `clearTimers` 已清掉了这一版唯一的
    // 定时器，页面留在后台（没有后续 visibilitychange）就再没有任何重试，被丢弃时最新草稿丢失。所以补一个重试定时器：
    // 到点还没落地就走一次**普通** PUT（同戳同内容，服务器按戳 / text_hash 幂等，不会写两次；`send` 遇到在途请求复用它，
    // 同槽不会并发两个 PUT）。普通 PUT 失败走既有的退避重试（`retries` 计数、上限 `RETRY_MAX_MS`），不会无界死循环；
    // keepalive 先落地则 `land` / `clearTimers` 会清掉它。
    entry.retry = setTimeout(() => void send(slot, false), RETRY_MS)
    return attempt.then(({ result, stamp, textHash }) => land(slot, v, result, stamp, textHash))
  }
  // 普通请求：inflight 只跟踪它，并加超时。超时后按 unavailable 走重试；迟到的真实结果仍然会落地（可能已经写成功了）
  let timedOut = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<{ result: SaveResult; stamp?: number; textHash?: string }>((resolve) => {
    timer = setTimeout(() => {
      timedOut = true
      resolve({ result: 'unavailable' })
    }, INFLIGHT_TIMEOUT_MS)
  })
  const p: Promise<'accepted' | 'unavailable' | 'abandoned'> = Promise.race([attempt, timeout]).then(({ result, stamp, textHash }) => {
    clearTimeout(timer)
    if (slot.inflight === p) slot.inflight = null
    return land(slot, v, result, stamp, textHash)
  })
  void attempt.then(({ result, stamp, textHash }) => {
    if (timedOut) void land(slot, v, result, stamp, textHash) // 超时之后才回来的结果也要处理（它可能已经把这一版写成功了）
  })
  slot.inflight = p
  return p
}

/**
 * 一次落地（不论结果、不论是不是最新版本）：先按结果处理（`landCore`），然后统一过**槽不变式**——
 * 槽里还有未落定的版本，却既没有在途请求、也没有任何定时器（防抖 / 重试），就补一个，不逐个分支去补。
 * 典型的漏洞：A 在途时编辑出 B，B 的防抖到点后复用了 A 的 promise（清掉了自己的定时器）；A 落地为 unavailable，
 * 因为 A 已不是最新版本不安排重试，B 就既没有防抖也没有重试，永远挂在发件箱里。
 */
async function land(
  slot: Slot,
  v: number,
  result: SaveResult,
  serverStamp: number | undefined,
  serverHash?: string,
): Promise<'accepted' | 'unavailable' | 'abandoned'> {
  const r = await landCore(slot, v, result, serverStamp, serverHash)
  ensureScheduled(slot, r)
  return r
}

function ensureScheduled(slot: Slot, last: 'accepted' | 'unavailable' | 'abandoned'): void {
  const e = slot.entry
  if (!e || slot.inflight) return
  if (e.debounce || e.retry) return
  if (last === 'unavailable') {
    beginFailing(slot)
    e.retries++
    e.retry = setTimeout(() => void send(slot, false), retryDelay(e.retries))
  } else {
    void send(slot, false)
  }
}

async function landCore(
  slot: Slot,
  v: number,
  result: SaveResult,
  serverStamp: number | undefined,
  serverHash: string | undefined,
): Promise<'accepted' | 'unavailable' | 'abandoned'> {
  const entry = slot.entry
  const latest = !!entry && entry.version === v

  if (result === 'unavailable') {
    // 没写成：文本还在槽里。只有最新版本替自己重试（旧版本已被取代，新版本有自己的防抖 / 冲刷）
    // **不因时间放弃**：只要页面还在就一直试，间隔有上限的退避；页面回到可见 / 网络恢复时立即再试一次
    if (entry) beginFailing(slot)
    if (entry && latest) {
      entry.retries++
      clearTimeout(entry.retry)
      entry.retry = setTimeout(() => void send(slot, false), retryDelay(entry.retries))
    }
    return 'unavailable'
  }

  // 同一版本的另一次尝试（普通 PUT 与 pagehide keepalive 重叠）先写进去了：服务器回 stale，但它那一行的戳
  // **就是这一版自己的戳**——这是本版本已被接受，不是别处的写入。按已接受收尾，不能当成跨标签页替换而放弃。
  // 判据只有一条：**戳相等 且 服务器那一行的文本哈希等于本版文本的哈希**（内容身份，不靠本地计数）。
  // client 段只有 10 位，两个页面仍有 1/1024 的同戳概率——戳相等但内容不同的是别的页面赢了，按普通 stale 规则走（放弃，交给对账）。
  //（误放弃会让 draftSnapshot 在对账完成前拿到上一版的戳，提交记错戳、被接受后清不掉草稿）
  if (
    result === 'stale' &&
    entry &&
    latest &&
    serverStamp !== undefined &&
    serverStamp === entry.stamp &&
    serverHash !== undefined &&
    serverHash === textHash(entry.text)
  )
    result = 'saved'

  if (result === 'stale') {
    // 服务器说：它那里有比这一版**更新的戳**，这一版没被采纳。只拿**这一版自己的基线**（`baseStamp`）和服务器戳比：
    // ≤ 基线 = 本版是在已知的最新状态上编辑的（墙钟回拨属于这种）——仍是最新版本时换更大的戳重写（有界）；
    // > 基线 = 有我们编辑时不知道的写入（别的标签页写了更新稿）：不提升、不重写，放弃这一版（调用方对账采纳服务器值）。
    // 之后对账读到的戳不会改已经 post 的版本的基线，所以 A 在途时 B 被保存、对账读到 B，A 的 stale 回来仍然被放弃，不盖掉 B。
    // 基线未知（首读失败时 post 的版本）一律按「可提升」：用户的输入不能丢。
    const promotable = serverStamp === undefined || !entry || entry.baseStamp === undefined || serverStamp <= entry.baseStamp
    // stale 回的戳只说明服务器有更新的文本——本页没有采用它，不推进基线
    if (!entry) return 'accepted'
    if (!latest) return send(slot, false) // 更新的版本带着自己的戳去写；旧版本不换戳、不重写
    if (!promotable) {
      clearTimers(entry)
      slot.entry = null
      endFailing(slot)
      emit(slot, { kind: 'abandoned' })
      return 'abandoned'
    }
    // 无论是否到上限都**推进戳**：`observeStamp` 已被 stale 回的服务器戳抬高下限，新戳一定比它大
    entry.stamp = nextWriteStamp()
    if (++entry.staleRetries > MAX_STALE_RETRIES) {
      // 有界：两端互相压戳时别立刻再冲一轮——退避一段时间后**重新走一遍完整的 stale 判定**（计数清零；仍可提升就用上面推进过的新戳重写）。
      // 这种状态也算「连续失败」：超过一分钟就出灰字，不是静默循环
      entry.staleRetries = 0
      entry.retries++
      beginFailing(slot)
      clearTimeout(entry.retry)
      entry.retry = setTimeout(() => void send(slot, false), retryDelay(entry.retries))
      return 'unavailable'
    }
    return send(slot, false)
  }

  // saved / removed / too_large：服务器处理了这一版
  if (serverStamp !== undefined) {
    slot.known = Math.max(slot.known, serverStamp)
    slot.read = true
  }
  // 服务器处理过一次写入并回了它那一行的戳（保存、或空文本留下的墓碑）= 基线已知：退出「首读失败按已见」的模式，
  // 之后再遇到没见过的戳就按跨标签页冲突规则放弃，不再以本地为准盖掉别处更新的稿。
  // 超限（413）服务器什么都没存、也没回戳，基线仍未知
  if (serverStamp !== undefined) slot.unknown = false
  if (entry && latest) {
    clearTimers(entry)
    slot.entry = null
    if (result === 'too_large') slot.rejected = entry.text // 不重试，但文本留着（hook 卸载后重开也拿得回）
    endFailing(slot)
    emit(slot, { kind: 'accepted', result, ...(serverStamp !== undefined ? { stamp: serverStamp } : {}) })
    return 'accepted'
  }
  if (entry) return send(slot, false) // 落地时本地已有更新的版本：以本地为准再写一次
  return 'accepted'
}

/** 页面回到可见 / 网络恢复：有失败待重试的槽立即再试一次（不等退避） */
export function retryNow(): void {
  for (const slot of slots.values()) {
    if (!slot.entry || slot.inflight) continue
    clearTimers(slot.entry)
    void send(slot, false)
  }
}

/** 仅测试：发件箱是模块级状态，用例之间要能回到空 */
export function __resetOutboxForTests(): void {
  for (const slot of slots.values()) {
    if (slot.entry) clearTimers(slot.entry)
    clearTimeout(slot.stallTimer)
  }
  slots.clear()
  versionCounter = 0
}

// 页面离开 / 转入后台：对所有未落定的槽发 keepalive（在模块级监听，与任何 hook 实例的生命周期无关）
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => flushAll(true))
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushAll(true)
    else retryNow()
  })
  window.addEventListener('online', () => retryNow())
}
