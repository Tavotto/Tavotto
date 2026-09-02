/**
 * 活动信号 → 遥测的白名单映射（ADR 0041 §4）。
 *
 * 守四件事：只有三种 kind 会映射；只有从浮动栏发起的才算；payload 只有闭集的
 * action_id 与选区大小桶；没同意时一个字节都不发。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  postTelemetryEvent: vi.fn().mockResolvedValue(undefined),
}))

import { postTelemetryEvent } from '@/lib/api'
import { ACTIVITY_KINDS, emitActivity, type ActivityDetail } from './activity'
import {
  activityToTelemetry,
  captureContextBarMore,
  fromContextBar,
  startActivityTelemetry,
} from './activityTelemetry'
import { readinessStatusBucket, selectionSizeBucket, setTelemetryEnabled } from './telemetry'

const post = vi.mocked(postTelemetryEvent)
let stop: (() => void) | null = null

beforeEach(() => {
  post.mockClear()
  setTelemetryEnabled(true)
  stop = startActivityTelemetry()
})
afterEach(() => {
  stop?.()
  setTelemetryEnabled(false)
})

describe('activityToTelemetry（纯函数）', () => {
  it('十种排列模式各落到一个闭集 action_id；成组 / 取消成组各一条', () => {
    const cases: [ActivityDetail, string][] = [
      [{ kind: 'selection.aligned', mode: 'left', ref: 'selection', count: 2 }, 'align_left'],
      [{ kind: 'selection.aligned', mode: 'hcenter', ref: 'page', count: 2 }, 'align_center'],
      [{ kind: 'selection.aligned', mode: 'right', ref: 'primary', count: 2 }, 'align_right'],
      [{ kind: 'selection.aligned', mode: 'top', ref: 'selection', count: 2 }, 'align_top'],
      [{ kind: 'selection.aligned', mode: 'vcenter', ref: 'selection', count: 2 }, 'align_middle'],
      [{ kind: 'selection.aligned', mode: 'bottom', ref: 'selection', count: 2 }, 'align_bottom'],
      [{ kind: 'selection.aligned', mode: 'hdist', ref: 'selection', count: 3 }, 'distribute_h'],
      [{ kind: 'selection.aligned', mode: 'vdist', ref: 'selection', count: 3 }, 'distribute_v'],
      [{ kind: 'selection.aligned', mode: 'samew', ref: 'selection', count: 2 }, 'same_width'],
      [{ kind: 'selection.aligned', mode: 'sameh', ref: 'selection', count: 2 }, 'same_height'],
      [{ kind: 'selection.grouped', count: 4 }, 'group'],
      [{ kind: 'selection.ungrouped', count: 7 }, 'ungroup'],
    ]
    for (const [detail, action] of cases) {
      const out = activityToTelemetry(detail, true)
      expect(out?.event).toBe('context_bar_multi_used')
      expect(out?.properties.action_id).toBe(action)
    }
  })

  it('选区大小只以桶出网：2 / 3_5 / 6_plus', () => {
    expect(selectionSizeBucket(2)).toBe('2')
    expect(selectionSizeBucket(3)).toBe('3_5')
    expect(selectionSizeBucket(5)).toBe('3_5')
    expect(selectionSizeBucket(6)).toBe('6_plus')
    expect(selectionSizeBucket(40)).toBe('6_plus')
    const out = activityToTelemetry({ kind: 'selection.grouped', count: 5 }, true)
    expect(out?.properties).toEqual({ action_id: 'group', selection_size_bucket: '3_5' })
  })

  it('不是从浮动栏发起的不映射（属性页 / 命令面板同一个 action 不算）', () => {
    expect(activityToTelemetry({ kind: 'selection.grouped', count: 2 }, false)).toBeNull()
  })

  it('其余十五种 kind 一条都不映射——activity bus 不是遥测的转发器', () => {
    const others = ACTIVITY_KINDS.filter(
      (k) => !['selection.aligned', 'selection.grouped', 'selection.ungrouped'].includes(k),
    )
    expect(others.length).toBe(ACTIVITY_KINDS.length - 3)
    for (const kind of others) {
      expect(activityToTelemetry({ kind } as ActivityDetail, true)).toBeNull()
    }
  })

  it('payload 里只有两个键，没有 id / gid / 文件名', () => {
    const out = activityToTelemetry(
      { kind: 'selection.aligned', mode: 'left', ref: 'selection', count: 2 },
      true,
    )
    expect(Object.keys(out!.properties).sort()).toEqual(['action_id', 'selection_size_bucket'])
  })
})

describe('订阅与来源作用域', () => {
  it('fromContextBar 作用域里发出的信号 → 一条 context_bar_multi_used', () => {
    fromContextBar(() => emitActivity({ kind: 'selection.aligned', mode: 'hdist', ref: 'selection', count: 3 }))
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('context_bar_multi_used', {
      action_id: 'distribute_h',
      selection_size_bucket: '3_5',
    })
  })

  it('作用域之外的同一条信号不发', () => {
    emitActivity({ kind: 'selection.aligned', mode: 'hdist', ref: 'selection', count: 3 })
    expect(post).not.toHaveBeenCalled()
  })

  it('作用域随同步调用结束：之后的信号不再算浮动栏', () => {
    fromContextBar(() => undefined)
    emitActivity({ kind: 'selection.grouped', count: 2 })
    expect(post).not.toHaveBeenCalled()
  })

  it('动作抛出也要把作用域收回去', () => {
    expect(() =>
      fromContextBar(() => {
        throw new Error('x')
      }),
    ).toThrow()
    emitActivity({ kind: 'selection.grouped', count: 2 })
    expect(post).not.toHaveBeenCalled()
  })

  it('「更多」按钮直接记一条 more', () => {
    captureContextBarMore(6)
    expect(post).toHaveBeenCalledWith('context_bar_multi_used', {
      action_id: 'more',
      selection_size_bucket: '6_plus',
    })
  })

  it('没同意 = 不发（同意态是后端说了算的缓存）', () => {
    setTelemetryEnabled(false)
    fromContextBar(() => emitActivity({ kind: 'selection.grouped', count: 2 }))
    captureContextBarMore(2)
    expect(post).not.toHaveBeenCalled()
  })

  it('startActivityTelemetry 幂等：调两次只订阅一次', () => {
    const again = startActivityTelemetry()
    fromContextBar(() => emitActivity({ kind: 'selection.grouped', count: 2 }))
    expect(post).toHaveBeenCalledTimes(1)
    again()
    stop = null
  })
})

describe('readinessStatusBucket', () => {
  it('全可编辑 / 混合 / 仅排版；零张图没有桶', () => {
    expect(readinessStatusBucket({ total: 3, editable: 3 })).toBe('all_editable')
    expect(readinessStatusBucket({ total: 3, editable: 1 })).toBe('mixed')
    expect(readinessStatusBucket({ total: 3, editable: 0 })).toBe('layout_only')
    expect(readinessStatusBucket({ total: 0, editable: 0 })).toBeNull()
  })
})
