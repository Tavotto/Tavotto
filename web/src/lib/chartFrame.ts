/** Prototype paired-frame consumer. Science edits are stored in source S.
 * This module owns no store, undo stack, network request or persistence writer.
 * Its draft mutation is intended to run inside the existing document transaction.
 */
import type { FigureDocument, PanelChartFrame, PanelObject, PanelOverride } from '@/types/document'
import { frameGeometryPatch, rebaseFrameOverride } from '@/lib/figureFrame'

export interface ComputedRenderFrame {
  version: 1
  source: 'computed-svg'
  source_viewport_pt: [number, number]
  rect_pt: [number, number, number, number]
  edit_basis: 'source-viewport-pt'
}
/** Layout basis only. Native sizes already store F's dimensions. No second model.
 * Persisted shape's single origin is `PanelChartFrame` in `@/types/document`. */
export type ComputedChartView = PanelChartFrame
export type ComputedPanel = PanelObject & { chartFrame?: ComputedChartView }
export interface PairedFrameAuthority {
  inputIdentity: string
  renderRevision: string
  frame: ComputedRenderFrame
}
const MM_PER_PT = 25.4/72

export function validComputedFrame(raw: unknown): raw is ComputedRenderFrame {
  if (!raw || typeof raw !== 'object') return false
  const f = raw as ComputedRenderFrame
  const keys = Object.keys(f).sort().join(',')
  if (keys !== 'edit_basis,rect_pt,source,source_viewport_pt,version'
    || f.version !== 1 || f.source !== 'computed-svg' || f.edit_basis !== 'source-viewport-pt') return false
  if (!Array.isArray(f.source_viewport_pt) || f.source_viewport_pt.length !== 2
    || !Array.isArray(f.rect_pt) || f.rect_pt.length !== 4
    || ![...f.source_viewport_pt,...f.rect_pt].every(x => typeof x === 'number' && Number.isFinite(x))) return false
  const [sw,sh]=f.source_viewport_pt,[l,t,w,h]=f.rect_pt
  return Math.min(sw,sh,w,h)>0 && l<=0 && t<=0 && l+w>=sw && t+h>=sh
}

function checked(authority: PairedFrameAuthority, inputIdentity: string): ComputedRenderFrame {
  if (!authority.renderRevision || !inputIdentity || authority.inputIdentity !== inputIdentity || !validComputedFrame(authority.frame)) {
    throw new Error('exact computed frame is stale or unavailable')
  }
  return authority.frame
}

/** Convert one newly authored F-relative edit; never run on a restored full S list. */
export function sourceFrameOverride(edit: PanelOverride, authority: PairedFrameAuthority, inputIdentity: string): PanelOverride {
  const f=checked(authority,inputIdentity),[sw,sh]=f.source_viewport_pt,[l,t,w,h]=f.rect_pt
  return rebaseFrameOverride(edit,{ basis_mm:[w,h], frame_mm:[-l,-t,sw,sh] })
}

/** The inverse of `sourceFrameOverride` for a drag baseline: a persisted S-space
 * anchor (an override value) expressed in F-space fractions, so a DOM delta
 * measured in F can be added before the write-side adapter rebases once. */
export function sourceFrameValue(value: [number, number], authority: PairedFrameAuthority, inputIdentity: string): [number, number] {
  const f=checked(authority,inputIdentity),[sw,sh]=f.source_viewport_pt,[l,t,w,h]=f.rect_pt
  return [value[0]*(sw/w)+(-l/w), value[1]*(sh/h)+(-t/h)]
}

/** Move only the outer frame. Source S patches, data, units and science anchor stay. */
export function computedFramePatch(panel: ComputedPanel, authority: PairedFrameAuthority, inputIdentity: string) {
  const f=checked(authority,inputIdentity)
  return framePatchFrom(panel,f)
}

/** `computedFramePatch` 的本体（origin delta 折进 x/y、重算 crop、native 尺寸与
 * chartFrame 一起写）。fallible 的输入先算再改，失败抛错由调用方决定回滚语义。 */
function framePatchFrom(panel: ComputedPanel, f: ComputedRenderFrame) {
  if (!validComputedFrame(f)) throw new Error('exact computed frame is stale or unavailable')
  const [l,t,w,h]=f.rect_pt
  const [oldL,oldT]=panel.chartFrame?.originPt ?? [0,0]
  if (![oldL,oldT].every(Number.isFinite)) throw new Error('invalid previous layout frame origin')
  const patch=frameGeometryPatch(panel,{
    basis_mm:[panel.nativeW,panel.nativeH],
    frame_mm:[(l-oldL)*MM_PER_PT,(t-oldT)*MM_PER_PT,w*MM_PER_PT,h*MM_PER_PT],
  },{rebaseOverrides:false})
  if (!patch) throw new Error('new frame does not intersect the user crop')
  return { ...patch, chartFrame:{version:1,renderProfile:'native-manifest/2',originPt:[l,t]} as ComputedChartView }
}

/**
 * 被动变体（`useEngineSync` 的 nativeSizeFixes）：/2 渲染带回的 `render_frame` 原点与
 * 面板持久化的 `originPt` 不一致时，走**同一个**帧适配（origin delta 进 placement、
 * crop 重算、native 尺寸同批写）——被动同步本身就是渲染到达路径，不走
 * `exactFrameAuthority` 的闸。帧无效或与用户裁剪不相交时返回 null：调用方保持
 * 现状（持久化的坐标合同不因一次回退删掉），等下一次渲染再对版本。
 */
export function passiveComputedFramePatch(panel: ComputedPanel, renderFrame: ComputedRenderFrame):
  ReturnType<typeof framePatchFrom> | null {
  try {
    return framePatchFrom(panel,renderFrame)
  } catch {
    return null
  }
}

/** One existing transaction may call this after obtaining the exact paired frame.
 * The current page is the default, even when the frame includes blank negative
 * margins. Expanding a page is a separate explicit user intent, never inferred
 * from a negative outer frame or an unproved painted-ink rectangle.
 */
export function applyComputedFrameDraft(
  doc: FigureDocument, objectIndex: number, expectedPanelId: string,
  authority: PairedFrameAuthority, inputIdentity: string,
  opts: { expandPage?: boolean } = {},
): { dx: number; dy: number } {
  const panel=doc.objects[objectIndex]
  if (panel?.type !== 'panel' || panel.id !== expectedPanelId) throw new Error('panel path changed')
  const patch=computedFramePatch(panel,authority,inputIdentity)
  // Compute every fallible input before mutating the supplied draft.
  let dx=0,dy=0,right=doc.page.w,bottom=doc.page.h
  if (opts.expandPage) {
    const boxes=doc.objects.map((o,i) => i===objectIndex ? {...o,...patch} : o)
    if (boxes.some(o=>![o.x,o.y,o.w,o.h].every(Number.isFinite))) throw new Error('invalid page geometry')
    dx=-Math.min(0,...boxes.map(o=>o.x));dy=-Math.min(0,...boxes.map(o=>o.y))
    right=Math.max(doc.page.w,...boxes.map(o=>o.x+o.w))+dx
    bottom=Math.max(doc.page.h,...boxes.map(o=>o.y+o.h))+dy
  }
  Object.assign(panel,patch)
  if (opts.expandPage) {
    for (const o of doc.objects) { o.x+=dx; o.y+=dy }
    for (const g of doc.guides) g.pos+=g.axis==='x' ? dx : dy
    doc.page.w=right;doc.page.h=bottom
  }
  return {dx,dy}
}
