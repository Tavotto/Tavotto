/**
 * 预检的面板 `kind` 的主语是「**本次导出嵌入的产物**」，不是文件后缀（#477）。
 *
 * 带 override 且有脚本的 PNG 面板，导出时是脚本重画出的矢量；预检若还按后缀判成位图，就会
 * 对着一张矢量报「位图内文字无法核验」（阻断式确认，且把「已知悉」写进样式检查报告），并
 * 对重画出的图跳过字号 / 轴 / 裁切检查。判据只在 `exportRoute.ts`，同源向量在
 * `exportRoute.test.ts`；这里钉的是预检**用了**它——以及用了之后字号检查真的出场。
 */
import { describe, expect, it } from 'vitest'
import { loadProfile } from './profile'
import { buildOriginalSpec, buildSpec, runSpec } from './preflight'
import type { PanelInfo } from './api'
import type { FigureDocument, PanelObject, PanelOverride } from '@/types/document'

const profile = loadProfile()
const NOT_VERIFIABLE = 'raster-text-not-verifiable'
const TINY = 2 // 远低于任何档位的字号底线

const manifest = {
  stem: 'slow_png', size_mm: [160, 120] as [number, number],
  elements: [{
    gid: 'axes_0.title', role: 'title', label: '标题', bbox: [0.1, 0.1, 0.5, 0.1] as [number, number, number, number],
    draggable: false, editable: [{ prop: 'fontsize', type: 'number', value: TINY }],
  }],
}
const EDIT = [{ gid: 'axes_0.title', prop: 'text', value: 'Title EDITED' }] as unknown as PanelOverride[]
const FRAME = [{ gid: 'figure', prop: 'frame', value: 'figsize' }] as unknown as PanelOverride[]
const GUARD = { version: 1, requiredFrame: 'figsize', sourceId: 'slow_png.png', bytesSha256: 'a'.repeat(64), sizeBytes: 10 }

const png = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1', type: 'panel', fileId: 'slow_png.png', fileKind: 'raster', pxW: 1890,
  nativeW: 160, nativeH: 120, overrides: [], x: 0, y: 0, w: 160, h: 120, ...over,
}) as unknown as PanelObject
const withScript = (script: boolean): Record<string, PanelInfo> =>
  ({ 'slow_png.png': { id: 'slow_png.png', mtime: 1, ...(script ? { script: 'slow_png.py' } : {}) } as unknown as PanelInfo })

function render(p: PanelObject) {
  const key = `${p.fileId} ${JSON.stringify(p.overrides)}`
  return { byKey: { [key]: { manifest, lastPatches: JSON.stringify(p.overrides) } as never }, latest: { [p.fileId]: key } }
}
function check(p: PanelObject, scope: 'canvas' | 'original', script = true, formats: string[] = ['pdf']) {
  const assets = withScript(script)
  const spec = scope === 'original'
    ? buildOriginalSpec(p, assets, render(p), { widthMm: 160, heightMm: 120 }, formats)
    : buildSpec({ page: { w: 180, h: 140, margin: 0 }, objects: [p] } as unknown as FigureDocument, assets, render(p))
  return { kind: spec.panels[0].kind, ids: runSpec(spec, profile).map((i) => i.id) }
}

describe('预检的面板 kind 跟着「本次导出嵌入的产物」走', () => {
  it('带 override + 有脚本（原图范围）：按矢量判，不报位图无法核验，字号检查真的出场', () => {
    const r = check(png({ overrides: EDIT }), 'original')
    expect(r.kind).toBe('pdf')
    expect(r.ids).not.toContain(NOT_VERIFIABLE)
    expect(r.ids).toContain('font-below-absolute-floor')
  })

  it('同一面板在画布范围：同样按矢量判', () => {
    const r = check(png({ overrides: EDIT }), 'canvas')
    expect(r.kind).toBe('pdf')
    expect(r.ids).not.toContain(NOT_VERIFIABLE)
    expect(r.ids).toContain('font-below-absolute-floor')
  })

  it('没有 override：导出嵌的就是那张 PNG，仍是位图且仍报无法核验', () => {
    for (const scope of ['original', 'canvas'] as const) {
      const r = check(png(), scope)
      expect(r.kind).toBe('raster')
      expect(r.ids).toContain(NOT_VERIFIABLE)
    }
  })

  it('未编辑 + 有脚本 + 要 EPS 不要 SVG（原图范围）：后端 rerender 重画成矢量，不报无法核验，字号检查出场', () => {
    for (const formats of [['eps', 'pdf'], ['eps'], ['eps', 'pdf', 'png']]) {
      const r = check(png(), 'original', true, formats)
      expect(r.kind).toBe('pdf')
      expect(r.ids).not.toContain(NOT_VERIFIABLE)
      expect(r.ids).toContain('font-below-absolute-floor')
    }
  })

  it('未编辑 + 有脚本 + EPS 同选 SVG / 不选 EPS：仍嵌磁盘 PNG，按位图；没有脚本时 EPS 也不重画', () => {
    for (const formats of [['eps', 'svg'], ['svg', 'pdf'], ['pdf', 'png']]) {
      const r = check(png(), 'original', true, formats)
      expect(r.kind).toBe('raster')
      expect(r.ids).toContain(NOT_VERIFIABLE)
    }
    expect(check(png(), 'original', false, ['eps', 'pdf']).kind).toBe('raster')
  })

  it('画布范围与所选格式无关', () => {
    expect(check(png(), 'canvas', true, ['eps', 'pdf']).kind).toBe('raster')
  })

  it('带 override 但素材表里没有脚本（原图范围）：后端退回磁盘文件，仍是位图', () => {
    const r = check(png({ overrides: EDIT }), 'original', false)
    expect(r.kind).toBe('raster')
    expect(r.ids).toContain(NOT_VERIFIABLE)
  })

  it('受守卫 PNG 仅 FRAME 覆盖：原图范围 = 位图（未编辑），画布范围 = 矢量（重画）', () => {
    const p = png({ overrides: FRAME, artifactValidation: GUARD } as Partial<PanelObject>)
    expect(check(p, 'original').kind).toBe('raster')
    expect(check(p, 'canvas').kind).toBe('pdf')
  })

  it('守卫无效：不抛，原图范围按位图口径（导出本身会被拒）', () => {
    const p = png({ overrides: FRAME, artifactValidation: { ...GUARD, version: 2 } } as unknown as Partial<PanelObject>)
    expect(() => check(p, 'original')).not.toThrow()
    expect(check(p, 'original').kind).toBe('raster')
  })
})
