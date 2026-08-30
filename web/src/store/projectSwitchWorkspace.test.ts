/**
 * 换项目不许把**旧文档**的工作区偏好抹掉（评审 #208 的 P2）。
 *
 * `resetForNewProject()` 里那句 `useWorkspaceStore.clear()` 上面写着「本机那
 * 一档按 documentId 存，切回去仍然作数」。它排在 `switchDocument()` 之前时那
 * 句话是假的：`startWorkspacePersistence` 的订阅此刻认的还是**旧**文档 id，
 * 于是这一清会把 `{mode:'layout'}` 写进 `tavotto.workspace.<旧 id>`，用户在
 * 那份文档里停的那张图当场被顶掉——而他只是切了个项目，从没表达过「我不要
 * 快速编辑了」。派生状态不许覆盖用户偏好。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import { canvasToDoc, type CanvasData, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useProjectStore } from './projectStore'
import { startWorkspacePersistence, useWorkspaceStore } from './workspace'

const PANEL: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 10,
  y: 20,
  w: 40,
  h: 30,
}

const OLD_DOC = 'doc-old'
const KEY = `tavotto.workspace.${OLD_DOC}`

globalThis.fetch = (async (url: unknown) => {
  const u = String(url)
  const body = u.includes('/api/projects/recent')
    ? { recent: [] }
    : u.includes('/api/projects/open')
      ? { id: 'p2', path: '/new', name: 'new', writable: true }
      : u.includes('/api/panels')
        ? { figures_dir: '/new', panels: [] }
        : {}
  return new Response(JSON.stringify(body), { status: 200 })
}) as typeof fetch

let stop: (() => void) | null = null

beforeEach(() => {
  localStorage.clear()
  setCurrentProjectId('p1')
  const canvas: CanvasData = {
    id: 'c1',
    name: 'Fig 1',
    page: { w: 150, h: 100 },
    objects: [{ ...PANEL }],
    guides: [],
  }
  useDocumentStore.setState({
    doc: canvasToDoc(canvas),
    canvases: [canvas],
    activeCanvasId: 'c1',
    openTabs: ['c1'],
    documentId: OLD_DOC,
    past: [],
    future: [],
    txn: null,
  })
  stop = startWorkspacePersistence()
})

afterEach(() => {
  stop?.()
  stop = null
  useWorkspaceStore.getState().clear()
  localStorage.clear()
})

describe('换项目与工作区偏好', () => {
  it('用户在旧文档里停在快速编辑上，切项目之后那一档还在', async () => {
    useWorkspaceStore.getState().enterFastEdit(PANEL.id)
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({
      mode: 'fast_edit',
      panelId: PANEL.id,
    })

    await useProjectStore.getState().open('/new')

    // 内存里当然回到排版（新项目是一份空白文档），但**旧文档那一档不许被动**
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({
      mode: 'fast_edit',
      panelId: PANEL.id,
    })
  })
})
