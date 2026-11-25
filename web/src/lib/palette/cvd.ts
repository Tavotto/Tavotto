/**
 * 推荐配色的「看不看得出来」：sRGB → CAM02-UCS 色差，加 Machado 2009 的三种色觉模拟（ADR 0210 §五）。
 *
 * 与参考实现 colorspacious 逐值对拍（`tests/golden/palette_cvd_vectors.json`，
 * `scripts/dev/gen_palette_cvd_vectors.py`）：观察条件取它的默认（D65 白点、Y_b = 20、
 * L_A = 64/π/5 cd/m²、平均环境），色觉模拟取严重度 100 的矩阵，在线性 sRGB 上乘，
 * 再夹回 [0, 1]（屏幕上看到的是夹回后的那个颜色，色差也按它算）。纯函数、无依赖。
 */

export type Vision = 'normal' | 'protanomaly' | 'deuteranomaly' | 'tritanomaly'
export const VISIONS: readonly Vision[] = ['normal', 'protanomaly', 'deuteranomaly', 'tritanomaly']

type Vec3 = [number, number, number]
type Mat3 = [Vec3, Vec3, Vec3]

/** Machado, Oliveira & Fernandes 2009，严重度 1.0（与 colorspacious 的 MACHADO_ET_AL_MATRICES[...][100] 同值） */
const MACHADO_100: Record<Exclude<Vision, 'normal'>, Mat3> = {
  protanomaly: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deuteranomaly: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritanomaly: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
}

function mul(m: Mat3, v: Vec3): Vec3 {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ]
}

function matmul(a: Mat3, b: Mat3): Mat3 {
  const col = (j: number): Vec3 => [b[0][j], b[1][j], b[2][j]]
  const c0 = mul(a, col(0))
  const c1 = mul(a, col(1))
  const c2 = mul(a, col(2))
  return [
    [c0[0], c1[0], c2[0]],
    [c0[1], c1[1], c2[1]],
    [c0[2], c1[2], c2[2]],
  ]
}

function inv(m: Mat3): Mat3 {
  const [[a, b, c], [d, e, f], [g, h, i]] = m
  const A = e * i - f * h
  const B = -(d * i - f * g)
  const C = d * h - e * g
  const det = a * A + b * B + c * C
  return [
    [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
    [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
    [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
  ]
}

/** IEC 61966-2-1:1999 给的 XYZ → 线性 sRGB；反过来用它的逆（与 colorspacious 同一做法） */
const XYZ100_TO_SRGB1: Mat3 = [
  [3.2406, -1.5372, -0.4986],
  [-0.9689, 1.8758, 0.0415],
  [0.0557, -0.204, 1.057],
]
const SRGB1_TO_XYZ100 = inv(XYZ100_TO_SRGB1)

const M_CAT02: Mat3 = [
  [0.7328, 0.4296, -0.1624],
  [-0.7036, 1.6975, 0.0061],
  [0.003, 0.0136, 0.9834],
]
const M_HPE: Mat3 = [
  [0.38971, 0.68898, -0.07868],
  [-0.22981, 1.1834, 0.04641],
  [0, 0, 1],
]
const M_HPE_CAT02_INV = matmul(M_HPE, inv(M_CAT02))

// —— 观察条件（colorspacious 的 CIECAM02Space.sRGB 默认）——
const XYZ_W: Vec3 = [95.047, 100, 108.883]
const Y_B = 20
const L_A = ((64 / Math.PI) * 0.2)
const F = 1
const C_SURR = 0.69
const N_C = 1

const RGB_W = mul(M_CAT02, XYZ_W)
const D = Math.min(1, Math.max(0, F * (1 - (1 / 3.6) * Math.exp((-L_A - 42) / 92))))
const D_RGB = RGB_W.map((w) => (D * XYZ_W[1]) / w + 1 - D) as Vec3
const K = 1 / (5 * L_A + 1)
const F_L = 0.2 * K ** 4 * (5 * L_A) + 0.1 * (1 - K ** 4) ** 2 * Math.cbrt(5 * L_A)
const N = Y_B / XYZ_W[1]
const Z = 1.48 + Math.sqrt(N)
const N_BB = 0.725 * (1 / N) ** 0.2
const N_CB = N_BB

function adapt(rgbPrime: Vec3): Vec3 {
  return rgbPrime.map((v) => {
    const s = Math.sign(v)
    const t = ((F_L * s * v) / 100) ** 0.42
    return s * 400 * (t / (t + 27.13)) + 0.1
  }) as Vec3
}

const A_W = (() => {
  const aw = adapt(mul(M_HPE_CAT02_INV, RGB_W.map((w, i) => w * D_RGB[i]) as Vec3))
  return (2 * aw[0] + aw[1] + aw[2] / 20 - 0.305) * N_BB
})()

function srgbToLinear(c: number): number {
  return c < 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

/** `#RRGGBB`（大小写都行）→ [0, 1] 的 sRGB；认不出就是 null */
export function hexToRgb01(hex: string): Vec3 | null {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim())
  if (!m) return null
  return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255]
}

/** 线性 sRGB 上的色觉模拟，夹回 [0, 1] */
function simulateLinear(lin: Vec3, vision: Vision): Vec3 {
  if (vision === 'normal') return lin
  // colorspacious 在 sRGB1 上夹回；线性化是单调的，0 与 1 两端不变，所以在线性空间夹等价
  return mul(MACHADO_100[vision], lin).map((v) => Math.min(1, Math.max(0, v))) as Vec3
}

/** sRGB（0–1）→ CAM02-UCS 的 (J', a', b')，可选先做色觉模拟 */
export function toUcs(rgb: Vec3, vision: Vision = 'normal'): Vec3 {
  const lin = simulateLinear(rgb.map(srgbToLinear) as Vec3, vision)
  const xyz = mul(SRGB1_TO_XYZ100, lin).map((v) => v * 100) as Vec3
  const rgbC = mul(M_CAT02, xyz).map((v, i) => v * D_RGB[i]) as Vec3
  const ra = adapt(mul(M_HPE_CAT02_INV, rgbC))
  const a = ra[0] - (12 / 11) * ra[1] + ra[2] / 11
  const b = (ra[0] + ra[1] - 2 * ra[2]) / 9
  const hRad = Math.atan2(b, a)
  const A = (2 * ra[0] + ra[1] + ra[2] / 20 - 0.305) * N_BB
  if (A <= 0) return [0, 0, 0]
  const J = 100 * (A / A_W) ** (C_SURR * Z)
  const e = (12500 / 13) * N_C * N_CB * (Math.cos(hRad + 2) + 3.8)
  const t = (e * Math.hypot(a, b)) / (ra[0] + ra[1] + (21 / 20) * ra[2])
  const C = t ** 0.9 * (J / 100) ** 0.5 * (1.64 - 0.29 ** N) ** 0.73
  const M = C * F_L ** 0.25
  const Jp = (1.7 * J) / (1 + 0.007 * J)
  const Mp = Math.log(1 + 0.0228 * M) / 0.0228
  return [Jp, Mp * Math.cos(hRad), Mp * Math.sin(hRad)]
}

/** 两个颜色在某种视觉下的 CAM02-UCS 色差；认不出的色值当作无穷远（不拦人） */
export function deltaE(hexA: string, hexB: string, vision: Vision = 'normal'): number {
  const a = hexToRgb01(hexA)
  const b = hexToRgb01(hexB)
  if (!a || !b) return Number.POSITIVE_INFINITY
  const p = toUcs(a, vision)
  const q = toUcs(b, vision)
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2])
}

export interface ClosestPair {
  /** 下标指向传入的 colors */
  i: number
  j: number
  vision: Vision
  deltaE: number
}

/** 一组颜色在四种视觉下最近的那一对（少于两个颜色时是 null） */
export function closestPair(colors: readonly string[]): ClosestPair | null {
  let best: ClosestPair | null = null
  const pts = VISIONS.map((v) =>
    colors.map((c) => {
      const rgb = hexToRgb01(c)
      return rgb ? toUcs(rgb, v) : null
    }),
  )
  VISIONS.forEach((vision, vi) => {
    const row = pts[vi]
    for (let i = 0; i < row.length; i++) {
      for (let j = i + 1; j < row.length; j++) {
        const p = row[i]
        const q = row[j]
        if (!p || !q) continue
        const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2])
        if (!best || d < best.deltaE) best = { i, j, vision, deltaE: d }
      }
    }
  })
  return best
}

/** WCAG 2 相对亮度对比度（对白底 ≥ 3:1 才算细线、小点看得清） */
export function contrastRatio(hexA: string, hexB = '#FFFFFF'): number {
  const lum = (h: string): number => {
    const rgb = hexToRgb01(h)
    if (!rgb) return 0
    const [r, g, b] = rgb.map(srgbToLinear)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const [hi, lo] = [lum(hexA), lum(hexB)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}
