import { describe, expect, it } from 'vitest'
import { containsChartState, type PersistedProjectDocument } from './documentFormat'
import { emptyDocument, emptyProject, type FigureDocument, type PanelObject, type ProjectDocument } from '@/types/document'

const panel = (state: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'input.svg', fileKind: 'svg',
  nativeW: 10, nativeH: 10, x: 0, y: 0, w: 10, h: 10,
  overrides: [],
  ...state,
})

describe('containsChartState：只按字段在场判正文内容', () => {
  it('空文档 / 空项目没有图表状态', () => {
    expect(containsChartState(emptyDocument())).toBe(false)
    expect(containsChartState(emptyProject())).toBe(false)
  })

  it('图 / 项目文档都按全部画布看；面板上的 chart 或 chartSource 在场即为真', () => {
    const figure: FigureDocument = { ...emptyDocument(), objects: [panel({ chart: null as unknown as PanelObject['chart'] })] }
    expect(containsChartState(figure)).toBe(true)
    const withSource: FigureDocument = { ...emptyDocument(), objects: [panel({ chartSource: null as unknown as PanelObject['chartSource'] })] }
    expect(containsChartState(withSource)).toBe(true)

    const project = emptyProject()
    project.canvases.push({ id: 'c2', name: 'Figure 2', page: { w: 100, h: 80 }, objects: [panel({ chartSource: { schema: 99 } as unknown as PanelObject['chartSource'] })], guides: [] })
    expect(containsChartState(project)).toBe(true)
    expect(containsChartState({ ...project, activeCanvasId: 'c2' })).toBe(true)
  })

  it('只有别的对象 / 别的字段不算：坏状态也不靠缺席猜', () => {
    const figure: FigureDocument = { ...emptyDocument(), objects: [
      { ...panel(), id: 't', type: 'text', text: 'chart', sizePt: 9, bold: false, color: '#000', align: 'left' } as FigureDocument['objects'][number],
    ] }
    expect(containsChartState(figure)).toBe(false)
    // 字段在场但状态是坏的（null）仍按内容选中 schema 4——不能把它降成 3 丢掉。
    expect(containsChartState({ ...emptyDocument(), objects: [panel({ chart: null as unknown as PanelObject['chart'] })] })).toBe(true)
  })

  it('schema 4 的项目文档是同一类型（PersistedProjectDocument）也照内容判', () => {
    const project: ProjectDocument = { ...emptyProject(), schema: 4 }
    const persisted: PersistedProjectDocument = project
    expect(persisted.schema).toBe(4)
    expect(containsChartState(persisted)).toBe(false)
    persisted.canvases[0].objects.push(panel({ chart: null as unknown as PanelObject['chart'] }))
    expect(containsChartState(persisted)).toBe(true)
  })
})
