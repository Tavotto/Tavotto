/**
 * 「把 Agent 加的并进来」（ADR 0208 §五）：后端说「磁盘多出来的只有 Agent 追加的对象」时，桌面一次 commit 并进当前画布
 * （一步可撤销）、把项目文件的基线推到磁盘那一份；冲突对话框的主按钮换成「并进来」；不用等 ⌘S，回到前台时挂胶囊。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAgentAdditions: vi.fn(),
  fetchLayoutList: vi.fn(async () => ({ names: [], modified: {} })),
  saveLayout: vi.fn(),
}))

import { fetchAgentAdditions, saveLayout } from '@/lib/api'
import { LayoutDialog } from '@/components/LayoutDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal, msg } from '@/i18n'
import { mergeAgentAdditions, mergeableAgentAdditions, watchAgentAdditions } from '@/lib/agentMerge'
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

let root: Root | null = null

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
  if (root) await act(async () => root!.unmount())
  root = null
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

describe('冲突对话框', () => {
  const openConflict = async (revision: string) => {
    useUiStore.getState().setLayoutOpen(true, 'saveToProject', {
      name: '组图',
      conflict: { name: '组图', revision, summary: null },
    })
    const el = document.createElement('div')
    document.body.appendChild(el)
    root = createRoot(el)
    await act(async () => {
      root!.render(
        <TooltipProvider>
          <LayoutDialog />
        </TooltipProvider>,
      )
    })
    await act(async () => {
      await Promise.resolve()
    })
  }
  const footer = () => document.querySelector('[role="dialog"] [data-dialog-footer]')!

  it('项目里那份只多了 Agent 加的：主按钮「并进来」、覆盖退到次按钮、撤掉红色冲突提示；点它并进来并接着保存（基线是磁盘那份）', async () => {
    mockAdd.mockResolvedValue(additions())
    mockSave.mockResolvedValue({ ok: true, revision: 'rev-after', name: '组图' })
    useUiStore.getState().setStatus(msg('save.projectConflict', { file: 'tavottofile/组图.json' }, 'workspace'), 'error')
    await openConflict('rev-disk')
    expect(useUiStore.getState().status).toBeNull()
    const merge = footer().querySelector<HTMLButtonElement>('[data-layout-merge-agent]')!
    expect(merge.getAttribute('data-variant')).toBe('primary')
    expect(footer().querySelector('[data-layout-overwrite]')!.getAttribute('data-variant')).toBe('danger-tinted')
    expect(document.querySelector('[data-layout-conflict]')!.textContent).toContain('2 个标注')
    await act(async () => {
      merge.click()
    })
    expect(useUiStore.getState().layoutOpen).toBe(false)
    expect(useDocumentStore.getState().doc.objects).toHaveLength(3)
    await vi.waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    expect(mockSave.mock.calls[0][2]).toBe('rev-disk')
    expect(mockSave.mock.calls[0][3]).toEqual({ target: 'project' })
  })

  it('后端给的修订号不是 409 回的那一份（又被改过）：照旧的岔口（改名 / 覆盖）', async () => {
    mockAdd.mockResolvedValue(additions())
    await openConflict('rev-someone-else')
    expect(footer().querySelector('[data-layout-merge-agent]')).toBeNull()
    expect(footer().querySelector('[data-layout-rename]')).not.toBeNull()
  })
})

describe('胶囊', () => {
  it('回到前台时问一次：有就挂「并进来」胶囊，点它并进来；同一个修订号不重复挂', async () => {
    mockAdd.mockResolvedValue(additions())
    const stop = watchAgentAdditions(1_000_000)
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await Promise.resolve()
      await Promise.resolve()
    })
    const st = useUiStore.getState()
    expect(st.statusAction).toBeTruthy()
    useUiStore.setState({ status: null, statusAction: null })
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(useUiStore.getState().statusAction).toBeFalsy()
    await act(async () => {
      st.statusAction!.run()
    })
    expect(useDocumentStore.getState().doc.objects).toHaveLength(3)
    stop()
  })
})
