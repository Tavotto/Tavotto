/**
 * 渐变的属性栏（ADR 0186 §四，第一期只在属性栏调）：「填充」/「描边」那一行颜色后面一个类型下拉（不新增行），
 * 色标 / 角度 / 中心与半径在「更多」里。判据的主语是**文档里的那个对象**：每次改动一条 commit，撤销回到纯色。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
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
const rows = () => host.querySelectorAll('[data-prop], [data-fold]').length

describe('类型下拉：纯色 / 线性 / 径向', () => {
  it('填充那一行多一个下拉、不新增行；选「线性」写 fillGradient（首色 = 当前填充），选回「纯色」删字段、fill 一直在', async () => {
    await seed([rect()], ['s1'])
    await mount()
    const before = rows()
    await pickKind('填充类型', '线性')
    const g = byId<ShapeObject>('s1').fillGradient!
    expect(g.kind).toBe('linear')
    expect(g.stops[0].color).toBe('#ffcc00')
    expect(byId<ShapeObject>('s1').fill).toBe('#ffcc00')
    expect(past()).toBe(1)
    expect(rows()).toBe(before)
    await pickKind('填充类型', '径向')
    expect(byId<ShapeObject>('s1').fillGradient!.kind).toBe('radial')
    expect(byId<ShapeObject>('s1').fillGradient!.stops).toEqual(g.stops)
    await pickKind('填充类型', '纯色')
    expect('fillGradient' in byId<ShapeObject>('s1')).toBe(false)
    expect(past()).toBe(3)
  })

  it('没有填充时没有填充类型下拉；描边那一行也有（写 strokeGradient）', async () => {
    await seed([rect({ fill: null })], ['s1'])
    await mount()
    expect(host.querySelector('[role="combobox"][aria-label="填充类型"]')).toBeNull()
    await pickKind('描边类型', '径向')
    expect(byId<ShapeObject>('s1').strokeGradient!.kind).toBe('radial')
  })
})

describe('「更多」里的渐变细节', () => {
  const linear = { kind: 'linear' as const, stops: [{ offset: 0, color: '#ff0000' }, { offset: 1, color: '#0000ff' }], from: [0, 0.5] as [number, number], to: [1, 0.5] as [number, number] }

  it('色标：加一个（落在中间、中间色）、改位置、改不透明度、删到只剩两个就删不了', async () => {
    await seed([rect({ fillGradient: linear, stroke: false })], ['s1'])
    await mount()
    await openMore()
    const add = host.querySelector<HTMLButtonElement>('[data-gradient-add-stop="fill"]')!
    await act(async () => add.click())
    let g = byId<ShapeObject>('s1').fillGradient!
    expect(g.stops.map((s) => s.offset)).toEqual([0, 0.5, 1])
    expect(g.stops[1].color).toBe('#800080')
    await typeNumber('色标 2 的位置', '30')
    await typeNumber('色标 2 的不透明度', '40')
    g = byId<ShapeObject>('s1').fillGradient!
    expect(g.stops[1]).toEqual({ offset: 0.3, color: '#800080', opacity: 0.4 })
    const del = (n: number) => host.querySelector<HTMLButtonElement>(`[aria-label="删除色标 ${n}"]`)!
    await act(async () => del(2).click())
    expect(byId<ShapeObject>('s1').fillGradient!.stops).toHaveLength(2)
    expect(del(1).disabled).toBe(true)
  })

  it('线性：角度按对象的实际尺寸算（40 × 25 mm 上的 90° = 从下到上）；径向：中心与半径', async () => {
    await seed([rect({ fillGradient: linear })], ['s1'])
    await mount()
    await openMore()
    expect(host.querySelector<HTMLInputElement>('input[aria-label="渐变角度"]')!.value).toBe('0')
    await typeNumber('渐变角度', '90')
    const g = byId<ShapeObject>('s1').fillGradient as typeof linear
    expect(g.from).toEqual([0.5, 1])
    expect(g.to).toEqual([0.5, 0])
    await seed([rect({ fillGradient: { kind: 'radial', stops: linear.stops, center: [0.5, 0.5], radius: 0.5 } })], ['s1'])
    await mount()
    await openMore()
    await typeNumber('中心 X', '25')
    await typeNumber('渐变半径', '80')
    expect(byId<ShapeObject>('s1').fillGradient).toMatchObject({ center: [0.25, 0.5], radius: 0.8 })
  })

  it('自由路径同一套控件', async () => {
    await seed([closedPath()], ['p2'])
    await mount()
    await pickKind('填充类型', '线性')
    expect(byId<PathObject>('p2').fillGradient!.kind).toBe('linear')
  })
})
