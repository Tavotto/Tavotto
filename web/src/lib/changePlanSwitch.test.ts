/**
 * 开关在前端的读法：探测到的能力说了算；还没探测到时按本机记的上一次；从没读到过 = 默认开。
 * 能力一变就记一份（`changePlanStore` 订阅 `aiStore.caps`）。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { useAiStore } from '@/store/aiStore'
import '@/store/changePlanStore'
import { changePlanEnabled, readChangePlanCache, writeChangePlanCache } from './changePlanSwitch'

beforeEach(() => {
  localStorage.clear()
  useAiStore.setState({ caps: null })
})

describe('changePlanEnabled', () => {
  it('探测到的能力说了算（显式 false 才关，缺省 = 开）', () => {
    expect(changePlanEnabled({ change_plan_enabled: false })).toBe(false)
    expect(changePlanEnabled({ change_plan_enabled: true })).toBe(true)
    writeChangePlanCache(false)
    expect(changePlanEnabled({})).toBe(false) // 老后端不给该键：退到本机记的
    expect(changePlanEnabled({ change_plan_enabled: true })).toBe(true) // 但给了就以给的为准
  })

  it('还没探测到：从没读到过 = 开；读到过关就一直按关', () => {
    expect(changePlanEnabled(null)).toBe(true)
    writeChangePlanCache(false)
    expect(changePlanEnabled(undefined)).toBe(false)
    writeChangePlanCache(true)
    expect(changePlanEnabled(null)).toBe(true)
  })

  it('能力里的值一变就记一份，下一次没探测完也按它办', () => {
    useAiStore.setState({ caps: { change_plan_enabled: false } as never })
    expect(readChangePlanCache()).toBe(false)
    useAiStore.setState({ caps: null })
    expect(changePlanEnabled(useAiStore.getState().caps)).toBe(false)
  })

  it('存储不可用也不抛，按默认开', () => {
    const real = Object.getOwnPropertyDescriptor(window, 'localStorage')!
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('blocked')
      },
    })
    try {
      expect(() => writeChangePlanCache(false)).not.toThrow()
      expect(changePlanEnabled(null)).toBe(true)
    } finally {
      Object.defineProperty(window, 'localStorage', real)
    }
  })
})
