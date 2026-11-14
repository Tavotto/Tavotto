/**
 * 自由路径（ADR 0185）在属性栏 / 浮动栏 / 格式刷里不是死胡同：「转为路径」之后照样能改颜色、线宽、填充、端型。
 *
 * 路径的填充 / 描边字段与形状逐字相同，所以它走**同一套控件**（`ShapeSection`）；哪一行出现、写哪个字段只问
 * `lib/markStyle` 的能力函数。含开放子路径时多出端型（与箭头同一组控件）。判据的主语：用户在真属性页里操作之后
 * **文档里的那个对象**——改动只进一次 commit，撤销按步回到形状本来的样子。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { ObjectQuickActions } from '@/canvas/context-bar/SingleObjectBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { convertToPath, copySelectionStyle, pasteSelectionStyle } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject, type PathObject, type ShapeObject } from '@/types/document'
import { Inspector } from './Inspector'

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
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_path_inspector_' + Math.random())
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

/** 系统取色盘：一次 input，再失焦（ColorField 在拖动期间开事务，失焦收成一条历史） */
async function pickColor(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`input[type=color][aria-label="${label}：取色盘"]`)
  expect(input, `${label} 的取色盘不在`).toBeTruthy()
  await act(async () => {
    input!.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input!.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => input!.blur())
}
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

describe('属性栏：路径与封闭形状同一套外观控件', () => {
  it('闭合路径：填充 / 描边颜色 / 线宽 / 线型都在，没有端型、没有圆角 / 边数', async () => {
    await seed([closedPath()], ['p2'])
    await mount()
    expect(host.querySelector('input[type=color][aria-label="填充：取色盘"]')).not.toBeNull()
    expect(host.querySelector('input[type=color][aria-label="描边：取色盘"]')).not.toBeNull()
    expect(host.querySelector('input[aria-label="线宽"]')).not.toBeNull()
    expect(host.textContent).toContain('线型')
    expect(host.textContent).not.toContain('终点')
    expect(host.querySelector('input[aria-label="圆角"]')).toBeNull()
  })

  it('开放路径：多出起点 / 终点端型（与箭头同一组控件）', async () => {
    await seed([openPath()], ['p1'])
    await mount()
    expect(host.textContent).toContain('终点')
    expect(host.textContent).toContain('起点')
  })

  it('改描边颜色 / 线宽 / 填充：写的是路径自己的字段，每次一条 commit', async () => {
    await seed([closedPath()], ['p2'])
    await mount()
    await pickColor('描边', '#ff0000')
    expect(byId<PathObject>('p2').color).toBe('#ff0000')
    expect(past()).toBe(1)
    await typeNumber('线宽', '3.5')
    expect(byId<PathObject>('p2').strokePt).toBe(3.5)
    expect(past()).toBe(2)
    await pickColor('填充', '#0000ff')
    expect(byId<PathObject>('p2').fill).toBe('#0000ff')
    expect(past()).toBe(3)
    expect(byId<PathObject>('p2').type).toBe('path')
  })

  it('形状 → 转为路径 → 改颜色 → 撤销两次：回到原来的形状、原来的颜色', async () => {
    const original = rect()
    await seed([original], ['s1'])
    await mount()
    await act(async () => convertToPath(['s1']))
    expect(byId('s1').type).toBe('path')
    await pickColor('描边', '#ff0000')
    expect(byId<PathObject>('s1').color).toBe('#ff0000')
    expect(past()).toBe(2)
    await act(async () => useDocumentStore.getState().undo())
    expect(byId<PathObject>('s1')).toMatchObject({ type: 'path', color: '#1b1b18' })
    await act(async () => useDocumentStore.getState().undo())
    expect(JSON.parse(JSON.stringify(byId('s1')))).toEqual(original)
  })

  it('选区里混着形状与路径：同一套控件，改颜色两个都改、一条 commit', async () => {
    await seed([rect(), closedPath()], ['s1', 'p2'])
    await mount()
    await pickColor('描边', '#00ff00')
    expect(byId<ShapeObject>('s1').color).toBe('#00ff00')
    expect(byId<PathObject>('p2').color).toBe('#00ff00')
    expect(past()).toBe(1)
  })
})

describe('浮动栏：路径有颜色 + 线宽', () => {
  it('单选路径时出现，改颜色写进路径', async () => {
    await seed([closedPath()], ['p2'])
    await mount(<ObjectQuickActions obj={byId('p2')} />)
    await pickColor('描边', '#123456')
    expect(byId<PathObject>('p2').color).toBe('#123456')
    expect(past()).toBe(1)
  })
})

describe('格式刷：形状 ↔ 路径互刷填充描边', () => {
  it('形状 → 路径：同名字段照搬，形状独有的（圆角）不写', async () => {
    await seed(
      [rect({ color: '#aa0000', fill: '#00aa00', fillOpacity: 0.4, strokePt: 3, dash: 'dashed', cornerRadius: 2 }), closedPath()],
      ['s1'],
    )
    copySelectionStyle()
    useSelectionStore.getState().set(['p2'])
    pasteSelectionStyle()
    const p = byId<PathObject>('p2')
    expect(p).toMatchObject({ type: 'path', color: '#aa0000', fill: '#00aa00', fillOpacity: 0.4, strokePt: 3, dash: 'dashed' })
    expect('cornerRadius' in p).toBe(false)
    expect(past()).toBe(1)
  })

  it('路径 → 形状：同名字段照搬，路径独有的（端型）不写', async () => {
    await seed([openPath({ color: '#0000aa', strokePt: 4, headEnd: 'triangle', fill: '#eeeeee' }), rect()], ['p1'])
    copySelectionStyle()
    useSelectionStore.getState().set(['s1'])
    pasteSelectionStyle()
    const s = byId<ShapeObject>('s1')
    expect(s).toMatchObject({ type: 'shape', shape: 'rect', color: '#0000aa', strokePt: 4, fill: '#eeeeee' })
    expect('headEnd' in s).toBe(false)
  })
})
