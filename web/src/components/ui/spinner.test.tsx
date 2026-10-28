/**
 * Spinner（全产品唯一的加载图形）与 IconSlot（图标 + 文字行的唯一对齐手段）的契约。
 * 规则正文在 `docs/rules/frontend/ui-visual-discipline.md` 图标节；门禁（不许 LoaderCircle 式的手写转圈、
 * 不许 margin-top 凑对齐）在 `iconography.test.tsx`。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'

import { ICON_SIZE, IconSlot } from './Icon'
import { Spinner } from './Spinner'

async function mount(node: React.ReactNode) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  await act(async () => root.render(node))
  return { host, unmount: () => act(async () => root.unmount()) }
}

describe('Spinner', () => {
  it('淡底环 + 短弧；转，减少动态效果时不转；装饰性默认 aria-hidden', async () => {
    const { host, unmount } = await mount(<Spinner size={ICON_SIZE.md} />)
    const svg = host.querySelector('svg')!
    expect(svg.classList.contains('icon-spinner')).toBe(true)
    expect(svg.getAttribute('class')).toContain('animate-spin')
    expect(svg.getAttribute('class')).toContain('motion-reduce:animate-none')
    expect(svg.getAttribute('aria-hidden')).toBe('true')
    const circles = host.querySelectorAll('circle')
    expect(circles).toHaveLength(2)
    expect(circles[0].getAttribute('opacity')).toBe('0.25') // 底环
    expect(circles[1].getAttribute('stroke-dasharray')).toMatch(/^15\.\d+ 56\.\d+$/) // 约 100° 的弧
    await unmount()
  })

  it('描边：16px 以上 2.5/24（16px 上 1.67px），小档位不低于 1.4px', async () => {
    for (const [px, minPx] of [
      [ICON_SIZE.lg, 2.5 * (20 / 24)],
      [ICON_SIZE.md, 2.5 * (16 / 24)],
      [ICON_SIZE.sm, 1.4],
      [ICON_SIZE.xs, 1.4],
    ] as const) {
      const { host, unmount } = await mount(<Spinner size={px} />)
      const sw = Number(host.querySelector('svg')!.getAttribute('stroke-width'))
      expect((sw * px) / 24, `${px}px 档`).toBeGreaterThanOrEqual(minPx - 1e-6)
      await unmount()
    }
  })

  it('给了 aria-label 就是有名字的图片，不再 aria-hidden', async () => {
    const { host, unmount } = await mount(<Spinner aria-label="加载中" />)
    const svg = host.querySelector('svg')!
    expect(svg.getAttribute('role')).toBe('img')
    expect(svg.hasAttribute('aria-hidden')).toBe(false)
    await unmount()
  })
})

describe('IconSlot', () => {
  it('槽高 = 行高（h-lh），图标居中，不被挤扁；className 可覆盖槽高', async () => {
    const { host, unmount } = await mount(
      <IconSlot className="h-5 text-sm">
        <Spinner size={ICON_SIZE.md} />
      </IconSlot>,
    )
    const slot = host.firstElementChild!
    expect(slot.className).toContain('inline-flex')
    expect(slot.className).toContain('items-center')
    expect(slot.className).toContain('shrink-0')
    expect(slot.className).toContain('h-5')
    expect(slot.className).not.toContain('h-lh') // 被覆盖，不是并存
    await unmount()
    const m = await mount(<IconSlot />)
    expect(m.host.firstElementChild!.className).toContain('h-lh')
    await m.unmount()
  })
})
