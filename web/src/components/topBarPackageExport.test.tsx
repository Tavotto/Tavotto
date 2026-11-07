import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/lib/api'
import { formatMessage, i18n } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { canvasToDoc, emptyProject, type PanelObject, type ProjectDocument } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { DocumentMenu } from './TopBar'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const scene = 'b47a8d7e67dd3b5da31dc9c7bdc2db0a95dbaaf7d4878d5869964177efaa103e'
function project(format: 'legacy' | 'v4'): ProjectDocument {
  const pd = emptyProject()
  pd.schema = format === 'v4' ? 4 : 3
  pd.project.name = 'Whole Project'
  pd.canvases[0].name = 'Active canvas'
  const panel: PanelObject = {
    id: 'p1', type: 'panel', fileId: 'deleted.svg', fileKind: 'svg', x: 1, y: 2, w: 35, h: 35, nativeW: 35, nativeH: 35,
    overrides: [{ gid: 'axes_0.lines_0', prop: 'color', value: 'red' }, { gid: 'axes_0.lines_0', prop: 'color', value: 'blue' }],
  }
  if (format === 'v4') {
    panel.chartSource = { schema: 1, sourceId: 'deleted.svg', bytesSha256: scene, sizeBytes: 381, sceneDigest: scene }
    panel.chart = {
      model: {
        schema: 'tavotto.chart-model/1', rev: 1, scene_digest: scene,
        axes: [{ id: 'ax1', rect: [10, 10, 80, 80], xlim: [0, 1], ylim: [0, 1], xscale: 'linear', yscale: 'linear', invert_x: false, invert_y: false, kind: 'numeric' }],
        series: [{ id: 's1', axes: 'ax1', kind: 'line', node: 'curve', x: 'x', y: 'y', style: { color: '#0000ff', linewidth: 3 } }],
        dataset: { columns: { x: [0, 1], y: [0.25, 0.25] } },
        data_digest: '977531c48efa3068889df31859033220e1bf29925cff6a04840753d498992ca6',
        engine: { matplotlib: '3.11.2', numpy: '2.5.4', tavotto: '0.20.0' },
      },
      binding: { model_rev: 1, scene_digest: scene, entries: [{ object: 'series:s1', node: 'curve' }, { object: 'axes:ax1', node: 'old_axis' }] },
    }
  }
  pd.canvases.push({ ...structuredClone(pd.canvases[0]), id: 'inactive', name: 'Inactive canvas', objects: [panel] })
  return pd
}

function install(pd: ProjectDocument, id = 'd_package') {
  useDocumentStore.setState({
    doc: canvasToDoc(pd.canvases[0]), canvases: pd.canvases, activeCanvasId: pd.activeCanvasId,
    projectMeta: { ...pd.project, createdAt: pd.createdAt }, documentId: id,
    documentFormat: pd.schema === 4 ? 'v4' : 'legacy', loadSeq: useDocumentStore.getState().loadSeq + 1,
    recentDocs: [], projectFile: null,
  })
}

let host: HTMLDivElement
let root: Root
const requests: { url: URL; body: { stem: string; doc: ProjectDocument; settings: object } }[] = []
let replies: ((response: Response) => void)[]
const response = (name = 'Project.tavotto', v4 = true, missing: string[] = []) => Response.json({
  name, url: `/api/download/${name}`, assets: 2, missing, ...(v4 ? { document_format: 'v4', chart_sources: 1 } : {}),
})

async function clickExport() {
  const trigger = host.querySelector<HTMLButtonElement>('[data-document-menu]')!
  await act(async () => {
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await new Promise(resolve => setTimeout(resolve, 0))
  })
  const item = document.querySelector<HTMLElement>('[data-export-package]')
  expect(item).not.toBeNull()
  // Radix在卸载之后才归还焦点；下一次键盘操作从完成关闭的同一触发器出发。
  let dispose = () => {}
  let checkClosed = () => {}
  const restored = new Promise<void>((resolve, reject) => {
    const onFocus = () => {
      if (trigger.getAttribute('data-state') !== 'closed' || item!.isConnected || document.activeElement !== trigger) return
      dispose(); resolve()
    }
    const timeout = setTimeout(() => { dispose(); reject(new Error('Menu did not finish closing and restore its trigger focus')) }, 1000)
    dispose = () => { clearTimeout(timeout); trigger.removeEventListener('focusin', onFocus) }
    checkClosed = onFocus
    trigger.addEventListener('focusin', onFocus)
  })
  try {
    await act(async () => { item!.click() })
    // 若焦点本来就在trigger，关闭没有额外focusin；仍核同一closed/disconnect事实。
    checkClosed()
    await act(async () => { await restored })
  } finally { dispose() }
}

beforeEach(async () => {
  requests.length = 0; replies = []
  setCurrentProjectId('p_captured')
  install(project('v4'))
  useUiStore.getState().setStatus(null)
  vi.spyOn(api, 'createPackage')
  vi.stubGlobal('fetch', vi.fn((url: unknown, init?: RequestInit) => {
    const parsed = new URL(String(url), 'http://localhost')
    if (!parsed.pathname.endsWith('/package')) return Promise.resolve(Response.json({}, { status: 404 }))
    requests.push({ url: parsed, body: JSON.parse(String(init?.body)) })
    return new Promise<Response>(resolve => replies.push(resolve))
  }))
  host = document.createElement('div'); document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<DocumentMenu />))
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useUiStore.getState().setStatus(null)
  setCurrentProjectId(null)
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})

describe('顶部项目包入口的完整快照与迟到回执', () => {
  it('v4传完整深拷贝项目，包括无原文件的附件与非活动画布，不借素材重链接', async () => {
    await clickExport()
    expect(api.createPackage).toHaveBeenCalledTimes(1)
    const [, captured, settings, opts] = vi.mocked(api.createPackage).mock.calls[0]
    expect(opts).toEqual({ format: 'v4', pj: 'p_captured' }); expect(settings).toEqual({})
    expect(requests[0].url.pathname).toBe('/api/documents/v4/package')
    expect(requests[0].url.searchParams.get('pj')).toBe('p_captured')
    expect(requests[0].body.stem).toBe('Whole Project')
    expect(captured).toEqual(requests[0].body.doc)
    const inactive = (captured as ProjectDocument).canvases[1]
    expect(inactive.name).toBe('Inactive canvas')
    const source = inactive.objects[0] as PanelObject
    expect(source.chartSource?.sourceId).toBe('deleted.svg')
    expect(source.chart?.model.dataset.columns.y).toEqual([0.25, 0.25])
    expect(source.overrides.map(edit => edit.value)).toEqual(['red', 'blue'])
    expect(captured).not.toHaveProperty('objects')
    expect(inactive).not.toBe(useDocumentStore.getState().canvases[1])
    useDocumentStore.getState().canvases[1].name = 'Changed while exporting'
    const original = useDocumentStore.getState().canvases[1].objects[0] as PanelObject
    original.chart!.model.dataset.columns.y[0] = 99
    original.chartSource!.sizeBytes = 999
    original.overrides[0].value = 'green'
    expect(inactive.name).toBe('Inactive canvas')
    expect(source.chart!.model.dataset.columns.y).toEqual([0.25, 0.25])
    expect(source.chartSource!.sizeBytes).toBe(381)
    expect(source.overrides.map(edit => edit.value)).toEqual(['red', 'blue'])
    expect(requests).toHaveLength(1)
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('deleted.svg'))).toBe(false)
    await act(async () => replies[0](response()))
    expect(useUiStore.getState().status).toMatchObject({ key: 'status.packaged', values: { name: 'Project.tavotto', count: 2 } })
  })

  it('普通schema3项目仍用旧三参调用和原路由，旧四键回执成功', async () => {
    await act(async () => install(project('legacy')))
    await clickExport()
    expect(vi.mocked(api.createPackage).mock.calls[0]).toHaveLength(3)
    expect(requests[0].url.pathname).toBe('/api/package')
    expect(requests[0].body.doc.schema).toBe(3)
    expect(requests[0].body.doc.canvases).toHaveLength(2)
    await act(async () => replies[0](response('Legacy.tavotto', false)))
    expect(useUiStore.getState().status).toMatchObject({ key: 'status.packaged', values: { name: 'Legacy.tavotto' } })
  })

  it.each([{ missing: ['one.png'] }, { missing: ['one.png', 'two.pdf', 'three.jpg'] }])('v4包已生成但缺普通素材$missing，准确说明数量与恢复前需补齐', async ({ missing }) => {
    await clickExport()
    await act(async () => replies[0](response('Partial.tavotto', true, missing)))
    const status = useUiStore.getState().status
    expect(status).toEqual({ key: 'status.packagedIncomplete', ns: 'workspace', values: { name: 'Partial.tavotto', count: missing.length } })
    expect(useUiStore.getState().statusTone).toBe('done')
    expect(formatMessage(status)).toBe(`已生成项目包 Partial.tavotto，缺少 ${missing.length} 项素材。恢复前请补齐。`)
    await act(async () => { await i18n.changeLanguage('en-US') })
    const noun = missing.length === 1 ? 'asset' : 'assets'
    expect(formatMessage(status)).toBe(`Created project package Partial.tavotto with ${missing.length} missing ${noun}. Add the missing ${noun} before restoring.`)
    expect(vi.mocked(api.createPackage).mock.calls[0][3]).toEqual({ format: 'v4', pj: 'p_captured' })
  })

  it('legacy缺普通素材仍保旧三参和原状态，不启用v4提示', async () => {
    await act(async () => install(project('legacy')))
    await clickExport()
    await act(async () => replies[0](response('Legacy.tavotto', false, ['old-missing.png'])))
    expect(vi.mocked(api.createPackage).mock.calls[0]).toHaveLength(3)
    expect(useUiStore.getState().status).toEqual({ key: 'status.packaged', ns: 'workspace', values: { name: 'Legacy.tavotto', count: 2 } })
  })

  it.each([{ missing: [] }, { missing: ['data.csv'] }, { missing: ['data.csv', 'image.png'] }])('运行依赖未确认与已知缺素材分别准确提示：$missing', async ({ missing }) => {
    await clickExport()
    await act(async () => replies[0](Response.json({
      name: 'Execution.tavotto', url: '/exports/Execution.tavotto', assets: 2, missing,
      document_format: 'v4', chart_sources: 1,
      execution_coverage: { state: 'incomplete', issues: [{ script: 'plot.py', reason: 'dependency_set_undeclared' }] },
    })))
    const status = useUiStore.getState().status
    expect(status?.key).toBe(missing.length ? 'status.packagedIncompleteWithExecution' : 'status.packagedExecutionIncomplete')
    expect(status?.values).toEqual({ name: 'Execution.tavotto', ...(missing.length ? { count: missing.length } : {}) })
    expect(formatMessage(status)).toContain('运行依赖')
    if (missing.length) expect(formatMessage(status)).toContain(`缺少 ${missing.length} 项素材`)
    else expect(formatMessage(status)).not.toContain('缺少')
    await act(async () => { await i18n.changeLanguage('en-US') })
    const text = formatMessage(status)
    expect(text).toContain(missing.length ? 'unconfirmed runtime dependencies' : 'Runtime dependencies have not been fully confirmed')
    if (missing.length) expect(text).toContain(`${missing.length} missing ${missing.length === 1 ? 'asset and' : 'assets and'}`)
  })

  it.each(['project', 'format', 'document', 'load'] as const)('不完整包迟到回执也不覆盖已切换的%s状态', async dimension => {
    await clickExport()
    await act(async () => {
      const state = useDocumentStore.getState()
      if (dimension === 'project') setCurrentProjectId('p_new')
      if (dimension === 'format') useDocumentStore.setState({ documentFormat: 'legacy' })
      if (dimension === 'document') useDocumentStore.setState({ documentId: 'd_new' })
      if (dimension === 'load') useDocumentStore.setState({ loadSeq: state.loadSeq + 1 })
      useUiStore.getState().setStatus({ key: 'current-document-status' })
      replies[0](response('OldPartial.tavotto', true, ['one.png']))
    })
    expect(useUiStore.getState().status?.key).toBe('current-document-status')
  })

  it.each(['project', 'format', 'document', 'load'] as const)('迟到成功/失败不覆盖已切换的%s状态', async dimension => {
    await clickExport()
    await act(async () => {
      const state = useDocumentStore.getState()
      if (dimension === 'project') setCurrentProjectId('p_new')
      if (dimension === 'format') useDocumentStore.setState({ documentFormat: 'legacy' })
      if (dimension === 'document') useDocumentStore.setState({ documentId: 'd_new' })
      if (dimension === 'load') useDocumentStore.setState({ loadSeq: state.loadSeq + 1 })
      useUiStore.getState().setStatus({ key: 'current-document-status' })
      replies[0](dimension === 'format' || dimension === 'load'
        ? Response.json({ code: 'chart_source_missing', error: 'Source unavailable' }, { status: 422 }) : response())
    })
    expect(useUiStore.getState().status?.key).toBe('current-document-status')
  })

  it('同一文档再次明确导出，先发回执也不得覆盖后发操作结果', async () => {
    await clickExport(); await clickExport()
    expect(requests).toHaveLength(2)
    await act(async () => replies[1](response('Second.tavotto')))
    await act(async () => replies[0](Response.json({ error: 'First failed' }, { status: 422 })))
    expect(useUiStore.getState().status).toMatchObject({ key: 'status.packaged', values: { name: 'Second.tavotto' } })
  })

  it('当前v4缺失格式回执沿原失败状态呈现，不报成功', async () => {
    await clickExport()
    await act(async () => replies[0](response('Wrong.tavotto', false)))
    expect(useUiStore.getState().status?.key).toBe('status.packageFailed')
    expect(useUiStore.getState().statusTone).toBe('error')
  })
})
