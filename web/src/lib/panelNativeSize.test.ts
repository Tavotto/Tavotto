import { enablePatches, produceWithPatches } from 'immer'
import { describe, expect, it } from 'vitest'
import * as history from '@/lib/history'
import { writtenPageDims } from './panelNativeSize'
import { emptyProject, type FigureDocument, type PanelObject } from '@/types/document'

enablePatches()

function panel(id: string, w = 40, h = 30): PanelObject {
  return { id, type: 'panel', x: 0, y: 0, w, h, fileId: 'f', nativeW: 40, nativeH: 30, overrides: [] } as unknown as PanelObject
}
function doc(...objects: PanelObject[]): FigureDocument {
  const base = emptyProject().canvases[0] as unknown as FigureDocument
  return { ...base, objects } as FigureDocument
}
const plain = (m: Map<string, { w: boolean; h: boolean }>) => Object.fromEntries(m)

describe('writtenPageDims：按补丁里实际存在的路径认「写了哪几维」', () => {
  it('只写了 w：h 不算', () => {
    const d0 = doc(panel('a'))
    const [, patches] = produceWithPatches(d0, (d) => {
      ;(d.objects[0] as PanelObject).w = 20
    })
    expect(plain(writtenPageDims(d0, patches))).toEqual({ a: { w: true, h: false } })
  })

  it('事务里 h 动过又回到原值：压缩后仍留着 replace h，也算写了', () => {
    const d0 = doc(panel('a'))
    let cur = d0
    let txn = { label: null, patches: [] as never[], inverse: [] as never[] } as history.PatchPair<null>
    for (const [w, h] of [[30, 20], [20, 30]]) {
      const [next, p, inv] = produceWithPatches(cur, (d) => {
        const o = d.objects[0] as PanelObject
        o.w = w
        o.h = h
      })
      txn = history.accumulate(txn, p, inv)
      cur = next
    }
    const [patches] = history.compress(txn.patches, txn.inverse)
    // 前提：h 的前后值相等，而压缩后的补丁里确实还有它
    expect((cur.objects[0] as PanelObject).h).toBe(30)
    expect(patches.some((p) => p.path.join('.') === 'objects.0.h')).toBe(true)
    expect(plain(writtenPageDims(d0, patches))).toEqual({ a: { w: true, h: true } })
  })

  it('同一批里前面插了对象：后面的下标按挪过的序列认对象', () => {
    const d0 = doc(panel('a'), panel('b'))
    const [, patches] = produceWithPatches(d0, (d) => {
      d.objects.unshift(panel('c'))
      ;(d.objects[2] as PanelObject).w = 5
    })
    expect(writtenPageDims(d0, patches).get('b')?.w).toBe(true)
  })

  it('不碰 objects 的补丁不认任何对象', () => {
    const d0 = doc(panel('a'))
    const [, patches] = produceWithPatches(d0, (d) => {
      d.name = 'x'
    })
    expect(writtenPageDims(d0, patches).size).toBe(0)
  })
})
