import { panelRotation, rotateVec, unrotateVec, type PanelObject, type PanelRotation } from '@/types/document'

/**
 * 面板内容的变换（绕内容中心）：**先在内容空间翻转，再旋转落位**。
 *
 * 画布（`canvas/PanelView` 的 CSS transform）与时间线缩略图（`lib/timelineThumb` 的 canvas 2D）
 * 都从这一份取——各写一遍的话，新加一种变换总会只改到一边：缩略图就漏过翻转，两个只差翻转的
 * 节点画出来一模一样（Codex #679）。旋转按 `panelRotation` 归一到 90° 步进，与画布同一口径。
 */
export interface PanelContentTransform {
  rotate: PanelRotation
  scaleX: 1 | -1
  scaleY: 1 | -1
}

export function panelContentTransform(
  o: Pick<PanelObject, 'rotation' | 'flipH' | 'flipV'>,
): PanelContentTransform {
  return {
    rotate: panelRotation(o as PanelObject),
    scaleX: o.flipH ? -1 : 1,
    scaleY: o.flipV ? -1 : 1,
  }
}

/** CSS transform（从右往左应用：先 scale 翻转、再 rotate）；恒等时 `undefined` */
export function panelTransformCss(t: PanelContentTransform): string | undefined {
  return (
    [
      t.rotate ? `rotate(${t.rotate}deg)` : '',
      t.scaleX < 0 || t.scaleY < 0 ? `scale(${t.scaleX}, ${t.scaleY})` : '',
    ]
      .filter(Boolean)
      .join(' ') || undefined
  )
}

/**
 * 同一变换写成 SVG `transform`，绕 (cx, cy)（面板包围盒中心 = 内容中心，视图坐标）。SVG 的变换列表
 * 也是从右往左作用到点上：先在中心处翻转、再绕中心旋转——与 `panelTransformCss` 同一个结果。
 * 画在面板内容坐标系里的覆盖物（问题面板的元素悬停轮廓）要跟着内容一起翻转 / 旋转就用它。恒等时 `undefined`。
 */
export function panelTransformSvg(t: PanelContentTransform, cx: number, cy: number): string | undefined {
  const flip = t.scaleX < 0 || t.scaleY < 0
  return (
    [
      t.rotate ? `rotate(${t.rotate} ${cx} ${cy})` : '',
      flip ? `translate(${cx} ${cy}) scale(${t.scaleX} ${t.scaleY}) translate(${-cx} ${-cy})` : '',
    ]
      .filter(Boolean)
      .join(' ') || undefined
  )
}

/**
 * 同一变换作用到 canvas 2D 上（原点已经平移到内容中心）。canvas 的变换按调用顺序右乘，
 * 与 CSS 的「从右往左」同一个结果：先 rotate 再 scale 的调用 = 先翻转、再旋转。
 */
export function applyPanelTransform(ctx: CanvasRenderingContext2D, t: PanelContentTransform): void {
  if (t.rotate) ctx.rotate((t.rotate * Math.PI) / 180)
  if (t.scaleX < 0 || t.scaleY < 0) ctx.scale(t.scaleX, t.scaleY)
}

/**
 * 同一变换作用到向量上：内容空间（未翻转、未旋转，y 向下）→ 页面 / 屏幕空间。先翻转、再旋转，与
 * `panelTransformCss` 同序。图内元素的框、读数、吸附线落到页面都走它（`canvas/elementGeometry`）。
 */
export function contentToPageVec(t: PanelContentTransform, x: number, y: number): [number, number] {
  return rotateVec(x * t.scaleX, y * t.scaleY, t.rotate)
}

/**
 * `contentToPageVec` 的逆：页面 / 屏幕向量 → 内容空间（先反转旋转、再反翻转）。指针点选、拖动、方向键
 * 微调把屏幕上的位移折回内容分数时走它——翻转过的面板上往右拖，图里的元素在画面上也往右走。
 */
export function pageToContentVec(t: PanelContentTransform, x: number, y: number): [number, number] {
  const [u, v] = unrotateVec(x, y, t.rotate)
  return [u * t.scaleX, v * t.scaleY]
}
