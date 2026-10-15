/**
 * graphemeCount 的特性检测：没有 Intl.Segmenter 的 WebView 里，导入树模块不能在顶层抛错
 * （否则编辑器起不来）；退回按码点数。有 Segmenter 时按字素簇数。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const original = Intl.Segmenter
const ZWJ_FAMILY = '👨‍👩‍👧' // 5 个码点，1 个字素簇
const DECOMPOSED_E = 'é' // 2 个码点，1 个字素簇

afterEach(() => {
  Object.defineProperty(Intl, 'Segmenter', { value: original, configurable: true, writable: true })
  vi.resetModules()
})

describe('graphemeCount', () => {
  it('有 Segmenter：按字素簇数', async () => {
    vi.resetModules()
    const { graphemeCount } = await import('./useBoldWidthLock')
    expect(graphemeCount('abc')).toBe(3)
    expect(graphemeCount(DECOMPOSED_E)).toBe(1)
    expect(graphemeCount(ZWJ_FAMILY)).toBe(1)
  })

  it('没有 Intl.Segmenter：模块照常导入，退回按码点数', async () => {
    Object.defineProperty(Intl, 'Segmenter', { value: undefined, configurable: true, writable: true })
    vi.resetModules()
    const mod = await import('./useBoldWidthLock') // 顶层不许抛
    expect(mod.graphemeCount('abc')).toBe(3)
    expect(mod.graphemeCount(DECOMPOSED_E)).toBe(2)
    expect(mod.graphemeCount(ZWJ_FAMILY)).toBe(5)
  })

  it('懒构造：导入时不 new Segmenter，第一次调用才建', async () => {
    const ctor = vi.fn(function (this: unknown, ...a: ConstructorParameters<typeof Intl.Segmenter>) {
      return new original(...a)
    })
    Object.defineProperty(Intl, 'Segmenter', { value: ctor, configurable: true, writable: true })
    vi.resetModules()
    const mod = await import('./useBoldWidthLock')
    expect(ctor).not.toHaveBeenCalled()
    mod.graphemeCount('a')
    mod.graphemeCount('b')
    expect(ctor).toHaveBeenCalledTimes(1)
  })
})
