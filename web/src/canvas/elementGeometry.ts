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
import type { Rect } from '@/lib/geometry'
import { contentToPageVec, panelContentTransform, panelTransformSvg } from '@/lib/panelTransform'
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
