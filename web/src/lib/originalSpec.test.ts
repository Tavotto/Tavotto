import { beforeEach, describe, expect, it } from 'vitest'
import type { AssetOriginalSpec } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import {
  FALLBACK_MM,
  getOriginalOutputSpec,
  ignoredTransforms,
  resolveOriginalSpec,
} from './originalSpec'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject } from '@/types/document'
import { literal } from '@/i18n'

/**
 * 原图规格（ADR 0028）。判据打在**纯函数核心**上，绑定层只是把三个 store 里
 * 的东西取出来交给它。
 *
 * 这批用例要挡住的第一件事是**「按原图导出」偷偷套用画布缩放**：用户把面板在
 * 版上缩到一半，图还是原来那么大——跟着缩的话字号会一起缩，那正是共享规则
 * §8 点名的那条。
 */

const panel = (p: Partial<PanelObject> = {}): PanelObject => ({
  id: 'obj1',
  type: 'panel',
  fileId: 'figs/a.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  ...p,
})

const vectorAsset: AssetOriginalSpec = {
  source_kind: 'vector',
  logical_w_mm: 80,
  logical_h_mm: 60,
  px_w: null,
  px_h: null,
  dpi: null,
  dpi_source: 'unknown',
  viewport_pt: [226.772, 170.079],
  transparent: null,
}

const rasterAsset = (over: Partial<AssetOriginalSpec> = {}): AssetOriginalSpec => ({
  source_kind: 'raster',
  logical_w_mm: 50.8,
  logical_h_mm: 25.4,
  px_w: 600,
  px_h: 300,
  dpi: 300,
  dpi_source: 'metadata',
  viewport_pt: null,
  transparent: false,
  ...over,
})

describe('来源优先级', () => {
  it('① 渲染回来的图幅压过文档里那份——图幅不是派生字段', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/a.pdf',
      panel: panel({ nativeW: 80, nativeH: 60 }),
      renderSizeMm: [120, 90],
      asset: vectorAsset,
    })
    expect([spec.widthMm, spec.heightMm]).toEqual([120, 90])
    expect(spec.origin).toBe('render_metadata')
    expect(spec.sourceKind).toBe('figure')
  })

  it('② 没渲染过就用文档里那份（它是上一次同步到的图幅）', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/a.pdf',
      panel: panel({ nativeW: 100, nativeH: 75 }),
      asset: vectorAsset,
    })
    expect([spec.widthMm, spec.heightMm]).toEqual([100, 75])
    expect(spec.origin).toBe('document')
  })

  it('③ 还没进文档的图用素材清单里的磁盘事实', () => {
    const spec = resolveOriginalSpec({ figureId: 'figs/a.pdf', asset: vectorAsset })
    expect([spec.widthMm, spec.heightMm]).toEqual([80, 60])
    expect(spec.origin).toBe('asset')
    expect(spec.viewportPt).toEqual([226.772, 170.079])
  })

  it('④ 一个来源都没有：用明确 fallback 并**说出来**', () => {
    const spec = resolveOriginalSpec({ figureId: 'figs/a.pdf' })
    expect([spec.widthMm, spec.heightMm]).toEqual([FALLBACK_MM.w, FALLBACK_MM.h])
    expect(spec.origin).toBe('fallback')
    expect(spec.fallback).toBe(true)
  })
})

describe('矢量 / 位图 / 可编辑 Figure 各报各的维度', () => {
  it('矢量报视口，不编像素网格', () => {
    const spec = resolveOriginalSpec({ figureId: 'figs/a.pdf', asset: vectorAsset })
    expect(spec.sourceKind).toBe('vector')
    expect([spec.pixelWidth, spec.pixelHeight, spec.dpi]).toEqual([null, null, null])
    expect(spec.dpiSource).toBe('unknown')
  })

  it('位图保源像素网格与它自己的密度', () => {
    const spec = resolveOriginalSpec({ figureId: 'figs/a.png', asset: rasterAsset() })
    expect([spec.pixelWidth, spec.pixelHeight]).toEqual([600, 300])
    expect([spec.dpi, spec.dpiSource]).toEqual([300, 'metadata'])
    expect(spec.viewportPt).toBeNull()
  })

  it('两轴密度不同的位图：量过了，答案就是"没有单一密度"', () => {
    // 后端对 300×150 dpi 的 PNG 刻意回 `dpi: null` + `dpi_source: metadata`
    // ——毫米数上面已经按各自的轴算过了，没有哪一个数能描述这张图。
    // 掉进反算那条路的话，界面会报一个 `derived` 的 300，而**素材就在场**：
    // 那是替文件回答一个它明确拒绝回答的问题。
    const spec = resolveOriginalSpec({
      figureId: 'figs/aniso.png',
      panel: panel({ fileKind: 'raster', nativeW: 50.8, nativeH: 50.8, pxW: 600, pxH: 300 }),
      asset: rasterAsset({ dpi: null, dpi_source: 'metadata' }),
    })
    expect([spec.dpi, spec.dpiSource]).toEqual([null, 'metadata'])
  })

  it('对照组：素材不在场时才反算——那时"没有单一密度"这句话没人说过', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/aniso.png',
      panel: panel({ fileKind: 'raster', nativeW: 50.8, nativeH: 25.4, pxW: 600, pxH: 300 }),
      assetPresent: false,
    })
    expect([spec.dpi, spec.dpiSource]).toEqual([300, 'derived'])
  })

  it('缺 DPI 元数据时如实标 assumed，不冒充 metadata', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/a.png',
      asset: rasterAsset({ dpi: 600, dpi_source: 'assumed' }),
    })
    expect(spec.dpiSource).toBe('assumed')
  })

  it('透明背景照实报；没测量的报 null 而不是 false', () => {
    expect(
      resolveOriginalSpec({ figureId: 'a.png', asset: rasterAsset({ transparent: true })})
        .transparent,
    ).toBe(true)
    expect(resolveOriginalSpec({ figureId: 'a.pdf', asset: vectorAsset }).transparent).toBeNull()
  })

  it('可编辑 Figure 的图幅是矢量语义：不给像素数（那由导出 DPI 决定）', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/a.pdf',
      panel: panel({ pxW: 600, pxH: 300 }),
      renderSizeMm: [120, 90],
    })
    expect([spec.pixelWidth, spec.pixelHeight]).toEqual([null, null])
  })
})

describe('源不可用（source_missing）', () => {
  it('保留上次已知的规格并标 stale', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/a.png',
      panel: panel({ fileKind: 'raster', nativeW: 50.8, nativeH: 25.4, pxW: 600, pxH: 300 }),
      asset: null,
      assetPresent: false,
    })
    expect([spec.widthMm, spec.heightMm]).toEqual([50.8, 25.4])
    expect(spec.stale).toBe(true)
    expect(spec.fallback).toBe(false)
  })

  it('只剩文档那份时密度是**反算**出来的，不冒充读到了元数据', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/a.png',
      panel: panel({ fileKind: 'raster', nativeW: 50.8, nativeH: 25.4, pxW: 600, pxH: 300 }),
      assetPresent: false,
    })
    expect(spec.dpi).toBe(300)
    expect(spec.dpiSource).toBe('derived')
  })

  it('像素维度未知时报 unknown，不除出一个数来', () => {
    const spec = resolveOriginalSpec({
      figureId: 'figs/a.pdf',
      panel: panel(),
      assetPresent: false,
    })
    expect([spec.dpi, spec.dpiSource]).toEqual([null, 'unknown'])
  })

  it('runtime 面板不在素材清单里是常态，不是"源丢了"', () => {
    const spec = resolveOriginalSpec({
      figureId: 'runtime:show.py#fig',
      panel: panel({ fileId: 'runtime:show.py#fig', fileKind: 'runtime' }),
      assetPresent: false,
    })
    expect(spec.stale).toBe(false)
    expect(spec.sourceKind).toBe('figure')
  })
})

describe('原图规格忽略 layout 变换', () => {
  it('用户在画布上缩到一半，规格一个数都不变', () => {
    const scaled = panel({ w: 40, h: 30 })
    const spec = resolveOriginalSpec({ figureId: 'figs/a.pdf', panel: scaled })
    expect([spec.widthMm, spec.heightMm]).toEqual([80, 60])
    expect(spec.ignored).toContain('scale')
  })

  it('落位、裁剪、旋转、翻转、透明度都进 ignored（顺序固定）', () => {
    const o = panel({
      x: 12,
      y: 34,
      w: 40,
      h: 30,
      crop: { x: 0, y: 0, w: 0.5, h: 0.5 },
      rotation: 90,
      flipH: true,
      opacity: 0.5,
    })
    expect(ignoredTransforms(o)).toEqual(['scale', 'crop', 'rotation', 'flip', 'opacity'])
  })

  it('没动过的面板 ignored 是空的——不无中生有地吓唬用户', () => {
    expect(ignoredTransforms(panel())).toEqual([])
  })

  it('只裁剪没缩放的面板不许被报成"缩放过"', () => {
    // 裁一半之后 w 天生就是图幅的一半；拿 w 直接比图幅的话每一张裁过的图
    // 都会被报成缩放过
    const cropped = panel({ w: 40, h: 30, crop: { x: 0, y: 0, w: 0.5, h: 0.5 } })
    expect(ignoredTransforms(cropped)).toEqual(['crop'])
  })

  it('90° 旋转的面板按内容长宽比对，不按页面包围盒', () => {
    const rotated = panel({ w: 60, h: 80, rotation: 90 })
    expect(ignoredTransforms(rotated)).toEqual(['rotation'])
  })
})

describe('路径形态不影响规格', () => {
  for (const id of ['figs/a.pdf', 'figs\\sub\\a.pdf', '/abs/figs/a.pdf', 'C:\\figs\\a.pdf']) {
    it(`${id}`, () => {
      const spec = resolveOriginalSpec({ figureId: id, panel: panel({ fileId: id }) })
      expect([spec.widthMm, spec.heightMm]).toEqual([80, 60])
      expect(spec.figureId).toBe(id)
    })
  }
})

describe('绑定层：从三个 store 取输入', () => {
  const info = {
    id: 'figs/a.pdf',
    name: 'a',
    folder: 'figs',
    kind: 'pdf' as const,
    native_w_mm: 80,
    native_h_mm: 60,
    mtime: 1,
    original_spec: vectorAsset,
  }

  beforeEach(async () => {
    globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
    useAssetStore.setState({ panels: [info], byId: { 'figs/a.pdf': info } })
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_spec')
  })

  it('文档与素材清单都不认识的 id 回 null——不发明一张不存在的图', () => {
    expect(getOriginalOutputSpec('figs/nope.pdf')).toBeNull()
  })

  it('只在素材清单里（还没进文档）也给得出规格', () => {
    const spec = getOriginalOutputSpec('figs/a.pdf')
    expect(spec?.origin).toBe('asset')
    expect([spec?.widthMm, spec?.heightMm]).toEqual([80, 60])
  })

  it('进了文档之后按文档那份走，画布上的缩放不进规格', () => {
    useDocumentStore.getState().commit(literal('放入'), (d) => {
      d.objects.push(panel({ fileId: 'figs/a.pdf', w: 20, h: 15 }))
    })
    const spec = getOriginalOutputSpec('figs/a.pdf')
    expect(spec?.origin).toBe('document')
    expect([spec?.widthMm, spec?.heightMm]).toEqual([80, 60])
    expect(spec?.ignored).toContain('scale')
  })
})
