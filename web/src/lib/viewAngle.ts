/**
 * 三维子图的视角 → 三条坐标轴在屏幕上的方向（审计 T24 的方向示意）。
 *
 * 与 matplotlib `Axes3D` 同一套约定（`get_proj` / `proj3d._view_axes`）：
 * 视线从 `eye = (cos e·cos a, cos e·sin a, sin e)` 望向盒中心，`u` 是屏幕右、
 * `v` 是屏幕上，roll 绕视线转。返回的是**正交投影**下三条单位轴的
 * (右, 上) 分量——示意图只要方向对，透视缩短不画。
 *
 * 纯函数，无 DOM；用例钉住几个能手算的视角。
 */

type V3 = [number, number, number]

export interface ViewAxes2d {
  /** 每条轴：[屏幕右分量, 屏幕上分量]，范围 [-1, 1] */
  x: [number, number]
  y: [number, number]
  z: [number, number]
}

const D2R = Math.PI / 180

const cross = (a: V3, b: V3): V3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
]
const dot = (a: V3, b: V3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const norm = (a: V3): number => Math.hypot(a[0], a[1], a[2])
const unit = (a: V3): V3 => {
  const n = norm(a)
  return n > 0 ? [a[0] / n, a[1] / n, a[2] / n] : a
}

/** Rodrigues：把 p 绕单位轴 k 转 angle 弧度 */
function rotateAbout(p: V3, k: V3, angle: number): V3 {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const kxp = cross(k, p)
  const kdp = dot(k, p)
  return [
    p[0] * c + kxp[0] * s + k[0] * kdp * (1 - c),
    p[1] * c + kxp[1] * s + k[1] * kdp * (1 - c),
    p[2] * c + kxp[2] * s + k[2] * kdp * (1 - c),
  ]
}

const round6 = (v: number) => Math.round(v * 1e6) / 1e6

export function viewAxes2d(elevDeg: number, azimDeg: number, rollDeg = 0): ViewAxes2d {
  // 正对着上 / 下看（|elev| 恰好 90°）时 matplotlib 自己的 u 也退化成 NaN；
  // 示意图往回挪一丁点，画出「几乎正俯视」而不是画不出来
  const elev = Math.abs(elevDeg) === 90 ? Math.sign(elevDeg) * 89.999 : elevDeg
  const e = elev * D2R
  const a = azimDeg * D2R
  const r = rollDeg * D2R
  const w: V3 = [Math.cos(e) * Math.cos(a), Math.cos(e) * Math.sin(a), Math.sin(e)]
  // 竖直轴是 z；越过天顶（|elev| > 90°）后「上」翻过来，与 matplotlib 同一条
  const vertical: V3 = [0, 0, Math.abs(elev) > 90 ? -1 : 1]
  let u = unit(cross(vertical, w))
  let v = cross(w, u)
  if (rollDeg) {
    u = rotateAbout(u, w, -r)
    v = rotateAbout(v, w, -r)
  }
  const proj = (axis: V3): [number, number] => [round6(dot(axis, u)), round6(dot(axis, v))]
  return { x: proj([1, 0, 0]), y: proj([0, 1, 0]), z: proj([0, 0, 1]) }
}
