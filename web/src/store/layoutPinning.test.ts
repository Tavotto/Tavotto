import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/** 与 uiStore.test 同一做法：偏好在模块加载时读，每个用例先摆 localStorage 再 resetModules */
const LS_KEY = 'tavotto.ui'
const realWidth = window.innerWidth

async function freshStore(width: number, saved?: object) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true })
  localStorage.clear()
  if (saved) localStorage.setItem(LS_KEY, JSON.stringify(saved))
  vi.resetModules()
  return await import('./uiStore')
}

afterEach(() => Object.defineProperty(window, 'innerWidth', { value: realWidth, configurable: true }))

describe('固定左栏 / 属性栏：没设过与设为关分得清', () => {
  beforeEach(() => localStorage.clear())

  it('全新安装两个值都是 null；≥1280 默认都算固定，更窄都不算', async () => {
    const m = await freshStore(1440)
    const s = m.useUiStore.getState()
    expect(s.leftPinned).toBeNull()
    expect(s.rightPinned).toBeNull()
    expect(m.isLeftPinned(s)).toBe(true)
    expect(m.isRightPinned(s)).toBe(true)
    expect(m.isLeftPinned({ ...s, layout: 'medium' })).toBe(false)
    expect(m.isRightPinned({ ...s, layout: 'medium' })).toBe(false)
  })

  it('用户显式选过的值永远优先：左栏显式关，宽屏也不固定', async () => {
    const m = await freshStore(1440, { prefsVersion: 3, leftPinned: false, rightPinned: false })
    const s = m.useUiStore.getState()
    expect(m.isLeftPinned(s)).toBe(false)
    expect(m.isRightPinned(s)).toBe(false)
  })

  it('显式固定属性栏：窄窗口里照旧挤占（不是覆盖层）', async () => {
    const m = await freshStore(1100, { prefsVersion: 3, rightPinned: true })
    const s = m.useUiStore.getState()
    expect(m.isRightOverlay(s)).toBe(false)
  })

  it('v2 → v3：等于旧默认的 rightPinned:true 当没表过态，亲手关掉的 false 保留', async () => {
    const a = (await freshStore(1100, { prefsVersion: 2, rightPinned: true })).useUiStore.getState()
    expect(a.rightPinned).toBeNull()
    const b = (await freshStore(1100, { prefsVersion: 2, rightPinned: false })).useUiStore.getState()
    expect(b.rightPinned).toBe(false)
  })

  it('老用户本机存的 leftPinned:false 不被新默认翻成固定', async () => {
    const m = await freshStore(1440, { prefsVersion: 2, leftPinned: false })
    expect(m.isLeftPinned(m.useUiStore.getState())).toBe(false)
  })
})

describe('G1：< 1280 属性栏默认不挤占画布', () => {
  it('1093 宽开机：属性栏是覆盖层且不开；常驻标记（偏好的开合）留着', async () => {
    const m = await freshStore(1093)
    const s = m.useUiStore.getState()
    expect(s.layout).toBe('medium')
    expect(m.isRightOverlay(s)).toBe(true)
    expect(s.rightOpen).toBe(false)
    expect(s.leftOpen).toBe(true)
  })

  it('1440 宽开机：右栏照旧开着、停靠', async () => {
    const m = await freshStore(1440)
    const s = m.useUiStore.getState()
    expect(m.isRightOverlay(s)).toBe(false)
    expect(s.rightOpen).toBe(true)
  })

  it('覆盖层属性栏：选中即出现，取消选择即收回（不钉住就收）', async () => {
    const m = await freshStore(1093)
    const st = m.useUiStore
    st.getState().autoShowProperties()
    expect(st.getState().rightOpen).toBe(true)
    st.getState().autoHideProperties()
    expect(st.getState().rightOpen).toBe(false)
  })
})

describe('B4：素材抽屉让位', () => {
  it('宽屏没表过态 → 默认固定：加完图抽屉不收', async () => {
    const m = await freshStore(1440)
    const st = m.useUiStore
    expect(st.getState().leftOpen).toBe(true)
    st.getState().autoShowProperties()
    expect(st.getState().leftOpen).toBe(true)
  })

  it('宽屏显式不固定 → 照旧收', async () => {
    const m = await freshStore(1440, { prefsVersion: 3, leftPinned: false })
    m.useUiStore.getState().autoShowProperties()
    expect(m.useUiStore.getState().leftOpen).toBe(false)
  })

  it('不固定时：指针还在抽屉里（holdAssets）不收；离开后再调一次才收', async () => {
    const m = await freshStore(1440, { prefsVersion: 3, leftPinned: false })
    const st = m.useUiStore
    st.getState().autoShowProperties({ holdAssets: true })
    expect(st.getState().leftOpen).toBe(true)
    expect(st.getState().rightOpen).toBe(true)
    st.getState().autoShowProperties()
    expect(st.getState().leftOpen).toBe(false)
  })

  it('互斥断点上 holdAssets 整个推迟（开右栏本身会收左栏）', async () => {
    const m = await freshStore(1100)
    const st = m.useUiStore
    st.getState().autoShowProperties({ holdAssets: true })
    expect(st.getState().leftOpen).toBe(true)
    expect(st.getState().rightOpen).toBe(false)
  })
})
