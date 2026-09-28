/**
 * 左栏「样式」面板的纯计算：摆哪几行、每一格读哪些元素、页面上的值。
 *
 * 面板只**看与改**一张图的样式，不判规范，也不显示问题（用户 2026-09-26：问题只在「问题」面板里看）。
 *
 * 行 → 角色 × 属性的对应与 `lib/stylePresets.STYLE_ROLE_PROPS` 同一套：字号在
 * `legend` 容器上（引擎把它广播给每一条图例项），字体与粗 / 斜体在 `legend_text` 上（容器没有
 * `fontfamily` / `weight` / `style`）。
 */
import type { Manifest, ManifestElement } from './api'
import { toPageValue } from './stylePresets'
import type { ControlValue } from '@/components/inspector/textStyleModel'

/**
 * 「文字」分组里图内的四行：字号在 `sizeRole` 上、字体在 `familyRole` 上、粗体 / 斜体
 * （`weight` / `style`）在 `faceRole` 上。刻度文字的引擎字段里没有 `weight` / `style`
 * （`manifest._tick_fields`），`faceRole` 为 null：不摆一个点了不生效的开关。
 */
export const FIGURE_TEXT_ROWS = [
  { id: 'title', sizeRole: 'title', familyRole: 'title', faceRole: 'title' },
  { id: 'axis_label', sizeRole: 'axis_label', familyRole: 'axis_label', faceRole: 'axis_label' },
  { id: 'ticks', sizeRole: 'ticks', familyRole: 'ticks', faceRole: null },
  { id: 'legend', sizeRole: 'legend', familyRole: 'legend_text', faceRole: 'legend_text' },
] as const

/** 「线条」分组：数据线宽、边框线宽、刻度方向、刻度长度、刻度线宽 */
export const FIGURE_LINE_ROWS = [
  { id: 'dataLine', role: 'line', prop: 'linewidth' },
  { id: 'frame', role: 'axes', prop: 'spine_linewidth' },
  { id: 'tickDirection', role: 'ticks', prop: 'direction' },
  { id: 'tickLength', role: 'ticks', prop: 'length' },
  { id: 'tickWidth', role: 'ticks', prop: 'width' },
] as const

export type FigureTextRowId = (typeof FIGURE_TEXT_ROWS)[number]['id']
export type FigureLineRowId = (typeof FIGURE_LINE_ROWS)[number]['id']

/** 这个角色里**暴露了这条属性**的元素（没暴露的元素不参与读值，也不参与写） */
export function elementsWith(
  manifest: Manifest | null,
  role: string,
  prop: string,
): ManifestElement[] {
  if (!manifest) return []
  return manifest.elements.filter(
    (e) => e.role === role && e.editable.some((f) => f.prop === prop),
  )
}

/**
 * 脚本坐标系里的读数 → 页面上的读数。**四档不压扁**：mixed 仍是 mixed（不拿第一个
 * 冒充全部）、unavailable 仍是 unavailable；只有 uniform 的那个数乘缩放比。
 */
export function pageValueOf(v: ControlValue, prop: string, scale: number): ControlValue {
  return v.kind === 'uniform' ? { kind: 'uniform', value: toPageValue(prop, v.value, scale) } : v
}
