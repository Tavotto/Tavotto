import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  __setPresenceChannelFactory,
  __tabId,
  announceDocOpen,
  type PresenceChannel,
} from './docPresence'

/** BroadcastChannel 替身：记下发出去的消息，并能把「别的标签页」的消息灌进来 */
class FakeChannel implements PresenceChannel {
  sent: unknown[] = []
  private listeners: ((ev: { data: unknown }) => void)[] = []
  closed = false

  postMessage(data: unknown): void {
    this.sent.push(data)
  }

  addEventListener(_type: 'message', listener: (ev: { data: unknown }) => void): void {
    this.listeners.push(listener)
  }

  close(): void {
    this.closed = true
  }

  /** 模拟另一个标签页广播过来 */
  deliver(data: unknown): void {
    for (const fn of this.listeners) fn({ data })
  }
}

let last: FakeChannel | null = null
const conflicts: string[] = []
const onConflict = (ev: Event) => {
  conflicts.push((ev as CustomEvent<{ id: string }>).detail.id)
}

describe('跨标签页文档占用提示', () => {
  beforeEach(() => {
    conflicts.length = 0
    window.addEventListener('tavotto:doc-conflict', onConflict)
  })

  afterEach(() => {
    window.removeEventListener('tavotto:doc-conflict', onConflict)
    __setPresenceChannelFactory(null)
    last = null
  })

  it('环境里没有 BroadcastChannel 时整层静默跳过', () => {
    __setPresenceChannelFactory(null) // jsdom 的默认状态
    expect(() => announceDocOpen('d_1')).not.toThrow()
    expect(conflicts).toEqual([])
  })

  it('建频道失败也只是没有提示，不往上抛', () => {
    __setPresenceChannelFactory(() => {
      throw new Error('SecurityError')
    })
    expect(() => announceDocOpen('d_1')).not.toThrow()
    expect(conflicts).toEqual([])
  })

  it('打开文档时广播 doc-open', () => {
    __setPresenceChannelFactory((name) => {
      expect(name).toBe('tavotto:doc-presence') // 频道名派生自品牌常量
      last = new FakeChannel()
      return last
    })
    announceDocOpen('d_1')
    expect(last!.sent).toEqual([{ type: 'doc-open', docId: 'd_1', tabId: __tabId() }])
  })

  it('已经端着同一份时回 doc-held，端着别的则不吭声', () => {
    __setPresenceChannelFactory(() => (last = new FakeChannel()))
    announceDocOpen('d_1')
    last!.sent.length = 0

    last!.deliver({ type: 'doc-open', docId: 'd_other', tabId: 'tab_x' })
    expect(last!.sent).toEqual([]) // 别人开的是另一份，与我无关

    last!.deliver({ type: 'doc-open', docId: 'd_1', tabId: 'tab_x' })
    expect(last!.sent).toEqual([{ type: 'doc-held', docId: 'd_1', tabId: __tabId() }])
    expect(conflicts).toEqual([]) // 先来的那个不弹提示：它什么都没做错
  })

  it('收到别人的 doc-held → 报冲突（后来者才弹提示）', () => {
    __setPresenceChannelFactory(() => (last = new FakeChannel()))
    announceDocOpen('d_1')

    last!.deliver({ type: 'doc-held', docId: 'd_1', tabId: 'tab_x' })
    expect(conflicts).toEqual(['d_1'])
  })

  it('自己的回音与无关文档一律忽略', () => {
    __setPresenceChannelFactory(() => (last = new FakeChannel()))
    announceDocOpen('d_1')
    last!.sent.length = 0

    last!.deliver({ type: 'doc-held', docId: 'd_1', tabId: __tabId() }) // 自己发的
    last!.deliver({ type: 'doc-held', docId: 'd_2', tabId: 'tab_x' }) // 别的文档
    last!.deliver(null)
    last!.deliver('乱码')
    expect(conflicts).toEqual([])
    expect(last!.sent).toEqual([])
  })

  it('切到别的文档后，旧文档的冲突消息不再算数', () => {
    __setPresenceChannelFactory(() => (last = new FakeChannel()))
    announceDocOpen('d_1')
    announceDocOpen('d_2') // 切文档：现在端着的是 d_2

    last!.deliver({ type: 'doc-held', docId: 'd_1', tabId: 'tab_x' })
    expect(conflicts).toEqual([])
    last!.deliver({ type: 'doc-held', docId: 'd_2', tabId: 'tab_x' })
    expect(conflicts).toEqual(['d_2'])
  })
})
