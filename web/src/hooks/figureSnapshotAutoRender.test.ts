import { describe, expect, it } from 'vitest'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { crossFigureNeighbours, EngineRenderSync, renderTargets } from './useEngineSync'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { seedExactRender } from '@/test/renderFixtures'
import { literal } from '@/i18n'

const captured = (): PanelObject => ({
  id: 'historical-A', type: 'panel', fileId: 'History.pdf', fileKind: 'pdf',
  nativeW: 50.8, nativeH: 25.4, x: 0, y: 0, w: 50.8, h: 25.4,
  script: 'plot.py', overrides: [{ gid: 'curve', prop: 'color', value: 'red' }],
  figureSnapshot: {
    version: 1, sourceId: 'History.pdf', snapshotId: 'a'.repeat(64),
    receiptId: `sha256:${'b'.repeat(64)}`, patchHash: `sha256:${'c'.repeat(64)}`,
    executionOwner: { script: 'plot.py', run_config: 'rc-original' },
  },
})

describe('保存Figure的归档显示不隐式执行来源', () => {
  it('脚本元数据、已有编辑、tracked、latest、绑定样式与邻居均不把归档显示变成Run', () => {
    const p = captured()
    expect(renderTargets([p], null, { 'History.pdf': true }, { 'History.pdf': 'old-live' }, {}, true, new Set([p.id]))).toEqual([])
  })

  it('明确进入图内编辑保留原请求路径，普通无ref面板保持原渲染理由', () => {
    const p = captured()
    expect(renderTargets([p], p.id, {})).toEqual([p])
    const live = { ...p }
    delete live.figureSnapshot
    expect(renderTargets([live], null, {})).toEqual([live])
  })

  it('归档面板不作为自动运行的跨图邻居', () => {
    const p = captured()
    expect(crossFigureNeighbours([p], 'another', 'layout', null)).toEqual(new Set())
  })

  it('显式坏ref也不能以失败回退为由偷偷运行来源', () => {
    const p = { ...captured(), figureSnapshot: null } as unknown as PanelObject
    expect(renderTargets([p], null, { 'History.pdf': true })).toEqual([])
  })

  it('普通打开不拿同名旧live清单改写归档面板的几何', async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true
    const p = captured()
    useUiStore.setState({ elementPanelId: null })
    useRenderStore.getState().clear()
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_archived_geometry')
    useDocumentStore.getState().commit(literal('准备归档'), draft => { draft.objects = [p] })
    seedExactRender(p, { stem: 'History', size_mm: [100, 80], elements: [] })
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    try {
      await act(async () => root.render(createElement(EngineRenderSync)))
      const current = useDocumentStore.getState().doc.objects[0] as PanelObject
      expect([current.x, current.y, current.w, current.h, current.nativeW, current.nativeH])
        .toEqual([0, 0, 50.8, 25.4, 50.8, 25.4])
      expect(current.figureSnapshot).toEqual(p.figureSnapshot)
    } finally {
      await act(async () => root.unmount())
      container.remove()
    }
  })
})
