import type { EditableField, Manifest } from '@/lib/api'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { panelRotation, type PanelObject } from '@/types/document'

/** 单图拖边改图幅要的事实：此刻的图幅（脚本 mm）与引擎给 `size_mm` 的界 */
export interface FigureFrameFacts {
  sizeMm: [number, number]
  limits: { min?: number; max?: number }
}

/**
 * 这张面板此刻能不能拖边改图幅，能就给出事实。**权威只认精确的 manifest**（ADR 0017，调用方传
 * `exactPanelManifest`）：没有 manifest / 图元素没有可编辑的 `size_mm`（只读图、旧后端）、面板被裁过或旋转过
 * （页面上的框不再等于图幅 × 缩放比）、尺寸读不出来，一律 null——入口停摆，不猜。
 * 当前值与属性栏「图幅」同一个读法：用户改过的 override 优先于渲染时的初值。
 */
export function figureFrameFacts(panel: PanelObject, manifest: Manifest | null): FigureFrameFacts | null {
  if (!manifest || panel.crop || panelRotation(panel) !== 0) return null
  const field: EditableField | undefined = manifest.elements
    .find((e) => e.gid === 'figure')
    ?.editable.find((f) => f.prop === 'size_mm')
  if (!field) return null
  const ov = effectiveOverride(panel.overrides, 'figure', 'size_mm')
  const v = ov ? ov.value : field.value
  if (!Array.isArray(v) || v.length !== 2) return null
  const [w, h] = v as unknown[]
  if (typeof w !== 'number' || typeof h !== 'number' || !(w > 0) || !(h > 0)) return null
  return { sizeMm: [w, h], limits: { min: field.min, max: field.max } }
}
