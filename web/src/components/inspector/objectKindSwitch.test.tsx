/**
 * 属性栏对象标题兼作类型切换（cap-shape-switch）。
 *
 * 判据的主语：这里问的是**右栏头部那颗徽标**的形态与可达名，以及点下去之后
 * **文档里那个对象**的类型——不是「组件有没有渲染」。菜单渲染在 portal 里
 * （Radix），所以查它得用 `document`，查徽标才用 `host`。
 *
 * 切换本身的字段规则在 `lib/shapeSwitch.test.ts`，右键菜单那个入口在
 * `canvas/objectContextMenu.test.tsx`——三处各测自己那一层，不互相冒充。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import {
  emptyProject,
  type ArrowObject,
  type CanvasObject,
  type PanelObject,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { Inspector } from './Inspector'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const shapeOf = (id: string, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x: 23.9,
  y: 55.1,
  w: 36.7,
  h: 21.6,
  color: '#1B1B18',
  strokePt: 1,
  fill: null,
  ...over,
})

const arrowOf = (id = 'a1'): ArrowObject => ({
  id,
  type: 'arrow',
  x: 58.4,
  y: 34.7,
  w: 43.1,
  h: 8.6,
  start: { rx: 0, ry: 1 },
  end: { rx: 1, ry: 0 },
  color: '#1B1B18',
  strokePt: 1,
  head: 'end',
})

const textOf = (id = 't1'): TextObject => ({
  id,
  type: 'text',
  x: 0,
  y: 0,
  w: 30,
  h: 8,
  text: '一段标注',
  sizePt: 9,
  bold: false,
  color: '#1B1B18',
  align: 'left',
})

const panelOf = (id = 'p1'): PanelObject =>
  ({
    id,
    type: 'panel',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    nativeW: 40,
    nativeH: 30,
    overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

const badge = () => host.querySelector<HTMLElement>('[data-object-kind]')
const kindItems = () =>
  [...document.querySelectorAll<HTMLElement>('[data-kind-target]')].map(
    (el) => el.dataset.kindTarget!,
  )
const kindItem = (kind: string) =>
  document.querySelector<HTMLElement>(`[data-kind-target="${kind}"]`)
const objs = () => useDocumentStore.getState().doc.objects
const byId = <T extends CanvasObject = CanvasObject>(id: string) =>
  objs().find((o) => o.id === id) as T
const past = () => useDocumentStore.getState().past

async function mount(items: CanvasObject[], selected: string[] = items.map((o) => o.id)) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_kindsw_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useUiStore.setState({ rightOpen: true, rightTab: 'properties', layout: 'wide', elementPanelId: null })
  useSelectionStore.getState().set(selected)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <Inspector />
      </TooltipProvider>,
    )
  })
}

/**
 * 点开徽标下拉。**用 pointerdown 而不是 click**：Radix 的触发器在 pointerdown
 * 那一刻就开菜单（这样按住拖到某一项松手也能选中），`el.click()` 只发合成的
 * click，菜单根本不会打开——那时 `kindItems()` 是空数组，看起来像「没渲染出来」。
 *
 * 内容渲染在 portal 里，所以打开之后一律用 `document` 查，不用 `host`。
 */
async function openSwitch() {
  const trigger = badge()!
  await act(async () => {
    trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
  })
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
})

afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  useSelectionStore.getState().clear()
})

/* -------------------------------------------------------------------------- */
/*  徽标的两种形态                                                              */
/* -------------------------------------------------------------------------- */

describe('徽标：能切换的是按钮，不能切换的还是那颗静态徽标', () => {
  it('形状：徽标是按钮，写着当前类型，可达名说出「更改」', async () => {
    await mount([shapeOf('s1', { shape: 'ellipse' })])
    const b = badge()!
    expect(b.tagName).toBe('BUTTON')
    expect(b.textContent).toContain('椭圆')
    expect(b.getAttribute('aria-label')).toBe('类型：椭圆，点击更改')
    expect(b.getAttribute('aria-haspopup')).toBe('menu')
  })

  it('箭头：同样是按钮', async () => {
    await mount([arrowOf()])
    expect(badge()!.tagName).toBe('BUTTON')
    expect(badge()!.textContent).toContain('箭头')
  })

  it('文字：徽标还在（它回答「我在改什么」），但不是按钮', async () => {
    await mount([textOf()])
    expect(badge()!.tagName).toBe('SPAN')
    expect(badge()!.textContent).toBe('文字')
  })

  it('面板：同样是静态徽标', async () => {
    await mount([panelOf()])
    expect(badge()!.tagName).toBe('SPAN')
    expect(badge()!.textContent).toBe('面板')
  })

  it('多选跨族（矩形 + 箭头）：徽标整个不出现（没有一个类型可说）', async () => {
    await mount([shapeOf('s1'), arrowOf()])
    expect(badge()).toBeNull()
  })
})

/* -------------------------------------------------------------------------- */
/*  下拉的内容与写入                                                            */
/* -------------------------------------------------------------------------- */

describe('下拉：给同族的全部类型，当前那个带勾', () => {
  it('矩形：六种形状，矩形被勾上；每一项都是 radio 且有文字名', async () => {
    await mount([shapeOf('s1')])
    await openSwitch()
    expect(kindItems()).toEqual(['rect', 'ellipse', 'triangle', 'diamond', 'polygon', 'brace'])
    expect(kindItem('rect')!.getAttribute('role')).toBe('menuitemradio')
    expect(kindItem('rect')!.getAttribute('aria-checked')).toBe('true')
    expect(kindItem('ellipse')!.getAttribute('aria-checked')).toBe('false')
    // 图形之外必须有文字名：每一格的可读文字不为空
    expect(kindItem('brace')!.textContent).toContain('大括号')
  })

  it('直线：只给直线与箭头（不跨族）', async () => {
    await mount([shapeOf('ln', { shape: 'line' })])
    await openSwitch()
    expect(kindItems()).toEqual(['line', 'arrow'])
  })

  it('点「三角形」：文档里换了类型、id 不动、一条历史', async () => {
    await mount([shapeOf('s1')])
    await openSwitch()
    await act(async () => kindItem('triangle')!.click())
    const after = byId<ShapeObject>('s1')
    expect(after.shape).toBe('triangle')
    expect(after.id).toBe('s1')
    expect(past()).toHaveLength(1)
    expect(past()[0].label).toMatchObject({ key: 'history.switchKind', values: { name: '三角形' } })
  })

  it('切换之后徽标跟着变，属性页也换成新类型该有的字段（多边形边数）', async () => {
    await mount([shapeOf('s1')])
    await openSwitch()
    await act(async () => kindItem('polygon')!.click())
    expect(badge()!.textContent).toContain('多边形')
    expect(host.textContent).toContain('边数')
  })
})

describe('多选：同族才给，切换作用于全部', () => {
  it('矩形 + 椭圆：徽标写「多个值」，一个都不勾，选完两个都变', async () => {
    await mount([shapeOf('s1'), shapeOf('s2', { shape: 'ellipse' })])
    expect(badge()!.textContent).toContain('多个值')
    expect(badge()!.getAttribute('aria-label')).toBe('类型：多个值，点击更改')
    await openSwitch()
    expect(kindItem('rect')!.getAttribute('aria-checked')).toBe('false')
    expect(kindItem('ellipse')!.getAttribute('aria-checked')).toBe('false')
    await act(async () => kindItem('brace')!.click())
    expect(byId<ShapeObject>('s1').shape).toBe('brace')
    expect(byId<ShapeObject>('s2').shape).toBe('brace')
    expect(past()).toHaveLength(1)
    expect(past()[0].label).toMatchObject({
      key: 'history.switchKindCount',
      values: { count: 2, name: '大括号' },
    })
  })

  it('形状 + 文字：徽标不是按钮，也没有下拉可点', async () => {
    await mount([shapeOf('s1'), textOf()])
    expect(badge()).toBeNull()
  })
})

describe('键盘可达', () => {
  it('徽标能聚焦，Enter 打开下拉', async () => {
    await mount([shapeOf('s1')])
    const b = badge()!
    await act(async () => b.focus())
    expect(document.activeElement).toBe(b)
    await act(async () => {
      b.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(kindItems()).toHaveLength(6)
  })
})
