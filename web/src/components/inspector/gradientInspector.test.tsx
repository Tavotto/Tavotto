/**
 * 渐变的属性栏（ADR 0186 §四；2026-11-15 返工成 PPT 式横向色带）：「填充」/「描边」那一行渐变时是「开关 + 预览条 + 类型下拉」，
 * 色带常显在它下面（点空白加色标、拖动、往下拖出 / Delete 删除、键盘 slider），再下面一行只编辑选中的色标；径向的中心与半径在「更多」里。
 * 判据的主语是**文档里的那个对象**：每个动作一条 commit（拖动整段一条），降级纯色跟着首个色标走。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject, type PathObject, type ShapeObject } from '@/types/document'
import { Inspector } from './Inspector'

// Radix Select 打开时会把选中项滚进视野；jsdom 没有这个方法
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const rect = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's1',
  type: 'shape',
  shape: 'rect',
  x: 20,
  y: 20,
  w: 40,
  h: 25,
  color: '#1b1b18',
  strokePt: 1,
  fill: '#ffcc00',
  ...over,
})
const openPath = (over: Partial<PathObject> = {}): PathObject => ({
  id: 'p1',
  type: 'path',
  x: 10,
  y: 10,
  w: 30,
  h: 20,
  color: '#224466',
  strokePt: 2,
  fill: null,
  subpaths: [{ closed: false, nodes: [{ p: [0, 1] }, { p: [1, 0], in: [0.5, 0] }] }],
  ...over,
})
const closedPath = (over: Partial<PathObject> = {}): PathObject => ({
  ...openPath({ id: 'p2', fill: '#00aa00' }),
  subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [0.5, 1] }] }],
  ...over,
})

let root: Root
let host: HTMLDivElement
const doc = () => useDocumentStore.getState().doc
const byId = <T extends CanvasObject>(id: string) => doc().objects.find((o) => o.id === id) as T
const past = () => useDocumentStore.getState().past.length

async function seed(objs: CanvasObject[], sel: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_gradient_inspector_' + Math.random())
  useDocumentStore.getState().commit(literal('加标注'), (d) => {
    d.objects.push(...objs)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useUiStore.setState({ rightOpen: true, rightTab: 'properties', layout: 'wide', elementPanelId: null })
  useSelectionStore.getState().set(sel)
}
async function mount(node: React.ReactNode = <Inspector />) {
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useSelectionStore.getState().clear()
})

async function typeNumber(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)
  expect(input, `${label} 不在`).toBeTruthy()
  await act(async () => {
    input!.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input!.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  })
}

const openMore = async () => {
  const btn = host.querySelector<HTMLButtonElement>('[data-fold="stroke-more"] button')
  expect(btn, '更多不在').toBeTruthy()
  if (btn!.getAttribute('aria-expanded') !== 'true') await act(async () => btn!.click())
}
async function pickKind(label: string, option: string) {
  const trigger = host.querySelector<HTMLElement>(`[role="combobox"][aria-label="${label}"]`)
  expect(trigger, `${label} 下拉不在`).toBeTruthy()
  await act(async () => trigger!.click())
  const opt = [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent === option)
  expect(opt, `${option} 不在`).toBeTruthy()
  await act(async () => opt!.click())
}
/** jsdom 没有布局：给色带一个 100px 宽、20px 高的盒子，clientX 就是百分比 */
function sizeBar(target = 'fill') {
  const bar = host.querySelector<HTMLElement>(`[data-gradient-bar="${target}"]`)!
  expect(bar, '色带不在').toBeTruthy()
  bar.getBoundingClientRect = () => ({ left: 0, top: 0, right: 100, bottom: 20, width: 100, height: 20, x: 0, y: 0, toJSON() {} }) as DOMRect
  return bar
}
const node = (i: number, target = 'fill') => host.querySelector<HTMLElement>(`[data-gradient-bar="${target}"] [data-gradient-stop="${i}"]`)!
const pointer = (el: Element, type: string, x: number, y = 10) =>
  el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }))
const key = (el: Element, k: string, shift = false) =>
  el.dispatchEvent(new KeyboardEvent('keydown', { key: k, shiftKey: shift, bubbles: true, cancelable: true }))
const fillG = () => byId<ShapeObject>('s1').fillGradient!

const linear = {
  kind: 'linear' as const,
  stops: [
    { offset: 0, color: '#ff0000' },
    { offset: 1, color: '#0000ff' },
  ],
  from: [0, 0.5] as [number, number],
  to: [1, 0.5] as [number, number],
}
const three = { ...linear, stops: [linear.stops[0], { offset: 0.5, color: '#00ff00' }, linear.stops[1]] }

describe('「填充」那一行：渐变时是开关 + 类型下拉 + 角度 + 反向（没有预览条）', () => {
  it('选「线性」：写 fillGradient（首色 = 当前填充）；色块和十六进制不见了、没有预览条，角度与反向在这一行；色带常显在下面、不在「更多」里', async () => {
    await seed([rect()], ['s1'])
    await mount()
    expect(host.querySelector('input[aria-label="填充：取色盘"]')).not.toBeNull()
    await pickKind('填充类型', '线性')
    expect(fillG()).toMatchObject({ kind: 'linear', stops: [{ offset: 0, color: '#ffcc00' }, { offset: 1, color: '#ffffff' }] })
    expect(past()).toBe(1)
    expect(host.querySelector('input[aria-label="填充：取色盘"]')).toBeNull()
    expect(host.querySelector('[data-gradient-preview]')).toBeNull()
    // 角度与反向和类型下拉在同一行（「填充」那一行），不在色带那一行
    const kindRow = host.querySelector('[role="combobox"][aria-label="填充类型"]')!.closest('[data-row-grid]')!
    expect(kindRow.querySelector('input[aria-label="渐变角度"]')).not.toBeNull()
    expect(kindRow.querySelector('[aria-label="反向"]')).not.toBeNull()
    expect(kindRow.querySelector('[data-gradient-bar]')).toBeNull()
    const bar = host.querySelector('[data-gradient-bar="fill"]')!
    // 色带那一行只有色带本身：占满控件列
    expect(bar.parentElement!.children).toHaveLength(1)
    expect(bar.parentElement!.querySelector('input')).toBeNull()
    expect(bar).not.toBeNull()
    expect(bar.closest('[data-fold="stroke-more"]')).toBeNull()
    // 「更多」里不再有色标 / 角度
    expect(host.querySelector('[data-fold="stroke-more"] input[aria-label="渐变角度"]')).toBeNull()
    await pickKind('填充类型', '纯色')
    expect('fillGradient' in byId<ShapeObject>('s1')).toBe(false)
    expect(host.querySelector('[data-gradient-bar="fill"]')).toBeNull()
  })

  it('降级纯色跟着首个色标走：改首个色标的颜色 → fill 跟着变；切回纯色就是它', async () => {
    await seed([rect({ fillGradient: linear, fill: '#123456' })], ['s1'])
    await mount()
    await act(async () => node(1).focus())
    await act(async () => key(node(1), 'ArrowLeft', true)) // 动的是第二个：首色不变，fill 被同步成首色
    expect(byId<ShapeObject>('s1').fill).toBe('#ff0000')
    // 把第二个拖到最前面之前那一位置不行（不越过邻居）；用反向让蓝色成为首个
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="反向"]')!.click())
    expect(fillG().stops[0].color).toBe('#0000ff')
    expect(byId<ShapeObject>('s1').fill).toBe('#0000ff')
    await pickKind('填充类型', '纯色')
    expect(byId<ShapeObject>('s1').fill).toBe('#0000ff')
  })
})

describe('色带上的节点', () => {
  it('点色带空白处加一个色标：位置 = 点的地方，颜色 = 那里的插值色；它成为选中的那个', async () => {
    await seed([rect({ fillGradient: linear })], ['s1'])
    await mount()
    const bar = sizeBar()
    await act(async () => pointer(bar, 'pointerdown', 25))
    expect(fillG().stops).toEqual([linear.stops[0], { offset: 0.25, color: '#bf0040' }, linear.stops[1]])
    expect(node(1).getAttribute('data-selected')).toBe('true')
    expect(host.querySelector<HTMLInputElement>('input[aria-label="色标 2 的位置"]')!.value).toBe('25')
    expect(past()).toBe(1)
  })

  it('左右拖：位置跟着走、不越过邻居；整段拖动只进一条历史', async () => {
    await seed([rect({ fillGradient: { ...three, stops: [{ offset: 0.2, color: '#ff0000' }, three.stops[1], three.stops[2]] } })], ['s1'])
    await mount()
    sizeBar()
    await act(async () => pointer(node(1), 'pointerdown', 50))
    await act(async () => pointer(node(1), 'pointermove', 70))
    expect(fillG().stops[1].offset).toBe(0.7)
    await act(async () => pointer(node(1), 'pointermove', 5))
    expect(fillG().stops[1].offset).toBe(0.2) // 夹在左邻居（0.2）上，不越过
    await act(async () => pointer(node(1), 'pointerup', 5))
    expect(fillG().stops.map((s) => s.color)).toEqual(['#ff0000', '#00ff00', '#0000ff'])
    expect(past()).toBe(1)
  })

  it('往下拖出色带删除；只剩两个时拖出去也不删', async () => {
    await seed([rect({ fillGradient: three })], ['s1'])
    await mount()
    sizeBar()
    await act(async () => pointer(node(1), 'pointerdown', 50))
    await act(async () => pointer(node(1), 'pointermove', 50, 60))
    expect(node(1).className).toContain('opacity-30')
    await act(async () => pointer(node(1), 'pointerup', 50, 60))
    expect(fillG().stops.map((s) => s.color)).toEqual(['#ff0000', '#0000ff'])
    expect(past()).toBe(1)
    await act(async () => pointer(node(1), 'pointerdown', 100))
    await act(async () => pointer(node(1), 'pointermove', 100, 60))
    await act(async () => pointer(node(1), 'pointerup', 100, 60))
    expect(fillG().stops).toHaveLength(2)
  })

  it('没有 × 按钮', async () => {
    await seed([rect({ fillGradient: three })], ['s1'])
    await mount()
    expect(host.querySelector('[aria-label^="删除色标"]')).toBeNull()
  })
})

describe('键盘', () => {
  it('节点是 slider、能 Tab 到；←/→ 1%，⇧ 10%；Delete 删（至少两个）；Enter 开取色器', async () => {
    await seed([rect({ fillGradient: three })], ['s1'])
    await mount()
    const n = node(1)
    expect(n.getAttribute('role')).toBe('slider')
    expect(n.tabIndex).toBe(0)
    expect(n.getAttribute('aria-valuenow')).toBe('50')
    await act(async () => n.focus())
    await act(async () => key(n, 'ArrowRight'))
    expect(fillG().stops[1].offset).toBe(0.51)
    await act(async () => key(node(1), 'ArrowLeft', true))
    expect(fillG().stops[1].offset).toBe(0.41)
    // 取色器的入口与 ColorField 自己的一样：带面板时是色块按钮，否则是原生取色盘
    const row = host.querySelector('[data-gradient-selected="fill"]')!
    const swatch = row.querySelector<HTMLElement>('[data-color-swatch]') ?? row.querySelector<HTMLElement>('input[type=color]')!
    const opened = vi.spyOn(swatch, 'click')
    await act(async () => key(node(1), 'Enter'))
    expect(opened).toHaveBeenCalled()
    await act(async () => key(node(1), 'Delete'))
    expect(fillG().stops).toHaveLength(2)
    await act(async () => key(node(0), 'Delete'))
    expect(fillG().stops).toHaveLength(2)
  })

  it('色带本身聚焦时 Enter 在最大空档的中点加一个', async () => {
    await seed([rect({ fillGradient: linear })], ['s1'])
    await mount()
    const bar = host.querySelector<HTMLElement>('[data-gradient-bar="fill"]')!
    await act(async () => bar.focus())
    await act(async () => key(bar, 'Enter'))
    expect(fillG().stops.map((s) => s.offset)).toEqual([0, 0.5, 1])
  })
})

describe('选中色标那一行与角度', () => {
  it('只显示选中色标的颜色 · 位置 · 不透明度；改位置 / 不透明度写给它', async () => {
    await seed([rect({ fillGradient: three })], ['s1'])
    await mount()
    await act(async () => node(1).focus())
    await typeNumber('色标 2 的位置', '30')
    await typeNumber('色标 2 的不透明度', '40')
    expect(fillG().stops[1]).toEqual({ offset: 0.3, color: '#00ff00', opacity: 0.4 })
    expect(host.querySelectorAll('[data-gradient-selected="fill"] [data-color-field]')).toHaveLength(1)
  })

  it('线性：角度在「填充」那一行（40 × 25 mm 上 90° = 从下到上）；径向没有角度，中心与半径在「更多」里', async () => {
    await seed([rect({ fillGradient: linear })], ['s1'])
    await mount()
    const angle = host.querySelector<HTMLInputElement>('input[aria-label="渐变角度"]')!
    expect(angle.closest('[data-fold="stroke-more"]')).toBeNull()
    await typeNumber('渐变角度', '90')
    expect(fillG()).toMatchObject({ from: [0.5, 1], to: [0.5, 0] })
    await seed([rect({ fillGradient: { kind: 'radial', stops: linear.stops, center: [0.5, 0.5], radius: 0.5 } })], ['s1'])
    await mount()
    expect(host.querySelector('input[aria-label="渐变角度"]')).toBeNull()
    await openMore()
    await typeNumber('填充中心 X', '25')
    await typeNumber('填充半径', '80')
    expect(fillG()).toMatchObject({ center: [0.25, 0.5], radius: 0.8 })
  })
})

describe('描边渐变同一套', () => {
  it('描边类型选「径向」：描边那一行换成预览条，下面一条色带；首色同步进 color', async () => {
    await seed([rect({ fill: null })], ['s1'])
    await mount()
    expect(host.querySelector('[role="combobox"][aria-label="填充类型"]')).toBeNull()
    await pickKind('描边类型', '径向')
    expect(byId<ShapeObject>('s1').strokeGradient!.kind).toBe('radial')
    expect(host.querySelector('[data-gradient-preview]')).toBeNull()
    expect(host.querySelector('input[aria-label="描边：取色盘"]')).toBeNull()
    expect(host.querySelector('[aria-label="反向"]')).not.toBeNull()
    expect(host.querySelector('input[aria-label="渐变角度"]')).toBeNull() // 径向没有角度
    sizeBar('stroke')
    await act(async () => pointer(host.querySelector('[data-gradient-bar="stroke"]')!, 'pointerdown', 50))
    expect(byId<ShapeObject>('s1').strokeGradient!.stops).toHaveLength(3)
    expect(byId<ShapeObject>('s1').color).toBe(byId<ShapeObject>('s1').strokeGradient!.stops[0].color)
  })

  it('自由路径同一套控件', async () => {
    await seed([closedPath()], ['p2'])
    await mount()
    await pickKind('填充类型', '线性')
    expect(byId<PathObject>('p2').fillGradient!.kind).toBe('linear')
    expect(host.querySelector('[data-gradient-bar="fill"]')).not.toBeNull()
  })
})
