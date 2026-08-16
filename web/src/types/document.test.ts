import { describe, expect, it } from 'vitest'
import {
  canvasToDoc,
  docToCanvas,
  emptyProject,
  migrateToProject,
  type FigureDocument,
} from './document'

const legacyDoc = (): FigureDocument => ({
  schema: 2,
  name: 'fig3_layout',
  page: { w: 150, h: 100, margin: 5 },
  objects: [
    {
      id: 'o1', type: 'panel', fileId: 'a/p1.pdf', fileKind: 'pdf',
      nativeW: 35, nativeH: 17, x: 1.5, y: 2.5, w: 35, h: 17,
      overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'T' }],
      crop: { x: 0.1, y: 0.1, w: 0.8, h: 0.8 }, rotation: 90,
    },
    {
      id: 'o2', type: 'text', text: 'hello', sizePt: 9, bold: true,
      color: '#123456', align: 'center', x: 10, y: 20, w: 30, h: 8,
      groupId: 'g1',
    },
  ] as FigureDocument['objects'],
  guides: [{ axis: 'x', pos: 42 }],
  layoutGroups: [
    { id: 'g1', kind: 'row', order: ['o1', 'o2'], gap: 4, align: 'center' },
  ],
})

describe('schema 2 → 3 迁移', () => {
  it('内容与尺寸逐字段一致，成为唯一画布', () => {
    const legacy = legacyDoc()
    const pd = migrateToProject(structuredClone(legacy))!
    expect(pd.schema).toBe(3)
    expect(pd.canvases).toHaveLength(1)
    const c = pd.canvases[0]
    expect(c.name).toBe('fig3_layout')
    expect(c.page).toEqual(legacy.page)
    expect(c.objects).toEqual(legacy.objects)
    expect(c.guides).toEqual(legacy.guides)
    expect(c.layoutGroups).toEqual(legacy.layoutGroups)
    expect(pd.activeCanvasId).toBe(c.id)
    expect(pd.project.name).toBe('fig3_layout')
  })

  it('schema 3 原样通过；activeCanvasId 失配时回退第一张', () => {
    const pd = emptyProject()
    expect(migrateToProject(pd)).toBe(pd)
    const broken = { ...pd, activeCanvasId: 'nope' }
    expect(migrateToProject(broken)!.activeCanvasId).toBe(pd.canvases[0].id)
  })

  it('不认识的负载返回 null', () => {
    expect(migrateToProject(null)).toBeNull()
    expect(migrateToProject({ schema: 1 })).toBeNull()
    expect(migrateToProject({ schema: 3, canvases: [] })).toBeNull()
    expect(migrateToProject('x')).toBeNull()
  })

  it('canvasToDoc / docToCanvas 互逆', () => {
    const legacy = legacyDoc()
    const canvas = docToCanvas(legacy, 'c_test')
    const back = canvasToDoc(canvas)
    expect(back.name).toBe(legacy.name)
    expect(back.objects).toEqual(legacy.objects)
    expect(back.layoutGroups).toEqual(legacy.layoutGroups)
    expect(docToCanvas(back, 'c_test')).toEqual(canvas)
  })
})
