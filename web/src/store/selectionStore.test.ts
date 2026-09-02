/**
 * 选区 store 的本地活动信号：**真的变了才发**，只带数量。
 * 「没变也发」会让一次性提示与教程把同一个选区当成一连串新动作。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { onActivity, type ActivityDetail } from '@/lib/activity'
import { useSelectionStore } from './selectionStore'

const got: ActivityDetail[] = []
let off: () => void

beforeEach(() => {
  useSelectionStore.setState({ ids: [] })
  got.length = 0
  off?.()
  off = onActivity((d) => {
    if (d.kind === 'selection.changed') got.push(d)
  })
})

describe('selection.changed', () => {
  it('set / add / toggle / clear 各发一次，且只带数量', () => {
    const s = useSelectionStore.getState()
    s.set(['a'])
    s.add('b')
    s.toggle('a')
    s.clear()
    expect(got).toEqual([
      { kind: 'selection.changed', count: 1 },
      { kind: 'selection.changed', count: 2 },
      { kind: 'selection.changed', count: 1 },
      { kind: 'selection.changed', count: 0 },
    ])
    for (const d of got) expect(JSON.stringify(d)).not.toMatch(/"a"|"b"/)
  })

  it('没变就不发：同样的 set、已在选区里的 add、空选区的 clear', () => {
    const s = useSelectionStore.getState()
    s.clear()
    s.set([])
    s.set(['a'])
    got.length = 0
    s.set(['a'])
    s.add('a')
    expect(got).toEqual([])
    s.clear()
    got.length = 0
    s.clear()
    expect(got).toEqual([])
  })

  it('prune 是删对象的副作用，不发', () => {
    const s = useSelectionStore.getState()
    s.set(['a', 'b'])
    got.length = 0
    s.prune(new Set(['a']))
    expect(useSelectionStore.getState().ids).toEqual(['a'])
    expect(got).toEqual([])
  })
})
