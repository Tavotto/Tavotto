/** `GridOption.disabled`：禁用的格子不可点、方向键漫游跳过；全部禁用方向键不动；不传 = 与以前一样。 */
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { OptionGrid, type GridOption } from './OptionGrid'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const opts = (dis: Record<string, boolean> = {}): GridOption[] => ['a', 'b', 'c', 'd'].map((v) => ({ value: v, label: v.toUpperCase(), disabled: dis[v] }))
const onChange = vi.fn()
function Harness({ options, start = 'a' }: { options: GridOption[]; start?: string }) {
  const [v, setV] = useState(start)
  return <OptionGrid value={v} options={options} columns={4} ariaLabel="g" onChange={(x) => { onChange(x); setV(x) }} />
}
const mount = async (options: GridOption[], start?: string) => {
  onChange.mockReset()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider><Harness options={options} start={start} /></TooltipProvider>))
}
const cell = (v: string) => host.querySelector<HTMLButtonElement>(`[data-value="${v}"]`)!
const key = async (k: string) =>
  act(async () => {
    host.querySelector('[data-option-grid]')!.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
    await new Promise((r) => requestAnimationFrame(r))
  })

describe('OptionGrid 的 disabled 格', () => {
  it('不传 disabled：与以前一样（没有 disabled 属性，方向键逐格走）', async () => {
    await mount(opts())
    expect([...host.querySelectorAll('button')].every((b) => !b.disabled)).toBe(true)
    await key('ArrowRight')
    expect(onChange).toHaveBeenLastCalledWith('b')
  })

  it('禁用的格子带 disabled、点不动', async () => {
    await mount(opts({ b: true }))
    expect(cell('b').disabled).toBe(true)
    await act(async () => cell('b').click())
    expect(onChange).not.toHaveBeenCalled()
  })

  it('方向键跳过禁用的格子：向右越过 b 到 c，向左回到 a；越过尽头的就不动', async () => {
    await mount(opts({ b: true }))
    await key('ArrowRight')
    expect(onChange).toHaveBeenLastCalledWith('c')
    await key('ArrowLeft')
    expect(onChange).toHaveBeenLastCalledWith('a')
    onChange.mockClear()
    await mount(opts({ c: true, d: true }), 'b')
    await key('ArrowRight')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('全部禁用：方向键什么都不做', async () => {
    await mount(opts({ a: true, b: true, c: true, d: true }))
    await key('ArrowRight')
    await key('ArrowLeft')
    expect(onChange).not.toHaveBeenCalled()
  })
})
