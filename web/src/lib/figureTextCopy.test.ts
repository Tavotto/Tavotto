import { describe, expect, it } from 'vitest'
import type { ManifestElement, ManifestTextBlock } from './api'
import type { PanelObject } from '@/types/document'
import { fitWidthMm } from '@/canvas/textFit'
import { MM_PER_PT } from './units'
import {
  annotationTextOf,
  BASELINE_MONO_PER_MILLE,
  BASELINE_SANS_PER_MILLE,
  BASELINE_SERIF_PER_MILLE,
  figureTextToAnnotation,
  isCopyableText,
} from './figureTextCopy'

/**
 * 图内文字 → 标注：期望值都是**测试侧独立算的页面坐标**（不调用 `figureTextCopy` 的任何换算）：
 * 页面点 = 面板左上角 + 分数 × 面板尺寸（再按面板的旋转 / 翻转手算），第一行基线 = 文字块中心 + (asc − h / 2) 沿「向下」。
 * 标注这一侧的基线 = 框中心 + (行盒中线到基线 K em − 框高 / 2 + 框高 / 2 ……) 按渲染的公式单独写在 `baselineOf` 里。
 */

const K = { sans: BASELINE_SANS_PER_MILLE / 1000, serif: BASELINE_SERIF_PER_MILLE / 1000, mono: BASELINE_MONO_PER_MILLE / 1000 }

/** 面板：页面上 (10, 20)，100 × 75 mm，原生 50 × 37.5 mm（放大一倍） */
const panelOf = (extra: Record<string, unknown> = {}): PanelObject =>
  ({ id: 'p1', type: 'panel', x: 10, y: 20, w: 100, h: 75, nativeW: 50, nativeH: 37.5, ...extra }) as unknown as PanelObject

const block = (over: Partial<ManifestTextBlock> = {}): ManifestTextBlock => ({
  center: [0.5, 0.1],
  w: 0.2,
  h: 0.06,
  asc: 0.045,
  text: 'Reaction kinetics',
  math: false,
  size: 10,
  color: '#112233',
  weight: 'normal',
  style: 'normal',
  rotation: 0,
  ha: 'center',
  ...over,
})
const elOf = (tb: ManifestTextBlock | undefined, extra: Partial<ManifestElement> = {}): ManifestElement =>
  ({
    gid: 'axes_0.title',
    role: 'title',
    label: 'x',
    face: 'DejaVu Sans',
    text_block: tb,
    ...extra,
  }) as unknown as ManifestElement

/** 标注的第一行基线起点在页面上的位置：框中心 + 沿 u 的 (行首偏移) + 沿 v 的 (基线到框中心)。独立于实现再算一遍。 */
function baselineOf(a: NonNullable<ReturnType<typeof figureTextToAnnotation>>, k: number, lines = 1) {
  const phi = ((a.rotationDeg ?? 0) * Math.PI) / 180
  const u = [Math.cos(phi), Math.sin(phi)]
  const v = [-Math.sin(phi), Math.cos(phi)]
  const lh = a.lineHeight ?? 1.25
  const size = a.sizePt * MM_PER_PT
  const cx = a.x + a.w / 2
  const cy = a.y + a.h / 2
  const down = size * (k - ((lines - 1) * lh) / 2) // 基线在框中心之下
  return { x: cx + down * v[0], y: cy + down * v[1], u }
}

describe('图内文字 → 标注：摆放', () => {
  it('横排标题：与原文字同一个中心、同一条基线；字号按页面（面板放大一倍）', () => {
    const a = figureTextToAnnotation(panelOf(), elOf(block()))!
    expect(a.type).toBe('text')
    expect(a.rotationDeg).toBeUndefined() // 0 不写字段
    expect(a.sizePt).toBeCloseTo(20, 2) // 脚本 10 pt × 放大 2
    expect(a.color).toBe('#112233')
    expect(a.align).toBe('center')
    expect(a.bold).toBe(false)
    expect(a.italic).toBeUndefined()
    // 文字块中心：x = 10 + 0.5 × 100，y = 20 + 0.1 × 75
    expect(a.x + a.w / 2).toBeCloseTo(60, 6)
    // 第一行基线：块中心 + (asc − h/2) × 面板高 = 27.5 + (0.045 − 0.03) × 75
    const base = baselineOf(a, K.sans)
    expect(base.y).toBeCloseTo(20 + 0.1 * 75 + (0.045 - 0.03) * 75, 6)
    // 框宽 = 文字宽（0.2 × 100 mm）按收框公式
    expect(a.w).toBeCloseTo(fitWidthMm(0.2 * 100, 20), 9)
    expect(a.h).toBeCloseTo(1.25 * 20 * MM_PER_PT, 9)
  })

  it('y 轴标签（matplotlib 转角 90，逆时针）→ rotationDeg -90；基线与文字块中心都对得上', () => {
    const tb = block({ center: [0.02, 0.5], w: 0.3, h: 0.05, asc: 0.04, rotation: 90, text: 'Conversion' })
    const a = figureTextToAnnotation(panelOf(), elOf(tb, { gid: 'axes_0.ylabel', role: 'axis_label' }))!
    expect(a.rotationDeg).toBe(-90)
    // 居中对齐：沿阅读方向（页面上是竖直）框中心 = 文字块中心 (0.02, 0.5) 的那一维
    expect(a.y + a.h / 2).toBeCloseTo(20 + 0.5 * 75, 6)
    // 阅读方向朝上：u = (0, −1)；「向下」v = (1, 0)：基线在块中心右侧 (asc − h/2) × 面板高
    const base = baselineOf(a, K.sans)
    expect(base.u[0]).toBeCloseTo(0, 9)
    expect(base.u[1]).toBeCloseTo(-1, 9)
    expect(base.x).toBeCloseTo(10 + 0.02 * 100 + (0.04 - 0.025) * 75, 6)
    expect(base.y).toBeCloseTo(20 + 0.5 * 75, 6)
  })

  it('面板自己转 90°（顺时针）：y 轴标签读起来变成横排，rotationDeg 0；位置按旋转后的页面算', () => {
    // 90°：内容宽 / 高互换——页面框 75 × 100，内容 100 × 75，绕页面框中心 (47.5, 70) 转
    const p = panelOf({ w: 75, h: 100, rotation: 90, x: 10, y: 20, nativeW: 50, nativeH: 37.5 })
    const tb = block({ center: [0.02, 0.5], w: 0.3, h: 0.05, asc: 0.04, rotation: 90, text: 'Conversion' })
    const a = figureTextToAnnotation(p, elOf(tb))!
    expect(a.rotationDeg).toBeUndefined() // 0
    // 内容坐标 (0.02, 0.5) → 相对内容中心 (−48, 0)（mm）→ 顺时针 90°：(x, y) → (−y, x) = (0, −48)
    // 文字块中心 C = (47.5, 22)；阅读方向 u = (1, 0)、向下 v = (0, 1)，基线在 C 之下 (asc − h/2) × 75
    expect(a.x + a.w / 2).toBeCloseTo(47.5, 6)
    expect(baselineOf(a, K.sans).y).toBeCloseTo(70 - 48 + (0.04 - 0.025) * 75, 6)
  })

  it('面板水平翻转：横排标题仍读得顺（角 0），左对齐变右对齐；中心按镜像后的页面位置', () => {
    const tb = block({ center: [0.25, 0.1], ha: 'left' })
    const a = figureTextToAnnotation(panelOf({ flipH: true }), elOf(tb))!
    expect(a.rotationDeg).toBeUndefined()
    expect(a.align).toBe('right')
    // 镜像后的块中心 x = 60 − (0.25 − 0.5) × 100 → 85；右对齐的框右边 = 块右边 = 中心 + wText / 2
    const wText = 0.2 * 100
    expect(a.x + a.w).toBeCloseTo(85 + wText / 2, 6)
  })

  it('面板裁剪：分数仍摊在未裁剪的完整内容上', () => {
    // 裁掉左边 20%：可见窗 = 内容的 [0.2, 1] × [0, 1]，面板页面框 100 × 75 装的是 0.8 宽的窗口 → 完整内容 125 × 75
    const p = panelOf({ crop: { x: 0.2, y: 0, w: 0.8, h: 1 } })
    const a = figureTextToAnnotation(p, elOf(block({ center: [0.5, 0.1] })))!
    // 完整内容左边 = 面板左 − 0.2 × 125 = −15；中心 = −15 + 0.5 × 125
    expect(a.x + a.w / 2).toBeCloseTo(-15 + 0.5 * 125, 6)
  })

  it('多行：行距取 matplotlib 的相邻基线间距，第一行基线仍与原文字重合', () => {
    const tb = block({ text: 'line1\nline two', center: [0.5, 0.4], h: 0.11, asc: 0.04, step: 0.065, ha: 'left', w: 0.14 })
    const a = figureTextToAnnotation(panelOf(), elOf(tb))!
    const size = a.sizePt * MM_PER_PT
    expect(a.lineHeight).toBeCloseTo((0.065 * 75) / size, 3)
    expect(a.h).toBeCloseTo(2 * a.lineHeight! * size, 6)
    const base = baselineOf(a, K.sans, 2)
    expect(base.y).toBeCloseTo(20 + 0.4 * 75 + (0.04 - 0.055) * 75, 6)
    // 左对齐：文字块左边 = 中心 − wText / 2 = 框左边
    expect(a.x).toBeCloseTo(60 - (0.14 * 100) / 2, 6)
    expect(a.text).toBe('line1\nline two')
  })

  it('转了任意角度（30°）：框绕自己的中心转，第一行基线起点与原文字对得上', () => {
    const tb = block({ center: [0.4, 0.5], w: 0.15, h: 0.06, asc: 0.045, rotation: 30, ha: 'left' })
    const a = figureTextToAnnotation(panelOf(), elOf(tb))!
    expect(a.rotationDeg).toBe(-30) // 逆时针 30° = 顺时针 −30°
    const base = baselineOf(a, K.sans)
    // 原文字：块中心 C，阅读方向 u0 = (cos30, −sin30)，向下 v0 = (sin30, cos30)；
    // 左对齐的第一行基线起点 = C − (wText / 2) u0 + (asc − h / 2) v0，其中 asc、h 是以面板高算的 mm
    const th = (30 * Math.PI) / 180
    const u0 = [Math.cos(th), -Math.sin(th)]
    const v0 = [Math.sin(th), Math.cos(th)]
    const C = [10 + 0.4 * 100, 20 + 0.5 * 75]
    const wText = 0.15 * 100
    const down = (0.045 - 0.03) * 75
    const want = [C[0] - (wText / 2) * u0[0] + down * v0[0], C[1] - (wText / 2) * u0[1] + down * v0[1]]
    // 标注这一侧的第一行起点：框左边中点 + …… = 框中心 − (w / 2) u + 基线下移 v
    const phi = (-30 * Math.PI) / 180
    const u = [Math.cos(phi), Math.sin(phi)]
    const got = [base.x - (a.w / 2) * u[0], base.y - (a.w / 2) * u[1]]
    expect(got[0]).toBeCloseTo(want[0], 5)
    expect(got[1]).toBeCloseTo(want[1], 5)
  })

  it('字体族：具体字体名归到最接近的通用族；基线常数跟族走', () => {
    const times = figureTextToAnnotation(panelOf(), elOf(block(), { face: 'Times New Roman' }))!
    expect(times.fontFamily).toBe('serif')
    const mono = figureTextToAnnotation(panelOf(), elOf(block(), { face: 'Courier New' }))!
    expect(mono.fontFamily).toBe('monospace')
    const dj = figureTextToAnnotation(panelOf(), elOf(block(), { face: 'DejaVu Sans' }))!
    expect(dj.fontFamily).toBe('sans-serif')
    // 同一块、不同族：框中心的竖直位置差 = 两个 K 之差（基线重合 → 框要跟着挪）
    expect(times.y + times.h / 2 - (dj.y + dj.h / 2)).toBeCloseTo((K.sans - K.serif) * dj.sizePt * MM_PER_PT, 6)
    expect(mono.y + mono.h / 2 - (dj.y + dj.h / 2)).toBeCloseTo((K.sans - K.mono) * dj.sizePt * MM_PER_PT, 6)
  })

  it('原图字体、粗体、斜体：sourceFont 取 face_src，粗斜以那张脸为准；没有身份时走仓库判据', () => {
    const sha = 'a'.repeat(64)
    const a = figureTextToAnnotation(
      panelOf(),
      elOf(block({ weight: 'bold', style: 'oblique' }), {
        face_src: { family: 'DejaVu Sans', sha256: sha, face: 0, bold: true, italic: true },
      } as Partial<ManifestElement>),
    )!
    expect(a.sourceFont).toEqual({ family: 'DejaVu Sans', sha256: sha, face: 0, bold: true, italic: true })
    expect(a.bold).toBe(true)
    expect(a.italic).toBe(true)
    // 字体没有粗体：图里画的是常规脸，复制过来不再模拟
    const plain = figureTextToAnnotation(
      panelOf(),
      elOf(block({ weight: 'bold' }), {
        face_src: { family: 'SimHei', sha256: sha, face: 0, bold: false, italic: false },
      } as Partial<ManifestElement>),
    )!
    expect(plain.bold).toBe(false)
    expect(figureTextToAnnotation(panelOf(), elOf(block({ weight: 600 })))!.bold).toBe(true)
    expect(figureTextToAnnotation(panelOf(), elOf(block({ weight: 'medium' })))!.bold).toBe(false)
    // 没有 face_src：不编身份
    expect(figureTextToAnnotation(panelOf(), elOf(block()))!.sourceFont).toBeUndefined()
  })
})

describe('图内文字 → 标注：哪些能复制', () => {
  it('只有带 text_block 的才是一段单独的文字；曲线 / 色块 / 刻度组没有', () => {
    expect(isCopyableText(elOf(block()))).toBe(true)
    expect(isCopyableText(elOf(undefined, { gid: 'axes_0.lines_0', role: 'line' }))).toBe(false)
    expect(figureTextToAnnotation(panelOf(), elOf(undefined))).toBeNull()
  })
  it('空文字不出标注', () => {
    expect(figureTextToAnnotation(panelOf(), elOf(block({ text: '  ' })))).toBeNull()
  })
})

describe('matplotlib 文字 → 标注文本', () => {
  it('普通文本保持普通文本，旧标记里会被误读的写法转义，字面不变', () => {
    expect(annotationTextOf('Reaction time (min)', false)).toEqual({ text: 'Reaction time (min)' })
    expect(annotationTextOf('a^{b} and c_{d}', false).text).toBe('a\\^{b} and c\\_{d}')
    expect(annotationTextOf('cost \\$5', false).text).toBe('cost $5') // matplotlib 非公式文字里 \$ 画成 $
    expect(annotationTextOf('line1\nline2', false).text).toBe('line1\nline2')
  })
  it('含 mathtext：LaTeX 子集写法，textFormat latex；文字段里的特殊字符转义成字面', () => {
    const r = annotationTextOf('Conversion $\\alpha$ (–)', true)
    expect(r).toEqual({ text: 'Conversion $\\alpha$ (–)', textFormat: 'latex' })
    expect(annotationTextOf('k = 0.125 min$^{-1}$', true).text).toBe('k = 0.125 min$^{-1}$')
    expect(annotationTextOf('{a} ~ $x$', true).text).toBe('\\{a\\} \\~{} $x$')
    expect(annotationTextOf('50% $x$', true).text).toBe('50\\% $x$')
  })
})
