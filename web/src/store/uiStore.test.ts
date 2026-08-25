import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * uiStore 在模块加载那一刻就把 persisted 偏好读进来了，所以每个用例都得
 * 先摆好 localStorage、再 resetModules + 动态 import。
 */
async function freshStore() {
  vi.resetModules()
  return (await import('./uiStore')).useUiStore.getState()
}

const LS_KEY = 'tavotto.ui'

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
    expect(saved.prefsVersion).toBe(2)
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

describe('右栏宽度：v2 迁移与范围', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('全新安装：默认 360px', async () => {
    const s = await freshStore()
    expect(s.rightWidth).toBe(360)
  })

  it('v1 用户的 296–320 旧宽度迁到 360', async () => {
    localStorage.setItem(LS_KEY, JSON.stringify({ prefsVersion: 1, rightWidth: 304 }))
    const s = await freshStore()
    expect(s.rightWidth).toBe(360)
  })

  it('v1 用户主动关掉的右栏不因 v2 迁移被掰回来', async () => {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({ prefsVersion: 1, rightOpen: false, rightPinned: false, rightWidth: 320 }),
    )
    const s = await freshStore()
    expect(s.rightOpen).toBe(false)
    expect(s.rightPinned).toBe(false)
    expect(s.rightWidth).toBe(360)
  })

  it('v2 用户自己设过的宽度原样保留', async () => {
    localStorage.setItem(LS_KEY, JSON.stringify({ prefsVersion: 2, rightWidth: 420 }))
    const s = await freshStore()
    expect(s.rightWidth).toBe(420)
  })

  it('setRightWidth 收进 320–480', async () => {
    const s = await freshStore()
    s.setRightWidth(9999)
    expect((await import('./uiStore')).useUiStore.getState().rightWidth).toBe(480)
    s.setRightWidth(1)
    expect((await import('./uiStore')).useUiStore.getState().rightWidth).toBe(320)
  })

  it('localStorage 里的越界宽度开机即收进合法区间', async () => {
    localStorage.setItem(LS_KEY, JSON.stringify({ prefsVersion: 2, rightWidth: 9999 }))
    const s = await freshStore()
    expect(s.rightWidth).toBe(480)
  })
})

describe('断点：1366×768 左右栏可共存', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('1366 落在 wide（可双栏钉住），1279 仍互斥，1023 是抽屉', async () => {
    const { layoutFor } = await import('./uiStore')
    expect(layoutFor(1366)).toBe('wide')
    expect(layoutFor(1280)).toBe('wide')
    expect(layoutFor(1279)).toBe('medium')
    expect(layoutFor(1024)).toBe('medium')
    expect(layoutFor(1023)).toBe('narrow')
  })

  it('wide 下打开左栏不会关掉右栏', async () => {
    const w = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { value: 1366, configurable: true })
    try {
      const s = await freshStore()
      const store = (await import('./uiStore')).useUiStore
      expect(store.getState().layout).toBe('wide')
      if (!store.getState().rightOpen) s.toggleRight()
      if (!store.getState().leftOpen) s.toggleLeft()
      expect(store.getState().leftOpen).toBe(true)
      expect(store.getState().rightOpen).toBe(true)
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
    }
  })

  it('medium 下仍互斥：打开一侧收起另一侧', async () => {
    const w = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { value: 1100, configurable: true })
    try {
      await freshStore()
      const store = (await import('./uiStore')).useUiStore
      expect(store.getState().layout).toBe('medium')
      store.getState().setLeftTab('elements')
      expect(store.getState().leftOpen).toBe(true)
      expect(store.getState().rightOpen).toBe(false)
      store.getState().setRightTab('properties')
      expect(store.getState().rightOpen).toBe(true)
      expect(store.getState().leftOpen).toBe(false)
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
    }
  })
})

describe('选中对象时回到属性页', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('助手开着时选中对象 → 切回属性（助手状态由 aiStore 保留）', async () => {
    await freshStore()
    const store = (await import('./uiStore')).useUiStore
    store.getState().setRightTab('assistant')
    expect(store.getState().rightTab).toBe('assistant')
    store.getState().autoShowProperties()
    expect(store.getState().rightTab).toBe('properties')
    expect(store.getState().rightOpen).toBe(true)
  })
})
