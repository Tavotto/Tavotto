/**
 * 连接弹窗左侧「品牌竖线画」的纯函数部分（画布 / rAF 在 `BrandLines.tsx`）。
 *
 * 做法：品牌标志画到离屏 canvas、按小格子采样出**亮度表**（之后不再用离屏 canvas）；画布上铺一排等距竖线，
 * 每帧沿竖线一段段查亮度表——亮处线粗、暗处线细，线宽只分 4 档，一节一节像竹子。亮度表上叠一层缓慢向下流动的
 * 噪声；鼠标靠近时线散开再弹回（弹簧阻尼）；Logo 最亮的几处在鼠标进来时亮起蓝色检测框（吸附到网格）。
 * 这里的函数都不碰 DOM，可以直接单测。
 */

/** 线宽档数（亮度 → 0..3 档） */
export const TIER_COUNT = 4
/** 亮度分档的阈值（升序，三个阈值切出四档） */
export const TIER_EDGES = [0.12, 0.38, 0.68] as const

/**
 * RGBA 像素 → 亮度表：按 cols × rows 的格子取**不透明度**的平均（标志是画在透明底上的白图形，
 * 亮度 = 覆盖率）。返回 0..1 的 Float32Array，行优先。
 */
export function lumaFromRgba(rgba: ArrayLike<number>, w: number, h: number, cols: number, rows: number): Float32Array {
  const out = new Float32Array(cols * rows)
  const cw = w / cols
  const ch = h / rows
  for (let r = 0; r < rows; r++) {
    const y0 = Math.floor(r * ch)
    const y1 = Math.max(y0 + 1, Math.floor((r + 1) * ch))
    for (let c = 0; c < cols; c++) {
      const x0 = Math.floor(c * cw)
      const x1 = Math.max(x0 + 1, Math.floor((c + 1) * cw))
      let sum = 0
      let n = 0
      for (let y = y0; y < y1 && y < h; y++) {
        for (let x = x0; x < x1 && x < w; x++) {
          sum += rgba[(y * w + x) * 4 + 3] / 255
          n++
        }
      }
      out[r * cols + c] = n ? sum / n : 0
    }
  }
  return out
}

/** 查亮度表：u / v 是 0..1 的归一化坐标，范围外 = 0（标志框之外是暗的），格内取最近格 */
export function lumaAt(luma: ArrayLike<number>, cols: number, rows: number, u: number, v: number): number {
  if (u < 0 || v < 0 || u >= 1 || v >= 1) return 0
  return luma[Math.floor(v * rows) * cols + Math.floor(u * cols)]
}

/** 亮度 → 线宽档（0 最细 … 3 最粗） */
export function widthTier(v: number): number {
  let t = 0
  for (const e of TIER_EDGES) if (v >= e) t++
  return t
}

/** 便宜的流动噪声：几层正弦叠加，值域约 0..1；`t` 是秒，向下流动 = y 随 t 减小地取样 */
export function flowNoise(x: number, y: number, t: number): number {
  const a = Math.sin(x * 1.7 + y * 0.9 - t * 0.9)
  const b = Math.sin(x * 0.6 - y * 2.3 - t * 1.3 + 1.7)
  const c = Math.sin((x + y) * 3.1 - t * 0.5 + 4.2)
  return 0.5 + 0.27 * a + 0.16 * b + 0.07 * c
}

/** 吸附到网格 */
export function snapToGrid(v: number, step: number): number {
  return Math.round(v / step) * step
}

export interface Region {
  /** 格坐标（亮度表上的列 / 行），左上角 */
  c: number
  r: number
  /** 宽高（格） */
  w: number
  h: number
  /** 平均亮度 */
  score: number
}

/**
 * 最亮的几块区域：把亮度表切成 block × block 格的块，按平均亮度从高到低挑 `n` 块，
 * 两块的中心距离不得小于 `minGap` 格（避免挤在一起）。
 */
export function brightestRegions(luma: ArrayLike<number>, cols: number, rows: number, n: number, block = 8, minGap = 10): Region[] {
  const all: Region[] = []
  for (let r = 0; r + block <= rows; r += block) {
    for (let c = 0; c + block <= cols; c += block) {
      let sum = 0
      for (let y = 0; y < block; y++) for (let x = 0; x < block; x++) sum += luma[(r + y) * cols + c + x]
      all.push({ c, r, w: block, h: block, score: sum / (block * block) })
    }
  }
  all.sort((a, b) => b.score - a.score || a.r - b.r || a.c - b.c)
  const picked: Region[] = []
  for (const reg of all) {
    if (reg.score <= 0.05 || picked.length >= n) break
    if (picked.every((p) => Math.hypot(p.c - reg.c, p.r - reg.r) >= minGap)) picked.push(reg)
  }
  return picked
}

/** 弹簧阻尼一步（半隐式欧拉）：返回新的 [位置, 速度] */
export function springStep(pos: number, vel: number, target: number, dt: number, k = 120, damping = 9): [number, number] {
  const acc = k * (target - pos) - damping * vel
  const v = vel + acc * dt
  return [pos + v * dt, v]
}

/** 鼠标对一根线的推力目标：线在鼠标左 / 右就往外推，距离越近推得越远，超出半径为 0 */
export function repelTarget(lineX: number, mouseX: number, radius: number, strength: number): number {
  const d = lineX - mouseX
  const a = Math.abs(d)
  if (a >= radius) return 0
  const k = 1 - a / radius
  return (d >= 0 ? 1 : -1) * k * k * strength
}
