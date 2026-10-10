/**
 * 命令面板的最终命令集（ADR 0041 §3）：id 稳定、不重复注册、每条都有中英文文案
 * 与关键词、项目命令只在项目打开时出现、动作复用真实 helper。
 */
import { act } from 'react'
import { MOD } from '@/lib/utils'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  refreshProject: vi.fn().mockResolvedValue({}),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '/x', panels: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
  fetchTutorialStatus: vi.fn().mockResolvedValue(null),
}))

import { refreshProject } from '@/lib/api'
import { i18n } from '@/i18n'
import zhDialogs from '@/i18n/locales/zh-CN/dialogs.json'
import enDialogs from '@/i18n/locales/en-US/dialogs.json'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useProjectStore } from '@/store/projectStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectScanStore } from '@/store/projectScanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { CommandPalette, usePalette } from './CommandPalette'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// jsdom 没有 scrollIntoView；面板每次切换高亮都会调它
Element.prototype.scrollIntoView = vi.fn()

let root: Root | null = null
let host: HTMLDivElement | null = null

function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(<CommandPalette />))
}

const labels = () =>
  Array.from(document.querySelectorAll('[data-cmd-id] [data-cmd-label]')).map((el) =>
    (el.textContent ?? '').trim(),
  )

const commandIds = (dialogs: { palette: { commands: Record<string, unknown> } }) =>
  Object.keys(dialogs.palette.commands)

beforeEach(async () => {
  await i18n.changeLanguage('zh-CN')
  useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p1' } } as never)
  useUiStore.setState({ registryOpen: false })
  usePalette.setState({ open: true })
  vi.mocked(refreshProject).mockClear()
})
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
  usePalette.setState({ open: false })
})

describe('命令集', () => {
  it('六条整合要求的命令都在，且都有中英文 label + keywords', () => {
    for (const id of [
      'refresh-project',
      'readiness',
      'tutorial-start',
      'tutorial-reset',
      'hints-reset',
      'shortcut-help',
    ]) {
      for (const dialogs of [zhDialogs, enDialogs]) {
        const entry = (dialogs.palette.commands as Record<string, { label: string; keywords: string }>)[id]
        expect(entry?.label, `${id} label`).toBeTruthy()
        expect(entry?.keywords, `${id} keywords`).toBeTruthy()
      }
    }
  })

  it('中英文资源的命令 id 集合一致（没有一边多一条）', () => {
    expect(commandIds(zhDialogs).sort()).toEqual(commandIds(enDialogs).sort())
  })

  it('渲染出来的命令不重复', () => {
    mount()
    const seen = labels()
    expect(new Set(seen).size).toBe(seen.length)
    expect(seen).toContain('刷新项目')
    expect(seen).toContain('显示项目接入状态')
    expect(seen).toContain('重新显示操作提示')
    expect(seen).toContain('快捷键帮助')
  })

  // Codex #833：可用判据与动作同一个——选中的全隐藏了，「缩放到选中」不出现（隐藏不清选区，只看 ids 长度会留一条空转命令）
  it('「缩放到选中」：选中可见对象才出现；把它隐藏后不出现', async () => {
    const text: TextObject = {
      id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
      color: '#000', align: 'left', x: 10, y: 20, w: 30, h: 8,
    }
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_palette_zoom')
    useDocumentStore.getState().silent((d) => {
      d.objects.push(text)
    })
    const ids = () => Array.from(document.querySelectorAll<HTMLElement>('[data-cmd-id]')).map((el) => el.dataset.cmdId)
    try {
      useSelectionStore.setState({ ids: ['t1'] })
      mount()
      expect(ids()).toContain('zoom-selection')
      act(() =>
        useDocumentStore.getState().silent((d) => {
          d.objects[0].hidden = true
        }),
      )
      expect(ids()).not.toContain('zoom-selection')
    } finally {
      useSelectionStore.setState({ ids: [] })
    }
  })

  // Codex #833：快速编辑这一屏只画正在编辑的那张图；选区里只有看不见的版面对象时不出现，有那张图时只框它
  it('「缩放到选中」在快速编辑里只认正在编辑的那张图', async () => {
    const text: TextObject = {
      id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
      color: '#000', align: 'left', x: 10, y: 20, w: 30, h: 8,
    }
    const panel: PanelObject = {
      id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 80, nativeH: 60,
      overrides: [], x: 100, y: 80, w: 40, h: 30,
    }
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_palette_zoom_fe')
    useDocumentStore.getState().silent((d) => {
      d.objects.push(text, panel)
    })
    const spy = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    const cmd = () => document.querySelector<HTMLElement>('[data-cmd-id="zoom-selection"]')
    try {
      useSelectionStore.setState({ ids: ['t1'] })
      mount()
      expect(cmd()).not.toBeNull()
      // 进快速编辑本身就让命令重算（面板开着也一样）
      act(() => useWorkspaceStore.getState().enterFastEdit('p1'))
      expect(cmd()).toBeNull()
      act(() => useSelectionStore.setState({ ids: ['t1', 'p1'] }))
      expect(cmd()).not.toBeNull()
      act(() => cmd()!.click())
      expect(spy).toHaveBeenCalledWith({ x: 100, y: 80, w: 40, h: 30 })
    } finally {
      useSelectionStore.setState({ ids: [] })
      useWorkspaceStore.getState().clear()
      spy.mockRestore()
    }
  })

  // Codex #833（数据丢失）：快速编辑这一屏只画那张图——「全选」只收它，不把整版看不见的对象选上（接着 Delete
  // 就删掉它们）；「适应画布」与 ⌘1 / 舞台双击同一个取景框，适应那张图。对照组：排版里全选整版、适应页面
  it('「全选」与「适应画布」在快速编辑里只认正在编辑的那张图', async () => {
    const text: TextObject = {
      id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
      color: '#000', align: 'left', x: 10, y: 20, w: 30, h: 8,
    }
    const panel: PanelObject = {
      id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 80, nativeH: 60,
      overrides: [], x: 100, y: 80, w: 40, h: 30,
    }
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_palette_select_all_fe')
    useDocumentStore.getState().silent((d) => {
      d.objects.push(text, panel)
    })
    const page = useDocumentStore.getState().doc.page
    const fit = vi.spyOn(useViewportStore.getState(), 'fitAnimated').mockImplementation(() => {})
    const fitRect = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated').mockImplementation(() => {})
    // 点一条命令会关掉面板；下一条前重新打开（一次挂载，不留多余的根）
    const run = (id: string) => {
      act(() => usePalette.setState({ open: true }))
      act(() => document.querySelector<HTMLElement>(`[data-cmd-id="${id}"]`)!.click())
    }
    mount()
    try {
      useSelectionStore.setState({ ids: [] })
      run('select-all')
      expect(useSelectionStore.getState().ids).toEqual(['t1', 'p1'])
      run('fit')
      expect(fit).toHaveBeenLastCalledWith(page.w, page.h)

      useWorkspaceStore.getState().enterFastEdit('p1')
      useSelectionStore.setState({ ids: [] })
      run('select-all')
      expect(useSelectionStore.getState().ids).toEqual(['p1'])
      run('fit')
      // 那张图本身的矩形（含原点），不是 (0,0) 到它右下角的 140×110（Codex #833）
      expect(fit).toHaveBeenCalledTimes(1)
      expect(fitRect).toHaveBeenLastCalledWith({ x: 100, y: 80, w: 40, h: 30 })
    } finally {
      useSelectionStore.setState({ ids: [] })
      useWorkspaceStore.getState().clear()
      fit.mockRestore()
      fitRect.mockRestore()
    }
  })

  it('没有打开项目时项目命令整组不出现（embedded / playground）', () => {
    useProjectStore.setState({ phase: 'none', project: null } as never)
    mount()
    const seen = labels()
    expect(seen).not.toContain('刷新项目')
    expect(seen).not.toContain('显示项目接入状态')
    expect(seen).toContain('快捷键帮助')
  })

  it('「刷新项目」调统一刷新端点（reason=manual），不自己扫', () => {
    mount()
    const btn = Array.from(document.querySelectorAll<HTMLElement>('[data-cmd-id]')).find((b) =>
      b.textContent?.includes('刷新项目'),
    ) as HTMLElement
    act(() => btn.click())
    expect(refreshProject).toHaveBeenCalledWith('manual')
    expect(usePalette.getState().open).toBe(false)
  })

  it('「显示项目接入状态」打开接入中心，来源记为 palette', () => {
    const spy = vi.spyOn(useProjectReadinessStore.getState(), 'openCenter')
    mount()
    const btn = Array.from(document.querySelectorAll<HTMLElement>('[data-cmd-id]')).find((b) =>
      b.textContent?.includes('项目接入状态'),
    ) as HTMLElement
    act(() => btn.click())
    expect(spy).toHaveBeenCalledWith({ focus: null, source: 'palette' })
    expect(useUiStore.getState().registryOpen).toBe(true)
    spy.mockRestore()
  })

  it('排版时间线（ADR 0101）：「排版时间线…」打开抽屉；「把现在存为命名节点…」就地弹出命名小框（不开抽屉）', async () => {
    const { useTimelineStore } = await import('@/store/timelineStore')
    useUiStore.setState({ versionsOpen: false })
    useTimelineStore.setState({ namingOpen: false })
    mount()
    const find = (t: string) =>
      Array.from(document.querySelectorAll<HTMLElement>('[data-cmd-id]')).find((b) =>
        b.textContent?.includes(t),
      ) as HTMLElement
    act(() => find('把现在存为命名节点').click())
    expect(useTimelineStore.getState().namingOpen).toBe(true)
    expect(useUiStore.getState().versionsOpen).toBe(false)
    useUiStore.setState({ versionsOpen: false })
    act(() => root?.unmount())
    usePalette.setState({ open: true })
    mount()
    act(() => find('排版时间线').click())
    expect(useUiStore.getState().versionsOpen).toBe(true)
  })

  it('英文界面下按英文关键词能搜到', async () => {
    await i18n.changeLanguage('en-US')
    mount()
    const input = document.querySelector('[data-palette-input]') as HTMLInputElement
    act(() => {
      // React 的受控输入靠原生 setter 之外的值追踪；直接赋 value 会被它当成没变
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, 'rescan')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(labels()).toEqual(['Refresh project'])
  })
})

/**
 * 空查询时的顺序（审计 T50）。验收原话：**常用编辑动作无需猜内部关键词**。
 * 排序判据本身在 `lib/commandRanking.test.ts` 逐条反证过；这里量的是
 * 「面板真的按它渲染」以及「跑过的命令进了最近使用、且存在本机」。
 */
/**
 * 高亮行是身份不是位置（2026-09-16，学 beUI `useRowCursor`）。此前 `active` 是下标、
 * 查询变了只钳位不复位：↓↓ 停在第 3 行再打字，列表换成另一组，高亮仍停在「第 3 行」，
 * 回车执行的是一条用户没瞄准过的命令。
 */
describe('高亮行按身份记，不按位置记', () => {
  beforeEach(() => {
    localStorage.removeItem('tavotto.ui')
    useUiStore.setState({ recentCommands: [] })
  })
  const input = () => document.querySelector('[data-palette-input]') as HTMLInputElement
  const type = (text: string) =>
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input(), text)
      input().dispatchEvent(new Event('input', { bubbles: true }))
    })
  const key = (k: string) =>
    act(() => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }))
    })
  const highlighted = () =>
    (document.querySelector('[data-cmd-id][data-active]') as HTMLElement | null)?.dataset.cmdId
  const cmdIds = () =>
    Array.from(document.querySelectorAll('[data-cmd-id]')).map((el) => (el as HTMLElement).dataset.cmdId)

  it('查询一变，高亮回到新列表的首行，不停在旧下标上', () => {
    mount()
    key('ArrowDown')
    key('ArrowDown')
    expect(highlighted()).toBe(cmdIds()[2])
    type('显示')
    expect(cmdIds().length).toBeGreaterThanOrEqual(3)
    expect(highlighted()).toBe(cmdIds()[0])
  })

  it('↓↓ 后再打字，回车执行的是新列表的首行，不是「第 3 行」那条没瞄准过的命令', () => {
    mount()
    key('ArrowDown')
    key('ArrowDown')
    // 「显示」中三条：网格 / 标尺 / 接入状态——下标版会执行第 3 条
    type('显示')
    const ids = cmdIds()
    expect(ids.length).toBeGreaterThanOrEqual(3)
    expect(ids[2]).not.toBe(ids[0])
    key('Enter')
    expect(useUiStore.getState().recentCommands[0]).toBe(ids[0])
    expect(useUiStore.getState().recentCommands[0]).not.toBe(ids[2])
  })

  it('同一个查询里方向键仍按行走，越过末行不动', () => {
    mount()
    type('显示')
    const ids = cmdIds()
    for (let i = 0; i < ids.length + 2; i++) key('ArrowDown')
    expect(highlighted()).toBe(ids.at(-1))
    key('ArrowUp')
    expect(highlighted()).toBe(ids.at(-2))
  })

  it('查询按词切、顺序不限：「pdf 导出」也能中「导出 PDF」', () => {
    mount()
    type('pdf 导出')
    expect(labels()).toEqual(['导出 PDF / PNG…'])
    type('导出 png')
    expect(labels()).toEqual(['导出 PDF / PNG…'])
    // 两个词各自都在、但不在同一条命令上：不命中
    type('导出 网格')
    expect(labels()).toEqual([])
  })
})

describe('外壳（2026-10-07 设计审计 §10.1：Dialog chrome="palette"）', () => {
  it('不画 Esc 键帽（Esc 是所有对话框的约定，右上也不写）', () => {
    mount()
    const dialog = document.querySelector('[data-dialog="command-palette"]')!
    expect(dialog, '挂在共用对话框外壳上').not.toBeNull()
    const esc = [...dialog.querySelectorAll('kbd')].find((k) => k.textContent?.trim() === 'Esc')
    expect(esc).toBeUndefined()
    // 也没有右上角 ×：Esc / 点外面就是关
    expect(dialog.querySelector('[data-dialog-close]')).toBeNull()
  })

  it('组合框语义：输入框是 combobox，控制结果列表，高亮行经 aria-activedescendant 报出', () => {
    mount()
    const input = document.querySelector<HTMLInputElement>('[data-palette-input]')!
    expect(input.getAttribute('role')).toBe('combobox')
    const list = document.getElementById(input.getAttribute('aria-controls')!)
    expect(list?.getAttribute('role')).toBe('listbox')
    const active = document.querySelector<HTMLElement>('[data-cmd-id][data-active]')!
    expect(input.getAttribute('aria-activedescendant')).toBe(active.id)
    // 行里不再嵌按钮（option 里放可聚焦控件是嵌套交互）
    expect(document.querySelector('[data-cmd-id] button')).toBeNull()
  })

  it('打开时焦点在输入框里，焦点陷在对话框内；关闭后还给打开前的元素', async () => {
    usePalette.setState({ open: false })
    const before = document.createElement('button')
    document.body.appendChild(before)
    before.focus()
    mount()
    await act(async () => usePalette.setState({ open: true }))
    await act(async () => {})
    expect(document.activeElement).toBe(document.querySelector('[data-palette-input]'))
    await act(async () => usePalette.setState({ open: false }))
    await act(async () => {})
    expect(document.activeElement).toBe(before)
    before.remove()
  })

  // Codex #833（comment 4211499735）：命令把焦点交给了另一个非模态表面（命名小框的 autoFocus），
  // 面板退场时的焦点归还不能把它再抢回打开前的元素——否则小框的 onBlur 当场把自己关掉，命令一闪而过
  it('命令把焦点交给了别的表面（「把现在存为命名节点」）：关闭不抢回焦点，小框留着、焦点在它的输入框里', async () => {
    const { useTimelineStore } = await import('@/store/timelineStore')
    const { NamedNodeQuickBox } = await import('./NamedNodeQuickBox')
    useTimelineStore.setState({ namingOpen: false })
    usePalette.setState({ open: false })
    const before = document.createElement('button')
    document.body.appendChild(before)
    before.focus()
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    act(() =>
      root!.render(
        <>
          <CommandPalette />
          <NamedNodeQuickBox />
        </>,
      ),
    )
    await act(async () => usePalette.setState({ open: true }))
    await act(async () => {})
    expect(document.activeElement).toBe(document.querySelector('[data-palette-input]'))
    await act(async () =>
      document.querySelector<HTMLElement>('[data-cmd-id="save-named-version"]')!.click(),
    )
    // Radix FocusScope 的卸载归还排在 setTimeout(0) 里：等它跑完
    await act(async () => new Promise((r) => setTimeout(r, 20)))
    expect(useTimelineStore.getState().namingOpen).toBe(true)
    expect(document.activeElement).toBe(document.querySelector('[data-timeline-quick-name-input]'))
    // Esc 关小框：焦点回到打开命令面板之前的元素，不掉到 body
    await act(async () => {
      document
        .querySelector('[data-timeline-quick-name-input]')!
        .dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(useTimelineStore.getState().namingOpen).toBe(false)
    expect(document.activeElement).toBe(before)
    before.remove()
  })

  it('命令打开的是另一个对话框（导出）：焦点留在新对话框里，不被面板的归还拽回打开前的元素', async () => {
    const { Dialog } = await import('@/components/ui/Dialog')
    const PROBE = 'probe'
    function Export() {
      const open = useUiStore((s) => s.exportOpen)
      return (
        <Dialog open={open} onOpenChange={(v) => useUiStore.getState().setExportOpen(v)} title={PROBE} anchor="export-probe">
          <button data-probe-inner>{PROBE}</button>
        </Dialog>
      )
    }
    useUiStore.setState({ exportOpen: false })
    usePalette.setState({ open: false })
    const before = document.createElement('button')
    document.body.appendChild(before)
    before.focus()
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    act(() =>
      root!.render(
        <>
          <CommandPalette />
          <Export />
        </>,
      ),
    )
    await act(async () => usePalette.setState({ open: true }))
    await act(async () => {})
    await act(async () => document.querySelector<HTMLElement>('[data-cmd-id="export"]')!.click())
    await act(async () => new Promise((r) => setTimeout(r, 20)))
    const exportDialog = document.querySelector('[data-dialog="export-probe"]')!
    expect(exportDialog).not.toBeNull()
    expect(exportDialog.contains(document.activeElement)).toBe(true)
    // 关掉导出：焦点回到打开命令面板之前的那个元素（不是顶栏兜底按钮、不是 body）
    await act(async () => useUiStore.getState().setExportOpen(false))
    await act(async () => new Promise((r) => setTimeout(r, 20)))
    expect(document.activeElement).toBe(before)
    before.remove()
  })

  // Codex #833（erwanjun 复核 8a349482）：Ctrl-K → Esc → 退场动画还没放完又 Ctrl-K。Radix 的 Presence 让退场中的
  // Content 原样留着、重开时不重挂载，于是挂载时的初始焦点不再跑；而关的那一刻焦点已经还给了打开者——
  // 它这时正被重开的模态层 aria-hidden 着，键盘用户落在一个读屏看不见、陷阱之外的按钮上。
  // jsdom 没有 CSS 动画：把共用对话框外壳的 animationName 按 data-state 报出来，Presence 才会等 animationend
  it('退场动画没放完就再按 Ctrl-K：同一个输入框重新拿到焦点，不留在被 aria-hidden 的打开者上', async () => {
    const { useKeyboard } = await import('@/hooks/useKeyboard')
    function Keys() {
      useKeyboard()
      return null
    }
    const real = window.getComputedStyle
    const spy = vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
      const styles = real(el, pseudo)
      if (!(el instanceof HTMLElement) || !el.hasAttribute('data-dialog')) return styles
      return new Proxy(styles, {
        get: (target, prop) =>
          prop === 'animationName'
            ? el.getAttribute('data-state') === 'closed'
              ? 'pop-out'
              : 'pop-in'
            : Reflect.get(target, prop),
      })
    })
    usePalette.setState({ open: false })
    const before = document.createElement('button')
    document.body.appendChild(before)
    const ctrlK = () =>
      act(async () => {
        ;(document.activeElement ?? window).dispatchEvent(
          new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true, cancelable: true }),
        )
      })
    const paletteInput = () => document.querySelector<HTMLInputElement>('[data-palette-input]')
    try {
      before.focus()
      host = document.createElement('div')
      document.body.appendChild(host)
      root = createRoot(host)
      act(() =>
        root!.render(
          <>
            <Keys />
            <CommandPalette />
          </>,
        ),
      )
      await ctrlK()
      expect(usePalette.getState().open).toBe(true)
      const input = paletteInput()!
      expect(document.activeElement).toBe(input)
      await act(async () => {
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      })
      expect(usePalette.getState().open).toBe(false)
      // 退场中：Content 还在（没有 animationend），焦点已经还给打开者
      expect(document.querySelector('[data-dialog="command-palette"]')?.getAttribute('data-state')).toBe('closed')
      expect(document.activeElement).toBe(before)
      await ctrlK()
      expect(usePalette.getState().open).toBe(true)
      // 前提：确实是同一个被保留的 Content / 输入框（不是重挂载——那条路走挂载时的初始焦点）
      expect(paletteInput()).toBe(input)
      expect(before.closest('[aria-hidden="true"]'), '打开者此时被重开的模态层藏着').not.toBeNull()
      expect(document.activeElement).toBe(input)
      // 再 Esc：仍然还给最初的打开者
      await act(async () => {
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      })
      expect(usePalette.getState().open).toBe(false)
      expect(document.activeElement).toBe(before)
    } finally {
      spy.mockRestore()
      before.remove()
    }
  })

  it('选中行用 selected（ink 10%），不是 surface-2；行 32 / 13', () => {
    mount()
    const active = document.querySelector('[data-cmd-id][data-active]')!
    expect(active.className).toContain('bg-selected')
    expect(active.className).not.toContain('bg-surface-2')
    expect(active.className).toContain('h-8')
    expect(active.className).toContain('text-base')
  })

  it('有选区时输入行右端说出命令作用在谁身上', async () => {
    const { useSelectionStore } = await import('@/store/selectionStore')
    mount()
    expect(document.querySelector('[data-palette-selection]')).toBeNull()
    act(() => useSelectionStore.setState({ ids: ['a', 'b'] }))
    expect(document.querySelector('[data-palette-selection]')?.textContent).toContain('2')
    act(() => useSelectionStore.setState({ ids: [] }))
  })
})

describe('空查询时的顺序（审计 T50）', () => {
  // 「最近使用」是本机偏好，模块初始化时就从 localStorage 读进来了：
  // 不清的话上一条用例点过什么，这一条的第一屏就跟着变
  beforeEach(() => {
    localStorage.removeItem('tavotto.ui')
    useUiStore.setState({ recentCommands: [] })
  })

  const sectionIds = () =>
    Array.from(document.querySelectorAll('[data-palette-section]')).map(
      (el) => (el as HTMLElement).dataset.paletteSection,
    )
  const cmdIds = () =>
    Array.from(document.querySelectorAll('[data-cmd-id]')).map(
      (el) => (el as HTMLElement).dataset.cmdId,
    )
  const clickCmd = (id: string) => {
    const btn = document.querySelector(`[data-cmd-id="${id}"]`) as HTMLElement
    act(() => btn.click())
  }

  it('段标题按固定顺序出现，且都有译文', () => {
    mount()
    expect(sectionIds()).toEqual(['common', 'other'])
    for (const el of document.querySelectorAll('[data-palette-section]')) {
      expect((el.textContent ?? '').trim()).not.toBe('')
      expect(el.textContent).not.toContain('palette.section')
    }
  })

  it('教程 / 刷新 / 接入状态沉到「其他」，常用编辑动作在它们之前', () => {
    mount()
    const ids = cmdIds()
    for (const low of ['refresh-project', 'readiness', 'tutorial-start']) {
      expect(ids.indexOf('export')).toBeLessThan(ids.indexOf(low))
      expect(ids.indexOf('add-text')).toBeLessThan(ids.indexOf(low))
    }
  })

  it('跑过一条就进「最近使用」，并排在常用之前', () => {
    useUiStore.setState({ recentCommands: [] })
    mount()
    clickCmd('shortcut-help')
    expect(useUiStore.getState().recentCommands).toEqual(['shortcut-help'])
    // 关掉的面板重新挂一次，看它是不是排到了前面
    act(() => root?.unmount())
    usePalette.setState({ open: true })
    mount()
    expect(sectionIds()[0]).toBe('recent')
    expect(cmdIds()[0]).toBe('shortcut-help')
  })

  it('最近使用存本机、不进文档', () => {
    useUiStore.setState({ recentCommands: [] })
    mount()
    clickCmd('shortcut-help')
    const saved = JSON.parse(localStorage.getItem('tavotto.ui') ?? '{}')
    expect(saved.recentCommands).toEqual(['shortcut-help'])
  })

  it('有查询时不显示段标题，但顺序仍是那一份', () => {
    useUiStore.setState({ recentCommands: [] })
    mount()
    const input = document.querySelector('[data-palette-input]') as HTMLInputElement
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, '教程')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(sectionIds()).toEqual([])
    expect(cmdIds().length).toBeGreaterThan(0)
  })
})

describe('外观：图标与键帽（2026-10-01）', () => {
  it('有快捷键的命令画成一个个键帽，图标只来自图标集（没有 emoji / 手写 svg）', () => {
    mount()
    const exp = document.querySelector('[data-cmd-id="export"]')!
    expect([...exp.querySelectorAll('kbd')].map((k) => k.textContent)).toEqual([MOD, 'E'])
    expect(exp.querySelector('svg'), '导出有图标').not.toBeNull()
    // 没有合适图标的命令不硬凑：行仍在、图标槽留空对齐
    const all = document.querySelector('[data-cmd-id="select-all"]')!
    expect(all.querySelector('svg')).toBeNull()
  })
})

describe('显示项目检查结果（project-scan）', () => {
  it('卡片正聚焦着某个准备会话时，点它要放下聚焦，才轮得到扫描结果', () => {
    useProjectScanStore.setState({ scan: { project_id: 'p1' } as never, forced: false })
    useProjectPreparationStore.setState({ focus: 'script:plot.py' })
    useUiStore.setState({ guideCard: 'closed' })
    mount()
    act(() => {
      ;document.querySelector<HTMLElement>('[data-cmd-id="project-scan"]')!.click()
    })
    expect(useProjectPreparationStore.getState().focus).toBeNull()
    expect(useProjectScanStore.getState().forced).toBe(true)
    expect(useUiStore.getState().guideCard).toBe('card')
    useProjectScanStore.setState({ scan: null, forced: false })
  })
})
