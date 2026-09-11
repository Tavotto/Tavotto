/**
 * 标注（箭头 / 矩形 / 椭圆 / 线）的属性页（审计 T28）：
 *   1. 分组标题不再重复对象类型——右栏头部已经写着「箭头」/「矩形」；
 *   2. 箭头把端型排在颜色 / 线宽之前；矩形把填充排在描边之前；
 *   3. 单选只常驻层级，六向「对齐到画布」收进「更多排列」，能力一条不减；
 *   4. 图形选择有文字名与键盘路径（radiogroup + 方向键），不依赖猜图标。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type ArrowObject, type ShapeObject } from '@/types/document'
import { Inspector } from './Inspector'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const arrowOf = (): ArrowObject =>
  ({
    id: 'a1',
    type: 'arrow',
    x: 58.4,
    y: 34.7,
    w: 43.1,
    h: 8.6,
    start: { x: 58.4, y: 43.3 },
    end: { x: 101.5, y: 34.7 },
    color: '#1B1B18',
    strokePt: 1,
  }) as unknown as ArrowObject

const rectOf = (): ShapeObject =>
  ({
    id: 's1',
    type: 'shape',
    shape: 'rect',
    x: 23.9,
    y: 55.1,
    w: 36.7,
    h: 21.6,
    color: '#1B1B18',
    strokePt: 1,
    fill: null,
  }) as unknown as ShapeObject

let root: Root
let host: HTMLDivElement

const all = (sel: string) => [...host.querySelectorAll(sel)]
// 折叠区标题都是 h3 或带 aria-expanded 的按钮。**排除类型徽标**：它也是个带
// aria-expanded 的按钮（属性栏对象标题兼作类型切换，cap-shape-switch），但它是
// 「我在改什么」那句话，不是分组标题——不排掉的话下面「分组标题不重复对象类型」
// 那条会把徽标自己数成重复的那一份
const headings = () =>
  all('h3, button[aria-expanded]:not([data-object-kind])').map((el) => el.textContent?.trim() ?? '')
const disclosure = (title: string) =>
  all('button[aria-expanded]').find((b) => b.textContent?.startsWith(title)) as HTMLButtonElement
/** 某段可见文字在整棵属性页里的先后位置；找不到返回 -1 */
const orderOf = (text: string) => host.textContent?.indexOf(text) ?? -1

async function mount(obj: ArrowObject | ShapeObject) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_mark_layout')
  useDocumentStore.getState().commit(literal('加标注'), (d) => {
    d.objects.push(obj)
  })
  useUiStore.setState({
    rightOpen: true,
    rightTab: 'properties',
    layout: 'wide',
    elementPanelId: null,
  })
  useSelectionStore.getState().set([obj.id])
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

beforeEach(() => {
  localStorage.clear()
})

afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
})

describe('标题不重复对象类型', () => {
  /**
   * 「头部写着箭头」现在由**类型徽标**承担，不再是 h2——没起过名字的标注，
   * `objectLabel` 的兜底就是类型名，徽标和 h2 会并排写同一个词
   * （cap-shape-switch 把徽标变成类型切换之后，那份重复更扎眼）。所以这里查
   * 的是整个头部，而不是某一个标签名：断言的是「说没说出口」，不是「摆在哪个
   * 元素里」。
   */
  const header = () => host.querySelector('header')!.textContent ?? ''

  it('箭头：头部写「箭头」且只写一次，分组标题是「外观」', async () => {
    await mount(arrowOf())
    expect(header()).toContain('箭头')
    expect(header().match(/箭头/g)).toHaveLength(1)
    expect(headings()).toContain('外观')
    expect(headings().filter((h) => h === '箭头')).toHaveLength(0)
  })

  it('矩形：头部写「矩形」且只写一次，分组标题同样是「外观」', async () => {
    await mount(rectOf())
    expect(header()).toContain('矩形')
    expect(header().match(/矩形/g)).toHaveLength(1)
    expect(headings()).toContain('外观')
    expect(headings().filter((h) => h === '形状')).toHaveLength(0)
  })
})

describe('高频属性排在前面', () => {
  it('箭头：终点 / 起点在线宽（与色块）之前', async () => {
    await mount(arrowOf())
    // 颜色是「线宽」那一行里的色块（2026-09-11 用户反馈：色号框只留色块），
    // 没有自己的可见标签；判序用「线宽」这一行
    expect(orderOf('终点')).toBeGreaterThan(0)
    expect(orderOf('线宽')).toBeGreaterThan(0)
    expect(orderOf('终点')).toBeLessThan(orderOf('线宽'))
    expect(orderOf('起点')).toBeLessThan(orderOf('线宽'))
  })

  it('矩形：填充在描边之前，圆角还在', async () => {
    await mount(rectOf())
    expect(orderOf('填充')).toBeGreaterThan(0)
    expect(orderOf('填充')).toBeLessThan(orderOf('描边'))
    expect(orderOf('圆角')).toBeGreaterThan(0)
  })

  it('矩形填充与画布文字的背景同一个控件：关着是「＋添加填充」', async () => {
    await mount(rectOf())
    const add = all('[data-effect-add]').find((a) => a.textContent?.includes('添加填充'))
    expect(add).toBeDefined()
    await act(async () => (add as HTMLButtonElement).click())
    const live = useDocumentStore.getState().doc.objects.find((o) => o.id === 's1') as ShapeObject
    expect(live.fill).toBe('#FFFFFF')
    // 开了以后是真开关，关掉即清空——不再另配一颗「无」按钮
    const toggle = host.querySelector<HTMLElement>('[aria-label="填充"]')!
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    await act(async () => toggle.click())
    const after = useDocumentStore.getState().doc.objects.find((o) => o.id === 's1') as ShapeObject
    expect(after.fill).toBeNull()
  })
})

describe('单选：层级常驻，完整排列收进「更多排列」', () => {
  it('层级四颗常驻；对齐到画布默认不铺开', async () => {
    await mount(arrowOf())
    expect(headings()).toContain('层级')
    const zbar = host.querySelector('[aria-label="层级"][role="toolbar"]')!
    expect(zbar.querySelectorAll('button')).toHaveLength(4)
    expect(host.querySelector('[data-single-align]')).toBeNull()
  })

  it('展开「更多排列」后六向对齐一颗不少（能力没丢）', async () => {
    await mount(arrowOf())
    await act(async () => disclosure('更多排列').click())
    const align = host.querySelector('[data-single-align]')!
    expect(align.querySelectorAll('button')).toHaveLength(6)
  })
})

describe('图形选择有名称和键盘路径', () => {
  /** 端型选择器（组件工作台批次起）是一个 combobox：触发钮 + 打开后的 listbox */
  const endCombo = () =>
    host.querySelector<HTMLButtonElement>('[role="combobox"][aria-label="终点"]')!

  it('端型是 combobox，打开后每一格都有文字名，不只有图形', async () => {
    await mount(arrowOf())
    expect(endCombo()).toBeTruthy()
    await act(async () => endCombo().click())
    const options = [...host.querySelectorAll('[role="listbox"][aria-label="终点"] [role="option"]')]
    expect(options).toHaveLength(4)
    for (const o of options) {
      expect(o.textContent?.trim().length).toBeGreaterThan(0)
    }
  })

  it('方向键在选项间漫游并改值，不用鼠标点图标', async () => {
    await mount(arrowOf())
    const before = (
      useDocumentStore.getState().doc.objects.find((o) => o.id === 'a1') as ArrowObject
    ).headEnd
    const key = (k: string) =>
      act(async () => {
        endCombo().dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
      })
    await key('ArrowDown') // 打开（当前项成为活动项）
    await key('ArrowDown') // 移到下一档
    await key('Enter') // 选中
    const after = (
      useDocumentStore.getState().doc.objects.find((o) => o.id === 'a1') as ArrowObject
    ).headEnd
    expect(after).not.toBe(before)
  })
})
