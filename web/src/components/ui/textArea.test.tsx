/**
 * `TextArea` 的高度跟着内容走（2026-09-14 二审 A1）。
 *
 * 缺陷的形状：调用点 `rows={text.split('\n').length}` 只数硬换行，一行源码两行显示的
 * 图例名第二行被裁掉。jsdom 不排版（scrollHeight 恒 0、行高读不出），所以这里把
 * scrollHeight 装成可控的数，只验「量到多少就长到多少、封顶后转内部滚动」这条契约。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TextArea } from './Input'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const LABEL = 'text'
const METRICS = {
  lineHeight: '16px',
  paddingTop: '4px',
  paddingBottom: '4px',
  borderTopWidth: '1px',
  borderBottomWidth: '1px',
} as const

const show = (value: string, maxRows = 4) =>
  act(async () =>
    root.render(
      <TextArea aria-label={LABEL} value={value} maxRows={maxRows} onChange={() => {}} style={METRICS} />,
    ),
  )

const field = () => host.querySelector('textarea') as HTMLTextAreaElement

const fakeScrollHeight = (el: HTMLTextAreaElement, px: number) =>
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => px })

describe('TextArea 高度跟着内容走', () => {
  it('没有 JS 量测结果时只是一行起点（rows=1），不再由硬换行数决定', async () => {
    await show('a\nb\nc')
    expect(field().rows).toBe(1)
  })

  it('内容需要两行就长到两行；超过 maxRows 封顶并转为内部滚动', async () => {
    await show('a')
    const el = field()
    // 两行：16 × 2 + 上下内边距 8 + 边框 2 = 42
    fakeScrollHeight(el, 42)
    await show('ab')
    expect(el.style.height).toBe('42px')
    expect(el.style.overflowY).toBe('hidden')
    // 六行：超过 4 行上限（16 × 4 + 10 = 74），封顶并允许滚动
    fakeScrollHeight(el, 106)
    await show('abc')
    expect(el.style.height).toBe('74px')
    expect(el.style.overflowY).toBe('auto')
  })

  it('量不到（scrollHeight 为 0）就什么都不写，保持起点', async () => {
    await show('a')
    await show('ab')
    expect(field().style.height).toBe('')
  })
})
