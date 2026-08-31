/**
 * 统一导出请求的构造 —— **全产品只有这一份**（ADR 0031）。
 *
 * 改造前，「这次导出要什么」在四个地方各说一遍：对话框拼一份载荷、后端从
 * `spec` 里逐个取自己的默认值、`codex-plugin` 的 bridge 又一份、打包端点再
 * 一份。四份的默认值并不一样，于是同一张图在两条入口下能出来两个不同的文件。
 *
 * 这个模块只做**构造与判定**，不发请求、不改文档、不碰 store 以外的东西：
 *
 * ```text
 * defaultScope(mode)          这次默认按原图还是按画布（工作流说了算，用户可切）
 * originalAvailability(id)    原图能不能导 —— 不能导时**说出原因**，不静默改画布
 * buildExportRequest(input)   UI 状态 → 线上的那个结构（唯一一处）
 * snapshotRevision(request)   这一份快照的指纹（导出完回来一比，说"期间又被编辑"）
 * ```
 *
 * ### `scope=original` 里没有布局
 *
 * `original` 段里**根本没有** x/y/w/h、页面尺寸、裁切字段——不是"记得别填"，
 * 是这个类型里没有那几个键。想让画布缩放漏进原图导出，得先改这个结构，
 * 而改它会当场撞上 `exportRequest.test.ts` 与 `tests/test_export_original.py`。
 * 被忽略的变换逐项进 `ignored`：**忽略而不说等于骗人**（ADR 0028）。
 */
import type { ExportObject, ExportRequest } from './api'
import { checkFilename, stripOutputExtension, type FilenameReason } from './exportName'
import { getOriginalOutputSpec, type OriginalOutputSpec } from './originalSpec'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { toExportObjects } from './exportPayload'
import type { FigureDocument, PanelObject } from '@/types/document'
import type { WorkspaceMode } from '@/store/workspace'

export type ExportScope = 'original' | 'canvas'
export type ExportFormat = 'pdf' | 'png'
export type OverwritePolicy = 'ask' | 'replace' | 'rename'
export type ExportBackground = 'white' | 'transparent'

/** 位图格式的判据。「PPI 有没有意义」全产品只问这一句 */
export const RASTER_FORMATS: readonly ExportFormat[] = ['png']
export const VECTOR_FORMATS: readonly ExportFormat[] = ['pdf']

/** 与 `engine/exportreq.py` 的 `PPI_MIN/PPI_MAX/PPI_DEFAULT` 同源 */
export const PPI_MIN = 36
export const PPI_MAX = 1200
export const PPI_DEFAULT = 600

export function hasRaster(formats: readonly string[]): boolean {
  return formats.some((f) => RASTER_FORMATS.includes(f as ExportFormat))
}

/**
 * 这次默认按哪个范围。
 *
 * 快速编辑在编一张图 → 默认按原图；画布排版在编版面 → 默认按画布。
 * **默认不是强制**：两个按钮都在，用户随时切（§五）。
 */
export function defaultScope(mode: WorkspaceMode): ExportScope {
  return mode === 'fast_edit' ? 'original' : 'canvas'
}

/**
 * 原图导出为什么不可用。**闭集**——界面按它说一句人话，不接受自由文本。
 *
 * `none` 是可用。其余每一条都对应一个用户能理解、而且**能动手解决**的情形。
 */
export type OriginalBlockReason =
  | 'none'
  /** 这次没有"当前这张图"（画布上没选中面板，也没在快速编辑里） */
  | 'no_figure'
  /** 文档与素材清单都不认识它：不发明一张不存在的图 */
  | 'unknown_figure'
  /** 源文件此刻不可用（掉线 / 被删）。规格还在（上一次已知的那份），但导不出来 */
  | 'source_stale'

export interface OriginalAvailability {
  ok: boolean
  reason: OriginalBlockReason
  spec: OriginalOutputSpec | null
}

/**
 * 「按原图导出」现在能不能用。
 *
 * **不可用时不隐藏这个选项，也不静默改成画布**（§五 必须调整的最后一条）：
 * 一个消失的按钮无法解释自己，而一次悄悄换掉的范围会让用户拿到一张
 * 他没要的图。
 */
export function originalAvailability(figureId: string | null): OriginalAvailability {
  if (!figureId) return { ok: false, reason: 'no_figure', spec: null }
  const spec = getOriginalOutputSpec(figureId)
  if (!spec) return { ok: false, reason: 'unknown_figure', spec: null }
  /*
   * 源文件够不够得着。**判据是"素材清单里还有没有它"，不是 `spec.stale`。**
   *
   * 后端解析面板源的第一步就是 `safe_resolve()`，文件不在就 404 —— 它排在
   * "去注册表找脚本重渲染"之前，所以"引擎能重新画一张"这个指望在这条路上
   * 兑现不了（`app._resolve_panel_source`）。
   *
   * `spec.stale` 答的是另一个问题（"这份规格是不是上一次已知的"）：一张刚
   * 渲染过、manifest 还在手上的图，`stale` 是 **false**，而它的磁盘文件可能
   * 早就没了。拿它当"能不能导"的判据，那张图会得到一个按下去必然失败的按钮。
   * **能不能做与做了会怎样，判据必须是同一个**（PR #214 评审）。
   *
   * runtime 素材（ADR 0013）从来不在 `/api/panels` 里，它走 worker 那条路，
   * 不需要磁盘原件——所以单独放行。
   */
  if (!sourceReachable(figureId)) return { ok: false, reason: 'source_stale', spec }
  return { ok: true, reason: 'none', spec }
}

/** 这张图此刻够不够得着（与后端 `_resolve_panel_source` 的前提逐条对应）。 */
function sourceReachable(figureId: string): boolean {
  if (figureId.startsWith('runtime:')) {
    return (useRuntimeAssetStore.getState().assets ?? []).some((a) => a.id === figureId)
  }
  return useAssetStore.getState().byId[figureId] != null
}

export interface ExportRequestInput {
  scope: ExportScope
  formats: readonly string[]
  /** 用户输入的原文（可能带扩展名、可能带首尾空白） */
  filename: string
  ppi: number
  background?: ExportBackground
  overwrite?: OverwritePolicy
  includeReport?: boolean
  acknowledged?: readonly string[]
  documentId: string | null
  doc: FigureDocument
  /** `scope=original` 时是哪一张图 */
  figureId?: string | null
  panel?: PanelObject | null
  spec?: OriginalOutputSpec | null
  /** 样式检查报告的前半份（检查结果）；服务端补上版本、时间与产物事实 */
  report?: Record<string, unknown>
}

export interface BuiltRequest {
  request: ExportRequest
  /** 这次会写出哪几个文件名（不含样式检查报告），供界面预览 */
  names: string[]
  revision: string
}

/** 文件名不合法的原因；合法回 `null`。输入时就地调它（§六） */
export function filenameProblem(raw: string, formats: readonly string[]): FilenameReason | null {
  return checkFilename(stripOutputExtension(raw, formats))
}

/**
 * UI 状态 → 线上的 `ExportRequest`。**唯一一处**。
 *
 * 多格式共享**同一次调用**的输出，所以 PDF 与 PNG 必然出自同一份对象快照
 * ——不是"我们记得要一致"，是它们物理上来自同一个数组。
 */
export function buildExportRequest(input: ExportRequestInput): BuiltRequest {
  const formats = ['pdf', 'png'].filter((f) => input.formats.includes(f))
  const filename = stripOutputExtension(input.filename, formats)
  const raster = hasRaster(formats)
  const request: ExportRequest = {
    scope: input.scope,
    formats,
    filename,
    // **只在有位图格式时是数字**。压成一个默认值的话，界面就会去显示一个
    // 不影响任何东西的设置，而用户会以为改它有用（T-49 同一个形状）
    ppi: raster ? clampPpi(input.ppi) : null,
    background: input.background ?? 'white',
    overwrite: input.overwrite ?? 'ask',
    validation: {
      policy: input.acknowledged?.length ? 'acknowledged' : 'block_on_error',
      acknowledged: [...(input.acknowledged ?? [])],
    },
    include_style_check_report: input.includeReport === true,
    document_id: input.documentId,
  }
  if (input.scope === 'canvas') {
    request.canvas = {
      page_w_mm: input.doc.page.w,
      page_h_mm: input.doc.page.h,
      // 顺序即 z 序（底 → 顶），隐藏对象不发 —— 与画布预览同一条投影
      objects: toExportObjects(input.doc.objects) as ExportObject[],
    }
  } else {
    const spec = input.spec ?? null
    request.original = {
      figure_id: input.figureId ?? '',
      overrides: input.panel?.overrides?.length ? input.panel.overrides : undefined,
      w_mm: spec?.widthMm ?? null,
      h_mm: spec?.heightMm ?? null,
      px_w: spec?.pixelWidth ?? null,
      px_h: spec?.pixelHeight ?? null,
      source_kind: spec?.sourceKind ?? 'unknown',
      ignored: spec?.ignored ? [...spec.ignored] : [],
    }
  }
  if (input.report) request.style_check_report = input.report
  const revision = snapshotRevision(request)
  request.document_revision = revision
  return { request, names: formats.map((f) => `${filename}.${f}`), revision }
}

function clampPpi(ppi: number): number {
  if (!Number.isFinite(ppi)) return PPI_DEFAULT
  return Math.min(PPI_MAX, Math.max(PPI_MIN, Math.round(ppi)))
}

/**
 * 这一份快照的指纹。
 *
 * 量的是**「现在再导一次会不会出来另一个文件」**，不是"文档有没有被动过"：
 * 改个画布名、折叠个侧栏、撤销又重做一次，导出结果一模一样，那就不该在完成
 * 时冒一句"导出期间文档被编辑过"。所以指纹取自**将要送去合成的那份载荷**，
 * 而不是某个自增计数器。
 *
 * 只是个指纹，不是密码学摘要：这里要的是"变了没有"，碰撞的代价是一次漏报
 * 提示，不是数据错误。
 */
export function snapshotRevision(request: ExportRequest): string {
  const material = JSON.stringify({
    scope: request.scope,
    canvas: request.canvas ?? null,
    original: request.original ?? null,
  })
  // FNV-1a 32 位，两轮不同种子拼成 64 位：短、稳定、跨平台逐字节一致
  return `${fnv1a(material, 0x811c9dc5)}${fnv1a(material, 0x01000193)}`
}

function fnv1a(text: string, seed: number): string {
  let h = seed >>> 0
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}
