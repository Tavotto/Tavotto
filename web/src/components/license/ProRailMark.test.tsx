import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ProRailMark } from './ProRailMark'

describe('ProRailMark', () => {
  it('没激活：就是图标集里的单色切面星，不带钛色', () => {
    const html = renderToStaticMarkup(<ProRailMark active={false} />)
    expect(html).not.toContain('data-pro-mark')
    expect(html).not.toContain('--color-pro-ti')
  })

  it('已激活：换成钛色两阶切面（两亮两暗 + 一圈边），颜色只走 --color-pro-ti-* 令牌', () => {
    const box = document.createElement('div')
    box.innerHTML = renderToStaticMarkup(<ProRailMark active />)
    const svg = box.querySelector('svg[data-pro-mark="titanium"]')
    expect(svg).not.toBeNull()
    const fills = [...svg!.querySelectorAll('path')].map((p) => p.getAttribute('fill'))
    expect(fills.filter((f) => f === 'var(--color-pro-ti-light)')).toHaveLength(2)
    expect(fills.filter((f) => f === 'var(--color-pro-ti-dark)')).toHaveLength(2)
    expect(svg!.querySelector('path[stroke="var(--color-pro-ti-edge)"]')).not.toBeNull()
  })
})
