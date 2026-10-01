import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { KeyCaps } from './Kbd'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement | null = null

/** 读屏会读到的文字：跳过 aria-hidden 子树（sr-only 的照常算） */
function spoken(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? ''
  if (!(node instanceof HTMLElement) || node.getAttribute('aria-hidden') === 'true') return ''
  return [...node.childNodes].map(spoken).join(' ').replace(/\s+/g, ' ').trim()
}

async function render(keys: string) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root!.render(<KeyCaps keys={keys} />))
  return host
}

afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  await setLocale('zh-CN')
})

describe('KeyCaps 的读屏文字', () => {
  it('替代组合之间读出「或」，不是连成一串键', async () => {
    const el = await render('Ctrl+Z / ⇧Ctrl+Z')
    expect(spoken(el)).toBe('Ctrl Z 或 ⇧ Ctrl Z')
    // 视觉上仍是「/」，但它对读屏隐藏
    expect(el.querySelector('[aria-hidden=true]')?.textContent).toBe('/')
  })

  it('英文界面读 or', async () => {
    await setLocale('en-US')
    const el = await render('Ctrl+Z / ⇧Ctrl+Z')
    expect(spoken(el)).toContain(' or ')
  })

  it('单组键位没有分隔', async () => {
    const el = await render('Ctrl+S')
    expect(spoken(el)).toBe('Ctrl S')
    expect(el.querySelector('.sr-only')).toBeNull()
  })
})
