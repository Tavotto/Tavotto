/**
 * 命中评分的分层（ADR 0086 修订）：「看得见的墨迹在哪，就选谁」。
 *
 * 分层：
 *   tier 0  描边命中（曲线 / 空心路径 / 填充元素自己画了的边线 / 箭头 / 字与图例的框），
 *           且没有被画在它上面的**不透明**（fill_alpha ≥ 0.5）填充盖住；
 *   tier 1  其余按旧规则（面积评分）：仅落在填充内部、背景类 bbox（子图、图像…）；
 *   tier 2  被上层不透明填充盖住的候选：看不见，只留给 ⌥ 轮换。
 * tier 0 内部：先按「离可见墨迹边缘」的 0.5 mm 档，再按墨迹面积，再按绘制次序（上层优先），
 * 最后登记序。
 *
 * 夹具是 100 × 100 mm 的方图：分数 0.01 = 1 mm，用例里的距离一眼能读。
 */
import { describe, expect, it } from 'vitest'

import type { ElementGeometry, Manifest, ManifestElement } from '@/lib/api'
import { OPAQUE_FILL_ALPHA, STROKE_DIST_BUCKET_MM, strokeDistBucket } from '@/lib/pathGeom'
import { pickElement, pickElementStack } from './interactions'

type Pt = [number, number]

function mk(
  gid: string,
  role: string,
  geometry: Partial<ElementGeometry> | null,
  extra: Partial<ManifestElement> = {},
): ManifestElement {
  const pts = geometry?.paths?.flatMap((p) => p.points) ?? [[0, 0]]
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  return {
    gid,
    role,
    label: gid,
    bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)],
    editable: [],
    draggable: false,
    ...(geometry ? { geometry: { kind: 'path', fill: false, stroke: true, stroke_pt: 1, ...geometry } as ElementGeometry } : {}),
    ...extra,
  } as unknown as ManifestElement
}

const line = (gid: string, a: Pt, b: Pt, extra: Partial<ElementGeometry> = {}) =>
  mk(gid, 'line', { kind: 'polyline', paths: [{ points: [a, b], closed: false }], ...extra })

const rect = (
  gid: string,
  [x0, y0, x1, y1]: [number, number, number, number],
  extra: Partial<ElementGeometry> = {},
) =>
  mk(gid, 'patch', {
    kind: 'path',
    fill: true,
    stroke: false,
    paths: [
      {
        points: [
          [x0, y0],
          [x1, y0],
          [x1, y1],
          [x0, y1],
        ],
        closed: true,
      },
    ],
    ...extra,
  })

const axes = () =>
  mk('axes_0', 'axes', null, { bbox: [0, 0, 1, 1] as [number, number, number, number] })

const man = (...elements: ManifestElement[]): Manifest =>
  ({ size_mm: [100, 100], elements: [axes(), ...elements] }) as unknown as Manifest

const gids = (m: Manifest, x: number, y: number) => pickElementStack(m, x, y).map((e) => e.gid)

describe('规则 2：描边压过仅落在填充内部的命中', () => {
  // 半透明幕墙盖着一条线：线画在上面（order 2 > 1）
  const curtain = rect('fill', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 0.12, paint_order: 1 })
  const ln = line('line', [0.1, 0.5], [0.9, 0.5], { paint_order: 2 })

  it('半透明幕墙下的线可选：点在线上选线，不选幕墙', () => {
    expect(gids(man(curtain, ln), 0.5, 0.5)[0]).toBe('line')
  })

  it('点在幕墙空白处（离线超出容差）仍选中幕墙', () => {
    expect(gids(man(curtain, ln), 0.5, 0.7)[0]).toBe('fill')
  })

  it('线画在不透明填充的下面：被盖住的线不抢，填充赢；线仍在候选表里（供 ⌥ 轮换），排在背景容器之后', () => {
    const opaque = rect('fill', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 1, paint_order: 5 })
    const under = line('line', [0.1, 0.5], [0.9, 0.5], { paint_order: 2 })
    expect(gids(man(under, opaque), 0.5, 0.5)).toEqual(['fill', 'axes_0', 'line'])
  })

  it('线画在不透明填充的上面：线可选', () => {
    const opaque = rect('fill', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 1, paint_order: 1 })
    const over = line('line', [0.1, 0.5], [0.9, 0.5], { paint_order: 2 })
    expect(gids(man(opaque, over), 0.5, 0.5)[0]).toBe('line')
  })

  it('盖住的阈值是 fill_alpha ≥ 0.5：0.49 不盖，0.5 盖', () => {
    const at = (alpha: number) =>
      gids(
        man(
          line('line', [0.1, 0.5], [0.9, 0.5], { paint_order: 2 }),
          rect('fill', [0.1, 0.1, 0.9, 0.9], { fill_alpha: alpha, paint_order: 5 }),
        ),
        0.5,
        0.5,
      )[0]
    expect(OPAQUE_FILL_ALPHA).toBe(0.5)
    expect(at(0.49)).toBe('line')
    expect(at(0.5)).toBe('fill')
  })

  it('引擎没给绘制事实（老 manifest）：不判遮挡，描边照样压过填充内部', () => {
    const m = man(
      rect('fill', [0.1, 0.1, 0.9, 0.9]),
      line('line', [0.1, 0.5], [0.9, 0.5]),
    )
    expect(gids(m, 0.5, 0.5)[0]).toBe('line')
  })

  it('线自己的次序未知（缺 paint_order）：即使上面有不透明填充也不判遮挡', () => {
    const m = man(
      line('line', [0.1, 0.5], [0.9, 0.5]),
      rect('fill', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 1, paint_order: 5 }),
    )
    expect(gids(m, 0.5, 0.5)[0]).toBe('line')
  })

  it('锁定的不透明填充照样盖着线（只是自己不参与被选）；隐藏的不盖', () => {
    const under = line('line', [0.1, 0.5], [0.9, 0.5], { paint_order: 2 })
    const opaque = rect('fill', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 1, paint_order: 5 })
    const m = man(under, opaque)
    // 填充锁定：自己不进候选；线被它盖着，掉到子图容器之后
    expect(pickElementStack(m, 0.5, 0.5, ['fill']).map((e) => e.gid)).toEqual(['axes_0', 'line'])
    // 填充隐藏：画面上没有它，线可选
    const hidden = {
      ...opaque,
      editable: [{ prop: 'visible', type: 'bool', value: false }],
    } as unknown as ManifestElement
    expect(gids(man(under, hidden), 0.5, 0.5)[0]).toBe('line')
  })

  it('填充元素自己画了边线：边线算描边命中；没画边线的不算', () => {
    // 点在大填充 A 的边线上，同时落在更小的无边线填充 B 里
    const a = rect('A', [0.1, 0.1, 0.9, 0.9], { stroke: true, stroke_pt: 1, fill_alpha: 0.2, paint_order: 1 })
    const b = rect('B', [0.05, 0.4, 0.3, 0.6], { fill_alpha: 0.2, paint_order: 2 })
    expect(gids(man(a, b), 0.1, 0.5)[0]).toBe('A')
    const aNoEdge = rect('A', [0.1, 0.1, 0.9, 0.9], { stroke: false, fill_alpha: 0.2, paint_order: 1 })
    expect(gids(man(aNoEdge, b), 0.1, 0.5)[0]).toBe('B')
  })
})

describe('规则 5 补：填充外沿的容差带照旧命中（旧行为不变）', () => {
  it('没画边线的填充，边界外 1 mm 内点击仍选中它（只算填充类，不升到描边层）', () => {
    const fill = rect('fill', [0.2, 0.2, 0.8, 0.8], { fill_alpha: 0.3, paint_order: 1 })
    expect(gids(man(fill), 0.81, 0.5)[0]).toBe('fill') // 外 1 mm
    expect(gids(man(fill), 0.85, 0.5)[0]).toBe('axes_0') // 外 5 mm：出了容差
    // 外沿容差带是填充类：同一点上有一条线时，线（描边层）赢
    const ln = line('line', [0.81, 0.1], [0.81, 0.9], { paint_order: 2 })
    expect(gids(man(fill, ln), 0.81, 0.5)[0]).toBe('line')
  })
})

describe('规则 3：描边层内部——距离档 → 墨迹面积 → 绘制次序 → 登记序', () => {
  it('短网格段不抢长曲线：点落在曲线上，网格段在 1.3 mm 外', () => {
    const curve = line('curve', [0.05, 0.5], [0.95, 0.5], { paint_order: 9 })
    const grid = line('grid', [0.48, 0.514], [0.52, 0.514], { paint_order: 1 })
    expect(gids(man(grid, curve), 0.5, 0.5)[0]).toBe('curve')
    // 对照：在网格段上点，选的是网格段
    expect(gids(man(grid, curve), 0.5, 0.514)[0]).toBe('grid')
  })

  it('距离档：0.5 mm 一档，扣掉半线宽', () => {
    const g = { stroke: true, stroke_pt: 2 } as ElementGeometry // 半宽约 0.35 mm
    expect(STROKE_DIST_BUCKET_MM).toBe(0.5)
    expect(strokeDistBucket(g, 0.3)).toBe(0) // 在墨迹里
    expect(strokeDistBucket(g, 0.35 + 0.4)).toBe(1)
    expect(strokeDistBucket(g, 0.35 + 0.6)).toBe(2)
    expect(strokeDistBucket(null, 0)).toBe(0)
  })

  it('同一档里按墨迹面积：短的赢', () => {
    const long = line('long', [0.05, 0.5], [0.95, 0.5], { paint_order: 9 })
    const short = line('short', [0.45, 0.5], [0.55, 0.5], { paint_order: 1 })
    expect(gids(man(long, short), 0.5, 0.5)[0]).toBe('short')
  })

  it('面积也相同：画在上层的赢（与登记序相反时也是）', () => {
    const lower = line('lower', [0.1, 0.5], [0.9, 0.5], { paint_order: 1 })
    const upper = line('upper', [0.1, 0.5], [0.9, 0.5], { paint_order: 2 })
    expect(gids(man(upper, lower), 0.5, 0.5).slice(0, 2)).toEqual(['upper', 'lower'])
    expect(gids(man(lower, upper), 0.5, 0.5).slice(0, 2)).toEqual(['upper', 'lower'])
  })

  it('次序也相同（或未知）：登记序', () => {
    const a = line('a', [0.1, 0.5], [0.9, 0.5])
    const b = line('b', [0.1, 0.5], [0.9, 0.5])
    expect(gids(man(a, b), 0.5, 0.5).slice(0, 2)).toEqual(['a', 'b'])
    expect(gids(man(b, a), 0.5, 0.5).slice(0, 2)).toEqual(['b', 'a'])
  })
})

describe('规则 4：仅落在填充内部的候选之间', () => {
  it('半透明填充之间保持旧的面积规则：小的赢', () => {
    const big = rect('big', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 0.2, paint_order: 5 })
    const small = rect('small', [0.4, 0.4, 0.6, 0.6], { fill_alpha: 0.2, paint_order: 1 })
    expect(gids(man(big, small), 0.5, 0.5).slice(0, 2)).toEqual(['small', 'big'])
  })

  it('上层不透明填充压过被它盖住的下层（哪怕下层面积更小）', () => {
    const big = rect('big', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 1, paint_order: 5 })
    const small = rect('small', [0.4, 0.4, 0.6, 0.6], { fill_alpha: 1, paint_order: 1 })
    expect(gids(man(big, small), 0.5, 0.5)).toEqual(['big', 'axes_0', 'small'])
    // 反过来小的在上：小的赢（旧规则也是它）
    const smallTop = rect('small', [0.4, 0.4, 0.6, 0.6], { fill_alpha: 1, paint_order: 9 })
    expect(gids(man(big, smallTop), 0.5, 0.5)).toEqual(['small', 'axes_0', 'big'])
  })
})

describe('规则 5：不轮换时旧行为不变', () => {
  it('子图容器让给里面的线；刻度类降权', () => {
    const m = man(
      line('line', [0.1, 0.5], [0.9, 0.5]),
      mk('tl', 'ticklabel', null, { bbox: [0.4, 0.45, 0.2, 0.1] as [number, number, number, number] }),
    )
    expect(gids(m, 0.2, 0.5)[0]).toBe('line')
    // 刻度标签（降权 ×20）输给线，但仍排在子图之前或之后按旧评分：只钉线先
    expect(gids(m, 0.5, 0.5)[0]).toBe('line')
  })

  const txt = (extra: Partial<ManifestElement> = {}) =>
    mk('txt', 'text', null, {
      bbox: [0.45, 0.47, 0.1, 0.06] as [number, number, number, number],
      ...extra,
    })

  it('字的框（本身是墨迹）压着线时，仍是字赢（面积小）', () => {
    const m = man(line('line', [0.1, 0.5], [0.9, 0.5], { paint_order: 1 }), txt({ paint_order: 2 }))
    expect(gids(m, 0.5, 0.5)[0]).toBe('txt')
  })

  it('字被后画的不透明 patch 盖住：patch 赢，字降到 tier 2', () => {
    const m = man(txt({ paint_order: 2 }), rect('p', [0.4, 0.4, 0.6, 0.6], { fill_alpha: 1, paint_order: 5 }))
    expect(gids(m, 0.5, 0.5)).toEqual(['p', 'axes_0', 'txt'])
  })

  it('字画在 patch 上面（框内标签）：仍选字', () => {
    const m = man(rect('p', [0.4, 0.4, 0.6, 0.6], { fill_alpha: 1, paint_order: 1 }), txt({ paint_order: 2 }))
    expect(gids(m, 0.5, 0.5)[0]).toBe('txt')
  })

  it('字缺 paint_order（老 manifest）：留在 tier 1 按面积比，不升描边层', () => {
    const small = rect('small', [0.46, 0.48, 0.54, 0.52], { fill_alpha: 1 })
    // 旧面积评分：更小的 patch 赢过字框
    expect(gids(man(txt(), small), 0.5, 0.5)[0]).toBe('small')
    // 字比 patch 小时字赢
    const big = rect('big', [0.2, 0.2, 0.8, 0.8], { fill_alpha: 1 })
    expect(gids(man(txt(), big), 0.5, 0.5)[0]).toBe('txt')
    // 线是描边层，次序未知的字不再抢它
    expect(gids(man(line('line', [0.1, 0.5], [0.9, 0.5]), txt()), 0.5, 0.5)[0]).toBe('line')
  })

  it('图像类背景 bbox 不抢容差内的线', () => {
    const m = man(
      line('line', [0.1, 0.5], [0.9, 0.5]),
      mk('img', 'image', null, { bbox: [0.3, 0.3, 0.4, 0.4] as [number, number, number, number] }),
    )
    expect(pickElement(m, 0.5, 0.51)?.gid).toBe('line')
  })

  describe('端点箭头（无 geometry、元素级 paint_order）', () => {
    const arrow = (order?: number) =>
      mk('arrow', 'arrow_patch', null, {
        arrow_endpoints: [
          [0.1, 0.5],
          [0.9, 0.5],
        ],
        ...(order === undefined ? {} : { paint_order: order }),
      })
    const opaque = (order: number) =>
      rect('fill', [0.1, 0.1, 0.9, 0.9], { fill_alpha: 1, paint_order: order })

    it('箭头画在不透明 patch 下面：被降层，patch 赢', () => {
      expect(gids(man(arrow(2), opaque(5)), 0.5, 0.5)).toEqual(['fill', 'axes_0', 'arrow'])
    })

    it('箭头画在不透明 patch 上面：仍是 tier 0', () => {
      expect(gids(man(opaque(1), arrow(2)), 0.5, 0.5)[0]).toBe('arrow')
    })

    it('箭头次序未知（老 manifest）：不判遮挡', () => {
      expect(gids(man(arrow(), opaque(5)), 0.5, 0.5)[0]).toBe('arrow')
    })
  })

  it('没有任何命中：回落到子图，再没有就是 figure', () => {
    const m = man(line('line', [0.1, 0.5], [0.9, 0.5]))
    expect(pickElement(m, 0.5, 0.9)?.gid).toBe('axes_0')
  })
})
