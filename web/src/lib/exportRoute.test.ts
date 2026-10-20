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
import { embedsDiskBitmap } from './exportRoute'
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
