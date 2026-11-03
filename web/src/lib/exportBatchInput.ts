/**
 * 「每张图单独导出」里**一张图的完整请求输入**，在一个同步块里读活 store 算出来（2026-10-21）。
 *
 * 为什么是个独立函数：单张导出的输入由对话框渲染时的 memo 链算（`ExportDialog` 里 `target` → `availability` →
 * `resolved` → `original` → `summary` → `buildRequestInput`），那条链只能「摆到一张图、等一次渲染」，对一批图没法在
 * 同一时刻算完。这里把**同一条链**按同一批库函数（`findExportPanel` / `originalAvailability` / `resolveDocumentSpec` /
 * `originalValidationFor` / `summaryFor` / `buildProofPayload`）写成一次同步调用——调用方对整批图在**同一个同步块**里
 * 连着调它（中间没有任何 await / then / 定时器），所以整批读的是同一个时刻的 store，等价于快照；
 * 与单张导出的一致性由 `ExportDialog.test.tsx` 的「批的请求体 == 单张导出的请求体」钉着。
 *
 * 只读 store，不写任何东西；route 由调用方先 await 好传进来（它是后端回答，不是文档状态）。
 */
import { artifactValidationIssue } from '@/lib/artifactValidation'
import {
  buildExportRequest,
  filenameProblem,
  hasRaster,
  originalAvailability,
  PPI_DEFAULT,
  type ExportRequestInput,
} from '@/lib/exportRequest'
import type { ExportableFigure } from '@/lib/exportFigures'
import { findExportPanel } from '@/lib/exportFigures'
import { copiesSourceVerbatim, type ExportRoute } from '@/lib/exportRoute'
import { buildProofPayload } from '@/lib/preflight'
import { DEFAULT_PROFILE_ID } from '@/lib/profile'
import { resolveDocumentSpec, type SpecCatalogEntry } from '@/lib/specBinding'
import { exportContextIssues, exportContextRaw, rawIssuesForObject, summaryFor } from '@/lib/validation'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { originalValidationFor, rawIssuesFor, useValidationStore } from '@/store/validationStore'

/** 整批共用的表单设置（点「导出」那一刻的值） */
export interface BatchShared {
  formats: string[]
  ppi: string
  transparent: boolean
  strict: boolean
  withReport: boolean
  /** 对话框里挑的规范 id（画布没绑规范时用它） */
  profileId: string
  catalog: SpecCatalogEntry[]
}

export type BatchInputResult =
  | { ok: true; input: ExportRequestInput }
  | { ok: false; reason: { kind: 'unavailable'; code: string } | { kind: 'filename'; code: string } | { kind: 'request'; error: unknown } }

export function buildBatchInput(
  figure: ExportableFigure,
  shared: BatchShared,
  route: ExportRoute | null,
): BatchInputResult {
  const { doc, canvases, activeCanvasId, documentId } = useDocumentStore.getState()
  const figureId = figure.figureId
  // 「每张图」= 当前画布上的图面板（隐藏的也算）；同一份素材在画布上有几个面板时取排在前面的那个
  // 选区 / 快速编辑选中的那个实例优先（与单张导出同一个判据 `findExportPanel`），但必须在当前画布上
  const context = findExportPanel(figureId)
  const onCanvas = doc.objects.find((o) => o.type === 'panel' && o.fileId === figureId)
  const target = context && context.canvasId === activeCanvasId ? context : findExportPanel(figureId, onCanvas?.id)
  const panel = target?.panel ?? null
  const availability = originalAvailability(figureId, { anyFigures: true, panel })
  if (!availability.ok) return { ok: false, reason: { kind: 'unavailable', code: availability.reason } }
  const spec = availability.spec
  const { formats } = shared
  const ppiNum = Number(shared.ppi)

  const checkCanvasId = target ? target.canvasId : activeCanvasId
  const checkOnActive = checkCanvasId === activeCanvasId
  const resolved = resolveDocumentSpec(
    checkOnActive ? (doc.profile ?? { id: shared.profileId }) : canvases.find((c) => c.id === checkCanvasId)?.profile,
    shared.catalog,
  )
  const profile = resolved.profile
  const checkProfileId = resolved.profileId ?? (checkOnActive ? shared.profileId : DEFAULT_PROFILE_ID)

  const exportIssues = exportContextIssues({ formats, dpi: ppiNum }, profile, { documentId, canvasId: checkCanvasId })
  const targetObjectId = panel ? panel.id : undefined
  const originalWidthMm = targetObjectId ? (spec?.widthMm ?? panel?.nativeW) : undefined
  const originalHeightMm = targetObjectId ? (spec?.heightMm ?? panel?.nativeH) : undefined
  const original =
    targetObjectId && originalWidthMm && originalHeightMm
      ? originalValidationFor(checkCanvasId, {
          objectId: targetObjectId,
          widthMm: originalWidthMm,
          heightMm: originalHeightMm,
          formats,
          route,
        })
      : null
  const v = useValidationStore.getState()
  const summary = summaryFor(original?.result?.issues ?? v.issues, {
    canvasId: checkCanvasId,
    objectId: targetObjectId,
    extra: exportIssues,
    ready: v.ready,
    failed: v.failed || !!original?.failed,
  })
  const errors = summary.issues.filter((i) => i.severity === 'error')
  const notVerifiable = summary.issues.filter((i) => i.severity === 'not_verifiable')
  const hasUnhandled = errors.length > 0 || notVerifiable.length > 0 || summary.failed
  const reportOn = shared.withReport || hasUnhandled

  const assets = useAssetStore.getState().byId
  const report = reportOn
    ? buildProofPayload(
        doc,
        assets,
        [
          ...(targetObjectId
            ? rawIssuesForObject(original?.result?.raw ?? rawIssuesFor(checkCanvasId), targetObjectId)
            : rawIssuesFor(checkCanvasId)),
          ...exportContextRaw({ formats, dpi: ppiNum }, profile),
        ],
        {
          dpi: ppiNum,
          formats,
          stem: figure.name,
          original:
            targetObjectId && panel && originalWidthMm && originalHeightMm
              ? { panel, widthMm: originalWidthMm, heightMm: originalHeightMm }
              : undefined,
        },
        profile,
        {
          forced: errors.length > 0,
          acknowledged: hasUnhandled ? [...new Set([...errors, ...notVerifiable].map((i) => i.ruleCode))] : [],
          checkFailed: summary.failed || !summary.ready,
          acknowledgedCheckFailed: (summary.failed || !summary.ready) && hasUnhandled,
        },
      )
    : undefined

  const copiesVerbatim = copiesSourceVerbatim({
    scope: 'original',
    specIsRaster: spec?.sourceKind === 'raster',
    panel,
    assets,
    formats,
    route,
    guardOk: !panel || !artifactValidationIssue(panel),
  })
  const transparentApplies = hasRaster(formats) && !copiesVerbatim

  const input: ExportRequestInput = {
    scope: 'original',
    formats,
    filename: figure.name,
    ppi: ppiNum || PPI_DEFAULT,
    background: shared.transparent && transparentApplies ? 'transparent' : 'white',
    includeReport: reportOn,
    strictInspection: shared.strict,
    profileId: checkProfileId,
    acknowledged: [...new Set(errors.map((i) => i.ruleCode))],
    documentId,
    doc,
    figureId,
    panel,
    spec,
    route,
    overwrite: 'ask',
    report: report as Record<string, unknown> | undefined,
  }
  const problem = filenameProblem(input.filename, input.formats)
  if (problem) return { ok: false, reason: { kind: 'filename', code: problem } }
  try {
    buildExportRequest(input)
  } catch (error) {
    return { ok: false, reason: { kind: 'request', error } }
  }
  return { ok: true, input }
}

/**
 * 「合成一个多页 PDF」那一项的请求输入（#1370）：每一页各自按它的原图口径建一次输入（同 `buildBatchInput`，格式固定
 * `pdf`），再合成一个——第一页的输入当底（检查 / 规范 / 文档快照都读它），`mergePages` 按页序带每一页的来源（同一张图
 * 写多页时重复），文件名是 PdfPages 的名字。任何一页建不出来整项失败（缺一页的多页 PDF 页序不对）。样式检查报告随这一项走（见下）。
 */
export function buildMergedPdfInput(
  figures: readonly ExportableFigure[],
  plan: { name: string; figureIds: readonly string[] },
  shared: BatchShared,
): BatchInputResult {
  const pdfShared: BatchShared = { ...shared, formats: ['pdf'] }
  const byId = new Map<string, Extract<BatchInputResult, { ok: true }>['input']>()
  for (const id of new Set(plan.figureIds)) {
    const fig = figures.find((f) => f.figureId === id)
    if (!fig) return { ok: false, reason: { kind: 'unavailable', code: 'unknown_figure' } }
    const res = buildBatchInput(fig, pdfShared, null)
    if (!res.ok) return res
    byId.set(id, res.input)
  }
  const first = byId.get(plan.figureIds[0])
  const pageInputs = [...byId.values()]
  if (!first) return { ok: false, reason: { kind: 'unavailable', code: 'unknown_figure' } }
  const input: ExportRequestInput = {
    ...first,
    formats: ['pdf'],
    filename: plan.name,
    // 报告随合成的这一项走（它是 PDF 的唯一作业）：要不要出由各页决定——用户勾了、或某页有没处理的校验问题；
    // 内容是首页的报告 + `pages`（按页序、每张图一份，各自的校验证据），不丢任何一页
    includeReport: pageInputs.some((i) => i.includeReport),
    report: pageInputs.some((i) => i.includeReport)
      ? { ...(first.report ?? {}), pages: pageInputs.map((i) => i.report ?? null) }
      : undefined,
    acknowledged: [...new Set([...byId.values()].flatMap((i) => [...(i.acknowledged ?? [])]))],
    mergePages: plan.figureIds.map((id) => {
      const i = byId.get(id)!
      return { figureId: id, panel: i.panel ?? null, spec: i.spec ?? null }
    }),
  }
  const problem = filenameProblem(input.filename, input.formats)
  if (problem) return { ok: false, reason: { kind: 'filename', code: problem } }
  try {
    buildExportRequest(input)
  } catch (error) {
    return { ok: false, reason: { kind: 'request', error } }
  }
  return { ok: true, input }
}
