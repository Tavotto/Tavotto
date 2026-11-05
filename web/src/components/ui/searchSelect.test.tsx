/** 带搜索框的下拉（ADR 0169 修订）：筛选、键盘、收起时不渲染选项。 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { filterOptions, SearchSelect } from './SearchSelect'
import type { SelectOption } from './Select'

const NAMES = ['Arial', 'Times New Roman', 'SimSun', 'Microsoft YaHei', 'Consolas', 'Georgia', 'Verdana']
const OPTIONS: SelectOption[] = NAMES.map((n) => ({
  value: n,
  label: n,
  searchText: n === 'SimSun' ? 'SimSun 宋体' : undefined,
}))

describe('filterOptions', () => {
  it.each([
    ['times', ['Times New Roman']],
    ['TIMESNEW', ['Times New Roman']],
    ['宋体', ['SimSun']],
    ['  ', NAMES],
    ['没有这个', []],
  ])('%s', (q, want) => {
    expect(filterOptions(OPTIONS, q).map((o) => o.value)).toEqual(want)
  })
})

describe('SearchSelect', () => {
  let root: Root | undefined
  let host: HTMLElement | undefined
  afterEach(async () => {
    await act(async () => root?.unmount())
    host?.remove()
  })

  const mount = async (onChange: (v: string) => void, options = OPTIONS) => {
    host = document.createElement('div')
    document.body.append(host)
    root = createRoot(host)
    await act(async () =>
      root!.render(
        <SearchSelect value="Arial" onChange={onChange} options={options} searchLabel="搜索字体" emptyLabel="没有匹配的字体" ariaLabel="字体" />,
      ),
    )
    return host.querySelector('[data-select-trigger]') as HTMLElement
  }
  const openIt = async (trigger: HTMLElement) => {
    await act(async () => trigger.click())
    return document.querySelector('[role=searchbox]') as HTMLInputElement
  }
  const type = async (input: HTMLInputElement, v: string) => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      set.call(input, v)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const press = (el: Element, key: string) =>
    act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true })))
  const rows = () => [...document.querySelectorAll('[role=option]')].map((r) => r.textContent)

  it('收起时不渲染选项', async () => {
    await mount(() => {})
    expect(rows()).toEqual([])
  })

  it('打开后焦点在搜索框，打字筛选，回车选中第一条', async () => {
    const onChange = vi.fn()
    const input = await openIt(await mount(onChange))
    expect(document.activeElement).toBe(input)
    await type(input, 'times')
    expect(rows()).toEqual(['Times New Roman'])
    await press(input, 'Enter')
    expect(onChange).toHaveBeenCalledWith('Times New Roman')
    expect(document.querySelector('[role=searchbox]')).toBeNull()
  })

  it('↓ 从当前值往下走，到底绕回', async () => {
    const onChange = vi.fn()
    const input = await openIt(await mount(onChange))
    await press(input, 'ArrowDown')
    await press(input, 'Enter')
    expect(onChange).toHaveBeenCalledWith('Times New Roman')
  })

  it('筛空时说一句，回车不选', async () => {
    const onChange = vi.fn()
    const input = await openIt(await mount(onChange))
    await type(input, 'zzz')
    expect(document.body.textContent).toContain('没有匹配的字体')
    await press(input, 'Enter')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('选中当前值不算修改', async () => {
    const onChange = vi.fn()
    const input = await openIt(await mount(onChange))
    await press(input, 'Enter')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('输入法组字中：回车与方向键不抢（回车是确认候选词）', async () => {
    const onChange = vi.fn()
    const input = await openIt(await mount(onChange))
    const composing = (key: string) =>
      act(async () => void input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, isComposing: true })))
    await composing('ArrowDown')
    await composing('Enter')
    expect(onChange).not.toHaveBeenCalled()
    expect(document.querySelector('[role=searchbox]')).not.toBeNull()
    // 组字中的 ↓ 没动高亮：组字结束后回车选的仍是当前值（Arial → 不算修改）
    await press(input, 'Enter')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('选项少时没有搜索框', async () => {
    await openIt(await mount(() => {}, OPTIONS.slice(0, 3)))
    expect(document.querySelector('[role=searchbox]')).toBeNull()
    expect(rows()).toHaveLength(3)
  })
})
