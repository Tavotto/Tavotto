/**
 * 画布页（审计 T31）：宽、高与横竖交换同一行；一次只展开一组；开关行不走
 * 44px 标签列（「对齐参考线」在 320px 属性栏里不再折行——折行本身 jsdom 量
 * 不到，这里钉的是结构：开关的标签没有固定宽度、文字占满剩余宽度）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import { CanvasPage } from './CanvasPage'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_canvaspage')
  useUiStore.setState({ snapEnabled: true, showGrid: true })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() =>
    root.render(
      <TooltipProvider>
        <CanvasPage />
      </TooltipProvider>,
    ),
  )
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const disclosure = (title: string) =>
  [...container.querySelectorAll<HTMLButtonElement>('button[aria-expanded]')].find((b) =>
    b.textContent?.includes(title),
  )!

describe('CanvasPage', () => {
  it('宽、高与横竖交换在同一行', () => {
    const row = container.querySelector('[data-page-size-row]')!
    expect(row.querySelectorAll('input').length).toBe(2)
    const swap = row.querySelector<HTMLButtonElement>('button[aria-label="横竖交换"]')
    expect(swap).not.toBeNull()
    const before = useDocumentStore.getState().doc.page
    expect(before.w).not.toBe(before.h)
    act(() => swap!.click())
    const after = useDocumentStore.getState().doc.page
    expect([after.w, after.h]).toEqual([before.h, before.w])
  })

  it('一次只展开一组：打开吸附时背景收起', () => {
    act(() => disclosure('背景').click())
    expect(disclosure('背景').getAttribute('aria-expanded')).toBe('true')
    act(() => disclosure('吸附').click())
    expect(disclosure('吸附').getAttribute('aria-expanded')).toBe('true')
    expect(disclosure('背景').getAttribute('aria-expanded')).toBe('false')
    // 再点同一组就收起
    act(() => disclosure('吸附').click())
    expect(disclosure('吸附').getAttribute('aria-expanded')).toBe('false')
  })

  it('收起时也报得出当前尺寸与网格状态', () => {
    // 「查看辅助」收着：摘要里要带网格间距，不能只说一个「网格」
    expect(disclosure('查看辅助').getAttribute('aria-expanded')).toBe('false')
    const gridSize = useUiStore.getState().gridSize
    expect(disclosure('查看辅助').textContent).toContain(`网格 ${gridSize} mm`)
    // 页面尺寸不折叠，但标题行常驻当前尺寸
    const page = useDocumentStore.getState().doc.page
    const header = [...container.querySelectorAll('section')].find((s) =>
      s.querySelector('h3')?.textContent?.includes('页面尺寸'),
    )!
    expect(header.querySelector('header')!.textContent).toContain(String(page.w / 10))
  })

  it('开关行：标签不占固定宽度的标签列，整行可点', () => {
    act(() => disclosure('吸附').click())
    const rows = [...container.querySelectorAll<HTMLLabelElement>('[data-toggle-row]')]
    const guides = rows.find((r) => r.textContent?.includes('对齐参考线'))
    expect(guides).toBeDefined()
    expect(guides!.tagName).toBe('LABEL')
    const label = guides!.querySelector('span')!
    expect(label.style.width).toBe('')
    expect(label.className).toContain('min-w-0')
    const before = useUiStore.getState().snapToGuides
    act(() => label.click())
    expect(useUiStore.getState().snapToGuides).toBe(!before)
  })
})
