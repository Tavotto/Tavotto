/**
 * 画布上的位图面板取哪个地址（issue #534）。
 *
 * `/api/file` 回的是**原字节**：PNG / JPEG 浏览器解得开，TIFF 在 Chromium / WebView2 里是一张裂图。
 * 浏览器解不开的位图走分档渲染（后端转 PNG），判据是「浏览器能力的允许清单」——新加一种素材格式时
 * 默认落在转 PNG 这条安全的路上。
 */
import { describe, expect, it } from 'vitest'

import { extOf, panelSrc } from '@/lib/api'

describe('panelSrc 的位图分支', () => {
  it.each(['a.png', 'b.jpg', 'c.JPEG', 'sub/dir/d.PNG'])('%s：浏览器解得开，走原文件', (id) => {
    expect(panelSrc(id, 'raster', 800, 5)).toContain('/api/file?id=')
  })

  it.each(['a.tif', 'b.tiff', 'c.TIF', 'sub/dir/d.Tiff', 'win\\dir\\e.tif'])(
    '%s：浏览器解不开，走分档渲染（PNG）',
    (id) => {
      const url = panelSrc(id, 'raster', 800, 5)!
      expect(url).toContain('/api/render?id=')
      expect(url).toContain('&w=800')
      expect(url).toContain('&m=5')
    },
  )

  it('矢量与未知形态不受影响', () => {
    expect(panelSrc('a.pdf', 'pdf', 400)).toContain('/api/render?id=')
    expect(panelSrc('a.tif', 'mystery', 400)).toBeNull()
  })
})

describe('extOf', () => {
  it.each([
    ['a.tif', 'tif'],
    ['A.TIFF', 'tiff'],
    ['dir.v2/fig', ''],
    ['.hidden', ''],
    ['dir\\x.PNG', 'png'],
  ])('%s → %s', (id, ext) => {
    expect(extOf(id)).toBe(ext)
  })
})
