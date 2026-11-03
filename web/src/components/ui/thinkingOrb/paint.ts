/**
 * 思考小球的画笔（ADR 0165）：上游按深浅直接写灰度值，这里统一用一种墨色（canvas 计算后的 `color`，
 * 即 `text-ink` / `text-ink-3` 的语义 token），深浅只用 `globalAlpha` 表示——亮暗主题因此同源，换主题只换墨色。
 *
 * 像素对齐（10-31 图标清晰度同一做法）：半径不到 0.6 个物理像素的点不画圆，画落在整像素上的 1 物理像素方块；
 * 其余点的圆心吸附到 0.5 物理像素。
 */
import type { OrbFrame } from './engine/core'

/** 最远的点的不透明度下限：20px 及以下提到 0.32，不然远点直接看不见 */
export function alphaFloor(size: number): number {
  return size <= 20 ? 0.32 : 0.18
}

/** white：0 = 最近最浓，1 = 最远最淡（上游约定） */
export function inkAlpha(white: number, a: number | undefined, floor: number): number {
  const w = Math.min(1, Math.max(0, white))
  return (floor + (1 - floor) * (1 - w) ** 1.15) * (a ?? 1)
}

/** `ink`：canvas 自己的计算后 `color`（即语义 token 的值），深浅只经 `globalAlpha` */
export function paintOrbFrame(ctx: CanvasRenderingContext2D, frame: OrbFrame, ink: string, size: number, dpr: number): void {
  const floor = alphaFloor(size)
  const px = 1 / dpr
  ctx.fillStyle = ink
  ctx.strokeStyle = ink
  const snap = (v: number) => (Math.round(v * dpr - 0.5) + 0.5) / dpr
  for (const l of frame.lines) {
    ctx.globalAlpha = inkAlpha(l.white, l.a, floor) * 0.75
    ctx.lineWidth = Math.max(px, l.w * 0.8)
    ctx.beginPath()
    ctx.moveTo(snap(l.x1), snap(l.y1))
    ctx.lineTo(snap(l.x2), snap(l.y2))
    ctx.stroke()
  }
  for (const d of frame.dots) {
    ctx.globalAlpha = inkAlpha(d.white, d.a, floor)
    if (d.r * dpr < 0.6) {
      ctx.fillRect(Math.round(d.x * dpr - 0.5) / dpr, Math.round(d.y * dpr - 0.5) / dpr, px, px)
    } else {
      ctx.beginPath()
      ctx.arc(snap(d.x), snap(d.y), d.r, 0, Math.PI * 2)
      ctx.fill()
    }
  }
  ctx.globalAlpha = 1
}
