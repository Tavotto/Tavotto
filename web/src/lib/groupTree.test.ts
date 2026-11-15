/**
 * 嵌套组与剪切蒙版的判据（ADR 0185 M4，`lib/groupTree`）：组链的读法与旧构建改过 groupId 时的退路、作用域里的单元、
 * 图层树、连续化、每个组生效的蒙版、裁一个对象的蒙版链、孤立蒙版、复制时换组 id。
 */
import { describe, expect, it } from 'vitest'
import type { CanvasObject } from '@/types/document'
import {
  canBeMask,
  clipChainOf,
  gatherContiguous,
  groupMasks,
  groupPathOf,
  inScope,
  isActiveMask,
  layerTree,
  orphanMasks,
  prefixOf,
  remapGroupPath,
  setGroupPath,
  unitKeyOf,
  unitMembers,
  type LayerNode,
} from './groupTree'

const rect = (id: string, extra: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id, type: 'shape', shape: 'rect', x: 0, y: 0, w: 10, h: 10, strokePt: 1, color: '#000', fill: null, ...extra }) as CanvasObject
const text = (id: string, extra: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id, type: 'text', x: 0, y: 0, w: 10, h: 5, text: 't', sizePt: 8, bold: false, color: '#000', align: 'left', ...extra }) as CanvasObject
const nested = (id: string, ...path: string[]) => rect(id, { groupId: path.at(-1), ...(path.length > 1 ? { groupPath: path } : {}) })

describe('组链', () => {
  it('一层组只写 groupId；两层及以上 groupPath 末项必须等于 groupId', () => {
    expect(groupPathOf(rect('a'))).toEqual([])
    expect(groupPathOf(rect('a', { groupId: 'g' }))).toEqual(['g'])
    expect(groupPathOf(nested('a', 'g0', 'g1'))).toEqual(['g0', 'g1'])
  })

  it('旧构建改过 groupId（链对不上）、链里有重复 / 空串：只认 [groupId]', () => {
    expect(groupPathOf(rect('a', { groupId: 'gNew', groupPath: ['g0', 'g1'] }))).toEqual(['gNew'])
    expect(groupPathOf(rect('a', { groupId: 'g1', groupPath: ['g1', 'g1'] }))).toEqual(['g1'])
    expect(groupPathOf(rect('a', { groupId: 'g1', groupPath: ['', 'g1'] }))).toEqual(['g1'])
    // 旧构建「取消成组」把 groupId 清了：不在任何组里，groupPath 残留不算数
    expect(groupPathOf(rect('a', { groupPath: ['g0', 'g1'] }))).toEqual([])
  })

  it('setGroupPath：空 = 两个字段都去掉，一层不写 groupPath', () => {
    const o = nested('a', 'g0', 'g1')
    setGroupPath(o, ['g1'])
    expect(o.groupId).toBe('g1')
    expect('groupPath' in o).toBe(false)
    setGroupPath(o, [])
    expect('groupId' in o).toBe(false)
  })
})

describe('作用域里的单元', () => {
  const objs = [nested('a', 'g0', 'g1'), nested('b', 'g0', 'g1'), nested('c', 'g0'), rect('d')]
  it('顶层：组外的对象是它自己，组里的都是最外层组', () => {
    expect(objs.map((o) => unitKeyOf(o, null))).toEqual(['g:g0', 'g:g0', 'g:g0', 'd'])
  })
  it('进了 g0：子组 g1 是一个单元，直接成员是它自己，组外 = null', () => {
    expect(objs.map((o) => unitKeyOf(o, 'g0'))).toEqual(['g:g1', 'g:g1', 'c', null])
    expect(objs.map((o) => inScope(o, 'g0'))).toEqual([true, true, true, false])
  })
  it('单元成员 / 组的前缀', () => {
    expect(unitMembers(objs, 'g:g0')).toEqual(['a', 'b', 'c'])
    expect(unitMembers(objs, 'g:g1')).toEqual(['a', 'b'])
    expect(unitMembers(objs, 'd')).toEqual(['d'])
    expect(unitMembers(objs, 'zz')).toEqual([])
    expect(prefixOf(objs, 'g1')).toEqual(['g0', 'g1'])
    expect(prefixOf(objs, 'nope')).toBeNull()
  })
})

describe('图层树', () => {
  const shape = (nodes: LayerNode[]): unknown =>
    nodes.map((n) => (n.kind === 'object' ? n.obj.id : { [n.gid]: shape(n.children), count: n.count }))
  it('顶层在最上面；组出现在它最上面那个成员的位置；嵌套逐层挂', () => {
    const objs = [rect('x'), nested('a', 'g0', 'g1'), nested('b', 'g0', 'g1'), nested('c', 'g0'), rect('d')]
    expect(shape(layerTree(objs))).toEqual(['d', { g0: ['c', { g1: ['b', 'a'], count: 2 }], count: 3 }, 'x'])
  })
  it('同一个组 id 挂在两个父组下（旧构建改乱的链）：各成一棵，对象一个不丢', () => {
    const objs = [nested('a', 'p', 'g'), nested('b', 'q', 'g')]
    const flat = (nodes: LayerNode[]): string[] => nodes.flatMap((n) => (n.kind === 'object' ? [n.obj.id] : flat(n.children)))
    expect(flat(layerTree(objs)).sort()).toEqual(['a', 'b'])
    expect(layerTree(objs)).toHaveLength(2)
  })
})

describe('连续化', () => {
  const ids = (xs: { id: string }[]) => xs.map((x) => x.id)
  it('挪到最上面那个成员的位置，彼此先后不变', () => {
    const objs = ['a', 'x', 'b', 'y', 'c', 'z'].map((id) => ({ id }))
    expect(ids(gatherContiguous(objs, new Set(['a', 'b', 'c'])))).toEqual(['x', 'y', 'a', 'b', 'c', 'z'])
  })
  it('本来就连续：原样返回同一个引用', () => {
    const objs = ['x', 'a', 'b', 'y'].map((id) => ({ id }))
    expect(gatherContiguous(objs, new Set(['a', 'b']))).toBe(objs)
  })
})

describe('剪切蒙版', () => {
  it('能当蒙版的：路径（至少一条两点以上的子路径）与封闭形状；文字、直线、图不行', () => {
    expect(canBeMask(rect('r'))).toBe(true)
    expect(canBeMask(rect('l', { shape: 'line' } as Partial<CanvasObject>))).toBe(false)
    expect(canBeMask(text('t'))).toBe(false)
    expect(canBeMask({ ...rect('p'), type: 'path', subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 1] }] }] } as unknown as CanvasObject)).toBe(true)
    expect(canBeMask({ ...rect('p'), type: 'path', subpaths: [{ closed: true, nodes: [{ p: [0, 0] }] }] } as unknown as CanvasObject)).toBe(false)
  })

  it('每个组生效的是直接成员里标了 clipMask 的最上面那个', () => {
    const objs = [
      nested('img', 'g0', 'g1'),
      rect('m1', { groupId: 'g1', groupPath: ['g0', 'g1'], clipMask: true }),
      rect('m1b', { groupId: 'g1', groupPath: ['g0', 'g1'], clipMask: true }),
      rect('m0', { groupId: 'g0', clipMask: true }),
      text('tx', { groupId: 'g0', clipMask: true }), // 文字不能当蒙版：不算
    ]
    const masks = groupMasks(objs)
    expect(masks.get('g1')?.id).toBe('m1b')
    expect(masks.get('g0')?.id).toBe('m0')
    expect(isActiveMask(objs[2], masks)).toBe(true)
    expect(isActiveMask(objs[1], masks)).toBe(false)
    // 外 → 内；蒙版不裁自己
    expect(clipChainOf(objs[0], masks).map((m) => m.id)).toEqual(['m0', 'm1b'])
    expect(clipChainOf(objs[3], masks).map((m) => m.id)).toEqual([])
    expect(clipChainOf(objs[2], masks).map((m) => m.id)).toEqual(['m0'])
  })

  it('组里没有被裁的对象了：蒙版算孤立的', () => {
    const objs = [rect('m', { groupId: 'g', clipMask: true }), rect('loose', { clipMask: true })]
    expect(orphanMasks(objs).sort()).toEqual(['loose', 'm'])
    expect(orphanMasks([...objs.slice(0, 1), rect('a', { groupId: 'g' })])).toEqual([])
  })
})

describe('复制时换组 id', () => {
  it('整条链按同一张表换', () => {
    const table = new Map<string, string>()
    const fresh = (g: string) => table.get(g) ?? (table.set(g, `${g}'`), `${g}'`)
    const a = nested('a', 'g0', 'g1')
    const b = nested('b', 'g0')
    remapGroupPath(a, fresh)
    remapGroupPath(b, fresh)
    expect(groupPathOf(a)).toEqual(["g0'", "g1'"])
    expect(groupPathOf(b)).toEqual(["g0'"])
  })
})
