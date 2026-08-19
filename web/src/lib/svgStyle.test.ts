/**
 * SVG 局部样式预览的适配器——断言全部打在**真实 matplotlib 输出**上
 * （fixture 由 scripts/dump_svg_fixture.py 生成）。
 *
 * 要看护的是「我们对 matplotlib 输出形状的理解」，而这恰恰是最容易想当然的
 * 地方：颜色不在 gid 根节点上、alpha 是分开的 fill-opacity/stroke-opacity、
 * 线宽等于默认值时 stroke-width 根本不输出、文字默认黑色时连 fill 都没有。
 * 手写 fixture 只能验证想象，所以这里用真产物。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { MATPLOTLIB_SVG } from './__fixtures__/matplotlibSvg'
import {
  adapterFor,
  applyStyleEdit,
  canPreviewStyle,
  canStyleEditApply,
  restoreStyleEdits,
  unitsPerPt,
  type StyleEdit,
} from './svgStyle'

let root: SVGSVGElement

beforeEach(() => {
  document.body.innerHTML = `<div id="host">${MATPLOTLIB_SVG}</div>`
  root = document.querySelector('svg')!
})

const node = (gid: string) => document.querySelector(`[id="${gid}"]`)!
const ctx = { unitsPerPt: 1 }

/**
 * CSSOM 会把颜色规范化（`#1f77b4` 读回来是 `rgb(31, 119, 180)`，浏览器与
 * jsdom 一致）。断言两边都过一遍同一台机器，写起来才还是人看得懂的十六进制。
 */
const css = (v: string): string => {
  const probe = document.createElement('span')
  probe.style.setProperty('color', v)
  return probe.style.getPropertyValue('color') || v
}

/** 子树里所有声明了某条样式的叶子，方便断言「改了哪些、没改哪些」 */
const declared = (gid: string, prop: string): string[] => {
  const el = node(gid)
  const out: string[] = []
  for (const n of [el, ...Array.from(el.querySelectorAll('*'))]) {
    const v = (n as SVGElement).style?.getPropertyValue(prop)
    if (v) out.push(v.trim())
  }
  return out
}

/** declared() 的颜色版：期望值写十六进制，比较前两边一起规范化 */
const colors = (gid: string, prop: string): string[] =>
  declared(gid, prop).map((v) => (v === 'none' ? v : css(v)))
const hex = (...v: string[]): string[] => v.map((x) => (x === 'none' ? x : css(x)))

/** 改一条 → 断言 → 还原 → 断言回到原样，一步到位 */
function roundTrip(gid: string, kind: Parameters<typeof applyStyleEdit>[1], value: unknown) {
  const before = node(gid).outerHTML
  const edits = applyStyleEdit(node(gid), kind, value, ctx)
  const after = node(gid).outerHTML
  return {
    edits,
    after,
    restore: () => {
      restoreStyleEdits(edits)
      expect(node(gid).outerHTML).toBe(before)
    },
  }
}

/* ------------------------------ 能力表本身 -------------------------------- */

describe('能力表是白名单', () => {
  it('表里没有的 role/prop 一律不支持（默认不预览）', () => {
    expect(canPreviewStyle('line', 'color')).toBe(true)
    // 会重排 / 改路径 / 改栅格内容的，一个都不给
    expect(canPreviewStyle('text', 'fontsize')).toBe(false)
    expect(canPreviewStyle('text', 'fontfamily')).toBe(false)
    expect(canPreviewStyle('text', 'weight')).toBe(false)
    expect(canPreviewStyle('line', 'marker')).toBe(false)
    expect(canPreviewStyle('line', 'linestyle')).toBe(false)
    expect(canPreviewStyle('scatter', 'size')).toBe(false)
    expect(canPreviewStyle('scatter', 'marker')).toBe(false)
    expect(canPreviewStyle('bar_series', 'bar_width')).toBe(false)
    expect(canPreviewStyle('axes', 'xlim')).toBe(false)
    expect(canPreviewStyle('axes', 'position')).toBe(false)
    expect(canPreviewStyle('colorbar', 'cmap')).toBe(false)
    // patch 的样式在表里，但**「填充」那个开关不在**：空心 patch 的 SVG 是
    // `fill: none`，把它换成颜色是新增语义，通用规则不许，只能回退后端
    expect(canPreviewStyle('patch', 'facecolor')).toBe(true)
    expect(canPreviewStyle('patch', 'fill')).toBe(false)
    expect(canPreviewStyle('patch', 'linestyle')).toBe(false)
    expect(canPreviewStyle('legend', 'ncol')).toBe(false)
    expect(canPreviewStyle('legend', 'fontsize')).toBe(false)
    expect(canPreviewStyle('图上没有的角色', 'color')).toBe(false)
  })

  it('image.alpha 明确不支持：透明度被烤进 PNG 栅格，SVG 上没有旋钮', () => {
    expect(canPreviewStyle('image', 'visible')).toBe(true)
    expect(canPreviewStyle('image', 'alpha')).toBe(false)
    expect(canPreviewStyle('image', 'cmap')).toBe(false)
  })
})

/* -------------------------------- 描边类 ---------------------------------- */

describe('line', () => {
  it('color 只作用于 stroke，不会把 fill: none 填成实心', () => {
    expect(colors('axes_0.lines_0', 'stroke')).toEqual(hex('#1f77b4'))
    const t = roundTrip('axes_0.lines_0', 'stroke', '#ff0000')
    expect(colors('axes_0.lines_0', 'stroke')).toEqual(hex('#ff0000'))
    // fill 本来是 none，绝不能被写成颜色
    expect(colors('axes_0.lines_0', 'fill')).toEqual(['none'])
    t.restore()
  })

  it('linewidth 写 stroke-width，且按 pt→user unit 换算', () => {
    expect(declared('axes_0.lines_0', 'stroke-width')).toEqual(['1.5'])
    const t = roundTrip('axes_0.lines_0', 'strokeWidth', 4)
    expect(declared('axes_0.lines_0', 'stroke-width')).toEqual(['4'])
    t.restore()

    const scaled = applyStyleEdit(node('axes_0.lines_0'), 'strokeWidth', 4, { unitsPerPt: 2 })
    expect(declared('axes_0.lines_0', 'stroke-width')).toEqual(['8'])
    restoreStyleEdits(scaled)
    expect(declared('axes_0.lines_0', 'stroke-width')).toEqual(['1.5'])
  })

  it('alpha 写 stroke-opacity（描边类没有 fill，不该凭空多一条 fill-opacity）', () => {
    const t = roundTrip('axes_0.lines_0', 'opacity', 0.3)
    expect(declared('axes_0.lines_0', 'stroke-opacity')).toEqual(['0.3'])
    expect(declared('axes_0.lines_0', 'fill-opacity')).toEqual([])
    t.restore()
  })

  it('visible 只在 gid 根节点上写 display，还原后属性整条消失', () => {
    const el = node('axes_0.lines_0') as SVGElement
    const edits = applyStyleEdit(el, 'display', false, ctx)
    expect(el.style.display).toBe('none')
    restoreStyleEdits(edits)
    expect(el.getAttribute('style')).toBeNull()
  })
})

/* --------------------------------- 柱形 ----------------------------------- */

describe('bar', () => {
  it('facecolor 改 fill、edgecolor 改 stroke，互不串味', () => {
    expect(colors('axes_0.barseries_0.bar_0', 'fill')).toEqual(hex('#ff7f0e'))
    expect(colors('axes_0.barseries_0.bar_0', 'stroke')).toEqual(hex('#333333'))

    const f = roundTrip('axes_0.barseries_0.bar_0', 'fill', '#00ff00')
    expect(colors('axes_0.barseries_0.bar_0', 'fill')).toEqual(hex('#00ff00'))
    expect(colors('axes_0.barseries_0.bar_0', 'stroke')).toEqual(hex('#333333'))
    f.restore()

    const s = roundTrip('axes_0.barseries_0.bar_0', 'stroke', '#0000ff')
    expect(colors('axes_0.barseries_0.bar_0', 'stroke')).toEqual(hex('#0000ff'))
    expect(colors('axes_0.barseries_0.bar_0', 'fill')).toEqual(hex('#ff7f0e'))
    s.restore()
  })

  it('线宽等于默认值时 matplotlib 不输出 stroke-width——照样要能改', () => {
    // 这条是真实踩过的形状：判据若写成「已声明 stroke-width」，柱形边框就拖不动
    expect(declared('axes_0.barseries_0.bar_0', 'stroke-width')).toEqual([])
    const t = roundTrip('axes_0.barseries_0.bar_0', 'strokeWidth', 2.5)
    expect(declared('axes_0.barseries_0.bar_0', 'stroke-width')).toEqual(['2.5'])
    t.restore()
  })

  it('alpha 同时写 fill-opacity 与 stroke-opacity（两条都画着）', () => {
    const t = roundTrip('axes_0.barseries_0.bar_0', 'opacity', 0.5)
    expect(declared('axes_0.barseries_0.bar_0', 'fill-opacity')).toEqual(['0.5'])
    expect(declared('axes_0.barseries_0.bar_0', 'stroke-opacity')).toEqual(['0.5'])
    t.restore()
  })
})

/* --------------------------------- 散点 ----------------------------------- */

describe('scatter：样式分散在 <defs> 模板与每个 <use> 上', () => {
  it('facecolor 只命中声明了 fill 的 <use>，不碰只有 stroke 的模板 path', () => {
    const fills = declared('axes_0.scatter_0', 'fill')
    expect(fills.length).toBeGreaterThan(0)
    expect(colors('axes_0.scatter_0', 'fill').every((v) => v === css('#2ca02c'))).toBe(true)
    const defPath = node('axes_0.scatter_0').querySelector('defs path') as SVGElement
    expect(defPath.style.getPropertyValue('fill')).toBe('')

    const t = roundTrip('axes_0.scatter_0', 'fill', '#123456')
    expect(colors('axes_0.scatter_0', 'fill').every((v) => v === css('#123456'))).toBe(true)
    // 模板 path 本来没有 fill，不能被凭空加上一条
    expect(defPath.style.getPropertyValue('fill')).toBe('')
    t.restore()
  })

  it('edgecolor / linewidth 必须连 <defs> 模板一起改（影子树里它的样式优先）', () => {
    const defPath = node('axes_0.scatter_0').querySelector('defs path') as SVGElement
    expect(css(defPath.style.getPropertyValue('stroke'))).toBe(css('#000000'))

    const t = roundTrip('axes_0.scatter_0', 'stroke', '#abcdef')
    expect(css(defPath.style.getPropertyValue('stroke'))).toBe(css('#abcdef'))
    expect(colors('axes_0.scatter_0', 'stroke').every((v) => v === css('#abcdef'))).toBe(true)
    t.restore()

    const w = roundTrip('axes_0.scatter_0', 'strokeWidth', 3)
    expect(defPath.style.getPropertyValue('stroke-width')).toBe('3')
    w.restore()
  })
})

/* --------------------------------- 填充 ----------------------------------- */

describe('fill_between：matplotlib 自己就是分开写两条 opacity 的', () => {
  it('原始输出确实是 fill-opacity + stroke-opacity，不是一个 opacity', () => {
    expect(declared('axes_0.fill_2', 'fill-opacity')).toEqual(['0.5'])
    expect(declared('axes_0.fill_2', 'stroke-opacity')).toEqual(['0.5'])
    expect(declared('axes_0.fill_2', 'opacity')).toEqual([])
  })

  it('改 alpha 覆盖的是那两条，还原后一字不差', () => {
    const t = roundTrip('axes_0.fill_2', 'opacity', 0.9)
    expect(declared('axes_0.fill_2', 'fill-opacity')).toEqual(['0.9'])
    expect(declared('axes_0.fill_2', 'stroke-opacity')).toEqual(['0.9'])
    // 不能顺手多写一条 opacity：那会与原有语义叠乘，也还原不回去
    expect(declared('axes_0.fill_2', 'opacity')).toEqual([])
    t.restore()
  })
})

/* --------------------------------- 箭头 ----------------------------------- */

describe('arrow_patch：杆 fill:none、帽 fill:<色>', () => {
  it('颜色同时作用于 stroke 与 fill，但绝不把杆的 fill: none 填上', () => {
    expect(colors('axes_0.arrows_3', 'fill').sort()).toEqual(hex('#e377c2', 'none').sort())

    const t = roundTrip('axes_0.arrows_3', 'strokeFill', '#00aa00')
    expect(colors('axes_0.arrows_3', 'stroke').every((v) => v === css('#00aa00'))).toBe(true)
    // 帽跟着变色，杆仍然是 none
    expect(colors('axes_0.arrows_3', 'fill').sort()).toEqual(hex('#00aa00', 'none').sort())
    t.restore()
  })
})

/* --------------------------- 「改得到吗」的判据 ----------------------------- */

describe('canStyleEditApply：能力表说「支持」不等于这个 artist 上改得到', () => {
  it('与 applyStyleEdit 逐一对齐（同一份 styleTargets，不许分叉）', () => {
    const cases: [string, Parameters<typeof applyStyleEdit>[1], unknown][] = [
      ['axes_0.lines_0', 'stroke', '#00aa00'],
      ['axes_0.lines_0', 'fill', '#00aa00'],          // 线是 fill: none → 改不到
      ['axes_0.patches_4', 'fill', '#00aa00'],
      ['axes_0.patches_5', 'fill', '#00aa00'],        // 空心 → 改不到
      ['axes_0.patches_5', 'stroke', '#00aa00'],
      ['axes_0.lines_0', 'strokeWidth', 2],
      ['axes_0.lines_0', 'opacity', 0.5],
      ['axes_0.title', 'textFill', '#ff0000'],
      ['axes_0.lines_0', 'display', false],
    ]
    for (const [gid, kind, value] of cases) {
      const el = node(gid)
      const predicted = canStyleEditApply(el, kind, value, ctx)
      const edits = applyStyleEdit(el, kind, value, ctx)
      expect(predicted, `${gid} / ${kind}`).toBe(edits.length > 0)
      restoreStyleEdits(edits)
    }
  })

  it('值本身不合法（颜色框里是空串）时同样回 false', () => {
    expect(canStyleEditApply(node('axes_0.lines_0'), 'stroke', '', ctx)).toBe(false)
    expect(canStyleEditApply(node('axes_0.lines_0'), 'strokeWidth', 'abc', ctx)).toBe(false)
  })

  it('判据本身不碰 DOM', () => {
    const before = node('axes_0.patches_4').outerHTML
    canStyleEditApply(node('axes_0.patches_4'), 'fill', '#00aa00', ctx)
    expect(node('axes_0.patches_4').outerHTML).toBe(before)
  })
})

/* ------------------------------- 独立形状 --------------------------------- */

describe('patch：`ax.fill()` 的 Polygon（fill 与 stroke 各一条）', () => {
  const FILLED = 'axes_0.patches_4'

  it('原始输出就是 fill + stroke 两条，互不串味', () => {
    expect(colors(FILLED, 'fill')).toEqual(hex('#17becf'))
    expect(colors(FILLED, 'stroke')).toEqual(hex('#5a3286'))
  })

  it('facecolor 只改 fill，描边分毫不动', () => {
    const t = roundTrip(FILLED, 'fill', '#00aa00')
    expect(colors(FILLED, 'fill')).toEqual(hex('#00aa00'))
    expect(colors(FILLED, 'stroke')).toEqual(hex('#5a3286'))
    t.restore()
  })

  it('edgecolor 只改 stroke，填充分毫不动', () => {
    const t = roundTrip(FILLED, 'stroke', '#aa0000')
    expect(colors(FILLED, 'stroke')).toEqual(hex('#aa0000'))
    expect(colors(FILLED, 'fill')).toEqual(hex('#17becf'))
    t.restore()
  })

  it('linewidth 写 stroke-width，并按 pt→user unit 换算', () => {
    expect(declared(FILLED, 'stroke-width')).toEqual(['1.2'])
    const t = roundTrip(FILLED, 'strokeWidth', 3)
    expect(declared(FILLED, 'stroke-width')).toEqual(['3'])
    t.restore()

    const scaled = applyStyleEdit(node(FILLED), 'strokeWidth', 3, { unitsPerPt: 2 })
    expect(declared(FILLED, 'stroke-width')).toEqual(['6'])
    restoreStyleEdits(scaled)
  })

  it('alpha 同时写 fill-opacity 与 stroke-opacity（两条都画着）', () => {
    const t = roundTrip(FILLED, 'opacity', 0.35)
    expect(declared(FILLED, 'fill-opacity')).toEqual(['0.35'])
    expect(declared(FILLED, 'stroke-opacity')).toEqual(['0.35'])
    // 不能顺手多写一条 opacity：那会与已有语义叠乘，也还原不回去
    expect(declared(FILLED, 'opacity')).toEqual([])
    t.restore()
  })

  it('visible 只在 gid 根节点上写 display，还原后属性整条消失', () => {
    const el = node(FILLED)
    const edits = applyStyleEdit(el, 'display', false, ctx)
    expect((el as unknown as SVGElement).style.display).toBe('none')
    restoreStyleEdits(edits)
    expect((el as unknown as SVGElement).style.getPropertyValue('display')).toBe('')
  })
})

describe('patch：`fill=False` 的 PathPatch —— facecolor 绝不把它填实', () => {
  const HOLLOW = 'axes_0.patches_5'

  it('原始输出是 fill: none', () => {
    expect(colors(HOLLOW, 'fill')).toEqual(['none'])
    expect(colors(HOLLOW, 'stroke')).toEqual(hex('#7f7f0f'))
  })

  it('改 facecolor：一个字节都不动，且如实报告「预览没生效」', () => {
    const before = node(HOLLOW).outerHTML
    const edits = applyStyleEdit(node(HOLLOW), 'fill', '#00aa00', ctx)
    expect(edits).toEqual([])          // 空数组 = 调用方据此回退后端
    expect(node(HOLLOW).outerHTML).toBe(before)
    expect(colors(HOLLOW, 'fill')).toEqual(['none'])
  })

  it('空心的描边照样能改（颜色 / 线宽 / alpha）', () => {
    const c = roundTrip(HOLLOW, 'stroke', '#0000aa')
    expect(colors(HOLLOW, 'stroke')).toEqual(hex('#0000aa'))
    c.restore()

    const w = roundTrip(HOLLOW, 'strokeWidth', 4)
    expect(declared(HOLLOW, 'stroke-width')).toEqual(['4'])
    w.restore()

    const a = roundTrip(HOLLOW, 'opacity', 0.5)
    expect(declared(HOLLOW, 'stroke-opacity')).toEqual(['0.5'])
    // 没有 fill 就不该凭空多一条 fill-opacity
    expect(declared(HOLLOW, 'fill-opacity')).toEqual([])
    a.restore()
  })
})

/* --------------------------------- 文字 ----------------------------------- */

describe('文字：颜色写在字形组上，而且可能本来就没有这条属性', () => {
  it('有颜色的文字：替换字形组的 fill', () => {
    expect(colors('axes_0.texts_0', 'fill')).toEqual(hex('#123456'))
    const t = roundTrip('axes_0.texts_0', 'textFill', '#ff00ff')
    expect(colors('axes_0.texts_0', 'fill')).toEqual(hex('#ff00ff'))
    t.restore()
  })

  it('默认黑色的标题**根本没有 fill**：适配器要能新增，还原时要能删干净', () => {
    expect(declared('axes_0.title', 'fill')).toEqual([])
    const before = node('axes_0.title').outerHTML
    const edits = applyStyleEdit(node('axes_0.title'), 'textFill', '#ff0000', ctx)
    expect(colors('axes_0.title', 'fill')).toEqual(hex('#ff0000'))
    restoreStyleEdits(edits)
    expect(node('axes_0.title').outerHTML).toBe(before)
  })

  it('alpha 走字形组的 opacity（matplotlib 输出的就是这个）', () => {
    expect(declared('axes_0.texts_1', 'opacity')).toEqual(['0.4'])
    const t = roundTrip('axes_0.texts_1', 'textOpacity', 0.8)
    expect(declared('axes_0.texts_1', 'opacity')).toEqual(['0.8'])
    t.restore()
  })
})

/* --------------------------------- 位图 ----------------------------------- */

describe('image', () => {
  it('gid 落在 <image> 自身而不是 <g>；隐藏只写 display，transform 分毫不动', () => {
    const el = node('axes_0.images_0') as unknown as SVGElement
    expect(el.tagName.toLowerCase()).toBe('image')
    const tf = el.getAttribute('transform')
    expect(tf).toBeTruthy()
    const edits = applyStyleEdit(el, 'display', false, ctx)
    expect(el.style.display).toBe('none')
    expect(el.getAttribute('transform')).toBe(tf)
    restoreStyleEdits(edits)
    expect(el.style.getPropertyValue('display')).toBe('')
  })
})

/* ------------------------------ 反复改与还原 ------------------------------- */

describe('连续改十次（拖滑块）', () => {
  it('base 只记第一次：还原回到最初，而不是某个中间值', () => {
    const el = node('axes_0.lines_0')
    const edits: StyleEdit[] = []
    for (let i = 0; i < 10; i++) {
      for (const e of applyStyleEdit(el, 'stroke', `#00000${i}`, ctx)) {
        if (!edits.some((x) => x.el === e.el)) edits.push(e)
      }
    }
    expect(colors('axes_0.lines_0', 'stroke')).toEqual(hex('#000009'))
    restoreStyleEdits(edits)
    expect(colors('axes_0.lines_0', 'stroke')).toEqual(hex('#1f77b4'))
  })
})

/* --------------------------------- 取值 ----------------------------------- */

describe('值不合法就什么都不改（据实回退，绝不假装预览成功）', () => {
  it('颜色只认 #rgb / #rrggbb', () => {
    expect(applyStyleEdit(node('axes_0.lines_0'), 'stroke', 'red', ctx)).toEqual([])
    expect(applyStyleEdit(node('axes_0.lines_0'), 'stroke', 42, ctx)).toEqual([])
    expect(colors('axes_0.lines_0', 'stroke')).toEqual(hex('#1f77b4'))
    expect(applyStyleEdit(node('axes_0.lines_0'), 'stroke', '#abc', ctx).length).toBeGreaterThan(0)
  })

  it('数值不是有限数就不改', () => {
    expect(applyStyleEdit(node('axes_0.lines_0'), 'strokeWidth', 'x', ctx)).toEqual([])
    expect(applyStyleEdit(node('axes_0.lines_0'), 'opacity', NaN, ctx)).toEqual([])
    expect(declared('axes_0.lines_0', 'stroke-width')).toEqual(['1.5'])
  })
})

/* --------------------------------- 单位 ----------------------------------- */

describe('unitsPerPt：viewBox 与 matplotlib pt 的关系是算出来的，不是写死的', () => {
  it('matplotlib 的 SVG viewBox 就是 pt，实测比值为 1', () => {
    // fixture 是 figsize=(4,3) 英寸 → 101.6×76.2 mm → viewBox 288×216
    expect(root.getAttribute('viewBox')).toBe('0 0 288 216')
    expect(unitsPerPt(root, [101.6, 76.2])).toBeCloseTo(1, 9)
  })

  it('图幅被 size_mm 改掉后比值跟着变，线宽不会跟着图一起缩', () => {
    expect(unitsPerPt(root, [50.8, 38.1])).toBeCloseTo(2, 9)
  })

  it('算不出来时退回 1，绝不让预览整个失效', () => {
    expect(unitsPerPt(null, [100, 80])).toBe(1)
    expect(unitsPerPt(root, undefined)).toBe(1)
    expect(unitsPerPt(root, [0, 0])).toBe(1)
  })
})

describe('adapterFor', () => {
  it('role 与 prop 都要对上', () => {
    expect(adapterFor('line', 'color')).toBe('stroke')
    expect(adapterFor('arrow_patch', 'color')).toBe('strokeFill')
    expect(adapterFor('bar', 'facecolor')).toBe('fill')
    expect(adapterFor('title', 'color')).toBe('textFill')
    expect(adapterFor('line', 'fontsize')).toBeNull()
  })
})
