/**
 * 摘要行（`ui/SummaryRow`，2026-10-01 属性栏重设计）的契约：
 *   1. 一行 = 名字 + 当前值 + ›，整行是一个按钮（aria-expanded）；
 *   2. 右值只在收起时出现，展开后收起（控件本身说得更准）；没有值就不画；
 *   3. 收起时内容不挂载（没有可聚焦的控件藏在里面），点开才有；
 *   4. 读屏的可达名里名字与值之间有分隔，不念成「背景#FFFFFF」。
 */
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { DURATION } from '@/lib/motion'
import { SummaryRow } from './SummaryRow'

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

function Demo({ value }: { value?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <SummaryRow label="小刻度" value={value} open={open} onToggle={() => setOpen((v) => !v)} data-fold="minor">
      <input aria-label="长度" />
    </SummaryRow>
  )
}

const button = () => host.querySelector<HTMLButtonElement>('[data-summary-row] > button')!
const valueNode = () => host.querySelector('[data-summary-value]')

describe('SummaryRow', () => {
  it('收起：名字 + 右值 + ›，内容不在 DOM 里；点开：控件出现、右值收起', async () => {
    await act(async () => root.render(<Demo value="不显示" />))
    expect(button().getAttribute('aria-expanded')).toBe('false')
    expect(button().textContent).toContain('小刻度')
    expect(valueNode()?.textContent).toBe('不显示')
    expect(button().querySelector('svg')).toBeTruthy()
    expect(host.querySelector('input')).toBeNull()

    await act(async () => button().click())
    expect(button().getAttribute('aria-expanded')).toBe('true')
    expect(host.querySelector('input[aria-label="长度"]')).toBeTruthy()
    expect(valueNode()).toBeNull()

    // 再点收起：退场动画走完后控件卸载
    await act(async () => button().click())
    await act(async () => {
      await new Promise((r) => setTimeout(r, DURATION.exit + 50))
    })
    expect(host.querySelector('input')).toBeNull()
    expect(valueNode()?.textContent).toBe('不显示')
  })

  it('没有值（undefined / 空串）就不画右值，不留空壳', async () => {
    for (const v of [undefined, '']) {
      await act(async () => root.render(<Demo value={v} />))
      expect(valueNode(), String(v)).toBeNull()
    }
  })

  it('可达名里名字与值之间有分隔：「小刻度, 不显示」，值不贴在名字后面', async () => {
    await act(async () => root.render(<Demo value="不显示" />))
    expect(button().textContent).toBe('小刻度, 不显示')
  })

  it('额外属性落在行的外壳上（e2e 的稳定锚点 data-fold）；triggerProps 的 data-* 落在开关按钮上', async () => {
    await act(async () => root.render(<Demo />))
    expect(host.querySelector('[data-fold="minor"] > button')).toBe(button())
    await act(async () =>
      root.render(
        <SummaryRow label="x" open={false} onToggle={() => {}} triggerProps={{ 'data-anchor': '' }}>
          y
        </SummaryRow>,
      ),
    )
    expect(host.querySelector('[data-anchor]')).toBe(button())
  })
})
