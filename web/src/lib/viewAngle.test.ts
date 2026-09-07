/**
 * 视角 → 屏幕方向（三维子图的方向示意，审计 T24）。
 *
 * **判据的另一侧是真 matplotlib**，不是这份实现的手推：下面的期望值由
 * `mpl_toolkits.mplot3d.proj3d._view_axes` 在 matplotlib 3.10.8 上算出来
 * （eye = R + (cos e·cos a, cos e·sin a, sin e)、竖直轴 z、|elev| > 90° 时
 * 竖直轴取 -1，与 `Axes3D.get_proj` 同一套），再抄成常量。两侧同源的对拍
 * 等于自己验自己；这里换的是**方法**，不只是输入。
 *
 * 重新生成：
 *
 *   python - <<'EOF'
 *   import numpy as np
 *   from mpl_toolkits.mplot3d import proj3d
 *   def axes(elev, azim, roll=0):
 *       e, a, r = np.deg2rad([elev, azim, roll])
 *       R = np.zeros(3)
 *       p = np.array([np.cos(e)*np.cos(a), np.cos(e)*np.sin(a), np.sin(e)])
 *       V = np.zeros(3); V[2] = -1 if abs(e) > 0.5*np.pi else 1
 *       u, v, _ = proj3d._view_axes(R + p, R, V, r)
 *       return {n: (round(float(ax@u), 6), round(float(ax@v), 6))
 *               for n, ax in (('x', np.eye(3)[0]), ('y', np.eye(3)[1]), ('z', np.eye(3)[2]))}
 *   EOF
 */
import { describe, expect, it } from 'vitest'
import { viewAxes2d } from './viewAngle'

/** [elev, azim, roll] → matplotlib 3.10.8 算出来的 (屏幕右, 屏幕上) */
const GOLDEN: [readonly [number, number, number], Record<'x' | 'y' | 'z', [number, number]>][] = [
  [[0, 0, 0], { x: [0, 0], y: [1, 0], z: [0, 1] }],
  [[0, -90, 0], { x: [1, 0], y: [0, 0], z: [0, 1] }],
  // matplotlib 的默认视角
  [[30, -60, 0], { x: [0.866025, -0.25], y: [0.5, 0.433013], z: [0, 0.866025] }],
  // roll 绕视线转：把「上」转到「右」
  [[0, 0, 90], { x: [0, 0], y: [0, 1], z: [-1, 0] }],
  [[60, -60, 0], { x: [0.866025, -0.433013], y: [0.5, 0.75], z: [0, 0.5] }],
  // 越过天顶：**只有 z 反号**，x / y 的左右分量不变（第一版用例照直觉写成
  // 「x 会反号」，而 matplotlib 不是那样——那条断言从没跑过）
  [[120, -60, 0], { x: [0.866025, -0.433013], y: [0.5, 0.75], z: [0, -0.5] }],
  [[30, -60, 25], { x: [0.89054, 0.139421], y: [0.270155, 0.603752], z: [-0.365998, 0.784886] }],
  [[-45, 135, -30], { x: [-0.862372, -0.079459], y: [-0.362372, 0.786566], z: [0.353553, 0.612372] }],
  // 正俯视：z 正对视线（屏幕上是一个点），x / y 铺在屏幕上。不退化成 NaN——
  // cos(π/2) 在浮点里是 6.12e-17 而不是 0
  [[90, -60, 0], { x: [0.866025, -0.5], y: [0.5, 0.866025], z: [0, 0] }],
  [[-90, -60, 0], { x: [0.866025, 0.5], y: [0.5, -0.866025], z: [0, 0] }],
]

describe('viewAxes2d 与 matplotlib 的 _view_axes 同一套', () => {
  for (const [[elev, azim, roll], want] of GOLDEN) {
    it(`elev ${elev} / azim ${azim} / roll ${roll}`, () => {
      const got = viewAxes2d(elev, azim, roll)
      for (const k of ['x', 'y', 'z'] as const) {
        expect(got[k][0], `${k} 的屏幕右分量`).toBeCloseTo(want[k][0], 5)
        expect(got[k][1], `${k} 的屏幕上分量`).toBeCloseTo(want[k][1], 5)
      }
    })
  }

  it('三条轴始终有限，且各自的屏幕长度不超过 1（正交投影的单位轴）', () => {
    for (let elev = -180; elev <= 180; elev += 7) {
      for (let azim = -180; azim <= 180; azim += 13) {
        const v = viewAxes2d(elev, azim, elev / 3)
        for (const k of ['x', 'y', 'z'] as const) {
          expect(Number.isFinite(v[k][0]) && Number.isFinite(v[k][1]), `${elev}/${azim} 的 ${k}`).toBe(true)
          expect(Math.hypot(...v[k])).toBeLessThanOrEqual(1 + 1e-6)
        }
      }
    }
  })
})
