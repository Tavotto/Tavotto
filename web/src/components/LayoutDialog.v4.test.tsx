import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LayoutDialog } from './LayoutDialog'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { setCurrentProjectId } from '@/lib/session'
import type { ProjectStatus } from '@/lib/api'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined, release: ((response: Response) => void) | undefined
beforeEach(async () => {
  document.body.innerHTML = ''; localStorage.clear(); setCurrentProjectId('p_dir')
  vi.stubGlobal('fetch', vi.fn(async (url: unknown) => {
    const path = new URL(String(url), 'http://localhost').pathname
    if (path === '/api/documents/v4/layouts') return new Promise<Response>(r => { release = r })
    if (path === '/api/layouts') return Response.json({ layouts: [] })
    return new Response('{}', { status: 404 })
  }))
  useProjectStore.setState({ project: { id: 'p_dir', document_dir: 'E:/legacy-parent' } as ProjectStatus })
  const project = emptyProject(); project.schema = 4
  await useDocumentStore.getState().switchDocument(project, 'dir4')
  useUiStore.setState({ layoutOpen: true, layoutIntent: 'save', layoutConflict: null, layoutName: null })
})
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; vi.unstubAllGlobals(); setCurrentProjectId(null) })
const mount = async () => { const el = document.createElement('div'); document.body.append(el); root = createRoot(el); await act(async () => { root!.render(<LayoutDialog />) }) }
const respond = (dir: string) => release!(Response.json({ layouts: [], document_dir: dir }, { headers: { 'X-Tavotto-Document-Format': 'v4' } }))

describe('v4保存目录只来自同版后端清单', () => {
  it('尚未读到目录时不借legacy父目录；拿到权威字段才显示真实v4位置', async () => {
    await mount()
    expect(document.querySelector('[data-layout-document-dir]')).toBeNull()
    await act(async () => { respond('E:/project/actual-v4') })
    expect(document.querySelector('[data-layout-document-dir]')?.getAttribute('title')).toBe('E:/project/actual-v4')
  })
  it('换成legacy后才到的v4清单不覆盖当前格式目录', async () => {
    await mount(); const old = release!
    await act(async () => { await useDocumentStore.getState().switchDocument(emptyProject(), 'legacy-dir') })
    await act(async () => { old(Response.json({ layouts: [], document_dir: 'E:/old-v4' }, { headers: { 'X-Tavotto-Document-Format': 'v4' } })) })
    expect(document.querySelector('[data-layout-document-dir]')?.getAttribute('title')).toBe('E:/legacy-parent')
  })
})
