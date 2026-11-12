/**
 * 跨标签页的「这份文档已经在别的窗口开着」提示。
 *
 * 这是**尽力而为的警告，不是租约**：不广播释放、不阻止打开，只在两个标签页
 * 同时端着同一个 documentId 时告诉用户「同时编辑会互相覆盖」。真正拦住覆盖写
 * 的是自动保存的乐观并发（putAutosave 带基线 + 后端 409 stale_write），这一层
 * 只负责把问题**提前**到用户开始打字之前。
 *
 * 握手只有两句：新开的标签页广播 doc-open，已经端着同一份的标签页回一句
 * doc-held——收到回音的那个（后来者）弹提示。先来的那个不弹：它什么都没做错。
 *
 * 接管（ADR 0181）：后开的窗口选「在这里编辑」时发 `takeover-request`；持有方把没存的改动存好后回
 * `takeover-ack`（`flushed` = 存没存成），随后自己变成「被接管」。**加字段 / 加消息都兼容旧标签页**：
 * 旧页不认识这两条消息、什么都不回，请求方等满限时就直接接管（载入盘上最新版）——旧页没存的那点改动
 * 之后仍会按它自己的节奏写盘，撞上请求方的写就是 `stale_write`，由「存成副本」那条提示兜底（不丢）。
 *
 * BroadcastChannel 不存在（老 Safari、jsdom 默认）时整层静默跳过：少一条提示
 * 不影响正确性，不值得为它引入 polyfill。
 */
import { PRODUCT_NAME } from './brand'
import { newId } from './id'

/** 频道名派生自品牌常量——产品名不在界面/协议里手写第二遍 */
const CHANNEL_NAME = `${PRODUCT_NAME.toLowerCase()}:doc-presence`

/** 本标签页的身份：模块实例即标签页，随机一次就够（不需要跨刷新稳定） */
const TAB_ID = newId('tab')

type PresenceMessage =
  | { type: 'doc-open'; docId: string; tabId: string }
  | { type: 'doc-held'; docId: string; tabId: string }
  | { type: 'doc-probe'; docId: string; tabId: string; reqId: string }
  | { type: 'doc-probe-ack'; docId: string; tabId: string; reqId: string }
  | { type: 'takeover-request'; docId: string; tabId: string; reqId: string }
  | { type: 'takeover-ack'; docId: string; tabId: string; reqId: string; flushed: boolean }

/** BroadcastChannel 里我们真正用到的那几个方法（测试可注入替身） */
export interface PresenceChannel {
  postMessage(data: unknown): void
  addEventListener(type: 'message', listener: (ev: { data: unknown }) => void): void
  close(): void
}

type ChannelFactory = (name: string) => PresenceChannel

let makeChannel: ChannelFactory | null =
  typeof BroadcastChannel === 'function' ? (name) => new BroadcastChannel(name) : null
let channel: PresenceChannel | null = null
/** 本标签页当前端着的文档；null = 还没打开任何文档 */
let heldDocId: string | null = null

function ensureChannel(): PresenceChannel | null {
  if (channel || !makeChannel) return channel
  try {
    channel = makeChannel(CHANNEL_NAME)
  } catch {
    makeChannel = null // 建不出来就整层放弃，不每次重试
    return null
  }
  channel.addEventListener('message', (ev) => receive(ev.data))
  return channel
}

const probes = new Map<string, () => void>()
const takeovers = new Map<string, (flushed: boolean) => void>()

/** 持有方在收到接管请求时要做的事：把没存的改动存好并让出，回「存成了没有」。由文档层登记 */
let takeoverHandler: (() => Promise<boolean>) | null = null
export function setTakeoverHandler(fn: (() => Promise<boolean>) | null): void {
  takeoverHandler = fn
}

/** 请求方等持有方回应的限时：超过就当对面已关 / 卡死 / 是旧版标签页，直接接管 */
export const TAKEOVER_TIMEOUT_MS = 3000
let takeoverTimeoutMs = TAKEOVER_TIMEOUT_MS
/** 仅供测试：缩短等对面回应的限时（传 null 恢复） */
export function __setTakeoverTimeoutForTests(ms: number | null): void {
  takeoverTimeoutMs = ms ?? TAKEOVER_TIMEOUT_MS
}

function receive(data: unknown): void {
  const msg = data as PresenceMessage | null
  if (!msg || typeof msg !== 'object' || msg.tabId === TAB_ID) return
  if (msg.type === 'doc-probe-ack') {
    probes.get(msg.reqId)?.()
    return
  }
  if (!heldDocId || msg.docId !== heldDocId) return // 别人开的是另一份，与我无关
  if (msg.type === 'takeover-request') {
    const handler = takeoverHandler
    if (!handler) return // 没登记 = 不回，请求方按超时接管
    const docId = heldDocId
    void handler()
      .catch(() => false)
      .then((flushed) =>
        channel?.postMessage({ type: 'takeover-ack', docId, tabId: TAB_ID, reqId: msg.reqId, flushed }),
      )
    return
  }
  if (msg.type === 'takeover-ack') {
    takeovers.get(msg.reqId)?.(msg.flushed)
    return
  }
  if (msg.type === 'doc-probe') {
    channel?.postMessage({ type: 'doc-probe-ack', docId: heldDocId, tabId: TAB_ID, reqId: msg.reqId })
    return
  }
  if (msg.type === 'doc-open') {
    // 我先端着这份：回一声，让后来者弹提示
    channel?.postMessage({ type: 'doc-held', docId: heldDocId, tabId: TAB_ID })
  } else if (msg.type === 'doc-held') {
    window.dispatchEvent(
      new CustomEvent('tavotto:doc-conflict', { detail: { id: heldDocId } }),
    )
  }
}

/** 打开/切换到某文档时广播一次；有别的标签页端着同一份就会收到回音。 */
export function announceDocOpen(docId: string): void {
  heldDocId = docId
  ensureChannel()?.postMessage({ type: 'doc-open', docId, tabId: TAB_ID })
}

/** 本标签页的身份，仅供测试构造「另一个标签页」的消息时区分用 */
export function __tabId(): string {
  return TAB_ID
}

/** 仅供测试注入替身（jsdom 没有 BroadcastChannel）；传 null 恢复成「环境里没有」 */
export function __setPresenceChannelFactory(factory: ChannelFactory | null): void {
  channel?.close()
  channel = null
  heldDocId = null
  makeChannel = factory
}

/**
 * 此刻有没有**别的**标签页端着这份文档（只问不占：不改自己端着的、不弹提示）。
 * 同样是尽力而为：频道不存在 / 没人回音 = false。用于重开项目文件时判断旧身份能不能沿用。
 */
export function isDocHeldElsewhere(docId: string, waitMs = 150): Promise<boolean> {
  const ch = ensureChannel()
  if (!ch) return Promise.resolve(false)
  return new Promise((resolve) => {
    const reqId = newId('probe')
    const done = (held: boolean) => {
      probes.delete(reqId)
      clearTimeout(timer)
      resolve(held)
    }
    const timer = setTimeout(() => done(false), waitMs)
    probes.set(reqId, () => done(true))
    ch.postMessage({ type: 'doc-probe', docId, tabId: TAB_ID, reqId })
  })
}

/**
 * 请求接管本标签页端着的这份排版。`answered` = 持有方在限时内回了话（`flushed` = 它的改动存成了没有）；
 * 没回话（没有频道 / 对面不在 / 旧版标签页）= `{ answered: false }`，调用方照样往下接管。
 */
export function requestTakeover(
  timeoutMs = takeoverTimeoutMs,
): Promise<{ answered: boolean; flushed: boolean }> {
  const ch = ensureChannel()
  const docId = heldDocId
  if (!ch || !docId) return Promise.resolve({ answered: false, flushed: false })
  return new Promise((resolve) => {
    const reqId = newId('takeover')
    const done = (r: { answered: boolean; flushed: boolean }) => {
      takeovers.delete(reqId)
      clearTimeout(timer)
      resolve(r)
    }
    const timer = setTimeout(() => done({ answered: false, flushed: false }), timeoutMs)
    takeovers.set(reqId, (flushed) => done({ answered: true, flushed }))
    ch.postMessage({ type: 'takeover-request', docId, tabId: TAB_ID, reqId })
  })
}
