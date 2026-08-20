/**
 * 前端埋点这一层的三条硬约束：
 *   ① 没同意时**一个请求都不发**；
 *   ② `captureTelemetry` 永不抛异常（调用点全在拖动 / 撤销 / 导出的栈里）；
 *   ③ edit_kind 永远来自闭表，绝不把标签文案或属性名带出去。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => ({ postTelemetryEvent: vi.fn(() => Promise.resolve({ accepted: true })) }))

import { postTelemetryEvent } from '@/lib/api'
import {
  boundedCount,
  captureTelemetry,
  classifyEditKind,
  setTelemetryEnabled,
  telemetryEnabled,
} from '@/lib/telemetry'

const posted = vi.mocked(postTelemetryEvent)

beforeEach(() => {
  posted.mockClear()
  posted.mockImplementation(() => Promise.resolve({ accepted: true }))
  setTelemetryEnabled(false)
})

afterEach(() => setTelemetryEnabled(false))

describe('captureTelemetry', () => {
  it('默认（还没问过）一个请求都不发', () => {
    expect(telemetryEnabled()).toBe(false)
    captureTelemetry('canvas_created', { creation_kind: 'blank' })
    expect(posted).not.toHaveBeenCalled()
  })

  it('关掉之后立刻停止发送', () => {
    setTelemetryEnabled(true)
    captureTelemetry('canvas_created', { creation_kind: 'blank' })
    expect(posted).toHaveBeenCalledTimes(1)
    setTelemetryEnabled(false)
    captureTelemetry('canvas_created', { creation_kind: 'blank' })
    expect(posted).toHaveBeenCalledTimes(1)
  })

  it('打开后转给后端，属性原样带过去', () => {
    setTelemetryEnabled(true)
    captureTelemetry('figure_opened', { asset_kind: 'pdf', editable: true })
    expect(posted).toHaveBeenCalledWith('figure_opened', {
      asset_kind: 'pdf',
      editable: true,
    })
  })

  it('请求失败不冒泡（rejected promise 也不行）', async () => {
    setTelemetryEnabled(true)
    posted.mockImplementation(() => Promise.reject(new Error('offline')))
    expect(() => captureTelemetry('canvas_created', { creation_kind: 'blank' })).not.toThrow()
    await Promise.resolve()
  })

  it('同步抛出也被吞掉', () => {
    setTelemetryEnabled(true)
    posted.mockImplementation(() => {
      throw new Error('boom')
    })
    expect(() => captureTelemetry('canvas_created', { creation_kind: 'blank' })).not.toThrow()
  })
})

describe('classifyEditKind', () => {
  it.each([
    ['history.moveObjects', 'layout'],
    ['history.editText', 'text'],
    ['history.addArrow', 'annotation'],
    ['history.resizeAxes', 'axes'],
    ['history.setProp', 'style'],
    ['history.applyStyle', 'style'],
  ])('%s → %s', (key, kind) => {
    expect(classifyEditKind(key)).toBe(kind)
  })

  it('复数形态的 key 也认（pasteObjects_other）', () => {
    expect(classifyEditKind('history.pasteObjects_other')).toBe('layout')
  })

  it('认不出来的一律 other——绝不把标签原文当成分类发出去', () => {
    expect(classifyEditKind('history.somethingBrandNew')).toBe('other')
    expect(classifyEditKind(undefined)).toBe('other')
    expect(classifyEditKind('')).toBe('other')
    // 用户内容长什么样都不影响结果
    expect(classifyEditKind('history./Users/me/secret/论文.pdf')).toBe('other')
  })

  it('结果永远在闭集里', () => {
    const allowed = new Set(['text', 'series', 'axes', 'annotation', 'layout', 'style', 'other'])
    for (const key of ['history.setProp', 'x', 'history.zzz', 'history.rotate']) {
      expect(allowed.has(classifyEditKind(key))).toBe(true)
    }
  })
})

describe('boundedCount', () => {
  it('截断到白名单上限，负数与 NaN 归零', () => {
    expect(boundedCount(3)).toBe(3)
    expect(boundedCount(10_000)).toBe(1000)
    expect(boundedCount(-1)).toBe(0)
    expect(boundedCount(Number.NaN)).toBe(0)
    expect(boundedCount(2.7)).toBe(2)
  })
})
