import type { CanvasObject } from '@/types/document'
import { DRAW_COLOR_DEFAULT } from './drawDefaults'
import { isFillStrokeMark, isStrokedMark, markFillable, markStrokeOn, type StrokedMark } from './markStyle'

/**
 * 外观的离散操作（ADR 0185 第二批：填充与描边逐步补齐）——**纯函数**，改的是传进来的那个对象（store 层在 immer 草稿上调）。
 * 命令表在 `store/appearanceActions`，快捷键（Illustrator 习惯）：D 默认填色描边、⇧X 互换填色和描边、/ 无填充、I 吸管。
 */

/**
 * D：回到新画对象的样子（与画布工具新建的形状 / 路径 / 箭头同一组缺省）：无填充、1 pt 墨色实线，描边的附加属性全部清掉。
 * 几何（路径的填充规则、星形的角数）不动。
 */
export function applyDefaultAppearance(o: StrokedMark): void {
  o.color = DRAW_COLOR_DEFAULT
  o.strokePt = 1
  delete o.strokeOpacity
  delete o.dash
  delete o.dashPattern
  delete o.lineCap
  delete o.lineJoin
  delete o.miterLimit
  if (o.type === 'arrow') return
  delete o.stroke
  o.fill = null
  delete o.fillOpacity
  if (o.type === 'shape') delete o.strokeAlign
}

/**
 * ⇧X：互换填色和描边色（连同各自的不透明度）。只对能填充的（封闭形状 / 路径）：
 * 原来没填充 → 换完没描边；原来没描边 → 换完没填充。
 */
export function swapFillStroke(o: StrokedMark): boolean {
  if (!isFillStrokeMark(o) || !markFillable(o)) return false
  const fill = o.fill
  const fillOpacity = o.fillOpacity
  const stroke = markStrokeOn(o) ? o.color : null
  const strokeOpacity = markStrokeOn(o) ? o.strokeOpacity : undefined
  o.fill = stroke
  if (stroke && strokeOpacity != null) o.fillOpacity = strokeOpacity
  else delete o.fillOpacity
  if (fill) {
    o.color = fill
    delete o.stroke
    if (fillOpacity != null) o.strokeOpacity = fillOpacity
    else delete o.strokeOpacity
  } else {
    o.stroke = false
    delete o.strokeOpacity
  }
  return true
}

/** /：无填充（只对能填充的） */
export function clearFill(o: StrokedMark): boolean {
  if (!isFillStrokeMark(o) || !markFillable(o) || o.fill == null) return false
  o.fill = null
  delete o.fillOpacity
  return true
}

/** 吸管吸到的颜色：填充色 / 描边色（小写 #rrggbb），吸不到的那一样是 null */
export interface ColorSample {
  fill: string | null
  stroke: string | null
}

/** CSS 计算出来的颜色串 → 小写 #rrggbb；透明 / none / 读不懂 → null */
export function parseCssColor(v: string | null | undefined): string | null {
  if (!v) return null
  const s = v.trim().toLowerCase()
  if (s === 'none' || s === 'transparent' || s.startsWith('url(')) return null
  const hex = /^#([0-9a-f]{6}|[0-9a-f]{3})$/.exec(s)
  if (hex) {
    const h = hex[1]
    return h.length === 3 ? `#${h.split('').map((c) => c + c).join('')}` : `#${h}`
  }
  const m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+%?))?\s*\)$/.exec(s)
  if (!m) return null
  if (m[4] != null) {
    const a = m[4].endsWith('%') ? Number(m[4].slice(0, -1)) / 100 : Number(m[4])
    if (!(a > 0)) return null
  }
  const to2 = (x: string) => Math.max(0, Math.min(255, Math.round(Number(x)))).toString(16).padStart(2, '0')
  return `#${to2(m[1])}${to2(m[2])}${to2(m[3])}`
}

/**
 * 吸管落到对象上：吸到的填充色给能填充的对象当填充、吸到的描边色给描边（Illustrator 吸管的默认：两样都取）。
 * 只吸到一样时：线状的东西（直线、曲线、箭头）只有描边——把它的颜色给描边；块状的只有填充——给填充，
 * 目标不能填充时退而给描边（把一根柱子的颜色吸给一支箭头）。回改没改。
 */
export function applyColorSample(o: CanvasObject, s: ColorSample): boolean {
  if (!isStrokedMark(o)) return false
  let changed = false
  const fillable = isFillStrokeMark(o) && markFillable(o)
  if (s.fill && fillable) {
    if (isFillStrokeMark(o) && o.fill !== s.fill) {
      o.fill = s.fill
      changed = true
    }
  }
  const strokeColor = s.stroke ?? (!fillable ? s.fill : null)
  if (strokeColor) {
    if (o.color !== strokeColor) {
      o.color = strokeColor
      changed = true
    }
    if (isFillStrokeMark(o) && o.stroke === false && s.stroke) {
      delete o.stroke
      changed = true
    }
  }
  return changed
}
