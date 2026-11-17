/** DOM 预览的坐标依据；只解读后端标记，不把 SVG 几何当作 manifest 权威。 */
export interface SvgPreviewCoordinates {
  space: 'native-pt' | 'root-user-units'
  width: number
  height: number
  unitsPerPt: number
  /** /2 fractions use render F, while target DOM coordinates remain physical S pt. */
  originX?: number
  originY?: number
}

const NATIVE_VIEWPORT = 'data-tavotto-native-viewport-pt'
const POSITIVE_NUMBER = /^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/

/**
 * 只认目标所属的最近 native wrapper；坏标记返回 null，不穿过它借用祖先坐标。
 * 无标记的普通 SVG 保持既有 root viewBox / manifest sizeMm 换算及缺席回退。
 */
export function svgPreviewCoordinates(
  svg: SVGSVGElement | null,
  target: Element | null = svg,
  sizeMm?: readonly number[],
): SvgPreviewCoordinates | null {
  if (svg && target && target !== svg && !svg.contains(target)) return null
  for (let node: Element | null = svg ? target ?? svg : null; node; node = node.parentElement) {
    const raw = node.getAttribute(NATIVE_VIEWPORT)
    if (raw !== null) {
      const parts = raw.trim().split(/\s+/)
      if (parts.length !== 2 || !parts.every(part => POSITIVE_NUMBER.test(part))) return null
      const [width, height] = parts.map(Number)
      if (![width, height].every(n => Number.isFinite(n) && n > 0)) return null
      const frame = node.getAttribute('data-tavotto-native-render-frame-pt')
      if (frame !== null) {
        const values = frame.trim().split(/\s+/).map(Number)
        if (values.length !== 4 || !values.every(Number.isFinite)) return null
        const [left, top, frameWidth, frameHeight] = values
        if (left > 0 || top > 0 || frameWidth <= 0 || frameHeight <= 0 || left+frameWidth < width || top+frameHeight < height) return null
        return { space: 'native-pt', width: frameWidth, height: frameHeight, unitsPerPt: 1, originX: left, originY: top }
      }
      return { space: 'native-pt', width, height, unitsPerPt: 1 }
    }
    if (node === svg) break
  }

  const vb = (svg?.getAttribute('viewBox') ?? '').split(/[\s,]+/).map(Number)
  const dimensions = vb.length >= 4 && Number.isFinite(vb[2]) && Number.isFinite(vb[3])
    ? [vb[2], vb[3]] : [100, 100]
  let unitsPerPt = 1
  if (svg && sizeMm?.length && vb.length >= 4 && Number.isFinite(vb[2]) && vb[2] > 0) {
    const widthPt = (Number(sizeMm[0]) / 25.4) * 72
    if (Number.isFinite(widthPt) && widthPt > 0) unitsPerPt = vb[2] / widthPt
  }
  return { space: 'root-user-units', width: dimensions[0], height: dimensions[1], unitsPerPt }
}
