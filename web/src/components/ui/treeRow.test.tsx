import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { TreeCount } from './TreeRow'

describe('TreeCount', () => {
  it('占 28px 的槽并居中：与行尾 ⋯（28px IconButton）同宽同右缘，中心线对齐（G4）', () => {
    const html = renderToStaticMarkup(<TreeCount>12</TreeCount>)
    expect(html).toMatch(/\bw-7\b/)
    expect(html).toMatch(/\bjustify-center\b/)
    expect(html).not.toMatch(/\bpl-2\b/)
  })
})
