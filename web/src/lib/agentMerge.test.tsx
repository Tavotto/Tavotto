/**
 * 「把 Agent 加的并进来」（ADR 0208 §五）：后端说「磁盘多出来的只有 Agent 追加的对象」时，桌面一次 commit 并进当前画布
 * （一步可撤销）、把项目文件的基线推到磁盘那一份。维护者 2026-11-25：没有要用户裁决的事就**不提示**——⌘S 撞上冲突时直接
 * 并进来再保存，回到前台时直接并进画布。
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAgentAdditions: vi.fn(),
  fetchLayoutList: vi.fn(async () => ({ names: [], modified: {} })),
  saveLayout: vi.fn(),
}))

import { ApiError, fetchAgentAdditions, saveLayout } from '@/lib/api'
import { literal } from '@/i18n'
import { mergeAgentAdditions, mergeableAgentAdditions, watchAgentAdditions } from '@/lib/agentMerge'
import { writeBoundProjectFile } from '@/store/projectSave'
import { captureSaveContext } from '@/store/saveContext'
import { setCurrentProjectId } from '@/lib/session'
import { activeProjectFile, setProjectFile, useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type ArrowObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockAdd = vi.mocked(fetchAgentAdditions)
const mockSave = vi.mocked(saveLayout)

const arrow = (id: string): ArrowObject => ({
  id,
  type: 'arrow',
  x: 10,
  y: 10,
  w: 20,
  h: 5,
  start: { rx: 0, ry: 0 },
  end: { rx: 1, ry: 1 },
  color: '#000000',
  strokePt: 0.75,
  head: 'end',
  anchor: { v: 1, by: 'agent', anchors: [{ role: 'to', panel: { id: 'figures/a.pdf', point: 'n' } }] },
})


beforeEach(async () => {
  vi.clearAllMocks()
  useProjectStore.setState({ project: { open: true, id: 'p1', document_dir: '/figs/a/tavottofile' } })
  setCurrentProjectId('p1')
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_agentmerge')
  useDocumentStore.getState().commit(literal('已有的'), (d) => {
    d.objects.push(arrow('a_mine0001'))
  })
  setProjectFile({ projectId: 'p1', name: '组图', file: 'tavottofile/组图.json', revision: 'rev-base', dirty: false })
})

afterEach(async () => {
  document.body.innerHTML = ''
  useUiStore.setState({ layoutOpen: false, status: null })
})

const additions = (canvasId = useDocumentStore.getState().activeCanvasId, ids = ['a_agent001', 't_agent002']) => ({
  available: true,
  revision: 'rev-disk',
  count: ids.length,
  canvases: [{ id: canvasId, objects: ids.map(arrow) }],
})

describe('能不能并', () => {
  it('后端给了、全在当前画布、id 不撞：可以并；问的是绑定的名字与基线', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = await mergeableAgentAdditions()
    expect(mockAdd).toHaveBeenCalledWith('组图', 'rev-base')
    expect(m).toMatchObject({ name: '组图', revision: 'rev-disk', count: 2 })
  })

  it('加在别的画布上：不并（撤销栈只管当前画布）', async () => {
    mockAdd.mockResolvedValue(additions('c_other0001'))
    expect(await mergeableAgentAdditions()).toBeNull()
  })

  it('id 撞上内存里已有的对象：不并', async () => {
    mockAdd.mockResolvedValue(additions(undefined, ['a_mine0001']))
    expect(await mergeableAgentAdditions()).toBeNull()
  })

  it('后端说没有：不并', async () => {
    mockAdd.mockResolvedValue({ available: false })
    expect(await mergeableAgentAdditions()).toBeNull()
  })
})

describe('并进来', () => {
  it('一次 commit 加进当前画布、一步撤销拿掉；基线推到磁盘那一份、标为待保存', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = (await mergeableAgentAdditions())!
    expect(mergeAgentAdditions(m)).toBe(true)
    const ids = () => useDocumentStore.getState().doc.objects.map((o) => o.id)
    expect(ids()).toEqual(['a_mine0001', 'a_agent001', 't_agent002'])
    expect(useDocumentStore.getState().doc.objects[1].anchor?.by).toBe('agent')
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-disk', dirty: true })
    useDocumentStore.getState().undo()
    expect(ids()).toEqual(['a_mine0001'])
  })
})

describe('不提示：⌘S 撞上只多了 Agent 加的标注时直接并进来再保存', () => {
  it('第一次写回 409（磁盘是 Agent 写过的那份）→ 并进来 → 以磁盘修订号再写一次；不弹对话框', async () => {
    mockAdd.mockResolvedValue(additions())
    mockSave
      .mockRejectedValueOnce(
        new ApiError('external_change', 409, { code: 'external_change', revision: 'rev-disk', summary: null }),
      )
      .mockResolvedValueOnce({ ok: true, revision: 'rev-after', name: '组图' })
    const ok = await writeBoundProjectFile(captureSaveContext())
    expect(ok).toBe(true)
    expect(mockSave).toHaveBeenCalledTimes(2)
    expect(mockSave.mock.calls[0][2]).toBe('rev-base')
    expect(mockSave.mock.calls[1][2]).toBe('rev-disk')
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['a_mine0001', 'a_agent001', 't_agent002'])
    expect(useUiStore.getState().layoutOpen).toBe(false)
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-after', dirty: false })
  })

  it('磁盘上还有别的改动（后端说不只是 Agent 加的）：照旧打开冲突对话框，什么都不并', async () => {
    mockAdd.mockResolvedValue({ available: false })
    mockSave.mockRejectedValueOnce(
      new ApiError('external_change', 409, { code: 'external_change', revision: 'rev-other', summary: null }),
    )
    const ok = await writeBoundProjectFile(captureSaveContext())
    expect(ok).toBe(false)
    expect(mockSave).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().layoutOpen).toBe(true)
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
  })

  it('并完再撞上冲突（这期间别处又改过）：只试一次，然后照旧打开冲突对话框', async () => {
    // 第二问后端又说「有一批新的」：仍然只并一次、写两次，不进循环
    mockAdd
      .mockResolvedValueOnce(additions())
      .mockResolvedValue({ ...additions(undefined, ['a_agent003']), revision: 'rev-again' })
    const conflict = (revision: string) =>
      new ApiError('external_change', 409, { code: 'external_change', revision, summary: null })
    mockSave.mockRejectedValueOnce(conflict('rev-disk')).mockRejectedValueOnce(conflict('rev-again'))
    const ok = await writeBoundProjectFile(captureSaveContext())
    expect(ok).toBe(false)
    expect(mockSave).toHaveBeenCalledTimes(2)
    expect(useUiStore.getState().layoutOpen).toBe(true)
    expect(useDocumentStore.getState().doc.objects).toHaveLength(3)
  })
})

describe('回到前台自动并进来', () => {
  it('窗口回到前台：有 Agent 加的标注就直接并进画布（可撤销），状态栏说一句，不挂要点的按钮', async () => {
    mockAdd.mockResolvedValue(additions())
    const stop = watchAgentAdditions(1_000_000)
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(useDocumentStore.getState().doc.objects).toHaveLength(3)
    const st = useUiStore.getState()
    expect(st.status?.key).toBe('agentMerge.done')
    expect(st.statusAction).toBeNull()
    stop()
  })
})

describe('Codex #2217：回答只对发请求时那一份成立、不进手势事务、没进文档就不推基线', () => {
  it('请求途中换成了项目副本（画布 id、排版名、基线都一样）：回答属于原来那份，作废，不并', async () => {
    let resolve!: (v: unknown) => void
    mockAdd.mockReturnValue(new Promise((r) => (resolve = r)) as never)
    const canvasId = useDocumentStore.getState().activeCanvasId
    const copy = useDocumentStore.getState().buildProject()
    const pending = mergeableAgentAdditions()
    await useDocumentStore.getState().switchDocument(structuredClone(copy), 'd_copy')
    setProjectFile({ projectId: 'p1', name: '组图', file: 'tavottofile/组图.json', revision: 'rev-base', dirty: false })
    expect(useDocumentStore.getState().activeCanvasId).toBe(canvasId)
    resolve(additions(canvasId))
    expect(await pending).toBeNull()
  })

  it('手势进行中：不并、基线不动（取消手势不会连标注一起撤掉）', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = (await mergeableAgentAdditions())!
    useDocumentStore.getState().beginTxn(literal('拖动'))
    expect(mergeAgentAdditions(m)).toBe(false)
    useDocumentStore.getState().endTxn({ discard: true })
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-base' })
  })

  it('写入被挡住（只读窗口）：commit 什么都没做 → 不推基线、不报成功', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = (await mergeableAgentAdditions())!
    const real = useDocumentStore.getState().commit
    useDocumentStore.setState({ commit: () => {} })
    try {
      expect(mergeAgentAdditions(m)).toBe(false)
    } finally {
      useDocumentStore.setState({ commit: real })
    }
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-base' })
  })
})
