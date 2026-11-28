/**
 * 组名在编辑动作里的去留（ADR 0191 修订 / 0208 前置 6.0）：成组（新组没名字）、取消成组（拆掉那层的名字走）、
 * 复制（副本是新组，名字带过去）、改名（一次提交、一步撤回）、字段三方合并。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type CanvasObject, type ShapeObject } from '@/types/document'
import { planAgentMerge } from '@/lib/agentMerge'
import { duplicateSelected, groupSelected, renameGroup, ungroupSelected } from './actions'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
const s = () => useDocumentStore.getState()
const shape = (id: string, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id, type: 'shape', shape: 'rect', x: 10, y: 20, w: 30, h: 20, strokePt: 1, color: '#1B1B18', fill: null, ...over,
})
const seed = (...objs: ShapeObject[]) => {
  s().commit(literal('放入对象'), (d) => void d.objects.push(...objs))
}
const byId = (id: string) => s().doc.objects.find((o) => o.id === id)!

beforeEach(async () => {
  localStorage.clear()
  useSelectionStore.getState().clear()
  await s().switchDocument(emptyProject(), 'd_test')
})

describe('成组 / 取消成组', () => {
  it('成组：新组没有名字，不带走成员原来的组名', () => {
    seed(shape('a', { groupId: 'g1', groupNames: { g1: '内' } }), shape('b', { groupId: 'g1', groupNames: { g1: '内' } }), shape('c'))
    useSelectionStore.getState().set(['a', 'c'])
    groupSelected()
    const a = byId('a')
    expect(a.groupPath).toHaveLength(2)
    expect(a.groupNames).toEqual({ g1: '内' }) // 内层名字原样在，新外层没有名字
    expect(byId('c').groupNames).toBeUndefined()
  })

  it('取消成组：被拆那层的名字走，子组的名字留', () => {
    seed(
      shape('a', { groupId: 'g2', groupPath: ['g1', 'g2'], groupNames: { g1: '外', g2: '内' } }),
      shape('b', { groupId: 'g1', groupNames: { g1: '外' } }),
    )
    useSelectionStore.getState().set(['a', 'b'])
    ungroupSelected()
    expect(byId('a').groupNames).toEqual({ g2: '内' })
    expect(byId('a').groupId).toBe('g2')
    expect(byId('b').groupNames).toBeUndefined()
    expect(byId('b').groupId).toBeUndefined()
  })
})

describe('复制', () => {
  it('副本是新组，名字带到新组 id 上；原件不动', () => {
    seed(shape('a', { groupId: 'g1', groupNames: { g1: '机理图' } }), shape('b', { groupId: 'g1', groupNames: { g1: '机理图' } }))
    useSelectionStore.getState().set(['a', 'b'])
    duplicateSelected()
    const [a, , c, d] = s().doc.objects
    expect(a.groupNames).toEqual({ g1: '机理图' })
    expect(c.groupId).not.toBe('g1')
    expect(c.groupNames).toEqual({ [c.groupId!]: '机理图' })
    expect(d.groupNames).toEqual({ [c.groupId!]: '机理图' })
  })
})

describe('renameGroup', () => {
  it('同组全员一次写入、一步撤回；超长截到 120 字', () => {
    seed(shape('a', { groupId: 'g1' }), shape('b', { groupId: 'g1' }), shape('z'))
    const before = s().past.length
    renameGroup('g1', 'x'.repeat(200))
    expect(s().past).toHaveLength(before + 1)
    expect(byId('a').groupNames!.g1).toHaveLength(120)
    expect(byId('b').groupNames).toEqual(byId('a').groupNames)
    expect(byId('z').groupNames).toBeUndefined()
    s().undo()
    expect(s().doc.objects.map((o) => o.groupNames)).toEqual([undefined, undefined, undefined])
  })

  it('不存在的组 / 名字没变：什么都不做', () => {
    seed(shape('a', { groupId: 'g1', groupNames: { g1: '甲' } }))
    const n = s().past.length
    renameGroup('nope', '乙')
    renameGroup('g1', '甲')
    expect(s().past).toHaveLength(n)
  })
})

describe('0212 三方合并：groupNames 按整字段并', () => {
  const mk = (names?: Record<string, string>): CanvasObject => shape('a', { groupId: 'g1', ...(names ? { groupNames: names } : {}) })
  const diff = (before: CanvasObject, after: CanvasObject) => ({
    id: 'c',
    added: [],
    deleted: [],
    changed: [{ before, after }],
    order: ['a'],
    base_order: ['a'],
    reordered: false,
    layout_groups: null,
  })

  it('Agent 改了名字、桌面没碰：并进来', () => {
    const merged = planAgentMerge([mk()], null, diff(mk(), mk({ g1: '机理图' })) as never)
    expect(merged!.objects[0].groupNames).toEqual({ g1: '机理图' })
  })

  it('两边都改了同一个组名：冲突（null）', () => {
    expect(planAgentMerge([mk({ g1: '桌面改的' })], null, diff(mk(), mk({ g1: 'Agent 改的' })) as never)).toBeNull()
  })

  it('桌面只加了对象、Agent 改了名字：不冲突', () => {
    const merged = planAgentMerge([mk(), shape('n')], null, diff(mk(), mk({ g1: '机理图' })) as never)
    expect(merged!.objects.map((o) => o.id)).toEqual(['a', 'n'])
    expect(merged!.objects[0].groupNames).toEqual({ g1: '机理图' })
  })
})
