/**
 * `ui/Select` 触发器的两条底线（#145 迁移时暴露的）。
 *
 * 1. **弹层关着时键盘用户也得看得见焦点在哪。** `outline-none` 只是关掉浏览器
 *    默认那圈；不补替代品就是把焦点指示整个删掉。原生 `<select>` 自带焦点环，
 *    迁到共享控件时缺了它就是一条 a11y 回归——而且是**所有**用到 Select 的地方
 *    一起回归。
 * 2. **值必须能被截断。** 触发器里的值可能是用户起的名字（接口标签后端允许 60
 *    字），撑出去会把它所在的那一行挤坏。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Select } from './Select'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <Select
        value="a"
        onChange={() => {}}
        ariaLabel="示例"
        options={[
          { value: 'a', label: '一个相当长的接口标签，长到足以把整行撑出去' },
          { value: 'b', label: 'b' },
        ]}
      />,
    )
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('Select 触发器', () => {
  const trigger = () => host.querySelector('[role="combobox"]') as HTMLElement

  it('有可见的键盘焦点指示（focus-visible 的替代品）', () => {
    const cls = trigger().className
    expect(cls, 'outline-none 关掉了默认焦点环，却没有补替代品').toContain(
      'focus-visible:focus-ring',
    )
  })

  it('长值会被截断，不会把整行撑出去', () => {
    const holder = trigger().querySelector('.truncate') as HTMLElement
    expect(holder, '值外面没有可截断的容器').toBeTruthy()
    // flex 子项要 min-w-0 才缩得下去，少了它 truncate 不生效
    expect(holder.className).toContain('min-w-0')
    expect(holder.textContent).toContain('接口标签')
  })
})
