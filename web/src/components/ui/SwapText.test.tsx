/**
 * 原位换字：DOM 文本永远是当前的，旧句只留在 `data-ghost`（生成内容）里退场。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SwapText } from './SwapText'

let host: HTMLDivElement
let root: Root | null = null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(async () => {
  await act(async () => root?.unmount())
  root = null
  host.remove()
  vi.restoreAllMocks()
})

async function mount(text: string) {
  root = createRoot(host)
  await act(async () => root!.render(<SwapText text={text} data-probe="" textClassName="text-ink-3" />))
}
async function update(text: string) {
  await act(async () => root!.render(<SwapText text={text} data-probe="" textClassName="text-ink-3" />))
}
const box = () => host.querySelector<HTMLElement>('.swap-text')!
// jsdom 没有 AnimationEvent；React 只读 event.animationName
const animationEnd = (animationName: string) =>
  Object.assign(new Event('animationend', { bubbles: true }), { animationName })
const inner = () => host.querySelector<HTMLElement>('[data-probe]')!

describe('SwapText', () => {
  it('首次渲染不播：没有 ghost；字的类与 data-* 落在里层那句字上', async () => {
    await mount('正在改写脚本…')
    expect(box().dataset.ghost).toBeUndefined()
    expect(inner().textContent).toBe('正在改写脚本…')
    expect(inner().classList.contains('text-ink-3')).toBe(true)
    expect(box().textContent, '外层只有这一句，没有第二份文本').toBe('正在改写脚本…')
  })

  it('换字：DOM 文本立刻是新字，旧句只在 data-ghost 里；进场播完 ghost 清掉', async () => {
    await mount('正在改写脚本…')
    await update('修改已写入脚本')
    expect(box().textContent, 'textContent 只有新字——读屏与 e2e 不会念到旧句').toBe('修改已写入脚本')
    expect(box().dataset.ghost).toBe('正在改写脚本…')
    // 退场的 animationend 不算数（它先结束）
    await act(async () => {
      box().dispatchEvent(animationEnd('swap-out'))
    })
    expect(box().dataset.ghost).toBe('正在改写脚本…')
    await act(async () => {
      inner().dispatchEvent(animationEnd('swap-in'))
    })
    expect(box().dataset.ghost).toBeUndefined()
  })

  it('连换两句：ghost 跟着换成最近那句，里层 span 是新节点（进场从头来）', async () => {
    await mount('一')
    await update('二')
    const second = inner()
    await update('三')
    expect(box().dataset.ghost).toBe('二')
    expect(inner()).not.toBe(second)
    expect(box().textContent).toBe('三')
  })

  it('同一句再渲染一次：不播、没有 ghost', async () => {
    await mount('一')
    await update('一')
    expect(box().dataset.ghost).toBeUndefined()
  })

  it('reduced-motion：直接换字，连 ghost 都没有', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce') }) as MediaQueryList)
    await mount('一')
    await update('二')
    expect(box().textContent).toBe('二')
    expect(box().dataset.ghost).toBeUndefined()
    vi.unstubAllGlobals()
  })
})
