import { beforeEach, describe, expect, it } from 'vitest'
import { arrowHeads, emptyProject, type ArrowObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { insertPreset, insertShape, insertSymbol, PRESET_IDS } from './presets'

const arrow = (over: Partial<ArrowObject>): ArrowObject => ({
  id: 'a1', type: 'arrow', x: 0, y: 0, w: 10, h: 10,
  start: { rx: 0, ry: 0.5 }, end: { rx: 1, ry: 0.5 },
  strokePt: 1, color: '#000', head: 'end', ...over,
})

describe('箭头端型新旧映射', () => {
  it('旧 head 字段推导为三角头', () => {
    expect(arrowHeads(arrow({ head: 'none' }))).toEqual({ start: 'none', end: 'none' })
    expect(arrowHeads(arrow({ head: 'end' }))).toEqual({ start: 'none', end: 'triangle' })
    expect(arrowHeads(arrow({ head: 'both' }))).toEqual({ start: 'triangle', end: 'triangle' })
  })

  it('新字段优先于旧字段', () => {
    expect(arrowHeads(arrow({ head: 'both', headStart: 'bar', headEnd: 'open' })))
      .toEqual({ start: 'bar', end: 'open' })
    expect(arrowHeads(arrow({ head: 'both', headEnd: 'open' })))
      .toEqual({ start: 'none', end: 'open' })
  })
})

describe('科研预设与形状插入', () => {
  beforeEach(async () => {
    localStorage.clear()
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_preset')
  })

  it('每个预设都能插入且成组', () => {
    for (const id of PRESET_IDS) {
      const before = useDocumentStore.getState().doc.objects.length
      insertPreset(id)
      const objs = useDocumentStore.getState().doc.objects.slice(before)
      expect(objs.length, id).toBeGreaterThan(0)
      if (objs.length > 1) {
        const gids = new Set(objs.map((o) => o.groupId))
        expect(gids.size, `${id} 应成一组`).toBe(1)
        expect([...gids][0]).toBeTruthy()
      }
    }
  })

  it('尺寸线：双向三角 + 两条旋转 90° 的界线', () => {
    insertPreset('dimension')
    const objs = useDocumentStore.getState().doc.objects
    const arrows = objs.filter((o) => o.type === 'arrow')
    const lines = objs.filter((o) => o.type === 'shape' && o.shape === 'line')
    expect(arrows).toHaveLength(1)
    expect(arrowHeads(arrows[0] as ArrowObject)).toEqual({ start: 'triangle', end: 'triangle' })
    expect(lines).toHaveLength(2)
    expect(lines.every((l) => l.rotationDeg === 90)).toBe(true)
  })

  it('插入形状与符号', () => {
    insertShape('polygon')
    const poly = useDocumentStore.getState().doc.objects.at(-1)!
    expect(poly.type === 'shape' && poly.shape === 'polygon' && poly.sides === 6).toBe(true)
    insertSymbol('μ')
    const sym = useDocumentStore.getState().doc.objects.at(-1)!
    expect(sym.type === 'text' && sym.text === 'μ').toBe(true)
  })

  it('插入可撤销', () => {
    insertShape('triangle')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    useDocumentStore.getState().undo()
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
  })
})
