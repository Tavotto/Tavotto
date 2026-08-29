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

describe('左侧工作区：默认常驻、可折叠、偏好跨会话', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('全新安装：左抽屉默认展开，停在素材页', async () => {
    const s = await freshStore()
    expect(s.leftOpen).toBe(true)
    expect(s.leftTab).toBe('assets')
  })

  it('用户点同一个轨道按钮收起，偏好落进本机', async () => {
    const s = await freshStore()
    s.railClick('assets')
    const store = (await import('./uiStore')).useUiStore
    expect(store.getState().leftOpen).toBe(false)
    expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '{}').leftOpen).toBe(false)
  })

  it('重启恢复：收起过就一直是收起的', async () => {
    const s = await freshStore()
    s.railClick('assets')
    const restarted = await freshStore()
    expect(restarted.leftOpen).toBe(false)
  })

  it('重启恢复：没动过就还是展开的', async () => {
    const s = await freshStore()
    s.setLeftWidth(320) // 动点别的，逼一次 persist
    const restarted = await freshStore()
    expect(restarted.leftOpen).toBe(true)
    expect(restarted.leftWidth).toBe(320)
  })
})

describe('窄窗口的自动让位不许覆盖桌面偏好', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  /**
   * 这一条是真实缺陷的看护，不是假想：`persist()` 曾经照抄当前状态，而互斥
   * 断点上的自动收起也写在同一个 `leftOpen` 上。于是「把窗口拖窄一次 + 之后
   * 随便改点别的」= 常驻左栏被永久关掉，**而用户从没关过它**。
   */
  it('缩到互斥断点自动收起左栏，本机存的仍然是「展开」', async () => {
    const w = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { value: 1400, configurable: true })
    try {
      await freshStore()
      const store = (await import('./uiStore')).useUiStore
      store.getState().setLeftTab('assets')
      store.getState().setRightTab('properties')
      expect(store.getState().leftOpen).toBe(true)
      expect(store.getState().rightOpen).toBe(true)

      // 窗口缩到 medium：两侧都开着，左侧自动让位
      store.getState().setLayout('medium')
      expect(store.getState().leftOpen).toBe(false)

      // 之后用户改了个完全无关的偏好，触发一次 persist
      store.getState().setShowGrid(false)
      expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '{}').leftOpen).toBe(true)
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
    }
  })

  it('拉回宽屏、重启之后左栏回来了', async () => {
    const w = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { value: 1400, configurable: true })
    try {
      await freshStore()
      const store = (await import('./uiStore')).useUiStore
      store.getState().setRightTab('properties')
      store.getState().setLeftTab('assets')
      store.getState().setLayout('medium')
      store.getState().setShowGrid(false)

      const restarted = await freshStore()
      expect(restarted.leftOpen).toBe(true)
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
    }
  })

  it('窄屏开机不铺右栏覆盖层，但本机存的常驻偏好没被改掉', async () => {
    const w = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { value: 900, configurable: true })
    try {
      const s = await freshStore()
      expect(s.rightOpen).toBe(false)
      s.setShowGrid(false) // 逼一次 persist
      expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '{}').rightOpen).toBe(true)
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
    }
  })

  it('用户自己关掉的那一侧照旧落盘（响应式豁免不是"什么都不记"）', async () => {
    const s = await freshStore()
    s.toggleRight()
    expect(JSON.parse(localStorage.getItem(LS_KEY) ?? '{}').rightOpen).toBe(false)
  })
})
