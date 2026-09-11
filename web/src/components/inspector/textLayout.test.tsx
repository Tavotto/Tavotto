/**
 * 画布文字的属性页布局（审计 T27）：
 *   1. 内容与排版排在位置与尺寸**之前**——改字 / 改字号 / 改对齐是高频；
 *   2. 位置与尺寸缩成一行折叠摘要（带当前 X / Y），展开后字段一条不少；
 *   3. 背景 / 描边与图内文字同一个控件（`EffectToggle`）：关着是「＋添加」，
 *      开了是真开关，关掉即清掉那一组；
 *   4. 大小写是带全名的菜单，不再是四个长得像的格子。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type TextObject } from '@/types/document'
import { Inspector } from './Inspector'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const textOf = (): TextObject =>
  ({
    id: 't1',
    type: 'text',
    x: 43.3,
    y: 20.6,
    w: 40,
    h: 4.2,
    text: 'UI',
    sizePt: 10,
  }) as unknown as TextObject

let root: Root
let host: HTMLDivElement

const q = <T extends Element>(sel: string) => host.querySelector<T>(sel)
const all = (sel: string) => [...host.querySelectorAll(sel)]
/** 按可见标题找分组标题元素（Section 的 h3 / Disclosure 的按钮） */
const headings = () =>
  all('h3, button[aria-expanded]').map((el) => el.textContent?.trim() ?? '')
const indexOfHeading = (text: string) => headings().findIndex((h) => h.startsWith(text))
const disclosure = (title: string) =>
  all('button[aria-expanded]').find((b) => b.textContent?.startsWith(title)) as HTMLButtonElement
const live = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 't1') as TextObject

beforeEach(async () => {
  localStorage.clear()
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_text_layout')
  useDocumentStore.getState().commit(literal('加文字'), (d) => {
    d.objects.push(textOf())
  })
  useUiStore.setState({
    rightOpen: true,
    rightTab: 'properties',
    layout: 'wide',
    elementPanelId: null,
  })
  useSelectionStore.getState().set(['t1'])
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
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('顺序：内容与排版在前，位置退到后面', () => {
  it('「文字」分组排在「位置与尺寸」之前', () => {
    const text = indexOfHeading('文字')
    const transform = indexOfHeading('位置与尺寸')
    expect(text).toBeGreaterThanOrEqual(0)
    expect(transform).toBeGreaterThanOrEqual(0)
    expect(text).toBeLessThan(transform)
  })

  it('单击文字后不展开任何分组就能改内容、字号、对齐', () => {
    const ta = q<HTMLTextAreaElement>('textarea')!
    expect(ta.value).toBe('UI')
    // 字号与对齐是 TypographyControls 出的，可见标签就是它们的名字
    expect(host.textContent).toContain('字号')
    expect(host.textContent).toContain('对齐')
  })
})

describe('位置与尺寸：折叠成一行 + 现状摘要', () => {
  it('默认收起，摘要报出当前 X / Y', () => {
    const d = disclosure('位置与尺寸')
    expect(d.getAttribute('aria-expanded')).toBe('false')
    expect(d.textContent).toContain('43.3')
    expect(d.textContent).toContain('20.6')
    expect(q('[data-transform-folded]')).toBeNull()
  })

  it('展开后字段一条不少（X / Y / W / H / 旋转），能力没丢', async () => {
    await act(async () => disclosure('位置与尺寸').click())
    const body = q('[data-transform-folded]')!
    // 标签在框外（Session 2 的 GeometryGrid：`X [ 43.3  mm ]`），输入框的可达名
    // 由 NumberField 从前缀 + 单位推出来（「X (mm)」）——认可达名，不认相邻文字
    const names = [...body.querySelectorAll('input')].map((i) => i.getAttribute('aria-label') ?? '')
    expect(body.querySelectorAll('input').length).toBeGreaterThanOrEqual(5)
    expect(names.join(' ')).toContain('X')
    expect(body.textContent).toContain('旋转')
  })

  it('展开状态按键记忆（inspectorPrefs），不是组件里的 useState', async () => {
    await act(async () => disclosure('位置与尺寸').click())
    expect(useInspectorPrefs.getState().moreOpen['text-transform']).toBe(true)
  })
})

describe('背景 / 描边：与图内文字同一个 EffectToggle', () => {
  const openMore = async () => {
    const more = all('button[aria-expanded]').find((b) =>
      b.textContent?.startsWith('更多'),
    ) as HTMLButtonElement
    await act(async () => more.click())
  }

  it('关着时是「＋添加」入口，没有颜色控件', async () => {
    await openMore()
    const adds = all('[data-effect-add]')
    expect(adds.length).toBe(2)
    expect(adds.map((a) => a.textContent).join(' ')).toContain('添加背景')
    expect(adds.map((a) => a.textContent).join(' ')).toContain('添加描边')
  })

  it('点「添加背景」→ 变成真开关，颜色跟在同一行', async () => {
    await openMore()
    const add = all('[data-effect-add]').find((a) => a.textContent?.includes('添加背景'))!
    await act(async () => (add as HTMLButtonElement).click())
    expect(live().bg).toBe('#FFFFFF')
    const toggle = host.querySelector<HTMLElement>('[aria-label="背景"]')!
    expect(toggle.getAttribute('aria-checked')).toBe('true')
  })

  it('把开关关掉就清掉背景（不再需要另一颗「无」按钮）', async () => {
    await openMore()
    const add = all('[data-effect-add]').find((a) => a.textContent?.includes('添加背景'))!
    await act(async () => (add as HTMLButtonElement).click())
    const toggle = host.querySelector<HTMLElement>('[aria-label="背景"]')!
    await act(async () => toggle.click())
    expect(live().bg).toBeUndefined()
    expect(all('[data-effect-add]').some((a) => a.textContent?.includes('添加背景'))).toBe(true)
  })
})

describe('大小写：全名菜单，不是四个长得像的格子', () => {
  it('触发器只有一个，格子里不再有 AA / aa / Aa / A.', async () => {
    const more = all('button[aria-expanded]').find((b) =>
      b.textContent?.startsWith('更多'),
    ) as HTMLButtonElement
    await act(async () => more.click())
    expect(q('[data-case-menu]')).not.toBeNull()
    const glyphs = all('button').map((b) => b.textContent?.trim())
    expect(glyphs).not.toContain('AA')
    expect(glyphs).not.toContain('aa')
    expect(glyphs).not.toContain('Aa')
    expect(glyphs).not.toContain('A.')
  })
})
