import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { buildExportRequest, originalAvailability } from '@/lib/exportRequest'
import { buildReviewRender } from '@/lib/reviewRender'
import { useDocumentStore } from '@/store/documentStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'

const panel: PanelObject = {
  id: 'p1', type: 'panel', fileId: 'runtime:show.py#fig', fileKind: 'runtime', nativeW: 80, nativeH: 60,
  overrides: [], x: 10, y: 5, w: 80, h: 60,
} as PanelObject

async function seed(objects: PanelObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_rr')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 150, h: 100 }
    d.objects = objects
  })
}
const base = (documentId: string | null, doc: ReturnType<typeof useDocumentStore.getState>['doc']) =>
  ({ formats: ['png'], filename: 'review', ppi: 300, documentId, doc }) as const

describe('buildReviewRender：取自导出请求，不另拼', () => {
  beforeEach(() => useWorkspaceStore.setState({ mode: 'layout', activePanelId: null }))

  it('排版：canvas 段与导出给的那一段逐字相等', async () => {
    await seed([panel])
    const { doc, documentId } = useDocumentStore.getState()
    const want = buildExportRequest({ ...base(documentId, doc), scope: 'canvas' }).request.canvas
    expect(want?.objects.length).toBe(1)
    // 与导出给的那一段相比，只多了面板的 object_id（文档对象自己的 id）；导出里的 `id`（项目内路径）原样还在，由引擎不用
    const got = buildReviewRender()!.canvas!
    expect(got.objects).toEqual(want!.objects.map((o) => ({ ...o, object_id: 'p1' })))
    expect({ ...got, objects: [] }).toEqual({ ...want, objects: [] })
  })

  it('排版：隐藏的对象不进载荷，object_id 仍按下标对到各自的面板；不合法的 id 不带；含路径的名字不当 label', async () => {
    const p2 = { ...panel, id: 'p2', name: 'Fig 2' } as PanelObject
    const hidden = { ...panel, id: 'hid', hidden: true } as PanelObject
    const bad = { ...panel, id: 'a/b', name: 'x/y' } as PanelObject
    await seed([hidden, p2, bad])
    const objs = buildReviewRender()!.canvas!.objects as { object_id?: string; label?: string }[]
    expect(objs).toHaveLength(2)
    expect(objs[0]).toMatchObject({ object_id: 'p2', label: 'Fig 2' })
    expect(objs[1].object_id).toBeUndefined()
    expect(objs[1].label).toBeUndefined()
  })

  it('单图编辑：original 段与导出给的那一段逐字相等', async () => {
    await seed([panel])
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    const { doc, documentId } = useDocumentStore.getState()
    const av = originalAvailability(panel.fileId, { panel })
    expect(av.ok).toBe(true)
    const want = buildExportRequest({
      ...base(documentId, doc), scope: 'original', figureId: panel.fileId, panel, spec: av.spec,
    }).request.original
    expect(want?.figure_id).toBe(panel.fileId)
    expect(buildReviewRender()).toEqual({ original: want })
  })

  it('单图编辑但图不在画布上：取不出，回 null', async () => {
    await seed([])
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'gone' })
    expect(buildReviewRender()).toBeNull()
  })
})
