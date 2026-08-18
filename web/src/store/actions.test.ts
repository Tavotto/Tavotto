import { literal } from '@/i18n'
import { beforeEach, describe, expect, it } from 'vitest'
import { emptyProject } from '@/types/document'
import type { ArrowObject, ShapeObject, TextObject } from '@/types/document'
import { duplicateSelected } from './actions'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'

/**
 * duplicateSelected 曾经在 Immer recipe 内部对草稿 Proxy 调 structuredClone，
 * 每次都抛 DataCloneError——⌘D / 右键复制 / 检查器复制三个入口全部静默失效。
 * 这批用例守住「克隆在 recipe 外面做」这条修复。
 */

const text = (id: string, partial: Partial<TextObject> = {}): TextObject => ({
  id, type: 'text', text: '文字', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 10, y: 20, w: 20, h: 8, ...partial,
})

const arrow = (id: string, partial: Partial<ArrowObject> = {}): ArrowObject => ({
  id, type: 'arrow', x: 10, y: 20, w: 30, h: 10,
  start: { rx: 0, ry: 1 }, end: { rx: 1, ry: 0 },
  strokePt: 1, color: '#1B1B18', head: 'end', ...partial,
})

const shape = (id: string, partial: Partial<ShapeObject> = {}): ShapeObject => ({
  id, type: 'shape', shape: 'rect', x: 10, y: 20, w: 30, h: 20,
  strokePt: 1, color: '#1B1B18', fill: null, ...partial,
})

/** 自动保存会 PUT 到后端；这里只要不抛就行 */
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()

const reset = async () => {
  localStorage.clear()
  useSelectionStore.getState().clear()
  await s().switchDocument(emptyProject(), 'd_test')
}

/** 放入对象并选中它们（放入本身占一条历史，与复制那条分开计） */
const seed = (...objects: (TextObject | ArrowObject | ShapeObject)[]) => {
  s().commit(literal('放入对象'), (d) => {
    d.objects.push(...objects)
  })
  useSelectionStore.getState().set(objects.map((o) => o.id))
}

describe('duplicateSelected', () => {
  beforeEach(reset)

  for (const [kind, make] of [
    ['箭头', arrow],
    ['文字', text],
    ['形状', shape],
  ] as const) {
    it(`${kind}：不抛异常、真的多出一个对象、副本 +4mm 且换了 id`, () => {
      seed(make('o1'))
      expect(() => duplicateSelected()).not.toThrow()

      const objects = s().doc.objects
      expect(objects).toHaveLength(2)
      const [src, copy] = objects
      expect(copy.id).not.toBe(src.id)
      expect(copy.type).toBe(src.type)
      expect(copy.x).toBe(src.x + 4)
      expect(copy.y).toBe(src.y + 4)
      // 选区跟随副本，接着拖动/删除动的是新对象
      expect(useSelectionStore.getState().ids).toEqual([copy.id])
    })
  }

  it('成组对象一起复制：副本共享一个新 groupId，不与原件粘连', () => {
    seed(text('t1', { groupId: 'g1' }), text('t2', { groupId: 'g1' }))
    duplicateSelected()

    const objects = s().doc.objects
    expect(objects).toHaveLength(4)
    const copies = objects.slice(2)
    expect(copies[0].groupId).toBeTruthy()
    expect(copies[0].groupId).toBe(copies[1].groupId)
    expect(copies[0].groupId).not.toBe('g1')
  })

  it('一次复制 = 一条历史，undo 一次整体撤销', () => {
    seed(text('t1'), arrow('a1'))
    const before = s().past.length

    duplicateSelected()
    expect(s().past).toHaveLength(before + 1)
    expect(s().past.at(-1)!.label).toBe('复制对象')
    expect(s().doc.objects).toHaveLength(4)

    expect(s().undo()).toBe('复制对象')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['t1', 'a1'])
  })

  it('没有选中对象时什么都不做', () => {
    seed(text('t1'))
    useSelectionStore.getState().clear()
    const before = s().past.length

    duplicateSelected()
    expect(s().doc.objects).toHaveLength(1)
    expect(s().past).toHaveLength(before)
  })
})
