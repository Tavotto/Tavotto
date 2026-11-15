/**
 * 渐变（ADR 0186）的前端消费者：编辑函数（角度 / 色标 / 换类型 / 换框）、画布与导出预览的 SVG、导出载荷、
 * EPS 透明判据、外观命令（D / ⇧X / / / 吸管）、转为路径、路径查找器、形状互换。
 * 字段读法与几何的同源对拍在 `gradient.golden.test.ts`。
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { PathBody } from '@/canvas/PathView'
import { ShapeView } from '@/canvas/ShapeView'
import { ThumbShape } from '@/components/ThumbShape'
import type { Gradient, PathObject, ShapeObject } from '@/types/document'
import { applyColorSample, applyDefaultAppearance, clearFill, swapFillStroke } from './appearance'
import { toPathObject } from './convertToPath'
import { isEpsTranslucent } from './epsOpacity'
import { toExportObjects } from './exportPayload'
import {
  addStop,
  gradientGeometry,
  linearAngle,
  remapGradients,
  removeStop,
  switchKind,
  updateStop,
  withLinearAngle,
} from './gradient'
import { pathFromMm, pathToMm } from './pathEdit'
import { planReplace } from './pathfinder'
import { switchObject } from './shapeSwitch'

const LIN: Extract<Gradient, { kind: 'linear' }> = {
  kind: 'linear',
  stops: [
    { offset: 0, color: '#ff0000' },
    { offset: 1, color: '#0000ff' },
  ],
  from: [0, 0.5],
  to: [1, 0.5],
}
const RAD: Gradient = { kind: 'radial', stops: LIN.stops, center: [0.5, 0.5], radius: 0.5 }

const rect = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 'r',
  type: 'shape',
  shape: 'rect',
  x: 10,
  y: 20,
  w: 40,
  h: 20,
  strokePt: 2,
  color: '#000000',
  fill: '#ffcc00',
  ...over,
})
const path = (over: Partial<PathObject> = {}): PathObject => ({
  id: 'p',
  type: 'path',
  x: 0,
  y: 0,
  w: 20,
  h: 10,
  strokePt: 1,
  color: '#000000',
  fill: '#ffffff',
  subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] }],
  ...over,
})

describe('编辑函数', () => {
  it('角度按实际尺寸：100 × 50 上设 45° 读回 45°；起止点盖住整个框（过中心、两端落在框的对角）', () => {
    const g = withLinearAngle(LIN, 45, 100, 50)
    expect(linearAngle(g, 100, 50)).toBe(45)
    // 方向 (1, -1)/√2，投影长 = (100 + 50)/√2；比例坐标里 x 跨 ±0.75、y 跨 ±1.5 的一半
    expect(g.from[0]).toBeCloseTo(0.5 - 0.375, 6)
    expect(g.to[1]).toBeCloseTo(0.5 - 0.75, 6)
    for (const deg of [0, 90, 180, 270, 30, 135]) expect(linearAngle(withLinearAngle(LIN, deg, 37, 11), 37, 11)).toBe(deg)
  })

  it('加色标落在最大的空档中间、颜色取两侧的中间；删到两个为止；改色标钳位、不透明度 1 不写', () => {
    const g = addStop(LIN)
    expect(g.stops).toEqual([LIN.stops[0], { offset: 0.5, color: '#800080' }, LIN.stops[1]])
    expect(removeStop(removeStop(g, 1), 0).stops).toHaveLength(2)
    expect(updateStop(LIN, 0, { offset: 1.4, opacity: 1 }).stops[0]).toEqual({ offset: 1, color: '#ff0000' })
    expect(updateStop(LIN, 0, { opacity: -2 }).stops[0]).toEqual({ offset: 0, color: '#ff0000', opacity: 0 })
  })

  it('换类型留着色标', () => {
    expect(switchKind(LIN, 'radial')).toMatchObject({ kind: 'radial', stops: LIN.stops })
    expect(switchKind(LIN, 'linear')).toBe(LIN)
  })

  it('换框（同一个旋转角）：渐变在页面上逐点不动', () => {
    for (const rot of [0, 30]) {
      const from = { x: 10, y: 10, w: 40, h: 20 }
      const to = { x: 12, y: 9, w: 30, h: 26 }
      const o = remapGradients({ fillGradient: LIN }, from, to, rot)
      // 框中心为原点、未旋转坐标里的位置，再转到页面：两边要一样
      const page = (b: typeof from, p: [number, number]) => {
        const lx = (p[0] - 0.5) * b.w
        const ly = (p[1] - 0.5) * b.h
        const t = (rot * Math.PI) / 180
        return [b.x + b.w / 2 + lx * Math.cos(t) - ly * Math.sin(t), b.y + b.h / 2 + lx * Math.sin(t) + ly * Math.cos(t)]
      }
      const g = o.fillGradient as typeof LIN
      page(from, LIN.from).forEach((v, i) => expect(page(to, g.from)[i]).toBeCloseTo(v, 9))
      page(from, LIN.to).forEach((v, i) => expect(page(to, g.to)[i]).toBeCloseTo(v, 9))
    }
  })
})

describe('画布与导出预览的 SVG', () => {
  it('ShapeView：填充 url(#…)、<linearGradient userSpaceOnUse>、色标；没有渐变时与原来一样是纯色', () => {
    const s = renderToStaticMarkup(<ShapeView obj={rect({ fillGradient: LIN, fillOpacity: 0.5 })} />)
    expect(s).toMatch(/<linearGradient id="(gf-[^"]+)" gradientUnits="userSpaceOnUse"/)
    const id = /<linearGradient id="([^"]+)"/.exec(s)![1]
    expect(s).toContain(`fill="url(#${id})"`)
    expect(s).toContain('stop-opacity="0.5"')
    expect(s).not.toContain('fill-opacity')
    const plain = renderToStaticMarkup(<ShapeView obj={rect()} />)
    expect(plain).not.toContain('Gradient')
    expect(plain).toContain('fill="#ffcc00"')
  })

  it('没有填充时填充渐变不画；描边渐变画在 stroke 上（ThumbShape 同一出口，单位 mm）', () => {
    const s = renderToStaticMarkup(
      <svg>
        <ThumbShape obj={rect({ fill: null, fillGradient: LIN, strokeGradient: RAD })} />
      </svg>,
    )
    expect(s).not.toContain('linearGradient')
    expect(s).toMatch(/<radialGradient id="gs-[^"]+" gradientUnits="userSpaceOnUse" cx="20" cy="10" r="20"/)
    expect(s).toMatch(/stroke="url\(#gs-/)
  })

  it('PathBody：路径的填充渐变与描边渐变', () => {
    const s = renderToStaticMarkup(
      <svg>
        <PathBody obj={path({ fillGradient: LIN, strokeGradient: RAD })} sw={1} w={20} h={10} />
      </svg>,
    )
    expect(s).toMatch(/fill="url\(#gf-/)
    expect(s).toMatch(/stroke="url\(#gs-/)
    expect(s).toContain('x2="20"')
  })

  it('几何：比例坐标 × 框，径向半径 × 长边', () => {
    expect(gradientGeometry(RAD, 40, 20)).toMatchObject({ cx: 20, cy: 10, r: 20 })
  })
})

describe('导出载荷与 EPS 透明判据', () => {
  it('只发生效的那份（填充渐变要有填充、描边渐变要有描边）', () => {
    const [a, b, c] = toExportObjects([
      rect({ fillGradient: LIN, strokeGradient: RAD }),
      rect({ id: 'b', fill: null, fillGradient: LIN, stroke: false, strokeGradient: RAD }),
      rect({ id: 'c' }),
    ]) as unknown as Record<string, unknown>[]
    expect(a.fill_gradient).toMatchObject({ kind: 'linear' })
    expect(a.stroke_gradient).toMatchObject({ kind: 'radial' })
    expect('fill_gradient' in b || 'stroke_gradient' in b).toBe(false)
    expect('fill_gradient' in c).toBe(false)
  })

  it('色标带透明 = EPS 画不出的半透明（与后端 ir.paint_translucent 同一条）', () => {
    const translucent: Gradient = { ...LIN, stops: [LIN.stops[0], { ...LIN.stops[1], opacity: 0.5 }] }
    const [a, b, c] = toExportObjects([
      rect({ fillGradient: translucent }),
      rect({ id: 'b', fillGradient: LIN }),
      path({ strokeGradient: translucent }),
    ])
    expect(isEpsTranslucent(a)).toBe(true)
    expect(isEpsTranslucent(b)).toBe(false)
    expect(isEpsTranslucent(c)).toBe(true)
  })
})

describe('外观命令', () => {
  it('D 清掉两个渐变；/ 清掉填充渐变；⇧X 渐变跟着颜色互换', () => {
    const d = rect({ fillGradient: LIN, strokeGradient: RAD })
    applyDefaultAppearance(d)
    expect(d.fillGradient).toBeUndefined()
    expect(d.strokeGradient).toBeUndefined()
    const c = rect({ fillGradient: LIN })
    clearFill(c)
    expect(c.fillGradient).toBeUndefined()
    const s = rect({ fillGradient: LIN })
    swapFillStroke(s)
    expect(s.strokeGradient).toBe(LIN)
    expect(s.fillGradient).toBeUndefined()
  })

  it('吸管吸到纯色：渐变让位', () => {
    const o = rect({ fillGradient: LIN, strokeGradient: RAD })
    expect(applyColorSample(o, { fill: '#123456', stroke: '#654321' })).toBe(true)
    expect(o.fillGradient).toBeUndefined()
    expect(o.strokeGradient).toBeUndefined()
  })
})

describe('别的对象操作带着渐变走', () => {
  it('转为路径：带上渐变，换到路径的框上（描边内缩让框变小，渐变在页面上不动）', () => {
    const shape = rect({ fillGradient: LIN })
    const p = toPathObject(shape)!
    const g = p.fillGradient as typeof LIN
    // 页面 x：形状 x + 比例 × 宽 → 路径上算回同一个数
    expect(p.x + g.from[0] * p.w).toBeCloseTo(shape.x, 9)
    expect(p.x + g.to[0] * p.w).toBeCloseTo(shape.x + shape.w, 9)
  })

  it('编辑锚点（pathFromMm 收框）：渐变跟着换框', () => {
    const p = path({ fillGradient: LIN })
    const subs = pathToMm(p)
    subs[0].nodes[1].p = [30, 0]
    subs[0].nodes[2].p = [30, 10]
    const q = pathFromMm(p, subs)!
    const g = q.fillGradient as typeof LIN
    expect(q.w).toBeCloseTo(30, 9)
    expect(q.x + g.to[0] * q.w).toBeCloseTo(20, 9)
  })

  it('路径查找器的结果：渐变按原比例铺满结果的框（不拿算的时候那个假框去换算）', () => {
    const src = path({ fillGradient: LIN })
    const plan = planReplace([src], [src], [{ key: 0, subs: pathToMm(src) }])!
    expect(plan.add[0].fillGradient).toEqual(LIN)
  })

  it('形状互换：形状之间带着，换成箭头不带', () => {
    const o = rect({ fillGradient: LIN, strokeGradient: RAD })
    expect(switchObject(o, 'ellipse')).toMatchObject({ fillGradient: LIN, strokeGradient: RAD })
    const a = switchObject(rect({ shape: 'line', fill: null, strokeGradient: RAD }), 'arrow')!
    expect('strokeGradient' in a).toBe(false)
  })
})
