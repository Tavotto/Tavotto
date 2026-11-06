import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it } from 'vitest'
import { Popover } from './Popover'

const OPEN = '打开'
let root: Root | undefined
let host: HTMLDivElement | undefined

afterEach(async () => {
  await act(async () => root?.unmount())
  await act(async () => new Promise<void>((resolve) => setTimeout(resolve, 0)))
  host?.remove()
  host = undefined
  root = undefined
})

const render = async (open: boolean, keepFocus = false) => {
  if (!host) {
    host = document.createElement('div')
    document.body.append(host)
    root = createRoot(host)
  }
  await act(async () => root!.render(
    <>
      <input data-probe="outside" />
      <Popover open={open} keepFocus={keepFocus} trigger={<button data-probe="trigger">{OPEN}</button>}>
        <input data-probe="inside" />
      </Popover>
    </>,
  ))
}

it('帮助浮层 keepFocus：打开和关闭都保留外部输入框的焦点', async () => {
  await render(false, true)
  const outside = host!.querySelector<HTMLInputElement>('[data-probe="outside"]')!
  outside.focus()
  await render(true, true)
  expect(document.activeElement).toBe(outside)
  await render(false, true)
  await act(async () => new Promise<void>((resolve) => setTimeout(resolve, 0)))
  expect(document.activeElement).toBe(outside)
})

it('外部输入框获得焦点关闭浮层：不抢回触发器', async () => {
  await render(true)
  expect(document.activeElement).toBe(document.querySelector('[data-probe="inside"]'))
  const outside = host!.querySelector<HTMLInputElement>('[data-probe="outside"]')!
  await act(async () => outside.focus())
  await render(false)
  await act(async () => new Promise<void>((resolve) => setTimeout(resolve, 0)))
  expect(document.activeElement).toBe(outside)
})
