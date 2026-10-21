/**
 * 换项目：工作区模式只由新文档的图数决定，旧文档的「停在哪张图上」不跟过来。
 *
 * 此前模式按 documentId 存本机一档、`resetForNewProject()` 的 `clear()` 必须排在
 * `switchDocument()` 之后，否则会把旧文档那一档抹掉（评审 #208 P2）。现在模式是文档的纯函数
 * （`startWorkspaceModeSync`），不落本机，没有「那一档」可抹；这里钉的是切换后内存里没有旧文档的残留。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setCurrentProjectId } from '@/lib/session'
import { canvasToDoc, type CanvasData, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useProjectStore } from './projectStore'
import { resetExportState, useExportStore } from './exportStore'
import { startWorkspaceModeSync, useWorkspaceStore } from './workspace'

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
  stop = startWorkspaceModeSync()
})

afterEach(() => {
  stop?.()
  stop = null
  useWorkspaceStore.getState().clear()
  localStorage.clear()
})

describe('换项目与工作区偏好', () => {
  it('旧文档只有一张图 = 单图编辑；切项目之后新文档是空白排版，不带旧文档的那张图', async () => {
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    expect(useWorkspaceStore.getState().activePanelId).toBe(PANEL.id)

    await useProjectStore.getState().open('/new')

    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(useWorkspaceStore.getState().activePanelId).toBeNull()
    // 不落本机：没有任何 `tavotto.workspace.*` 的键
    expect(Object.keys(localStorage).filter((k) => k.startsWith('tavotto.workspace.'))).toEqual([])
  })

  /**
   * 导出结果属于**旧项目**（PR #214 评审）。
   *
   * `outputs[].url` 是裸路径 `/exports/<name>`，渲染时由 `apiUrl()` 补上
   * **当前**项目的 `pj`——不清的话，切完项目再打开导出面板会看到旧项目的
   * 结果，而那些链接指向的是新项目的导出目录（不是 404 就是下到同名的
   * 另一张图）。轮询也会一直问一个属于旧项目的作业。
   */
  it('切项目把导出结果丢掉（**只清前端状态，不取消后端那个作业**）', async () => {
    resetExportState()
    useExportStore.setState({
      job: {
        job_id: 'j-old',
        status: 'done',
        outputs: [
          {
            format: 'pdf',
            name: 'Fig 1.pdf',
            url: '/exports/Fig 1.pdf',
            bytes: 1,
            dimensions: { px: null, mm: null },
            vector: true,
            status: 'done',
            replaced: false,
            error: null,
          },
        ],
        warnings: [],
        conflicts: [],
        error: null,
      },
      running: false,
    })

    await useProjectStore.getState().open('/new')

    expect(useExportStore.getState().job, '旧项目的导出结果不该跟着进新项目').toBeNull()
    expect(useExportStore.getState().running).toBe(false)
  })
})
