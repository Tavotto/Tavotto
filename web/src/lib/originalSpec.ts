/**
 * 「原图规格」——**全产品唯一的一份**（ADR 0028）。
 *
 * 「按原图导出」这句话必须有一个说得出口的定义，否则它只能在导出那一刻现猜。
 * 本模块回答两个问题，别处不许再答第二遍：
 *
 *   1. **这张图自己有多大**（逻辑 mm / 像素网格 / 物理密度 / 矢量视口）；
 *   2. **画布上的哪些变换在原图导出里不算数**。
 *
 * ### 来源优先级（顺序是判据的一部分，改动前先改 ADR）
 *
 * | # | 来源 | 什么时候有 | `origin` |
 * | - | --- | --- | --- |
 * | 1 | 这一变体渲染回来的 manifest `size_mm` | 可编辑 Figure 已经画过一次 | `render_metadata` |
 * | 2 | 文档里那个面板的 `nativeW/nativeH` | 面板在文档里（= 上一次同步到的图幅） | `document` |
 * | 3 | `/api/panels` 的 `original_spec` | 素材还在清单里 | `asset` |
 * | 4 | 明确 fallback（`FALLBACK_MM`） | 以上都没有 | `fallback` |
 *
 * 第 1 档在第 2 档之前，是因为**图幅不是派生字段**（`web/AGENTS.md`）：
 * `size_mm` 本身可以被 override 改，权威在这个变体自己渲染回来的 manifest 上，
 * 不在磁盘文件上。第 2 档在第 3 档之前，是因为它就是第 1 档同步下来的那份，
 * 而且**它在源文件消失之后还在**——`source_missing` 时界面要说的是"上次
 * 已知的规格"，不是"不知道"。
 *
 * ### 不做的事
 *
 * * **不读 x/y/w/h 来当尺寸**。用户把面板在画布上缩到 40 mm，图还是原来那么
 *   大；跟着缩的话字号会一起缩，那正是共享规则 §8 要挡的「原图导出偷偷套用
 *   画布缩放」。缩放/裁剪/旋转/翻转/透明度只进 `ignored`，让界面说出来。
 * * **不猜密度**。位图的 dpi 由后端按文件格式解析（`engine/originalspec.py`），
 *   文件没写就报 `assumed` 并带着这个标记一路到界面。
 * * **不发明一张不存在的图**：`getOriginalOutputSpec()` 对文档与素材清单都不
 *   认识的 id 回 `null`，不回一份编出来的规格。
 */
import type { PanelInfo, AssetOriginalSpec } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { panelFullSize } from '@/types/document'
import { useAssetStore } from '@/store/assetStore'
import { useRenderStore, renderKeyOf } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { findFigurePanel } from '@/store/workspace'

/** 素材形态。`figure` = 可编辑 Figure（图幅由引擎说了算） */
export type OriginalSourceKind = 'figure' | 'vector' | 'raster' | 'unknown'

/**
 * 物理密度是怎么来的。**四个取值，不许压成三个**：
 * `assumed`（文件没写，我们按格式假定）与 `derived`（由已知 mm 与像素反算）
 * 是两件不同的事，前者可能错得离谱，后者只是没有出处。
 */
export type DpiSource = 'metadata' | 'assumed' | 'derived' | 'unknown'

/** 这份规格取自哪一档（见上表） */
export type SpecOrigin = 'render_metadata' | 'document' | 'asset' | 'fallback'

/** 画布上设了、但原图导出不套用的变换 */
export type IgnoredTransform = 'scale' | 'crop' | 'rotation' | 'flip' | 'opacity'

export interface OriginalOutputSpec {
  /** 素材 id（文件相对路径，或 `runtime:` 前缀的稳定 id） */
  figureId: string
  sourceKind: OriginalSourceKind
  /** 逻辑尺寸（mm）——产品里一切长度的统一口径 */
  widthMm: number
  heightMm: number
  /** 像素网格；矢量源为 null */
  pixelWidth: number | null
  pixelHeight: number | null
  dpi: number | null
  dpiSource: DpiSource
  /** 矢量视口（pt）；位图源为 null */
  viewportPt: [number, number] | null
  /** 透明背景；没测量为 null */
  transparent: boolean | null
  origin: SpecOrigin
  /** 源文件此刻不可用，这是上一次已知的规格 */
  stale: boolean
  /** 没有任何可信来源，用的是占位值——界面**必须**说出来 */
  fallback: boolean
  /** 画布上设了但原图导出忽略的变换（按固定顺序，便于逐字比对） */
  ignored: IgnoredTransform[]
}

/**
 * 什么来源都没有时的占位尺寸：单栏宽 80 mm × 4:3。
 *
 * **它不是这张图的规格**，只是一个画得出来的数。每一条走到这里的 spec 都带
 * `fallback: true`，界面据此提示用户自己确认——静默用一个编出来的尺寸导出，
 * 比报错更糟。
 */
export const FALLBACK_MM = { w: 80, h: 60 } as const

/** 尺寸比较的容差（mm）：图幅同步与 mm 取整都在 0.05 这一档 */
const EPS = 0.05

export interface SpecInputs {
  figureId: string
  /** 文档里代表这张图的面板对象（同 fileId 的第一个）；没有就是还没进文档 */
  panel?: PanelObject | null
  /** 这一变体渲染回来的图幅（mm）——可编辑 Figure 的真实尺寸 */
  renderSizeMm?: readonly [number, number] | null
  /** `/api/panels` 报的素材事实；素材不在清单里就是 null */
  asset?: AssetOriginalSpec | null
  /** 素材是不是还在清单里。`false` = 源已不可用（掉线 / 被删） */
  assetPresent?: boolean
}

/**
 * 纯函数核心：给什么算什么，不读 store。
 * 判据都打在它上面——绑定层只负责把三个 store 里的东西取出来。
 */
export function resolveOriginalSpec(input: SpecInputs): OriginalOutputSpec {
  const { figureId, panel = null, renderSizeMm = null, asset = null } = input
  const assetPresent = input.assetPresent ?? asset != null
  const base: Omit<OriginalOutputSpec, 'ignored'> = pickSize({
    figureId,
    panel,
    renderSizeMm,
    asset,
    assetPresent,
  })
  return { ...base, ignored: ignoredTransforms(panel) }
}

function pickSize(i: Required<SpecInputs>): Omit<OriginalOutputSpec, 'ignored'> {
  const { figureId, panel, renderSizeMm, asset, assetPresent } = i
  const kind = sourceKind(panel, asset)
  // 源不在清单里 = 这份规格说的是"上次已知"。runtime 面板永远不在
  // `/api/panels` 里（`runtime:` 前缀的 id），那不是"源丢了"。
  const stale = !assetPresent && kind !== 'figure' && panel != null && !isRuntime(figureId)

  // ① 渲染回来的图幅：可编辑 Figure 唯一的真实尺寸
  if (renderSizeMm && renderSizeMm[0] > 0 && renderSizeMm[1] > 0) {
    return {
      figureId,
      sourceKind: 'figure',
      widthMm: renderSizeMm[0],
      heightMm: renderSizeMm[1],
      // 矢量语义：图幅是 pt/mm，像素网格由导出 DPI 决定，不是这张图的属性
      pixelWidth: null,
      pixelHeight: null,
      dpi: null,
      dpiSource: 'unknown',
      viewportPt: null,
      transparent: asset?.transparent ?? null,
      origin: 'render_metadata',
      stale: false,
      fallback: false,
    }
  }

  // ② 文档里那份（= 上一次同步到的图幅；源文件没了它也还在）
  if (panel && panel.nativeW > 0 && panel.nativeH > 0) {
    const px = panel.pxW && panel.pxH ? ([panel.pxW, panel.pxH] as const) : null
    return {
      figureId,
      sourceKind: kind,
      widthMm: panel.nativeW,
      heightMm: panel.nativeH,
      pixelWidth: px?.[0] ?? null,
      pixelHeight: px?.[1] ?? null,
      ...densityOf(px?.[0] ?? null, panel.nativeW, asset, stale),
      viewportPt: asset?.viewport_pt ? [asset.viewport_pt[0], asset.viewport_pt[1]] : null,
      transparent: asset?.transparent ?? null,
      origin: 'document',
      stale,
      fallback: false,
    }
  }

  // ③ 素材清单里的磁盘事实
  if (asset && asset.logical_w_mm > 0 && asset.logical_h_mm > 0) {
    return {
      figureId,
      sourceKind: asset.source_kind === 'vector' ? 'vector' : 'raster',
      widthMm: asset.logical_w_mm,
      heightMm: asset.logical_h_mm,
      pixelWidth: asset.px_w,
      pixelHeight: asset.px_h,
      dpi: asset.dpi,
      dpiSource: asset.dpi_source,
      viewportPt: asset.viewport_pt ? [asset.viewport_pt[0], asset.viewport_pt[1]] : null,
      transparent: asset.transparent,
      origin: 'asset',
      stale: false,
      fallback: false,
    }
  }

  // ④ 明确 fallback：一个都没有
  return {
    figureId,
    sourceKind: kind,
    widthMm: FALLBACK_MM.w,
    heightMm: FALLBACK_MM.h,
    pixelWidth: null,
    pixelHeight: null,
    dpi: null,
    dpiSource: 'unknown',
    viewportPt: null,
    transparent: null,
    origin: 'fallback',
    stale,
    fallback: true,
  }
}

const isRuntime = (figureId: string) => figureId.startsWith('runtime:')

function sourceKind(panel: PanelObject | null, asset: AssetOriginalSpec | null) {
  if (panel?.fileKind === 'runtime') return 'figure'
  if (panel?.fileKind === 'pdf' || asset?.source_kind === 'vector') return 'vector'
  if (panel?.fileKind === 'raster' || asset?.source_kind === 'raster') return 'raster'
  return 'unknown'
}

/**
 * 密度：素材清单里那份（有出处）优先；只剩文档时由 px 与 mm **反算**。
 *
 * 反算出来的数值可能与素材当初那份一模一样，但它的出处不同——`derived`
 * 说的是"这个数是我们除出来的，文件没在场"。合并进 `metadata` 的话，
 * 源文件丢了之后界面会声称自己读到了一份并不存在的元数据。
 */
function densityOf(
  pxW: number | null,
  widthMm: number,
  asset: AssetOriginalSpec | null,
  stale: boolean,
): { dpi: number | null; dpiSource: DpiSource } {
  if (asset && !stale && asset.dpi != null) {
    return { dpi: asset.dpi, dpiSource: asset.dpi_source }
  }
  // **量过了而且量出「没有单一密度」，是一个答案，不是没有答案。** 两轴密度
  // 不同的 PNG/JPEG（300×150）后端刻意回 `dpi: null` + `dpi_source:
  // 'metadata'`：毫米数上面已经按各自的轴算过了，再除一个单值出来等于替文件
  // 回答一个它明确拒绝回答的问题，而界面会把这个数当成读到的元数据。
  if (asset && !stale && asset.dpi_source === 'metadata') {
    return { dpi: null, dpiSource: 'metadata' }
  }
  if (pxW && widthMm > 0) {
    return { dpi: Math.round((pxW / widthMm) * 25.4 * 10) / 10, dpiSource: 'derived' }
  }
  return { dpi: null, dpiSource: 'unknown' }
}

/**
 * 这个面板在画布上设了、而原图导出不套用的变换。
 *
 * 顺序固定（判据逐字比对），`scale` 比的是**未裁剪的完整显示尺寸**与图幅：
 * 裁剪过的面板 `w` 天生就比图幅小，拿 `w` 直接比会把每一张裁过的图都报成
 * "缩放过"。
 */
export function ignoredTransforms(panel: PanelObject | null): IgnoredTransform[] {
  if (!panel) return []
  const out: IgnoredTransform[] = []
  const full = panelFullSize(panel)
  if (
    panel.nativeW > 0 &&
    (Math.abs(full.w - panel.nativeW) > EPS || Math.abs(full.h - panel.nativeH) > EPS)
  ) {
    out.push('scale')
  }
  if (panel.crop) out.push('crop')
  if (panel.rotation) out.push('rotation')
  if (panel.flipH || panel.flipV) out.push('flip')
  if (panel.opacity != null && panel.opacity < 1) out.push('opacity')
  return out
}

/* --------------------------- store 绑定层 --------------------------------- */

/**
 * 这张图的原图规格。文档与素材清单都不认识它时回 `null`——
 * **不发明一张不存在的图**。
 *
 * Prompt 12 的导出面板、Prompt 11 的定位、快速编辑工作区的规格行都调它。
 */
export function getOriginalOutputSpec(figureId: string): OriginalOutputSpec | null {
  const panel = findFigurePanel(figureId)?.panel ?? null
  const info: PanelInfo | undefined = useAssetStore.getState().byId[figureId]
  const runtime = isRuntime(figureId)
    ? (useRuntimeAssetStore.getState().assets ?? []).find((a) => a.id === figureId)
    : undefined
  if (!panel && !info && !runtime) return null

  const renderSizeMm = panel
    ? (useRenderStore.getState().byKey[renderKeyOf(panel)]?.manifest?.size_mm ?? null)
    : null
  // runtime 素材从来不在 `/api/panels` 里；它的图幅在清单里，描述符是兜底
  // （还没跑出过描述符的那一档 `size_mm` 也可能是 null —— 那时就退到文档里
  // 那份，再没有就是明确 fallback，绝不编一个数）
  const runtimeSize = runtime?.size_mm ?? runtime?.descriptor?.size_mm ?? null
  return resolveOriginalSpec({
    figureId,
    panel,
    renderSizeMm: renderSizeMm ?? runtimeSize,
    asset: info?.original_spec ?? null,
    assetPresent: info != null || runtime != null,
  })
}
