/**
 * 「按原图尺寸导出」这次导的是哪一张 —— 候选清单与默认对象（用户反馈 06）。
 *
 * 改造前对话框只认快速编辑正在编的那张（`workspace.activePanelId`），画布排版里
 * 选中一个面板再打开导出，它仍然说「先选中一张图」：`activePanelId` 按 ADR 0028
 * 只在 `mode === 'fast_edit'` 时非空，画布模式的选区在 `selectionStore` 里，
 * 而对话框没有读它。用户照着提示回画布点了图，按钮还是灰的。
 *
 * 现在的规则只有这一处：
 *
 * ```text
 * listExportableFigures()   项目里能按原图导的图：文档里的面板（所有画布）优先，
 *                           其次素材清单 / runtime 清单里还没上画布的
 * contextFigureId()         不用用户开口就知道的那张：快速编辑正在编的 →
 *                           画布上选中的面板（主选优先）→ 项目里只有一张时就是它
 * ```
 *
 * 对话框在这之上只加一层「用户在列表里点过哪一张」。**这里不判能不能导**：
 * 那是 `exportRequest.originalAvailability()` 的事（源文件在不在、规格认不认识），
 * 两份判据各管一件事，列表里照样列出源文件不见了的图——点它会得到一句
 * 「源文件找不到」而不是它凭空消失（ADR 0031 §六：不隐藏、说原因）。
 */
import { panelSrc } from './api'
import { engineTransport } from './engineTransport'
import { stemOf } from './openRequest'
import { useAssetStore } from '@/store/assetStore'
import { findFigurePanel, useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useWorkspaceStore } from '@/store/workspace'
import type { CanvasObject, PanelObject } from '@/types/document'

export type ExportableFigureKind = 'pdf' | 'raster' | 'runtime' | 'unknown'

export interface ExportableFigure {
  figureId: string
  /** 用户看得见的名字：面板改过名用面板名，否则文件名主干 */
  name: string
  /** 文档里代表它的面板（所有画布里找）；素材清单里还没上画布的图是 null */
  panel: PanelObject | null
  kind: ExportableFigureKind
  /** 缩略图的缓存换代：磁盘图是 mtime，runtime 图是预览换代计数 */
  stamp: number | undefined
  /** runtime 图：materialized cache 里此刻有没有可显示的预览 */
  cached: boolean
  /** 素材自报的图幅（mm），缩略图定宽高比用；没有就 null */
  sizeMm: [number, number] | null
}

const isPanel = (o: CanvasObject): o is PanelObject => o.type === 'panel'

function kindOf(panel: PanelObject | null, assetKind: string | undefined): ExportableFigureKind {
  const k = panel?.fileKind ?? assetKind
  if (k === 'pdf' || k === 'raster' || k === 'runtime') return k
  return 'unknown'
}

/**
 * 项目里能按原图尺寸导出的图，**文档里的排前面**（它们才是用户「目前的几张图」），
 * 同一张素材在几个画布上各有一个面板时只列一次（激活画布优先，与
 * `findFigurePanel()` 同一个优先级）。
 */
/**
 * 文档里的一个面板 → 它作为「一张图」的描述（名字、画法、缩略图换代、图幅）。
 * 导出候选清单与问题面板的缩略图共用这一份（2026-09-28 问题面板卡片化）。
 */
export function figureOfPanel(p: PanelObject): ExportableFigure {
  const info = useAssetStore.getState().byId[p.fileId]
  const runtimeStore = useRuntimeAssetStore.getState()
  const runtime = (runtimeStore.assets ?? []).find((a) => a.id === p.fileId)
  const kind = kindOf(p, info?.kind)
  return {
    figureId: p.fileId,
    name: p.name?.trim() || stemOf(p.fileId),
    panel: p,
    kind,
    stamp: kind === 'runtime' ? runtimeStore.previewNonce[p.fileId] : info?.mtime,
    cached: kind === 'runtime' ? (runtimeStore.byId[p.fileId]?.cached ?? runtime?.cached ?? false) : true,
    sizeMm:
      p.nativeW > 0 && p.nativeH > 0
        ? [p.nativeW, p.nativeH]
        : info
          ? [info.native_w_mm, info.native_h_mm]
          : (runtime?.size_mm ?? null),
  }
}

/**
 * 缩略图的图片地址：认得出是哪张图就够（核对修改归画布）。画法未知、runtime 图
 * 还没有缓存预览时给 null——不猜地址。内嵌画布走它自己的传输层。
 */
export function figureThumbSrc(figure: ExportableFigure, bucket: number): string | null {
  if (figure.kind === 'unknown' || (figure.kind === 'runtime' && !figure.cached)) return null
  const transport = engineTransport()
  return transport
    ? transport.panelSrc(figure.figureId, figure.kind, bucket, figure.stamp)
    : panelSrc(figure.figureId, figure.kind, bucket, figure.stamp)
}

export function listExportableFigures(): ExportableFigure[] {
  const doc = useDocumentStore.getState()
  const assets = useAssetStore.getState()
  const runtimeStore = useRuntimeAssetStore.getState()
  const out: ExportableFigure[] = []
  const seen = new Set<string>()

  const pushPanel = (p: PanelObject) => {
    if (seen.has(p.fileId)) return
    seen.add(p.fileId)
    out.push(figureOfPanel(p))
  }
  doc.doc.objects.filter(isPanel).forEach(pushPanel)
  for (const c of doc.canvases) {
    if (c.id === doc.activeCanvasId) continue
    c.objects.filter(isPanel).forEach(pushPanel)
  }

  for (const info of assets.panels) {
    if (seen.has(info.id)) continue
    seen.add(info.id)
    out.push({
      figureId: info.id,
      name: stemOf(info.id),
      panel: null,
      kind: kindOf(null, info.kind),
      stamp: info.mtime,
      cached: true,
      sizeMm: [info.native_w_mm, info.native_h_mm],
    })
  }
  for (const a of runtimeStore.assets ?? []) {
    if (seen.has(a.id)) continue
    seen.add(a.id)
    out.push({
      figureId: a.id,
      name: a.stem,
      panel: null,
      kind: 'runtime',
      stamp: runtimeStore.previewNonce[a.id],
      cached: runtimeStore.byId[a.id]?.cached ?? a.cached,
      sizeMm: a.size_mm,
    })
  }
  return out
}

/**
 * 不用用户开口就知道要导哪一张。三条按顺序问，答不上来就是 `null`
 * （那时由对话框里的列表让用户点）。
 *
 * 1. 快速编辑正在编的那张（`activePanelId`，ADR 0028 的工作区状态）；
 * 2. 画布上选中的面板：**主选优先**（`selectionStore` 的末位），主选不是面板
 *    （选的是文字 / 标注）就退到选区里的第一个面板——多选里混着一张图时，
 *    用户的意思八成是那张图；
 * 3. 上面都没有，而整个项目只有一张图：就是它。一张图的项目不该被要求「先选」。
 */
function contextPanel(): PanelObject | null {
  const objects = useDocumentStore.getState().doc.objects
  const active = useWorkspaceStore.getState().activePanelId
  if (active) {
    const o = objects.find((x) => x.id === active)
    if (o && isPanel(o)) return o
  }
  const ids = useSelectionStore.getState().ids
  const primary = objects.find((x) => x.id === ids.at(-1))
  if (primary && isPanel(primary)) return primary
  const firstPanel = ids
    .map((id) => objects.find((x) => x.id === id))
    .find((o): o is PanelObject => !!o && isPanel(o))
  return firstPanel ?? null
}

export function contextFigureId(figures: readonly ExportableFigure[]): string | null {
  return contextPanel()?.fileId ?? (figures.length === 1 ? figures[0].figureId : null)
}

/** Export keeps the chosen instance; a pinned id never falls back after removal/relink. */
export function findExportPanel(
  figureId: string,
  panelId?: string,
): { panel: PanelObject; canvasId: string } | null {
  const chosen = panelId ?? contextPanel()?.id
  if (chosen) {
    const found = findFigurePanel(figureId, chosen)
    if (found || panelId !== undefined) return found
  }
  return findFigurePanel(figureId)
}
