/**
 * 吸附反馈（ADR 0128 §四）的生命周期：与 snapXs / snapYs 同进同出；值相同不触发更新。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { useInteractionStore, type SnapFeedback } from './interactionStore'

const fb = (v = 40): SnapFeedback[] => [
  { axis: 'x', v, self: { x: 10, y: 20, w: 30, h: 10 }, target: { x: 60, y: 40, w: 20, h: 15 } },
]
const st = () => useInteractionStore.getState()

afterEach(() => st().end())

describe('snapFeedback 生命周期', () => {
  it('setSnap 带反馈就存，不带就清空', () => {
    st().setSnap([40], [], fb())
    expect(st().snapFeedback).toEqual(fb())
    st().setSnap([40], [])
    expect(st().snapXs).toEqual([40])
    expect(st().snapFeedback).toEqual([])
  })

  it('线相同但反馈变了：要更新；线和反馈都相同（哪怕是新数组）：不触发', () => {
    st().setSnap([40], [], fb())
    const s1 = st()
    st().setSnap([40], [], fb()) // 新数组、同值
    expect(st()).toBe(s1)
    st().setSnap([40], [], [{ ...fb()[0], target: undefined }])
    expect(st()).not.toBe(s1)
    expect(st().snapFeedback[0].target).toBeUndefined()
    const s2 = st()
    st().setSnap([40], [], [{ ...fb()[0], self: { x: 10, y: 20, w: 31, h: 10 }, target: undefined }])
    expect(st()).not.toBe(s2)
  })

  it('没有任何反馈的重复 setSnap：不触发', () => {
    st().setSnap([40], [])
    const s = st()
    st().setSnap([40], [])
    expect(st()).toBe(s)
  })

  it('begin 不动、end 清空', () => {
    st().begin('element')
    st().setSnap([40], [], fb())
    expect(st().snapFeedback).toHaveLength(1)
    st().end()
    expect(st().snapFeedback).toEqual([])
    expect(st().snapXs).toEqual([])
  })
})
