import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { openPackageFile } from './packageImport'
import { openPackage } from './api'
import { setCurrentProjectId } from './session'
import { useDocumentStore } from '@/store/documentStore'
import { materializeRelink, useClipboardStore } from './clipboard'
import { useAssetStore } from '@/store/assetStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject, type ProjectDocument } from '@/types/document'
import type { PackageOpenResult } from './api'

vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), openPackage: vi.fn() }))

const panel: PanelObject = {
  id: 'chart', type: 'panel', fileId: 'original.svg', fileKind: 'svg',
  x: 0, y: 0, w: 10, h: 10, nativeW: 10, nativeH: 10, overrides: [],
  chartSource: { schema: 1, sourceId: 'original.svg', bytesSha256: 'a'.repeat(64), sizeBytes: 10, sceneDigest: 'b'.repeat(64) },
}
const response = (): PackageOpenResult => {
  const doc = emptyProject()
  doc.schema = 4
  doc.canvases[0].objects = [structuredClone(panel)]
  return { doc, manifest: {}, missing: ['original.svg'], drifted: [], missing_chart_sources: [], verified_chart_sources: [panel.chartSource!] }
}
beforeEach(async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
  setCurrentProjectId('A')
  await useDocumentStore.getState().switchDocument(emptyProject(), 'before-package')
  useClipboardStore.getState().setPending(null)
  vi.mocked(openPackage).mockReset()
})
afterEach(() => { setCurrentProjectId(null); vi.unstubAllGlobals() })

it('已核实附件独立于图库原件，不弹普通重链接框', async () => {
  vi.mocked(openPackage).mockResolvedValue(response())
  await openPackageFile(new File([], 'chart.tavotto'))
  expect(useDocumentStore.getState().doc.objects[0]).toMatchObject(panel)
  expect(useClipboardStore.getState().pending).toBeNull()
  expect(useUiStore.getState().status?.key).toBe('status.packageOpened')
})

it('附件缺失显式失败，不宣布打开成功', async () => {
  vi.mocked(openPackage).mockResolvedValue({ ...response(), missing_chart_sources: ['a'.repeat(64)], verified_chart_sources: [] })
  await expect(openPackageFile(new File([], 'chart.tavotto'))).rejects.toThrow('图表来源缺失或损坏')
  expect(useDocumentStore.getState().documentId).toBe('before-package')
})

it.each([false, true])('上传期间项目换代（回到 A=%s）不把 A 的包安装到当前文档', async back => {
  let finish!: (res: PackageOpenResult) => void
  vi.mocked(openPackage).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const opening = openPackageFile(new File([], 'chart.tavotto'))
  setCurrentProjectId('B')
  if (back) setCurrentProjectId('A')
  finish(response())
  expect(await opening).toBe(false)
  expect(useDocumentStore.getState().documentId).toBe('before-package')
  expect(useClipboardStore.getState().pending).toBeNull()
  expect(useUiStore.getState().status?.key).toBe('layout.openSkipped')
})

it('普通面板与自包含图表共用 fileId，重链接只改普通面板', async () => {
  const res = response()
  ;(res.doc as ProjectDocument).canvases[0].objects.push({ ...structuredClone(panel), id: 'ordinary', chartSource: undefined })
  vi.mocked(openPackage).mockResolvedValue(res)
  await openPackageFile(new File([], 'chart.tavotto'))
  const pending = useClipboardStore.getState().pending!
  useAssetStore.setState({ byId: { 'replacement.svg': { id: 'replacement.svg', kind: 'svg', name: 'replacement', native_w_mm: 10, native_h_mm: 10 } } as never })
  materializeRelink(pending.missing.map(m => ({ ...m, relinkTo: 'replacement.svg' })))
  expect(useDocumentStore.getState().doc.objects[0]).toMatchObject(panel)
  expect(useDocumentStore.getState().doc.objects[1]).toMatchObject({ id: 'ordinary', fileId: 'replacement.svg' })
})
