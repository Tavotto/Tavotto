/**
 * 图内元素在画布上的落位：manifest 的分数框（figure 分数、top-origin）→ 页面 / 屏幕。
 *
 * 画布画这张图时，内容先在内容空间翻转、再旋转（`lib/panelTransform`，`PanelView` 的 CSS transform）。
 * 叠在图上说「这个元素在这儿」的东西——选中框与手柄（`OverlaySvg.ElementBoxes`）、拖动读数
 * （`MeasureChip`）、问题标记——都从这里取同一个变换；各写一遍的话翻转面板上总有一处漏掉，框就停在
 * 元素的镜像位置上（#832 / #833 评审）。
 */
import type { Rect4 } from '@/lib/axesLayout'
import { panelFullRect } from '@/lib/elementGeom'
import type { Rect, ResizeDir } from '@/lib/geometry'
import {
  contentToPageVec,
  pageToContentVec,
  panelContentTransform,
  panelTransformSvg,
} from '@/lib/panelTransform'
import type { PanelObject } from '@/types/document'

/**
 * 元素分数框 → 页面上看得见的外接框（mm）。面板只有直角旋转与镜像，变换后仍是轴对齐的框：
 * 中心按 `contentToPageVec` 绕面板中心落位，90 / 270 时宽高互换。
 */
export function elementRectOnPage(panel: PanelObject, box: Rect4): Rect {
  const full = panelFullRect(panel)
  const t = panelContentTransform(panel)
  const cx = panel.x + panel.w / 2
  const cy = panel.y + panel.h / 2
  const w = box[2] * full.w
  const h = box[3] * full.h
  const [ox, oy] = contentToPageVec(t, full.x + box[0] * full.w + w / 2 - cx, full.y + box[1] * full.h + h / 2 - cy)
  const [rw, rh] = t.rotate === 90 || t.rotate === 270 ? [h, w] : [w, h]
  return { x: cx + ox - rw / 2, y: cy + oy - rh / 2, w: rw, h: rh }
}

/** 内容分数位移 → 页面位移（mm）：元素在画面上真正走的方向 */
export function elementDeltaOnPage(panel: PanelObject, dfx: number, dfy: number): [number, number] {
  const full = panelFullRect(panel)
  return contentToPageVec(panelContentTransform(panel), dfx * full.w, dfy * full.h)
}

/**
 * 覆盖层的 SVG 变换：框与手柄在**未变换的内容屏幕坐标**里算（`panelFullRect` 直接 `toScreen`），整组用它
 * 绕面板屏幕框中心翻转、旋转到画面上。`panelBox` 是面板在屏幕上的框（px）。
 */
export function elementOverlayTransform(
  panel: PanelObject,
  panelBox: { x: number; y: number; w: number; h: number },
): string | undefined {
  return panelTransformSvg(panelContentTransform(panel), panelBox.x + panelBox.w / 2, panelBox.y + panelBox.h / 2)
}

const dirVec = (d: ResizeDir): [number, number] => [
  d.includes('e') ? 1 : d.includes('w') ? -1 : 0,
  d.includes('s') ? 1 : d.includes('n') ? -1 : 0,
]
/** 直角旋转 / 镜像后的单位方向回到方位名（纵向字母在前，与 `ResizeDir` 的拼法一致） */
const vecDir = ([x, y]: [number, number]): ResizeDir =>
  ((y < -0.5 ? 'n' : y > 0.5 ? 's' : '') + (x > 0.5 ? 'e' : x < -0.5 ? 'w' : '')) as ResizeDir

/**
 * 内容空间的方位（图内元素框的手柄按内容坐标摆）→ 它在画面上朝哪：翻转 / 旋转之后的方位。手柄光标要按
 * 画面上的方位取——翻转面板上内容的「东北」角画在西北，光标还按东北给就是斜反了。
 */
export function contentDirOnPage(panel: PanelObject, dir: ResizeDir): ResizeDir {
  return vecDir(contentToPageVec(panelContentTransform(panel), ...dirVec(dir)))
}

/** `contentDirOnPage` 的逆：画面上的方位（裁剪框的手柄按画面摆）→ 内容空间里是哪一边 */
export function pageDirInContent(panel: PanelObject, dir: ResizeDir): ResizeDir {
  return vecDir(pageToContentVec(panelContentTransform(panel), ...dirVec(dir)))
}

