import { describe, expect, it } from 'vitest'
import { applyLocale } from '@/i18n'
import {
  arrowHeads,
  canvasToDoc,
  defaultDocumentName,
  docToCanvas,
  emptyDocument,
  emptyProject,
  isRuntimePanel,
  legacyHead,
  migrateToProject,
  panelKind,
  type ArrowObject,
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

/**
 * 新文档的默认名（审计 T03）。
 *
 * 改造前是写死的 `fig_layout`——一个暴露实现习惯的名字，而它同时是顶栏显示的
 * 文档名、「另存为」的默认文件名和最近文档列表里的那一行。这里守两件事：
 * 它跟界面语言走（是给人看的），并且**磁盘安全**（它会直接当文件名用）。
 */
describe('新文档的默认名', () => {
  it('两个入口取的是同一个名字，且不是实现习惯里的那个', () => {
    expect(emptyDocument().name).toBe(defaultDocumentName())
    expect(emptyProject().project.name).toBe(defaultDocumentName())
    expect(defaultDocumentName()).not.toBe('fig_layout')
  })

  it('跟界面语言走', async () => {
    const zh = defaultDocumentName()
    await applyLocale('en-US')
    try {
      expect(defaultDocumentName()).not.toBe(zh)
      expect(defaultDocumentName().trim()).not.toBe('')
    } finally {
      await applyLocale('zh-CN')
    }
  })

  it('磁盘安全：两种语言下都不含路径分隔符与保留字符', async () => {
    // 「另存为」把它原样当文件名交给后端，净化只该动用户后来自己改的名字
    for (const lng of ['zh-CN', 'en-US'] as const) {
      await applyLocale(lng)
      const name = defaultDocumentName()
      expect(name, lng).not.toMatch(/[/\\:*?"<>|]/)
      expect(name, lng).not.toMatch(/^\.+$/)
      expect(name.trim(), lng).toBe(name)
      expect(name.length, lng).toBeGreaterThan(0)
    }
    await applyLocale('zh-CN')
  })
})

/**
 * 新旧箭头端型的一对互逆函数。
 *
 * `legacyHead` 守的是**老构建 / 老后端读到这份文档时看到什么**——它的效果在
 * 当前版本的界面上一处都看不见（新字段永远优先），所以只有直接盯着它的用例
 * 才拦得住「随手改成恒返回 end」这类退化。写在这里而不是某个消费点的用例里：
 * 属性栏改端型与类型切换成箭头是它的两个调用方，规则本身只有一份。
 */
describe('arrowHeads ↔ legacyHead：新旧端型字段互逆', () => {
  const arrow = (over: Partial<ArrowObject>): ArrowObject =>
    ({ id: 'a', type: 'arrow', x: 0, y: 0, w: 10, h: 10, start: { rx: 0, ry: 0 }, end: { rx: 1, ry: 1 }, strokePt: 1, color: '#000000', head: 'none', ...over }) as ArrowObject

  it.each([
    ['两端都有 → both', 'bar', 'triangle', 'both'],
    ['只有终点 → end', 'none', 'open', 'end'],
    ['只有起点 → 老字段表达不出「只有起点」，最接近的是 none', 'triangle', 'none', 'none'],
    ['两端都没有 → none', 'none', 'none', 'none'],
  ] as const)('%s', (_label, start, end, expected) => {
    expect(legacyHead({ start, end })).toBe(expected)
  })

  it('只写老字段时 arrowHeads 按它推导（读的那一半）', () => {
    expect(arrowHeads(arrow({ head: 'both' }))).toEqual({ start: 'triangle', end: 'triangle' })
    expect(arrowHeads(arrow({ head: 'end' }))).toEqual({ start: 'none', end: 'triangle' })
    expect(arrowHeads(arrow({ head: 'none' }))).toEqual({ start: 'none', end: 'none' })
  })

  it('写了新字段就以新字段为准（老字段只是回退）', () => {
    expect(arrowHeads(arrow({ head: 'both', headStart: 'none', headEnd: 'bar' }))).toEqual({
      start: 'none',
      end: 'bar',
    })
  })
})
