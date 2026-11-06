import { describe, expect, it } from 'vitest'
import { PRODUCT_NAME } from './brand'
import {
  chartVersionCanvas, chartVersionProject, containsChartState, documentAddressKey, documentApiPath,
  documentFormatForSchema, documentNamespaceKey, documentResponseFormat, parseDocumentFormat,
} from './documentFormat'
import { emptyDocument, type FigureDocument } from '@/types/document'

describe('显式文档格式，不改变旧缺省地址和键', () => {
  it.each([
    '/api/layouts', '/api/layouts/%E5%9B%BE%E4%B8%80?target=project',
    '/api/autosave/d_same/summary', '/api/layout-session/candidates?scope=project',
    '/api/layout-session/owner?doc_id=d_same', '/api/layout-session/last',
    '/api/versions/d_same/v1/thumb?f=png', '/api/versions/d_same/rebuild',
  ])('%s 的旧路径逐字保留，v4 显式隔离', (path) => {
    expect(documentApiPath(path)).toBe(path)
    expect(documentApiPath(path, 'legacy')).toBe(path)
    expect(documentApiPath(path, 'v4')).toBe(`/api/documents/v4${path.slice(4)}`)
  })

  it.each(['/api/package/open', '/api/package', '/api/engine/render', '/api/layouts-extra'])('不为尚未实现的 %s 创造v4别名', (path) => {
    expect(documentApiPath(path)).toBe(path)
    expect(() => documentApiPath(path, 'v4')).toThrow('unsupported_document_route')
  })

  it.each(['autosave.d_same', 'recovery.d_same', 'currentDoc', 'docIndex', 'tabs.d_same', 'projectDoc.pj', 'projectFile.d_same'])('%s 的本机键在两namespace隔离', (suffix) => {
    const key = `${PRODUCT_NAME.toLowerCase()}.${suffix}`
    expect(documentNamespaceKey(key)).toBe(key)
    expect(documentNamespaceKey(key, 'legacy')).toBe(key)
    expect(documentNamespaceKey(key, 'v4')).toBe(`${PRODUCT_NAME.toLowerCase()}.documents.v4.${suffix}`)
  })

  it('地址键包含format，同docId不共享队列/修订缓存', () => {
    expect(documentAddressKey('d_same')).toBe('d_same')
    expect(documentAddressKey('d_same', 'v4')).toBe('["v4","d_same"]')
    expect(documentAddressKey('d_same', 'v4')).not.toBe(documentAddressKey('d_same'))
    expect(() => documentNamespaceKey('foreign.key', 'v4')).toThrow('unsupported_document_key')
  })

  it('缺省格式是legacy，未知格式/schema不静默回落', () => {
    expect(parseDocumentFormat(undefined)).toBe('legacy')
    expect(parseDocumentFormat('legacy')).toBe('legacy')
    expect(parseDocumentFormat('v4')).toBe('v4')
    for (const value of [null, '', 4, 'v5', {}, true]) expect(parseDocumentFormat(value)).toBeNull()
    expect(documentFormatForSchema(2)).toBe('legacy')
    expect(documentFormatForSchema(3)).toBe('legacy')
    expect(documentFormatForSchema(4)).toBe('v4')
    for (const schema of [undefined, null, 1, 5, '4']) expect(documentFormatForSchema(schema)).toBeNull()
  })

  it('v4 GET要求自己的格式头，缺头/错误头不得降级', () => {
    expect(documentResponseFormat(null)).toBe('legacy')
    expect(documentResponseFormat('legacy')).toBe('legacy')
    expect(documentResponseFormat('v4', 'v4')).toBe('v4')
    for (const header of [null, 'legacy', 'v5', '']) expect(documentResponseFormat(header, 'v4')).toBeNull()
    expect(documentResponseFormat('v4')).toBeNull()
  })

  it.each([{ chart: null }, { chartSource: null }, { chartSource: { schema: 99 } }])('格式选择看字段在场，不把坏状态 %j 降成legacy', (state) => {
    const document = emptyDocument()
    expect(containsChartState(document)).toBe(false)
    document.objects = [{ id: 'p', type: 'panel', fileId: 'input.svg', fileKind: 'svg', nativeW: 10, nativeH: 10,
      x: 0, y: 0, w: 10, h: 10, overrides: [], ...state } as unknown as FigureDocument['objects'][number]]
    expect(containsChartState(document)).toBe(true)
    const project = chartVersionProject(document, { project: { id: 'p', name: 'n' }, canvasId: 'c', createdAt: 1, updatedAt: 1 })
    expect(containsChartState(project)).toBe(true)
  })
})

describe('v4版本仍只保存捕获的活动画布', () => {
  const doc = (): FigureDocument => ({
    ...emptyDocument(), name: '捕获的B画布',
    objects: [{ id: 'same', type: 'panel', x: 1, y: 2, w: 50, h: 40,
      fileId: '原图.svg', fileKind: 'svg', nativeW: 50, nativeH: 40,
      overrides: [{ gid: 'line', prop: 'color', value: 'red' }, { gid: 'line', prop: 'color', value: 'blue' }] }],
    framePad: { l: 1, r: 2, t: 3, b: 4 }, framePadTight: ['l'],
  })
  const identity = () => ({ project: { id: 'project', name: '项目' }, canvasId: 'canvas-B', createdAt: 10, updatedAt: 20 })

  it('单canvas schema4包装/投影复用既有转换，完整edits原序和身份保留', () => {
    const figure = doc(), captured = identity()
    const project = chartVersionProject(figure, captured)
    expect(project.schema).toBe(4)
    expect(project.project).toEqual(captured.project)
    expect(project.canvases).toHaveLength(1)
    expect(project.canvases[0].id).toBe('canvas-B')
    expect(project.activeCanvasId).toBe('canvas-B')
    expect(project.createdAt).toBe(10)
    expect(project.updatedAt).toBe(20)
    expect(chartVersionCanvas(project)).toEqual(figure)
    figure.objects[0].x = 99; captured.project.name = '后来项目'
    expect(project.canvases[0].objects[0].x).toBe(1)
    expect(project.project.name).toBe('项目')
    const restored = chartVersionCanvas(project)!
    restored.objects[0].x = 88
    expect(project.canvases[0].objects[0].x).toBe(1)
  })

  it('多画布节点不猜first/active进行恢复；旧schema与畸形节点不冒充v4', () => {
    const project = chartVersionProject(doc(), identity())
    expect(chartVersionCanvas({ ...project, canvases: [...project.canvases, structuredClone(project.canvases[0])] })).toBeNull()
    for (const raw of [null, {}, { ...project, schema: 3 }, { ...project, canvases: [] },
      { ...project, canvases: [{ ...project.canvases[0], objects: null }] }]) expect(chartVersionCanvas(raw)).toBeNull()
  })
})
