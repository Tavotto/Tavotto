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
    expect(buildReviewRender()).toEqual({ canvas: want })
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
