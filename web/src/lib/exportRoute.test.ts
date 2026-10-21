/**
 * 「面板在本次导出里嵌入的是不是磁盘位图」严格同源对的**前端那一侧**
 * （`embedsDiskBitmap` ↔ 后端 `app._resolve_panel_source` / `rendercore.sources.needs_execution`）。
 *
 * 向量 `tests/golden/export_route_vectors.json` 由 `tests/test_export_route_vectors.py` 拿后端
 * 真实代码求期望、这里拿 `embedsDiskBitmap` 求结论，两侧各跑一遍同一份。改判据时两侧与向量一起改。
 */
import { describe, expect, it } from 'vitest'
import file from '../../../tests/golden/export_route_vectors.json'
import { originalArtifactOverrides } from './artifactValidation'
import {
  copiesSourceVerbatim,
  embedsDiskBitmap,
  epsFromRoute,
  panelRouteOf,
  unavailablePanels,
  type ExportRoute,
  type PanelRoute,
} from './exportRoute'
import { epsAvailability } from './exportRequest'
import type { PanelInfo } from './api'
import type { PanelObject, PanelOverride } from '@/types/document'

interface Vector {
  name: string
  scope: 'canvas' | 'original'
  file_kind: 'raster' | 'pdf' | 'runtime'
  has_script: boolean
  guard: 'none' | 'valid' | 'invalid'
  overrides: PanelOverride[]
  backend_overrides: PanelOverride[] | null
  embeds_disk_bitmap: boolean
  formats: string[]
}
const vectors = file.vectors as unknown as Vector[]

const fileId = (v: Vector) => (v.file_kind === 'raster' ? 'r1.png' : v.file_kind === 'pdf' ? 'p1.pdf' : 'runtime:x#1')
const GUARDS = {
  valid: { version: 1, requiredFrame: 'figsize', sourceId: 'r1.png', bytesSha256: 'a'.repeat(64), sizeBytes: 10 },
  invalid: { version: 2, requiredFrame: 'figsize', sourceId: 'r1.png', bytesSha256: 'a'.repeat(64), sizeBytes: 10 },
}
function panelOf(v: Vector): PanelObject {
  const o = {
    id: 'p1', type: 'panel', fileId: fileId(v), fileKind: v.file_kind, nativeW: 160, nativeH: 120,
    overrides: v.overrides, x: 0, y: 0, w: 160, h: 120,
  } as unknown as PanelObject
  if (v.guard !== 'none') (o as { artifactValidation?: unknown }).artifactValidation = GUARDS[v.guard]
  return o
}
const assetsOf = (v: Vector): Record<string, PanelInfo> =>
  ({ [fileId(v)]: { id: fileId(v), mtime: 1, ...(v.has_script ? { script: 's.py' } : {}) } as unknown as PanelInfo })

describe('embedsDiskBitmap：与共享向量一致', () => {
  it('向量覆盖两个范围、三种文件、三种守卫', () => {
    expect(new Set(vectors.map((v) => v.scope))).toEqual(new Set(['canvas', 'original']))
    expect(new Set(vectors.map((v) => v.file_kind))).toEqual(new Set(['raster', 'pdf', 'runtime']))
    expect(new Set(vectors.map((v) => v.guard))).toEqual(new Set(['none', 'valid', 'invalid']))
    expect(new Set(vectors.map((v) => v.embeds_disk_bitmap))).toEqual(new Set([true, false]))
  })

  it.each(vectors.map((v) => [v.name, v] as const))('%s', (_n, v) => {
    expect(embedsDiskBitmap(panelOf(v), assetsOf(v), v.scope, v.formats)).toBe(v.embeds_disk_bitmap)
  })

  // 向量里的 backend_overrides 是「这个范围实际发给后端的 overrides」：原图范围已经过守卫。
  // 后端那侧拿它当输入，所以这里要钉住它确实就是前端发出去的那份。
  it.each(vectors.filter((v) => v.scope === 'original').map((v) => [v.name, v] as const))(
    '原图范围发给后端的 overrides：%s',
    (_n, v) => {
      if (v.backend_overrides === null) {
        expect(() => originalArtifactOverrides(panelOf(v))).toThrow()
      } else {
        expect(originalArtifactOverrides(panelOf(v))).toEqual(v.backend_overrides)
      }
    },
  )
  it.each(vectors.filter((v) => v.scope === 'canvas').map((v) => [v.name, v] as const))(
    '画布范围原样发 overrides：%s',
    (_n, v) => expect(v.backend_overrides).toEqual(v.overrides),
  )
})

// ---------------------------- 读后端 route（#1209 / A2b） ----------------------------

const pr = (over: Partial<PanelRoute> = {}): PanelRoute => ({
  source: 'disk',
  embeds_disk_bitmap: true,
  formats: {},
  ...over,
})
const routeOf = (
  scope: 'canvas' | 'original',
  formats: string[],
  panels: Record<string, PanelRoute>,
): ExportRoute => ({ scope, formats, panels })

describe('有 route：只读它，不推算', () => {
  it.each(vectors.filter((v) => v.backend_overrides !== null).map((v) => [v.name, v] as const))(
    'route 与旧判据相反时以 route 为准：%s',
    (_n, v) => {
      const flipped = routeOf(v.scope, v.formats, {
        [fileId(v)]: pr({ embeds_disk_bitmap: !v.embeds_disk_bitmap }),
      })
      expect(embedsDiskBitmap(panelOf(v), assetsOf(v), v.scope, v.formats, flipped)).toBe(!v.embeds_disk_bitmap)
    },
  )

  it('route 对不上（范围 / 格式不同 / 实例有歧义 / 没有这张图）就是没有，走退路', () => {
    const v = vectors.find((x) => x.scope === 'original' && x.file_kind === 'raster')!
    const o = panelOf(v)
    const legacy = embedsDiskBitmap(o, assetsOf(v), 'original', ['pdf'])
    const other = pr({ embeds_disk_bitmap: !legacy })
    const id = fileId(v)
    expect(panelRouteOf(routeOf('canvas', ['pdf'], { [id]: other }), 'original', id, ['pdf'])).toBeNull()
    expect(panelRouteOf(routeOf('original', ['pdf', 'eps'], { [id]: other }), 'original', id, ['pdf'])).toBeNull()
    expect(panelRouteOf(routeOf('original', ['pdf'], { [id]: other, [`${id}#2`]: other }), 'original', id, ['pdf'])).toBeNull()
    expect(panelRouteOf(routeOf('original', ['pdf'], {}), 'original', id, ['pdf'])).toBeNull()
    expect(panelRouteOf(null, 'original', id, ['pdf'])).toBeNull()
    expect(embedsDiskBitmap(o, assetsOf(v), 'original', ['pdf'], routeOf('original', ['pdf', 'eps'], { [id]: other }))).toBe(legacy)
  })

  it('画布范围：route 说画不出的面板被列出（脚本被移走的编辑过的位图）', () => {
    const r = routeOf('canvas', ['pdf'], {
      'a.png': pr({ source: 'unavailable', embeds_disk_bitmap: false }),
      'b.png#2': pr({ source: 'unavailable', embeds_disk_bitmap: false }),
      'c.png': pr(),
    })
    expect(unavailablePanels(r, ['pdf'])).toEqual(['a.png', 'b.png'])
    expect(unavailablePanels(r, ['png'])).toEqual([]) // 格式对不上 = 不是这次的答案
    expect(unavailablePanels(null, ['pdf'])).toEqual([])
    expect(unavailablePanels({ ...r, scope: 'original' }, ['pdf'])).toEqual([])
  })

  it('照抄源位图：route 说重画就不照抄；没有 route 的退路把 EPS 重画也算进去', () => {
    const v = vectors.find((x) => x.scope === 'original' && x.file_kind === 'raster' && x.has_script && x.overrides.length === 0 && x.guard === 'none')!
    const o = panelOf(v)
    const base = { scope: 'original' as const, specIsRaster: true, panel: o, assets: assetsOf(v), guardOk: true }
    expect(copiesSourceVerbatim({ ...base, formats: ['pdf'] })).toBe(true)
    // 旧的对话框内联判据在这里恒为 true；退路现在与后端一致：要 EPS 且不要 SVG 时有脚本的图会被重画
    expect(copiesSourceVerbatim({ ...base, formats: ['eps', 'png'] })).toBe(false)
    const redraw = routeOf('original', ['pdf'], { [fileId(v)]: pr({ source: 'redraw', embeds_disk_bitmap: false }) })
    expect(copiesSourceVerbatim({ ...base, formats: ['pdf'], route: redraw })).toBe(false)
    expect(copiesSourceVerbatim({ ...base, formats: ['pdf'], guardOk: false })).toBe(false)
    expect(copiesSourceVerbatim({ ...base, scope: 'canvas', formats: ['pdf'] })).toBe(false)
  })
})

describe('EPS 可用性读 route.offers', () => {
  const withEps = (eps: PanelRoute['offers']) => routeOf('original', ['pdf'], { 'f.pdf': pr({ offers: eps }) })
  it('后端原因码 → 界面的闭集', () => {
    expect(epsFromRoute(withEps({ eps: { how: 'redraw' } }), 'original', 'f.pdf')).toEqual({ ok: true, reason: 'none' })
    expect(epsFromRoute(withEps({ eps: { how: 'unavailable', reason: 'eps_needs_script' } }), 'original', 'f.pdf')).toEqual({ ok: false, reason: 'no_script' })
    expect(epsFromRoute(withEps({ eps: { how: 'unavailable', reason: 'eps_not_for_canvas' } }), 'original', 'f.pdf')).toEqual({ ok: false, reason: 'canvas_scope' })
  })
  it('没有 route / 没有 offers / 范围不同：回 null，由 epsAvailability 走旧判据', () => {
    expect(epsFromRoute(null, 'original', 'f.pdf')).toBeNull()
    expect(epsFromRoute(withEps({}), 'original', 'f.pdf')).toBeNull()
    expect(epsFromRoute({ ...withEps({ eps: { how: 'redraw' } }), scope: 'canvas' }, 'original', 'f.pdf')).toBeNull()
  })
  it('epsAvailability：route 在手时它说了算；画布范围恒不可用', () => {
    // 素材清单里没有 f.pdf（旧判据 = 无脚本）；route 说可以
    expect(epsAvailability('original', 'f.pdf').ok).toBe(false)
    expect(epsAvailability('original', 'f.pdf', withEps({ eps: { how: 'redraw' } })).ok).toBe(true)
    expect(epsAvailability('canvas', 'f.pdf', withEps({ eps: { how: 'redraw' } }))).toEqual({ ok: false, reason: 'canvas_scope' })
  })
})
