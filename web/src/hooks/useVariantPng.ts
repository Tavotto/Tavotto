import { chartSourceFields, type ChartRenderProfile } from '@/lib/chartSource'
import { rejectArtifactRenders, renderKey, renderRequestIdentity } from '@/store/renderStore'
import { artifactRequestFields, artifactKeySuffix, artifactValidationIssue, ArtifactValidationError, subjectOf, hasRealEdits, type AdoptionGuard } from '@/lib/artifactValidation'
import type { ChartSourceRef, FigureOrigin, FigureSnapshot, PanelChartState, PanelOverride } from '@/types/document'
import { sameFigureOrigin, validFigureOrigin } from '@/lib/figureSnapshot'
import { figureSnapshotPreviewIdentity, hasFigureSnapshotPreview } from '@/lib/figureSnapshotPreview'
/**
 * 「按 overrides 出一张缩略图」的可复用 hook（issue #131）。
 *
 * 布局版本时间线以前对面板一律用 `panelSrc(fileId, …)`——那是**磁盘上的素材**，
 * 不含任何图内修改。于是两个版本之间图内文字位置、字号、图例、axes 布局完全
 * 不同，缩略图却一模一样：用户点了恢复、画面确实变了，可时间线上看不出差别，
 * 只能得出「恢复失败」的结论。
 *
 * 这里走 `POST /api/engine/preview_png`（按 patches 出图、状态中立），并且：
 *   - 按变体键缓存 blob URL，同一版本反复展开只发一次；
 *   - 缓存**有界**（LRU，上限 CACHE_MAX），超出的当场 revoke，不泄漏 blob；
 *   - 组件卸载 / 变体切换时 abort 在途请求；
 *   - 失败**保留磁盘图**并把 `approximate` 置位——调用方必须据此标注
 *     「图内修改预览不可用」，绝不无提示地拿原图冒充版本视觉状态；
 *   - 只在 `enabled` 时发请求：版本列表里每一条都预渲染的话，一次展开对话框
 *     就是几十次 matplotlib 往返。
 */
import { useEffect, useState } from 'react'
import { EngineError, enginePreviewPng, enginePreviewPngSnapshot } from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { currentProjectId } from '@/lib/session'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import type { PreviewOwner } from '@/lib/previewOwner'

/** 缓存上限：一次对话框里用户来回比对的版本数远小于它 */
const CACHE_MAX = 24

/** 变体键 → blob URL。Map 的插入序天然就是 LRU 需要的顺序 */
const cache = new Map<string, string>()
let cacheEpoch = 0

/**
 * 缓存键**必须带项目与素材版本**。
 *
 * `fileId` 是项目内的相对路径：两个项目里同名同 overrides 的图完全可能是两张
 * 不同的图，只按 (fileId, overrides, bucket) 缓存的话，在项目 A 看过某个版本
 * 之后切到项目 B，会直接命中 A 的 blob 并把**别人的图**当成这一版的预览显示
 * 出来，而且一次请求都不发。素材本身被改过（mtime 变了）同理。
 */
const keyOf = (fileId: string, overrides: unknown[], bucket: number, rev: number, chart?: PanelChartState, chartSource?: ChartSourceRef, owner?: PreviewOwner, origin?: FigureOrigin, renderProfile?: ChartRenderProfile) =>
  `${owner ? owner.key : currentProjectId() ?? '-'} ${rev} ${bucket} ${fileId} ${renderRequestIdentity(overrides, chart, chartSource, origin, renderProfile)}`

function remember(key: string, url: string) {
  cache.set(key, url)
  while (cache.size > CACHE_MAX) {
    const oldest = cache.keys().next().value as string | undefined
    if (oldest == null) break
    const dead = cache.get(oldest)
    cache.delete(oldest)
    // 自己造的 blob 自己收：不 revoke 的话每开一次对话框都留一批在内存里
    if (dead && dead.startsWith('blob:')) URL.revokeObjectURL(dead)
  }
}

/** 换项目 / 测试隔离：整表释放 */
export function clearVariantPngCache(): void {
  for (const url of cache.values()) {
    if (url.startsWith('blob:')) URL.revokeObjectURL(url)
  }
  cache.clear()
  cacheEpoch++
}

export interface VariantPng {
  /** 出好的图；null = 还没有（调用方先显示磁盘原图） */
  url: string | null
  /** 正在出图 */
  loading: boolean
  /**
   * 这一版的图内修改**没能渲染出来**（引擎不可达 / 脚本报错 / 素材没脚本）。
   * 调用方必须把它显示出来——「近似预览」比一张骗人的原图诚实。
   */
  approximate: boolean
}

/**
 * 这一版要不要出引擎图：没有 override、或来源驱动面板零编辑（只有结构性 FRAME）都不出，
 * 磁盘原像素就是对的（ADR 0139 §五.1）。调用方据此判断「还在出图」——这条路径
 * 永远拿不到 url，不能当成没出好。
 */
export function variantNeedsRender(overrides: unknown[], artifactValidation?: AdoptionGuard | null, chart?: PanelChartState, reference?: FigureSnapshot, origin?: FigureOrigin): boolean {
  return origin !== undefined || reference !== undefined || !!chart || overrides.length > 0 && !(artifactValidation && 'policy' in artifactValidation && !hasRealEdits(overrides as PanelOverride[]))
}

export function useVariantPng(
  fileId: string,
  overrides: unknown[],
  bucket: number,
  enabled: boolean,
  /** 素材版本（mtime）：磁盘上那份被改过时旧缩略图必须作废 */
  rev = 0,
  artifactValidation?: AdoptionGuard | null,
  chart?: PanelChartState,
  chartSource?: ChartSourceRef,
  owner?: PreviewOwner,
  reference?: FigureSnapshot,
  origin?: FigureOrigin,
  renderProfile?: ChartRenderProfile,
): VariantPng {
  const active = !owner || owner.still()
  const archived = hasFigureSnapshotPreview({ figureSnapshot: reference, chart, chartSource })
  const archiveKey = archived ? figureSnapshotPreviewIdentity(reference) : null
  const cacheable = archived || origin !== undefined || artifactValidation === undefined
  const key = keyOf(fileId, overrides, bucket, rev, chart, chartSource, owner, origin, renderProfile) + artifactKeySuffix(origin === undefined ? artifactValidation : undefined)
    + (archived ? ` archive:${archiveKey ?? 'invalid'}` : '')
  const [state, setState] = useState<VariantPng & {key?: string}>(() => ({
    key,
    url: cacheable ? cache.get(key) ?? null : null,
    loading: false,
    approximate: false,
  }))

  useEffect(() => {
    if (!enabled || !active) {
      setState({ key, url: null, loading: false, approximate: !active })
      return
    }
    if (chartSource !== undefined && !chart) {
      setState({ key, url: null, loading: false, approximate: true })
      return
    }
    if (archived && archiveKey === null) {
      setState({ key, url: null, loading: false, approximate: true })
      return
    }
    if (origin !== undefined && (!validFigureOrigin(origin, fileId) || chart !== undefined || chartSource !== undefined)) {
      setState({ key, url: null, loading: false, approximate: true })
      return
    }
    const subject = subjectOf(fileId, overrides as PanelOverride[], artifactValidation)
    const issue = archived || origin !== undefined ? null : artifactValidationIssue(subject)
    if (issue) { setState({ key,url:null,loading:false,approximate:true}); return }
    // 没有 override 的面板与磁盘图一模一样，白跑一次引擎没有意义
    // 来源驱动面板零编辑（只有结构性 FRAME）显示磁盘原像素，同样不出引擎图（ADR 0139 §五.1）
    if (!variantNeedsRender(overrides, artifactValidation, chart, archived ? reference : undefined, origin)) {
      setState({ key, url: null, loading: false, approximate: false })
      return
    }
    const hit = cacheable ? cache.get(key) : undefined
    if (hit) {
      setState({ key, url: hit, loading: false, approximate: false })
      return
    }
    const ctrl = new AbortController()
    const project = captureProjectEpoch()
    const pj = owner ? owner.pj : project.pj
    let live = true
    const epoch = cacheEpoch
    const current = () => live && epoch === cacheEpoch && project.still() && (!owner || owner.still())
    setState({ key, url: null, loading: true, approximate: false })
    const transport = engineTransport()
    const frozenOrigin = origin === undefined ? undefined : structuredClone(origin)
    const pending = archived
      ? transport ? Promise.reject(new EngineError('This host does not support archived Figure previews.', '', 'figure_snapshot_preview_unsupported'))
        : enginePreviewPng(fileId, structuredClone(overrides), bucket, ctrl.signal,
          { figure_snapshot: structuredClone(reference) }, pj).then(blob => URL.createObjectURL(blob))
      : origin !== undefined
      ? transport
        ? !transport.supportsFigureOriginRender || !transport.previewPngSnapshot
          ? Promise.reject(new EngineError('This host does not support historical Figure inputs.', '', 'svg_source_unavailable'))
          : transport.previewPngSnapshot(fileId, structuredClone(overrides), bucket, ctrl.signal, undefined, undefined, structuredClone(frozenOrigin!)).then(frame => {
            if (!frame.manifest || !sameFigureOrigin(frozenOrigin, frame.figureOrigin)) {
              if (frame.url.startsWith('blob:')) URL.revokeObjectURL(frame.url)
              throw new EngineError('Mismatched historical Figure pixels.', '', 'svg_source_unavailable')
            }
            return frame.url
          })
        : enginePreviewPngSnapshot(fileId, structuredClone(overrides), bucket, ctrl.signal,
          { figure_origin: structuredClone(frozenOrigin!) }, pj).then(frame => frame.url)
      : chart
      ? transport
        ? !transport.supportsChartRender || !transport.previewPngSnapshot || (chartSource !== undefined && !transport.supportsChartSourceRender)
          ? Promise.reject(new EngineError('This host does not support paired chart pixels.', '', 'chart_render_unsupported'))
          : (chartSource !== undefined
            ? transport.previewPngSnapshot(fileId, overrides, bucket, ctrl.signal, structuredClone(chart), structuredClone(chartSource))
            : transport.previewPngSnapshot(fileId, overrides, bucket, ctrl.signal, chart)).then(frame => {
            if (typeof frame.chartRevision !== 'string' || !frame.chartRevision || !frame.manifest) {
              if (frame.url.startsWith('blob:')) URL.revokeObjectURL(frame.url)
              throw new EngineError('The chart response has no paired pixels and geometry.', '', 'chart_render_pair_missing')
            }
            return frame.url
          })
        : enginePreviewPngSnapshot(fileId, overrides, bucket, ctrl.signal, { ...artifactRequestFields(subject), chart: structuredClone(chart), ...chartSourceFields(chartSource), ...(renderProfile !== undefined ? { render_profile: renderProfile } : {}) }, pj).then(frame => frame.url)
      : artifactValidation
      ? transport ? Promise.reject(new ArtifactValidationError('unsupported_render_state'))
        : enginePreviewPngSnapshot(fileId, overrides, bucket, ctrl.signal, artifactRequestFields(subject), pj).then(s=>s.url)
      : transport
      ? transport.previewPngUrl(fileId, overrides, bucket, ctrl.signal)
      : enginePreviewPng(fileId, overrides, bucket, ctrl.signal, {}, pj).then((b) =>
          URL.createObjectURL(b),
        )
    void pending
      .then((url) => {
        if (!current()) { if (url.startsWith('blob:')) URL.revokeObjectURL(url); return }
        if (cacheable) remember(key, url)
        if (live) setState({ key, url, loading: false, approximate: false })
      })
      .catch((error) => {
        if (!archived && origin === undefined && current() && artifactValidation &&
            (error instanceof ArtifactValidationError || (error instanceof EngineError && error.code === 'artifact_source_unavailable'))) {
          rejectArtifactRenders(fileId, artifactValidation, error, renderKey(fileId, overrides, artifactValidation))
        }
        // 失败不是「空白」：退回磁盘图，但必须标成近似
        if (current()) setState({ key, url: null, loading: false, approximate: true })
      })
    return () => {
      live = false
      ctrl.abort()
    }
    // overrides 的内容变化由 key 表达（数组引用每次都是新的）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, active])

  return !active || state.key !== key
    ? {url:null,loading:enabled && active,approximate:true} : state
}
