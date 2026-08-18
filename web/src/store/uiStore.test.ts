import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * uiStore 在模块加载那一刻就把 persisted 偏好读进来了，所以每个用例都得
 * 先摆好 localStorage、再 resetModules + 动态 import。
 */
async function freshStore() {
  vi.resetModules()
  return (await import('./uiStore')).useUiStore.getState()
}

const LS_KEY = 'magplot.ui'

describe('右栏默认常驻', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('全新安装：右栏默认开着且钉住', async () => {
    const s = await freshStore()
    expect(s.rightOpen).toBe(true)
    expect(s.rightPinned).toBe(true)
  })

  it('老用户（没有 prefsVersion）补一次新默认', async () => {
    localStorage.setItem(LS_KEY, JSON.stringify({ rightOpen: false, rightPinned: false }))
    const s = await freshStore()
    expect(s.rightOpen).toBe(true)
    expect(s.rightPinned).toBe(true)
  })

  it('补过之后用户自己关掉的就一直是关的', async () => {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({ prefsVersion: 1, rightOpen: false, rightPinned: false }),
    )
    const s = await freshStore()
    expect(s.rightOpen).toBe(false)
    expect(s.rightPinned).toBe(false)
  })

  it('persist 会把版本号写回去，否则每次启动都被当成老用户', async () => {
    localStorage.setItem(LS_KEY, JSON.stringify({ rightOpen: false, rightPinned: false }))
    const s = await freshStore()
    s.toggleRight()
    const saved = JSON.parse(localStorage.getItem(LS_KEY) ?? '{}')
    expect(saved.prefsVersion).toBe(1)
    expect(saved.rightOpen).toBe(false)
  })

  it('窄屏开机不铺覆盖层，但常驻标记留着', async () => {
    const w = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { value: 900, configurable: true })
    try {
      const s = await freshStore()
      expect(s.layout).toBe('narrow')
      expect(s.rightOpen).toBe(false)
      expect(s.rightPinned).toBe(true)
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
    }
  })
})
