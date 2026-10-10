/**
 * ⌥⌘S 的命名小框（2026-10-07 设计审计 §10.1）：有标题（对话框的可达名指向它）；焦点离开小框就关
 * （与点外面同一条规则）；焦点还在小框里不关。
 * 主语：`data-timeline-quick-name` 在不在、`aria-labelledby` 指向的节点文字；开关认 `timelineStore.namingOpen`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { NamedNodeQuickBox } from './NamedNodeQuickBox'
import { useTimelineStore } from '@/store/timelineStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
let outside: HTMLButtonElement
const box = () => document.querySelector<HTMLElement>('[data-timeline-quick-name]')

beforeEach(async () => {
  outside = document.createElement('button')
  document.body.appendChild(outside)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useTimelineStore.getState().setNamingOpen(true)
  await act(async () => root.render(<NamedNodeQuickBox />))
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  useTimelineStore.getState().setNamingOpen(false)
})

describe('命名小框', () => {
  it('有标题：对话框的可达名指向它', () => {
    const id = box()!.getAttribute('aria-labelledby')!
    expect(document.getElementById(id)?.textContent).toBe('把现在存为命名节点')
  })

  it('焦点在小框里挪动：不关', async () => {
    const input = box()!.querySelector('input')!
    input.focus()
    const btn = box()!.querySelector('button')!
    await act(async () => btn.focus())
    expect(box()).not.toBeNull()
  })

  it('焦点去了小框外面的控件：关', async () => {
    box()!.querySelector('input')!.focus()
    await act(async () => outside.focus())
    expect(useTimelineStore.getState().namingOpen).toBe(false)
    expect(box()).toBeNull()
  })
})
