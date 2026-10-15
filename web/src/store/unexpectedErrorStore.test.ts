import { beforeEach, describe, expect, it } from 'vitest'
import { useUnexpectedErrorStore } from './unexpectedErrorStore'

beforeEach(() => useUnexpectedErrorStore.getState().dismiss())

describe('unexpectedErrorStore', () => {
  it('同时最多 1 张：已有一张时 show() 是空操作且不换 token', () => {
    const s = useUnexpectedErrorStore.getState()
    expect(s.show()).toBe(true)
    const first = useUnexpectedErrorStore.getState().card
    expect(useUnexpectedErrorStore.getState().show()).toBe(false)
    expect(useUnexpectedErrorStore.getState().card).toBe(first)
  })

  it('dismiss 后可再 show，token 递增（通知轨据此重建）', () => {
    useUnexpectedErrorStore.getState().show()
    const t1 = useUnexpectedErrorStore.getState().card!.token
    useUnexpectedErrorStore.getState().dismiss()
    expect(useUnexpectedErrorStore.getState().card).toBeNull()
    useUnexpectedErrorStore.getState().show()
    expect(useUnexpectedErrorStore.getState().card!.token).toBeGreaterThan(t1)
  })

  it('纯内存：状态里只有 card.token，没有错误原文的位置', () => {
    useUnexpectedErrorStore.getState().show()
    expect(Object.keys(useUnexpectedErrorStore.getState().card!)).toEqual(['token'])
  })
})
