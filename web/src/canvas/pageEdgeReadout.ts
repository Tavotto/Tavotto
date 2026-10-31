/**
 * 机械制图式尺寸标注里「线段与数字」的几何（纯函数）：
 * `start` / `end` 是页面起止边在舞台里的位置（px，沿这条尺寸线的方向，start <= end），`viewLen` 是舞台在这个方向的长度。
 * 页面边超出可视区时线段夹在 [0, viewLen]，数字居中于**可见段**中点；被夹的一端不画箭头；整段都看不见、或可见段短到放不下内缩后的尺寸线（< MIN_DIM_LEN）返回 null。
 * 只看页面矩形，不看数字内容，所以数字位数变化不抖。
 */
export interface DimSpan {
  /** 可见线段的起止（已夹进 [0, viewLen]） */
  a: number
  b: number
  /** 对应那一端的页面边真的在可视区内时才画箭头 */
  startArrow: boolean
  endArrow: boolean
  /** 数字中心（沿尺寸线方向）= 可见段中点 */
  center: number
}

/** 尺寸线两端各内缩这么多 px 再画（PageEdgeResize 用） */
export const DIM_INSET = 4
/** 可见段短于此（两端各内缩 DIM_INSET 后会交叉或为零）就不画尺寸线与数字 */
export const MIN_DIM_LEN = DIM_INSET * 2

export function dimSpan(start: number, end: number, viewLen: number): DimSpan | null {
  const a = Math.max(0, start)
  const b = Math.min(viewLen, end)
  if (!(b - a >= MIN_DIM_LEN)) return null
  return {
    a,
    b,
    startArrow: start >= 0 && start <= viewLen,
    endArrow: end >= 0 && end <= viewLen,
    center: (a + b) / 2,
  }
}
