/**
 * 版本检查点该恢复到哪一张画布（R-03）。
 *
 * 四条分支的后果差得很远：一条不动别的画布，两条会覆盖当前画布。
 * 所以四条都得有用例，尤其是"缺席"那两条——改造前它们全部走的是
 * 「直接写进当前画布」，而界面上一个字都没说。
 */
import { describe, expect, it } from 'vitest'
import { resolveRestoreTarget } from './versionTarget'

const canvases = [
  { id: 'c1', name: 'Fig 1' },
  { id: 'c2', name: 'Fig 2' },
]
const state = { activeCanvasId: 'c1', canvases }

describe('resolveRestoreTarget', () => {
  it('来自当前画布 → 直接恢复，不打扰', () => {
    expect(resolveRestoreTarget({ canvasId: 'c1' }, state)).toEqual({ kind: 'same' })
  })

  it('来自另一张仍存在的画布 → 切过去写，当前画布不动', () => {
    expect(resolveRestoreTarget({ canvasId: 'c2', canvasName: '旧名' }, state)).toEqual({
      kind: 'other',
      canvasId: 'c2',
      name: 'Fig 2', // 名字取**画布现在的名字**，不是检查点里存的那个旧名
    })
  })

  it('原画布已删除 → missing，并带上它当时的名字', () => {
    expect(resolveRestoreTarget({ canvasId: 'gone', canvasName: 'Fig 9' }, state)).toEqual({
      kind: 'missing',
      from: 'Fig 9',
    })
  })

  it('原画布已删除且没存过名字 → 退回 id，不编一个名字出来', () => {
    expect(resolveRestoreTarget({ canvasId: 'gone' }, state)).toEqual({
      kind: 'missing',
      from: 'gone',
    })
  })

  it('旧检查点没有画布身份 → unknown，**不当成当前画布**', () => {
    expect(resolveRestoreTarget({}, state)).toEqual({ kind: 'unknown' })
    // 这条正是 R-03 的原形态：改造前它与「来自当前画布」走同一条路，
    // 于是在画布 A 上恢复一个来自 B 的旧检查点，A 被静默覆盖。
    expect(resolveRestoreTarget({ canvasId: '' }, state)).toEqual({ kind: 'unknown' })
  })
})
