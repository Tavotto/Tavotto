import { describe, expect, it } from 'vitest'
import {
  canvasToDoc,
  docToCanvas,
  emptyProject,
  isRuntimePanel,
  migrateToProject,
  panelKind,
  type FigureDocument,
  type PanelObject,
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

describe('AssetSource 双形态（ADR 0013）', () => {
  const runtimePanel = (): PanelObject => ({
    id: 'r1', type: 'panel',
    fileId: 'runtime:panels/myplot.py#myplot', fileKind: 'runtime',
    nativeW: 120, nativeH: 90, x: 0, y: 0, w: 120, h: 90,
    source: {
      script: 'panels/myplot.py', entry: '__main__', stem: 'myplot',
      captureSource: 'pyplot', fingerprint: 'sha256:x', sizeMm: [120, 90],
    },
    overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'T' }],
  })

  it('panelKind 判别三种已知形态，未知取值 fail closed', () => {
    expect(panelKind({ fileKind: 'pdf' })).toBe('pdf')
    expect(panelKind({ fileKind: 'raster' })).toBe('raster')
    expect(panelKind({ fileKind: 'runtime' })).toBe('runtime')
    // 更新版本文档里的新形态：绝不猜成文件——消费方按缺失素材处理
    expect(panelKind({ fileKind: 'holo' as PanelObject['fileKind'] })).toBe('unknown')
    expect(isRuntimePanel({ fileKind: 'runtime' })).toBe(true)
    expect(isRuntimePanel({ fileKind: 'pdf' })).toBe(false)
  })

  it('含 runtime 面板的文档经迁移与画布换算逐字段保真（schema 不升版）', () => {
    const doc: FigureDocument = {
      ...legacyDoc(),
      objects: [runtimePanel()] as FigureDocument['objects'],
    }
    const pd = migrateToProject(doc)!
    const [o] = pd.canvases[0].objects
    expect(o).toEqual(runtimePanel())     // fileId / source / overrides 原样
    const back = canvasToDoc(pd.canvases[0])
    expect(back.objects).toEqual([runtimePanel()])
  })

  it('老文档（纯 FileAsset）不受新字段影响', () => {
    const pd = migrateToProject(legacyDoc())!
    const [o] = pd.canvases[0].objects
    expect(o.type === 'panel' && panelKind(o)).toBe('pdf')
    expect((o as PanelObject).source).toBeUndefined()
  })
})
