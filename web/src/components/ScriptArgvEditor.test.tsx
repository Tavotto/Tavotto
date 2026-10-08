/**
 * 运行参数编辑器（T03）：默认收起；一项一个 token（不是一串要拆的字符）；空项合法；敏感项遮住。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ScriptArgvEditor } from '@/components/ScriptArgvEditor'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useScriptArgvStore } from '@/store/scriptArgvStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root

const mount = (disabled = false) => {
  act(() => {
    root.render(
      <TooltipProvider>
        <ScriptArgvEditor script="plot.py" disabled={disabled} />
      </TooltipProvider>,
    )
  })
}
const inputs = () => [...host.querySelectorAll<HTMLInputElement>('li input')]
const setValue = (el: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const button = (label: RegExp) =>
  [...host.querySelectorAll('button')].find((b) =>
    label.test(b.getAttribute('aria-label') ?? b.textContent ?? ''),
  )!

beforeEach(() => {
  useScriptArgvStore.getState().clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('ScriptArgvEditor', () => {
  it('默认收起，且没有参数时不占地方：<details> 没有 open', () => {
    mount()
    expect(host.querySelector('details')?.hasAttribute('open')).toBe(false)
    expect(inputs()).toHaveLength(0)
  })

  it('添加两项、改值：草稿里是两个独立 token，含空格的值不被拆', () => {
    mount()
    act(() => button(/添加参数|Add argument/).click())
    act(() => button(/添加参数|Add argument/).click())
    const [a, b] = inputs()
    setValue(a, '--label')
    setValue(b, 'two words ')
    expect(useScriptArgvStore.getState().drafts['plot.py'].tokens).toEqual([
      '--label',
      'two words ',
    ])
  })

  it('空项合法（空串是一个 token），删除只删被点的一项', () => {
    useScriptArgvStore.setState({
      drafts: { 'plot.py': { tokens: ['x', '', 'x'], sensitive: false } },
    })
    mount()
    expect(inputs().map((i) => i.value)).toEqual(['x', '', 'x'])
    act(() => button(/删除 plot.py 的第 2 个参数|Remove argument 2 of plot.py/).click())
    expect(useScriptArgvStore.getState().drafts['plot.py'].tokens).toEqual(['x', 'x'])
  })

  it('勾选敏感后各项变成密码输入框；没有参数时勾选框不可用', () => {
    mount()
    expect(host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.disabled).toBe(true)
    act(() => useScriptArgvStore.getState().addToken('plot.py', 'tok'))
    act(() => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click())
    expect(useScriptArgvStore.getState().drafts['plot.py'].sensitive).toBe(true)
    expect(inputs()[0].type).toBe('password')
  })

  it('运行中（disabled）不能改参数', () => {
    useScriptArgvStore.setState({ drafts: { 'plot.py': { tokens: ['a'], sensitive: false } } })
    mount(true)
    expect(inputs()[0].disabled).toBe(true)
    expect(button(/添加参数|Add argument/).disabled).toBe(true)
  })
})
