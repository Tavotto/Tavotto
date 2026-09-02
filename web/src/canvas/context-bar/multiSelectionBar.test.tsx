/**
 * 多选浮动栏（Prompt 17）：出现 / 让位规则、动作全部走 store/actions、参照与
 * ArrangeSection 同源、落位与 OverlaySvg 同一份几何、Esc 只关本次、窄屏压缩、无障碍。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { boundsOf } from '@/lib/geometry'
import { ArrangeSection } from '@/components/inspector/ArrangeSection'
import { usePalette } from '@/components/CommandPalette'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { alignSelectedTo } from '@/store/actions'
import { useArrangeStore } from '@/store/arrangeStore'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type CanvasObject, type PanelObject, type TextObject } from '@/types/document'
import { useQuickEdit } from '../quickEditStore'
import { ContextBar } from './ContextBar'
import { placeToolbar, selectionScreenRect, sidebarInsets } from './position'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const text = (id: string, over: Partial<TextObject> = {}): TextObject =>
  ({
    id,
    type: 'text',
    text: id,
    sizePt: 10,
    bold: false,
    color: '#000000',
    align: 'left',
    x: 10,
    y: 100,
    w: 30,
    h: 8,
    ...over,
  }) as TextObject

const three = () => [
  text('t1', { x: 10, y: 100, w: 30 }),
  text('t2', { x: 50, y: 120, w: 20 }),
  text('t3', { x: 90, y: 140, w: 10 }),
]

let root: Root
let mountEl: HTMLDivElement

function setWindow(width: number, height = 800) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true })
  Object.defineProperty(window, 'innerHeight', { value: height, configurable: true, writable: true })
}

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_multibar_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  for (const o of items) {
    const anchor = document.createElement('div')
    anchor.setAttribute('data-object-id', o.id)
    document.body.appendChild(anchor)
  }
}

async function mount(extra?: React.ReactNode) {
  mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ContextBar />
        {extra}
      </TooltipProvider>,
    )
  })
}

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  setWindow(1200, 800)
  if (!('ResizeObserver' in globalThis)) {
    class RO {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    Object.defineProperty(globalThis, 'ResizeObserver', { value: RO, configurable: true, writable: true })
  }
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    tool: 'select',
    layout: 'wide',
    leftOpen: false,
    rightOpen: false,
    leftWidth: 300,
    rightWidth: 360,
    exportOpen: false,
    settingsOpen: false,
    confirm: null,
    status: null,
  })
  usePalette.setState({ open: false })
  useQuickEdit.getState().close()
  useInteractionStore.getState().end()
  useSelectionStore.getState().clear()
  useArrangeStore.getState().setAlignRef('selection')
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useRenderStore.getState().clear()
  engineRender.mockReset()
  await seed(three())
  await mount()
})

afterEach(async () => {
  await act(async () => {
    root.unmount()
  })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

const bar = () => document.querySelector<HTMLElement>('[data-context-bar]')
const multiBar = () => document.querySelector<HTMLElement>('[data-multi-selection-context-bar]')
const select = (ids: string[]) => act(async () => useSelectionStore.getState().set(ids))
const btn = (sel: string) => {
  const el = document.querySelector<HTMLButtonElement>(sel)
  if (!el) throw new Error(`no element for ${sel}`)
  return el
}
const click = (el: HTMLElement) => act(async () => el.click())
const objs = () => useDocumentStore.getState().doc.objects
const byId = (id: string) => objs().find((o) => o.id === id)!
const past = () => useDocumentStore.getState().past
const radio = (scope: Element, label: string) =>
  [...scope.querySelectorAll<HTMLElement>('[role="radio"]')].find((r) => r.textContent?.includes(label))!

describe('哪种目标出哪种工具条', () => {
  it('单选画布对象：还是原来的 Object bar，不带多选属性', async () => {
    await select(['t1'])
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('object')
    expect(multiBar()).toBeNull()
    expect(bar()!.querySelector('[aria-label="加粗"]')).toBeTruthy()
    expect(bar()!.querySelector('[data-align-mode]')).toBeNull()
  })

  it('单个图内元素：还是原来的 Element bar', async () => {
    const panel = {
      id: 'p1', type: 'panel', fileId: 'Fig1.pdf', x: 10, y: 10, w: 80, h: 60,
      nativeW: 80, nativeH: 60, overrides: [], script: 'fig.py',
    } as unknown as PanelObject
    await seed([...three(), panel])
    const line = {
      gid: 'axes_0.lines_0', role: 'line', label: 'line', bbox: [0.1, 0.1, 0.5, 0.5],
      editable: [
        { prop: 'color', kind: 'color' },
        { prop: 'linewidth', kind: 'number', min: 0.1, max: 12, step: 0.1 },
      ],
      draggable: false,
    } as unknown as ManifestElement
    const manifest: Manifest = { stem: 'Fig1', size_mm: [80, 60], elements: [line] }
    engineRender.mockResolvedValue({ rev: 1, manifest, svg: '<svg/>' })
    await act(async () => {
      await useRenderStore.getState().render('Fig1.pdf', [])
    })
    await act(async () => {
      useSelectionStore.getState().set(['p1'])
      useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.lines_0'] })
    })
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('element')
    expect(multiBar()).toBeNull()
    expect(bar()!.querySelector('[data-align-mode]')).toBeNull()
  })

  it('两个对象：多选栏出现，role=toolbar，aria-label=多选操作，计数 2', async () => {
    await select(['t1', 't2'])
    const el = multiBar()!
    expect(el).not.toBeNull()
    expect(el.getAttribute('role')).toBe('toolbar')
    expect(el.getAttribute('aria-label')).toBe('多选操作')
    expect(el.querySelector('[data-selection-count]')!.textContent).toBe('已选 2 个')
    expect(el.getAttribute('data-variant')).toBe('full')
  })

  it('三个对象：计数 3；来源不区分（程序化 set 与 add 都算）', async () => {
    await select(['t1', 't2'])
    await act(async () => useSelectionStore.getState().add('t3'))
    expect(multiBar()!.querySelector('[data-selection-count]')!.textContent).toBe('已选 3 个')
  })

  it('图内编辑态（elementPanelId 挂起、没选元素）不出多选栏', async () => {
    await select(['t1', 't2'])
    await act(async () => useUiStore.setState({ elementPanelId: 't1' }))
    expect(bar()).toBeNull()
  })
})

describe('按钮可用性', () => {
  it('两个对象：分布 aria-disabled，点了不动；等宽等高、成组可用', async () => {
    await select(['t1', 't2'])
    const h = btn('[data-align-mode="hdist"]')
    expect(h.getAttribute('aria-disabled')).toBe('true')
    await click(h)
    expect(past()).toHaveLength(0)
    expect(btn('[data-align-mode="samew"]').getAttribute('aria-disabled')).toBeNull()
    expect(btn('[data-group-action="group"]').getAttribute('aria-disabled')).toBeNull()
    expect(document.querySelector('[data-group-action="ungroup"]')).toBeNull()
  })

  it('三个对象：分布可用，点一下就是一条历史', async () => {
    await select(['t1', 't2', 't3'])
    const h = btn('[data-align-mode="hdist"]')
    expect(h.getAttribute('aria-disabled')).toBeNull()
    await click(h)
    expect(past()).toHaveLength(1)
  })

  it('选区里有组时多出「取消成组」', async () => {
    await seed([text('t1', { groupId: 'g1' }), text('t2', { x: 50, groupId: 'g1' })])
    await select(['t1', 't2'])
    expect(btn('[data-group-action="ungroup"]')).toBeTruthy()
    expect(btn('[data-group-action="group"]')).toBeTruthy()
  })
})

describe('动作走 store/actions', () => {
  it('对齐（选区）：左对齐 → x 收到 10；一条历史；标签与直接调 action 一致', async () => {
    await select(['t1', 't2', 't3'])
    await click(btn('[data-align-mode="left"]'))
    expect(objs().map((o) => o.x)).toEqual([10, 10, 10])
    expect(past()).toHaveLength(1)
    const viaBar = past()[0].label
    await seed(three())
    useSelectionStore.getState().set(['t1', 't2', 't3'])
    alignSelectedTo('left', 'selection')
    expect(past()[0].label).toEqual(viaBar)
  })

  it('对齐（画布）：切参照到「画布」后右对齐贴页宽', async () => {
    await select(['t1', 't2', 't3'])
    await click(radio(multiBar()!.querySelector('[data-align-ref-picker]')!, '画布'))
    await click(btn('[data-align-mode="right"]'))
    const pw = useDocumentStore.getState().doc.page.w
    expect(objs().map((o) => o.x + o.w)).toEqual([pw, pw, pw])
  })

  it('对齐（主选）：末位 id 不动，其余贴到它', async () => {
    await select(['t1', 't2', 't3'])
    await click(radio(multiBar()!.querySelector('[data-align-ref-picker]')!, '最后选中'))
    await click(btn('[data-align-mode="left"]'))
    expect(byId('t3').x).toBe(90)
    expect(byId('t1').x).toBe(90)
  })

  it('等宽 / 等高（主选）：文字高度由内容决定，等高对文字是 no-op、不占历史', async () => {
    await select(['t1', 't2', 't3'])
    await click(radio(multiBar()!.querySelector('[data-align-ref-picker]')!, '最后选中'))
    await click(btn('[data-align-mode="samew"]'))
    expect(objs().map((o) => o.w)).toEqual([10, 10, 10])
    await click(btn('[data-align-mode="sameh"]'))
    expect(objs().map((o) => o.h)).toEqual([8, 8, 8])
    expect(past()).toHaveLength(1)
  })

  it('水平 / 垂直分布', async () => {
    await seed([text('t1', { x: 10, y: 100, w: 30 }), text('t2', { x: 50, y: 135, w: 20 }), text('t3', { x: 90, y: 140, w: 10 })])
    await select(['t1', 't2', 't3'])
    await click(btn('[data-align-mode="hdist"]'))
    await click(btn('[data-align-mode="vdist"]'))
    const ys = objs().map((o) => o.y).sort((a, b) => a - b)
    expect(ys[1] - ys[0]).toBeCloseTo(ys[2] - ys[1])
    expect(past().map((p) => p.label.key)).toEqual(['history.alignWithRef', 'history.alignWithRef'])
  })

  it('成组 → 取消成组', async () => {
    await select(['t1', 't2'])
    await click(btn('[data-group-action="group"]'))
    const gid = byId('t1').groupId
    expect(gid).toBeTruthy()
    expect(byId('t2').groupId).toBe(gid)
    await click(btn('[data-group-action="ungroup"]'))
    expect(byId('t1').groupId).toBeUndefined()
    expect(past()).toHaveLength(2)
  })

  it('撤销回到对齐前：历史条目与 action 的同一条', async () => {
    await select(['t1', 't2', 't3'])
    await click(btn('[data-align-mode="left"]'))
    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(objs().map((o) => o.x)).toEqual([10, 50, 90])
    expect(multiBar()).not.toBeNull()
  })

  it('「更多」打开属性页，选区一个字不动', async () => {
    await select(['t1', 't2'])
    await click(btn('[data-multi-more]'))
    expect(useUiStore.getState().rightTab).toBe('properties')
    expect(useUiStore.getState().rightOpen).toBe(true)
    expect(useSelectionStore.getState().ids).toEqual(['t1', 't2'])
  })
})

describe('让位规则', () => {
  it('pointerdown 隐藏，pointerup 后选区仍 ≥2 就再现', async () => {
    await select(['t1', 't2'])
    expect(multiBar()).not.toBeNull()
    await act(async () => window.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })))
    expect(multiBar()).toBeNull()
    await act(async () => window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })))
    expect(multiBar()).not.toBeNull()
  })

  it.each(['move', 'resize', 'marquee', 'pan', 'guide', 'draw', 'crop'] as const)(
    '交互 kind=%s 期间隐藏，结束后再现',
    async (kind) => {
      await select(['t1', 't2'])
      await act(async () => useInteractionStore.getState().begin(kind))
      expect(multiBar()).toBeNull()
      await act(async () => useInteractionStore.getState().end())
      expect(multiBar()).not.toBeNull()
    },
  )

  it('QuickEdit 打开时隐藏', async () => {
    await select(['t1', 't2'])
    await act(async () => useQuickEdit.getState().open({ kind: 'object', id: 't1' }, 10, 10))
    expect(multiBar()).toBeNull()
    await act(async () => useQuickEdit.getState().close())
    expect(multiBar()).not.toBeNull()
  })

  it('裁剪 / 文字编辑 / 非选择工具 / 模态弹窗 / 命令面板：都让位', async () => {
    await select(['t1', 't2'])
    for (const patch of [
      { cropTargetId: 't1' },
      { editingTextId: 't1' },
      { tool: 'text' as const },
      { exportOpen: true },
      { settingsOpen: true },
    ]) {
      await act(async () => useUiStore.setState(patch))
      expect(multiBar(), JSON.stringify(patch)).toBeNull()
      await act(async () =>
        useUiStore.setState({ cropTargetId: null, editingTextId: null, tool: 'select', exportOpen: false, settingsOpen: false }),
      )
      expect(multiBar()).not.toBeNull()
    }
    await act(async () => usePalette.setState({ open: true }))
    expect(multiBar()).toBeNull()
    await act(async () => usePalette.setState({ open: false }))
    expect(multiBar()).not.toBeNull()
  })

  it('narrow 覆盖式抽屉开着时让位', async () => {
    await select(['t1', 't2'])
    await act(async () => useUiStore.setState({ layout: 'narrow', rightOpen: true }))
    expect(multiBar()).toBeNull()
  })

  it('选区掉到 1 个就换回单选栏', async () => {
    await select(['t1', 't2'])
    await select(['t1'])
    expect(multiBar()).toBeNull()
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('object')
  })
})

describe('Esc', () => {
  it('焦点在栏内：Esc 只关本次显示，事件被拦下，选区不动', async () => {
    await select(['t1', 't2'])
    const b = btn('[data-align-mode="left"]')
    b.focus()
    expect(document.activeElement).toBe(b)
    const ev = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    await act(async () => {
      window.dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(true)
    expect(multiBar()).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual(['t1', 't2'])
  })

  it('焦点在外：不抢全局 Esc（不 preventDefault），本次显示照样关', async () => {
    await select(['t1', 't2'])
    const ev = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    await act(async () => {
      window.dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(false)
    expect(multiBar()).toBeNull()
  })

  it('选区一变就重新允许出现；仅缩放 / 平移不解除也不重复关', async () => {
    await select(['t1', 't2'])
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(multiBar()).toBeNull()
    await act(async () => useViewportStore.setState({ zoom: 2, panX: 30 }))
    expect(multiBar()).toBeNull()
    await act(async () => useSelectionStore.getState().add('t3'))
    expect(multiBar()).not.toBeNull()
  })
})

describe('落位', () => {
  const expected = () => {
    const sel = useSelectionStore.getState().ids.map((id) => byId(id))
    const t = useViewportStore.getState()
    const ui = useUiStore.getState()
    const el = multiBar()!
    return placeToolbar(
      selectionScreenRect(boundsOf(sel)!, t),
      { w: el.offsetWidth, h: el.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight },
      sidebarInsets(ui),
    )
  }
  const left = () => parseFloat(multiBar()!.style.left)
  const top = () => parseFloat(multiBar()!.style.top)

  it('贴在联合选区上方，几何与 OverlaySvg 同一份换算', async () => {
    await select(['t1', 't2', 't3'])
    const e = expected()
    expect(e.placement).toBe('above')
    expect(multiBar()!.getAttribute('data-placement')).toBe('above')
    expect(left()).toBeCloseTo(e.x)
    expect(top()).toBeCloseTo(e.y)
  })

  it('zoom / pan 之后重贴', async () => {
    await select(['t1', 't2'])
    const before = { x: left(), y: top() }
    await act(async () => useViewportStore.setState({ zoom: 2, panX: 40, panY: 30 }))
    expect(left()).not.toBeCloseTo(before.x)
    expect(left()).toBeCloseTo(expected().x)
    expect(top()).toBeCloseTo(expected().y)
  })

  it('侧栏开合之后重贴：左栏开着时不压到它上面', async () => {
    await select(['t1', 't2'])
    await act(async () => useViewportStore.setState({ panX: -400 }))
    await act(async () => useUiStore.setState({ leftOpen: true, leftWidth: 300 }))
    expect(left()).toBe(44 + 300 + 8)
    await act(async () => useUiStore.setState({ leftOpen: false }))
    expect(left()).toBe(8)
  })

  it('顶部不够放就放到选区下方', async () => {
    await seed([text('t1', { y: 2 }), text('t2', { x: 50, y: 4 })])
    await select(['t1', 't2'])
    expect(multiBar()!.getAttribute('data-placement')).toBe('below')
    expect(top()).toBeCloseTo(expected().y)
  })

  it('左右不越界', async () => {
    await select(['t1', 't2'])
    await act(async () => useViewportStore.setState({ panX: -5000 }))
    expect(left()).toBe(8)
    await act(async () => useViewportStore.setState({ panX: 5000 }))
    expect(left()).toBe(window.innerWidth - multiBar()!.offsetWidth - 8)
  })

  it('对象挪动后重贴', async () => {
    await select(['t1', 't2'])
    const before = left()
    await act(async () => {
      useDocumentStore.getState().commit(literal('挪'), (d) => {
        for (const o of d.objects) o.x += 100
      })
    })
    expect(left()).toBeGreaterThan(before)
  })
})

describe('窄屏压缩', () => {
  it('可用宽度不够时压成弹层入口；弹层里还是同一批按钮', async () => {
    await select(['t1', 't2', 't3'])
    expect(multiBar()!.getAttribute('data-variant')).toBe('full')
    setWindow(500, 800)
    await act(async () => window.dispatchEvent(new Event('resize')))
    const el = multiBar()!
    expect(el.getAttribute('data-variant')).toBe('compact')
    expect(el.querySelector('[data-align-mode]')).toBeNull()
    expect(el.querySelector('[data-multi-menu="align"]')).toBeTruthy()
    expect(el.querySelector('[data-multi-menu="distribute"]')).toBeTruthy()
    expect(el.querySelector('[data-multi-menu="size"]')).toBeTruthy()
    expect(el.querySelector('[data-group-action="group"]')).toBeTruthy()
    expect(el.querySelector('[data-multi-more]')).toBeTruthy()
    await click(btn('[data-multi-menu="align"]'))
    const pop = document.querySelector('[data-radix-popper-content-wrapper]')!
    expect(pop).toBeTruthy()
    expect(pop.querySelector('[data-align-ref-picker]')).toBeTruthy()
    await click(pop.querySelector<HTMLElement>('[data-align-mode="left"]')!)
    expect(objs().map((o) => o.x)).toEqual([10, 10, 10])
  })

  it('停靠的侧栏把可用宽度吃掉时也压缩', async () => {
    await select(['t1', 't2'])
    await act(async () => useUiStore.setState({ leftOpen: true, rightOpen: true, rightWidth: 480 }))
    // 1200 − (44+300) − 480 = 376 < 600
    expect(multiBar()!.getAttribute('data-variant')).toBe('compact')
  })
})

describe('无障碍与键盘', () => {
  it('每个按钮都有可达名；参照分段有组名', async () => {
    await select(['t1', 't2', 't3'])
    const el = multiBar()!
    for (const b of el.querySelectorAll('button')) {
      const name = b.getAttribute('aria-label') || b.textContent?.trim()
      expect(name, b.outerHTML).toBeTruthy()
    }
    expect(el.querySelector('[role="radiogroup"]')!.getAttribute('aria-label')).toBe('参照')
    expect(el.querySelector('[data-align-mode="hdist"]')!.getAttribute('aria-label')).toBe('水平等距')
  })

  it('出现时不抢焦点；按钮都可 Tab 到', async () => {
    document.body.focus()
    await select(['t1', 't2'])
    expect(multiBar()!.contains(document.activeElement)).toBe(false)
    for (const b of multiBar()!.querySelectorAll('button')) expect(b.tabIndex).toBeGreaterThanOrEqual(0)
    const first = multiBar()!.querySelector('button')!
    first.focus()
    expect(document.activeElement).toBe(first)
  })
})

describe('与 ArrangeSection 共用参照', () => {
  it('浮动栏切参照，属性页当场跟着换；反过来一样', async () => {
    await act(async () => root.unmount())
    await mount(<ArrangeSection count={3} multi />)
    await select(['t1', 't2', 't3'])
    const inBar = () => multiBar()!.querySelector('[data-align-ref-picker]')!
    const inPanel = () =>
      [...mountEl.querySelectorAll<HTMLElement>('[role="radiogroup"]')].find(
        (g) => g.getAttribute('aria-label') === '参照',
      )!
    await click(radio(inBar(), '画布'))
    expect(radio(inPanel(), '画布').getAttribute('aria-checked')).toBe('true')
    expect(useArrangeStore.getState().alignRef).toBe('page')
    await click(radio(inPanel(), '最后选中'))
    expect(radio(inBar(), '最后选中').getAttribute('aria-checked')).toBe('true')
    // 参照切换不进历史、不动文档
    expect(past()).toHaveLength(0)
  })
})
