/**
 * 顶栏左上角「回到项目列表」（HomeButton → `projectStore.showPicker`）。
 *
 * 去 Picker = 工作台整个卸载：`startAutosave` 的清理会**取消**防抖中的那次写盘、摘掉
 * beforeunload；开着的连续编辑（改字号的安静计时器）卸载时只注销、不收尾。所以钉三件事：
 *   1. 防抖窗口里的最后一下改动，在工作台卸载之后仍在本机副本里（不能等计时器）；
 *   2. 开着的那一轮连续编辑先收尾，它的最后一笔也进了那次冲刷；
 *   3. 浏览器历史：去 Picker push 一格，后退回到编辑器，前进再回 Picker；
 *      从 Picker 走「返回当前项目」时把那一格退掉。
 * 加上按钮本身：名字（中英）、data-* 锚点、切换进行中置灰。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HomeButton } from '@/components/ProjectSwitcher'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { usePickerHistory } from '@/hooks/usePickerHistory'
import { i18n, literal } from '@/i18n'
import type { ProjectStatus } from '@/lib/api'
import { isPickerEntry } from '@/lib/pickerHistory'
import { startAutosave, useDocumentStore } from '@/store/documentStore'
import { registerGesture, resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useProjectStore } from '@/store/projectStore'
import type { CanvasData } from '@/types/document'
import { canvasToDoc } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () =>
  new Response(JSON.stringify({ ok: true, saved_at: 1, revision: 'r1' }), {
    status: 200,
  })) as typeof fetch

const DOC_ID = 'd_home'
const slotKey = `tavotto.autosave.${DOC_ID}`
const PROJECT = { open: true, id: 'pA', name: 'A', figures_dir: '/figs/A' } as ProjectStatus

function seedDocument(): void {
  const canvas: CanvasData = {
    id: 'c1',
    name: 'Fig 1',
    page: { w: 150, h: 100 },
    objects: [{ id: 't1', type: 'text', x: 10, y: 10, w: 30, h: 8, text: 'a' } as never],
    guides: [],
  }
  useDocumentStore.setState({
    doc: canvasToDoc(canvas),
    documentId: DOC_ID,
    projectMeta: { id: 'p1', name: 'proj', createdAt: 1 },
    canvases: [canvas],
    activeCanvasId: 'c1',
    openTabs: ['c1'],
    canvasSessions: {},
    past: [],
    future: [],
    txn: null,
    dirty: false,
    saveState: 'clean',
    saveIssue: null,
    // 换一份文档而不是编辑一下：不升代次的话自动保存会把这次装载当成编辑
    loadSeq: useDocumentStore.getState().loadSeq + 1,
  })
}

const moveTo = (x: number) =>
  useDocumentStore.getState().commit(literal('挪一下'), (d) => {
    d.objects[0].x = x
  })

const savedX = (): number | undefined => {
  const raw = localStorage.getItem(slotKey)
  if (!raw) return undefined
  return (JSON.parse(raw) as { canvases: { objects: { x: number }[] }[] }).canvases[0].objects[0].x
}

let stopAutosave: (() => void) | null = null

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  resetGestureCoordinator()
  window.history.replaceState(null, '')
  seedDocument()
  useProjectStore.setState({ phase: 'open', project: PROJECT, switching: false })
  stopAutosave = startAutosave()
})

afterEach(() => {
  stopAutosave?.()
  stopAutosave = null
  vi.useRealTimers()
})

describe('离开编辑器之前的收尾', () => {
  it('防抖窗口里的改动：去 Picker 当下就进了本机副本，工作台卸载之后还在', () => {
    moveTo(42)
    expect(savedX()).toBeUndefined() // 前提：还在防抖窗口里，一次都没写过
    useProjectStore.getState().showPicker()
    // 工作台卸载 = 自动保存的清理把防抖计时器取消掉
    stopAutosave?.()
    stopAutosave = null
    vi.advanceTimersByTime(5000)
    expect(useProjectStore.getState().phase).toBe('none')
    expect(savedX()).toBe(42)
    expect(useDocumentStore.getState().dirty).toBe(false)
  })

  it('开着的一轮连续编辑先收尾，它的最后一笔也在那次冲刷里', () => {
    const finish = vi.fn(() => moveTo(77))
    registerGesture(finish)
    useProjectStore.getState().showPicker()
    expect(finish).toHaveBeenCalledTimes(1)
    expect(savedX()).toBe(77)
  })

  it('切换进行中什么都不做：不冲刷、不动 phase、不占历史', () => {
    useProjectStore.setState({ switching: true })
    moveTo(5)
    useProjectStore.getState().showPicker()
    expect(useProjectStore.getState().phase).toBe('open')
    expect(savedX()).toBeUndefined()
    expect(isPickerEntry()).toBe(false)
  })

  it('回到编辑器时文档还是那一份（不换文档、不重载）', () => {
    moveTo(33)
    const doc = useDocumentStore.getState().doc
    useProjectStore.getState().showPicker()
    useProjectStore.getState().returnToCurrent()
    expect(useProjectStore.getState().phase).toBe('open')
    expect(useDocumentStore.getState().documentId).toBe(DOC_ID)
    expect(useDocumentStore.getState().doc).toBe(doc)
  })
})

describe('浏览器历史', () => {
  let root: Root
  let host: HTMLDivElement
  function Probe() {
    usePickerHistory()
    return null
  }
  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    act(() => root.render(<Probe />))
  })
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
  })
  const pop = (state: unknown) =>
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate', { state }))
    })

  it('去 Picker push 一格；后退回编辑器，前进再回 Picker', () => {
    const before = window.history.length
    act(() => useProjectStore.getState().showPicker())
    expect(window.history.length).toBe(before + 1)
    expect(isPickerEntry()).toBe(true)

    pop(null) // 后退
    expect(useProjectStore.getState().phase).toBe('open')

    pop({ tavottoPicker: true }) // 前进
    expect(useProjectStore.getState().phase).toBe('none')
  })

  it('从 Picker 点「返回当前项目」：把 Picker 那一格退掉', () => {
    act(() => useProjectStore.getState().showPicker())
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {})
    act(() => useProjectStore.getState().returnToCurrent())
    expect(back).toHaveBeenCalledTimes(1)
    back.mockRestore()
  })

  it('没有当前项目时后退不强行回编辑器（409 退回 Picker 的那条路）', () => {
    useProjectStore.setState({ phase: 'none', project: null })
    pop(null)
    expect(useProjectStore.getState().phase).toBe('none')
  })
})

describe('按钮', () => {
  let root: Root
  let host: HTMLDivElement
  const mount = () => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    act(() =>
      root.render(
        <TooltipProvider>
          <HomeButton />
        </TooltipProvider>,
      ),
    )
  }
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
  })
  const btn = () => host.querySelector<HTMLButtonElement>('[data-home-button]')!

  it('有名字、可聚焦；点一下回到项目列表', () => {
    mount()
    expect(btn().tagName).toBe('BUTTON')
    expect(btn().getAttribute('aria-label')).toBe('回到项目列表')
    btn().focus()
    expect(document.activeElement).toBe(btn())
    act(() => btn().click())
    expect(useProjectStore.getState().phase).toBe('none')
  })

  it('英文界面：Back to projects', async () => {
    await act(() => i18n.changeLanguage('en-US'))
    mount()
    expect(btn().getAttribute('aria-label')).toBe('Back to projects')
  })

  it('切换进行中置灰', () => {
    useProjectStore.setState({ switching: true })
    mount()
    expect(btn().disabled).toBe(true)
  })
})
