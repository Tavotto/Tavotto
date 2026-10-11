import type { ManifestElement } from './api'
import { round4, sameAfterRound4, type Rect4 } from './axesLayout'
import type { ResizeDir } from './geometry'
import type { PanelOverride } from '@/types/document'

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const tuple = (v: unknown, n: number): v is number[] =>
  Array.isArray(v) && v.length === n && v.every(finite)

/**
 * 独立形状的外框（figure 分数、y 向下）。能力由引擎的 size_frac 字段声明，
 * 不复用 axes 的 resizable / position：形状平移仍走 pos_frac 与既有随行规则。
 * shape_bounds 是未裁剪的真实外框，包含 FancyBboxPatch 的 padding。
 */
export function patchBoundsOf(el: ManifestElement): Rect4 | null {
  const field = el.editable.find((f) => f.prop === 'size_frac')
  if (!field || !tuple(el.shape_bounds, 4) || el.shape_bounds[2] <= 0 || el.shape_bounds[3] <= 0) return null
  // 只读已渲染的实况。尺寸覆盖可能被引擎拒绝（例如改子图后小于圆角下限），
  // 文档里仍有它不等于画面上采用了它；预览期间的新框另在 elementPreview / draft 里。
  return [...el.shape_bounds]
}

/** 向上量化到 override 的精度，避免松手 round4 后小于引擎要求的 padding / 圆角下限。 */
export function patchMinSize(el: ManifestElement): [number, number] {
  const size = tuple(el.shape_min_size, 2) ? el.shape_min_size : [0, 0]
  return size.map((n) => Math.max(1e-4, Math.ceil(n * 1e4) / 1e4)) as [number, number]
}

/** 八方向自由改宽高；对边 / 对角固定。不钳到图幅，脚本中的框可以跨出图幅。 */
export function resizePatchBox(
  start: Rect4,
  dir: ResizeDir,
  dx: number,
  dy: number,
  min: [number, number],
): Rect4 {
  let [left, top, width, height] = start
  const right = left + width
  const bottom = top + height
  if (dir.includes('w')) {
    left = Math.min(left + dx, right - min[0])
    width = right - left
  } else if (dir.includes('e')) width = Math.max(min[0], width + dx)
  if (dir.includes('n')) {
    top = Math.min(top + dy, bottom - min[1])
    height = bottom - top
  } else if (dir.includes('s')) height = Math.max(min[1], height + dy)
  return [left, top, width, height]
}

/**
 * 只在左下锚点移动时一并写位置，未移动则保留脚本的相对定位。
 * 文字、箭头、内层框不缩放；拖框整体移动继续使用 patchContents。
 */
export function patchResizePatches(el: ManifestElement, start: Rect4, next: Rect4): PanelOverride[] {
  if (!tuple(next, 4) || sameAfterRound4(start, next)) return []
  const min = patchMinSize(el)
  const size = [round4(next[2]), round4(next[3])]
  if (size[0] < min[0] || size[1] < min[1]) return []
  const patches: PanelOverride[] = [{ gid: el.gid, prop: 'size_frac', value: size }]
  const anchor = [next[0], next[1] + next[3]]
  // 写入未变的位置会把 data / axes 坐标钉到 figure 上，之后改子图布局时框就不再跟随。
  if (!sameAfterRound4([start[0], start[1] + start[3]], anchor)) {
    patches.push({ gid: el.gid, prop: 'pos_frac', value: anchor.map(round4) })
  }
  return patches
}
