import { parseDocumentFormat, type DocumentFormat } from './documentFormat'
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
  | { type: 'doc-open'; docId: string; tabId: string; format?: DocumentFormat }
  | { type: 'doc-held'; docId: string; tabId: string; format?: DocumentFormat }
  | { type: 'doc-probe'; docId: string; tabId: string; reqId: string; format?: DocumentFormat }
  | { type: 'doc-probe-ack'; docId: string; tabId: string; reqId: string; format?: DocumentFormat }

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
let heldFormat: DocumentFormat = 'legacy'

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

const probes = new Map<string, { docId: string; format: DocumentFormat; answer: () => void }>()

function receive(data: unknown): void {
  const msg = data as PresenceMessage | null
  if (!msg || typeof msg !== 'object' || msg.tabId === TAB_ID) return
  if (msg.type === 'doc-probe-ack') {
    const probe = probes.get(msg.reqId)
    if (probe?.docId === msg.docId && probe.format === parseDocumentFormat(msg.format)) probe.answer()
    return
  }
  if (parseDocumentFormat(msg.format) !== heldFormat || !heldDocId || msg.docId !== heldDocId) return
  if (msg.type === 'doc-probe') {
    channel?.postMessage({ type: 'doc-probe-ack', docId: heldDocId, tabId: TAB_ID, reqId: msg.reqId, ...(heldFormat === 'v4' ? { format: heldFormat } : {}) })
    return
  }
  if (msg.type === 'doc-open') {
    // 我先端着这份：回一声，让后来者弹提示
    channel?.postMessage({ type: 'doc-held', docId: heldDocId, tabId: TAB_ID, ...(heldFormat === 'v4' ? { format: heldFormat } : {}) })
  } else if (msg.type === 'doc-held') {
    window.dispatchEvent(
      new CustomEvent('tavotto:doc-conflict', { detail: { id: heldDocId } }),
    )
  }
}

/** 打开/切换到某文档时广播一次；有别的标签页端着同一份就会收到回音。 */
export function announceDocOpen(docId: string, format: DocumentFormat = 'legacy'): void {
  heldDocId = docId
  heldFormat = format
  ensureChannel()?.postMessage({ type: 'doc-open', docId, tabId: TAB_ID, ...(format === 'v4' ? { format } : {}) })
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
export function isDocHeldElsewhere(docId: string, waitMs = 150, format: DocumentFormat = 'legacy'): Promise<boolean> {
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
    probes.set(reqId, { docId, format, answer: () => done(true) })
    ch.postMessage({ type: 'doc-probe', docId, tabId: TAB_ID, reqId, ...(format === 'v4' ? { format } : {}) })
  })
}
