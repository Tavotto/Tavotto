/**
 * 左栏「样式」面板的纯计算：摆哪几行、每一格读哪些元素、页面上的值、这一格有没有问题。
 *
 * 面板只**看与改**一张图的样式，不判规范：「这一格不合规」读的是问题清单
 * （`validationStore` 那一条链，`preflight.runSpec` 唯一求值），这里只按
 * 「哪个对象 · 哪些元素 · 哪条属性」把已有的问题认回到格子上——不写第二份判据。
 *
 * 行 → 角色 × 属性的对应与 `lib/stylePresets.STYLE_ROLE_PROPS` 同一套：字号在
 * `legend` 容器上（引擎把它广播给每一条图例项），字体与粗 / 斜体在 `legend_text` 上（容器没有
 * `fontfamily` / `weight` / `style`）。
 */
import type { Manifest, ManifestElement } from './api'
import { SEVERITIES } from './profile'
import { propertyPathOf } from './typography'
import { toPageValue } from './stylePresets'
import type { ValidationIssue } from './validation'
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

/** 一格的「主语」：哪个对象、其中哪些元素（画布标注没有元素，`gids` 为 null）、哪几条属性 */
export interface CellSubject {
  objectIds: readonly string[]
  gids: readonly string[] | null
  props: readonly string[]
}

const RANK = new Map(SEVERITIES.map((s, i) => [s, i]))

/**
 * 落在这一格上的问题，按等级从重到轻。判据只有三条且全来自问题本身：
 * `objectRef.objectId`、`objectRef.gid`、`propertyPath`——与问题面板定位用的同一组字段。
 */
export function cellIssues(issues: readonly ValidationIssue[], cell: CellSubject): ValidationIssue[] {
  const ids = new Set(cell.objectIds)
  const gids = cell.gids ? new Set(cell.gids) : null
  const props = new Set(cell.props)
  return issues
    .filter(
      (i) =>
        !!i.objectRef.objectId &&
        ids.has(i.objectRef.objectId) &&
        (gids ? !!i.objectRef.gid && gids.has(i.objectRef.gid) : !i.objectRef.gid) &&
        !!i.propertyPath &&
        props.has(i.propertyPath),
    )
    .sort((a, b) => (RANK.get(a.severity) ?? 99) - (RANK.get(b.severity) ?? 99))
}

/** 画布标注那一行两格认的属性路径（与预检报的同一张表） */
export const CANVAS_SIZE_PATH = propertyPathOf('canvasText', 'sizePt') ?? 'sizePt'
export const CANVAS_FAMILY_PATH = propertyPathOf('canvasText', 'fontFamily') ?? 'fontFamily'
export const CANVAS_WEIGHT_PATH = propertyPathOf('canvasText', 'weight') ?? 'bold'
export const CANVAS_STYLE_PATH = propertyPathOf('canvasText', 'style') ?? 'italic'
