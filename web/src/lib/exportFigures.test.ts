/**
 * 「原图导出」的候选清单与默认对象（用户反馈 06）。
 *
 * 缺陷的形状：对话框只认快速编辑的 `activePanelId`，画布模式选中了面板仍说
 * 「先选中一张图」。这里钉住三件事：候选从哪来、排什么顺序；上下文按什么优先级
 * 挑；「没选」与「没得选」分得开。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject } from '@/types/document'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useWorkspaceStore } from '@/store/workspace'
import { contextFigureId, listExportableFigures } from './exportFigures'

const panel = (id: string, fileId: string, extra: Partial<PanelObject> = {}): PanelObject => ({
  id,
  type: 'panel',
  fileId,
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  ...extra,
})

const s = () => useDocumentStore.getState()

beforeEach(async () => {
  await s().switchDocument(emptyProject(), 'd_figs')
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  useSelectionStore.getState().clear()
  useAssetStore.setState({
    byId: {
      'a.pdf': { id: 'a.pdf', kind: 'pdf', mtime: 1, native_w_mm: 80, native_h_mm: 60 },
      'b.pdf': { id: 'b.pdf', kind: 'pdf', mtime: 2, native_w_mm: 90, native_h_mm: 70 },
      'c.png': { id: 'c.png', kind: 'raster', mtime: 3, native_w_mm: 50, native_h_mm: 40 },
    },
    panels: [
      { id: 'a.pdf', kind: 'pdf', mtime: 1, native_w_mm: 80, native_h_mm: 60 },
      { id: 'b.pdf', kind: 'pdf', mtime: 2, native_w_mm: 90, native_h_mm: 70 },
      { id: 'c.png', kind: 'raster', mtime: 3, native_w_mm: 50, native_h_mm: 40 },
    ],
  } as never)
  useRuntimeAssetStore.setState({ assets: [], byId: {}, previewNonce: {} } as never)
})

describe('候选清单', () => {
  it('文档里的面板排前面（激活画布 → 别的画布），素材清单里还没上画布的排后面，同一张图只列一次', () => {
    s().commit(literal('摆'), (d) => {
      d.objects = [panel('p1', 'b.pdf', { name: '产率' }), panel('p1b', 'b.pdf')]
    })
    const second = s().addCanvas('版二')
    s().commit(literal('摆'), (d) => {
      d.objects = [panel('p2', 'c.png', { fileKind: 'raster' })]
    })
    expect(s().activeCanvasId).toBe(second)

    const figs = listExportableFigures()
    expect(figs.map((f) => f.figureId)).toEqual(['c.png', 'b.pdf', 'a.pdf'])
    // 面板改过名用面板名，否则文件名主干；名字里不带扩展名
    expect(figs.map((f) => f.name)).toEqual(['c', '产率', 'a'])
    expect(figs[0].panel?.id).toBe('p2')
    expect(figs[1].panel?.id).toBe('p1')
    expect(figs[2].panel).toBeNull()
    expect(figs.map((f) => f.kind)).toEqual(['raster', 'pdf', 'pdf'])
    expect(figs[2].sizeMm).toEqual([80, 60])
  })

  it('runtime 素材也在清单里，带着「有没有物化预览」', () => {
    useRuntimeAssetStore.setState({
      assets: [
        {
          id: 'runtime:s.py#fig',
          script: 's.py',
          stem: 'fig',
          entry: 'main',
          status: 'fresh',
          cached: false,
          size_mm: [70, 50],
          capture_source: 'savefig',
          descriptor: null,
        },
      ],
      byId: {},
      previewNonce: {},
    } as never)
    const figs = listExportableFigures()
    const rt = figs.find((f) => f.figureId === 'runtime:s.py#fig')!
    expect(rt).toMatchObject({ kind: 'runtime', cached: false, name: 'fig', sizeMm: [70, 50] })
  })

  it('什么都没有 → 空清单', () => {
    useAssetStore.setState({ byId: {}, panels: [] } as never)
    expect(listExportableFigures()).toEqual([])
  })
})

describe('上下文里那张图', () => {
  const put = () =>
    s().commit(literal('摆'), (d) => {
      d.objects = [
        panel('p1', 'a.pdf'),
        panel('p2', 'b.pdf'),
        { id: 't1', type: 'text', text: 'x', sizePt: 9, x: 0, y: 0, w: 10, h: 5 } as never,
      ]
    })

  it('快速编辑正在编的那张最优先', () => {
    put()
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p2' })
    useSelectionStore.getState().set(['p1'])
    expect(contextFigureId(listExportableFigures())).toBe('b.pdf')
  })

  it('画布模式：选中的面板就是它（这正是缺陷所在——以前这里回 null）', () => {
    put()
    useSelectionStore.getState().set(['p2'])
    expect(contextFigureId(listExportableFigures())).toBe('b.pdf')
  })

  it('多选：主选（末位）是面板就按主选；主选是文字就退到选区里第一个面板', () => {
    put()
    useSelectionStore.getState().set(['p1', 'p2'])
    expect(contextFigureId(listExportableFigures())).toBe('b.pdf')
    useSelectionStore.getState().set(['p2', 'p1', 't1'])
    expect(contextFigureId(listExportableFigures())).toBe('b.pdf')
  })

  it('只选了文字、项目里不止一张图 → null（让用户在列表里点）', () => {
    put()
    useSelectionStore.getState().set(['t1'])
    expect(contextFigureId(listExportableFigures())).toBeNull()
  })

  it('什么都没选、整个项目只有一张图 → 就是它', () => {
    useAssetStore.setState({
      byId: { 'a.pdf': { id: 'a.pdf', kind: 'pdf', mtime: 1, native_w_mm: 80, native_h_mm: 60 } },
      panels: [{ id: 'a.pdf', kind: 'pdf', mtime: 1, native_w_mm: 80, native_h_mm: 60 }],
    } as never)
    expect(contextFigureId(listExportableFigures())).toBe('a.pdf')
  })

  it('什么都没选、项目里有几张图 → null', () => {
    expect(contextFigureId(listExportableFigures())).toBeNull()
  })
})
