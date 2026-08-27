/**
 * 焦点救援（#138）：一次状态切换把焦点元素摘掉之后，键盘用户不能被困住。
 *
 * WebKit 的顺序聚焦起点跟着那个消失的元素一起没了，Tab 与 Shift+Tab 双向都不动
 * ——jsdom 复现不了那个浏览器行为，所以这里钉的是**判据本身**：什么时候接管、
 * 什么时候必须放手。浏览器侧的证据在 `e2e/keyboard-golden-path.spec.ts`
 * （抽掉这次修复 → webkit 红、chromium 绿）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { rescueFocus } from './focusRescue'

/** jsdom 里 getClientRects 恒空——alive() 要它非空，统一打桩成「看得见」。 */
function visible(el: HTMLElement): HTMLElement {
  el.getClientRects = (() => [{}] as unknown as DOMRectList) as HTMLElement['getClientRects']
  return el
}

const mk = (tag: string, id: string): HTMLElement => {
  const el = visible(document.createElement(tag))
  el.id = id
  if (tag === 'button' || tag === 'input') el.tabIndex = 0
  document.body.appendChild(el)
  return el
}

/** MutationObserver 是微任务派发的，让它跑完 */
const flush = () => new Promise((r) => setTimeout(r, 0))

let successor: HTMLElement

beforeEach(() => {
  document.body.innerHTML = ''
  successor = mk('button', 'successor')
})

afterEach(() => vi.useRealTimers())

describe('rescueFocus', () => {
  it('焦点元素被摘掉 → 交给接手者', async () => {
    const trigger = mk('button', 'trigger')
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    rescueFocus(() => successor)
    trigger.remove()
    await flush()

    expect(document.activeElement).toBe(successor)
  })

  it('焦点元素还在 → 一步都不动（不跟用户抢焦点）', async () => {
    const trigger = mk('button', 'trigger')
    trigger.focus()

    rescueFocus(() => successor)
    mk('div', 'noise')            // 制造 DOM 变动，但焦点元素没被摘
    await flush()

    expect(document.activeElement).toBe(trigger)
  })

  it('别人先接住了焦点 → 撤，不再接管', async () => {
    const trigger = mk('button', 'trigger')
    const other = mk('input', 'other')
    trigger.focus()

    rescueFocus(() => successor)
    other.focus()                 // 应用自己安排好了
    trigger.remove()
    await flush()

    expect(document.activeElement).toBe(other)
  })

  it('接手者不在（或看不见）→ 什么都不做，绝不把焦点扔到看不见的元素上', async () => {
    const trigger = mk('button', 'trigger')
    trigger.focus()
    const hidden = document.createElement('button')   // 没有 getClientRects
    document.body.appendChild(hidden)

    rescueFocus(() => hidden)
    trigger.remove()
    await flush()

    expect(document.activeElement).not.toBe(hidden)
  })

  it('超时之后不再接管：迟到的卸载不该在两秒后突然抢焦点', async () => {
    vi.useFakeTimers()
    const trigger = mk('button', 'trigger')
    trigger.focus()
    rescueFocus(() => successor, { within: 50 })
    vi.advanceTimersByTime(60)
    vi.useRealTimers()

    trigger.remove()
    await flush()

    expect(document.activeElement).not.toBe(successor)
  })
})
