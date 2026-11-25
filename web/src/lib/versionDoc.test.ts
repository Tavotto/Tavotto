import { describe, expect, it } from 'vitest'
import { emptyDocument, emptyProject, type FigureDocument, type PanelObject } from '@/types/document'
import { isProjectVersion, projectVersionFromMeta, versionCanvasDoc } from './versionDoc'

const text = (id: string, value: string) => ({
  id, type: 'text' as const, text: value, sizePt: 9, bold: false,
  color: '#000', align: 'left' as const, x: 0, y: 0, w: 20, h: 8,
})

const chartPanel = (id: string): PanelObject => ({
  id, type: 'panel', fileId: 'input.svg', fileKind: 'svg',
  nativeW: 10, nativeH: 10, x: 0, y: 0, w: 10, h: 10,
  overrides: [], chart: null as unknown as PanelObject['chart'],
})

describe('versionCanvasDoc：节点正文 → 预览的那一张画布', () => {
  it('schema 2 原样返回（既有单画布节点路径不变）', () => {
    const doc: FigureDocument = { ...emptyDocument(), objects: [text('t1', '单画布')] }
    expect(versionCanvasDoc(doc, {})).toBe(doc)
    expect(isProjectVersion(doc)).toBe(false)
  })

  it('schema 3 项目节点按 meta.canvasId 取那一张，不拿 activeCanvasId 顶', () => {
    const project = emptyProject()
    project.canvases[0].objects = [text('a', '第一张')]
    project.canvases.push({
      id: 'c_two', name: 'Figure 2', page: { w: 100, h: 80 },
      objects: [text('b', '第二张')], guides: [],
    })
    project.activeCanvasId = project.canvases[0].id

    const second = versionCanvasDoc(project, { canvasId: 'c_two' })
    expect(second?.name).toBe('Figure 2')
    expect(second?.objects.map((o) => o.id)).toEqual(['b'])

    // 节点没记画布身份（旧节点 / 没带）：按项目正文自己的 activeCanvasId
    expect(versionCanvasDoc(project, {})?.objects.map((o) => o.id)).toEqual(['a'])
  })

  it('schema 4 项目节点保留图表面板状态（往返不降 schema、不丢 chart）', () => {
    const project = { ...emptyProject(), schema: 4 as const }
    project.canvases[0].objects = [chartPanel('p_chart')]
    const canvas = versionCanvasDoc(project, { canvasId: project.activeCanvasId })
    expect(canvas?.objects[0]).toMatchObject({ id: 'p_chart', type: 'panel' })
    expect(canvas?.objects[0]).toHaveProperty('chart')
    expect(isProjectVersion(project)).toBe(true)
  })

  it('记录的那张画布不在正文里：返回 null，明确失败，不退到别的画布', () => {
    const project = emptyProject()
    expect(versionCanvasDoc(project, { canvasId: 'c_missing' })).toBeNull()
  })

  it('不是能识别的项目文档：返回 null', () => {
    const broken = { schema: 3, canvases: [] } as unknown as FigureDocument
    expect(versionCanvasDoc(broken, {})).toBeNull()
  })
})

describe('projectVersionFromMeta：元数据侧与正文侧同一条判据', () => {
  it('schema 2 = 单画布；3 / 4 = 整份项目；缺席 = 不知道（null），不猜', () => {
    expect(projectVersionFromMeta({ schema: 2 })).toBe(false)
    expect(projectVersionFromMeta({ schema: 3 })).toBe(true)
    expect(projectVersionFromMeta({ schema: 4 })).toBe(true)
    expect(projectVersionFromMeta({})).toBeNull()
  })
})
