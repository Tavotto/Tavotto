/**
 * 属性能力层（ADR 0032）的模型面。
 *
 * 这一层存在的理由是「同一个属性在两类对象上是同一件事」，所以用例的形状
 * 也是成对的：同一条规范属性，图内与画布两侧各问一遍，答案必须一致或者
 * **说得出为什么不一致**。
 */
import { describe, expect, it } from 'vitest'

import {
  CANVAS_TEXT_DEFAULT_FAMILY,
  CANVAS_TEXT_FAMILIES,
  TYPOGRAPHY_PROPS,
  TYPOGRAPHY_PROPERTY_PATHS,
  canvasFieldOf,
  canvasFontStack,
  canvasTextDefaults,
  coerceTypography,
  commonSupport,
  effectiveCanvasFamily,
  inheritedCanvasValue,
  mathTextModeOf,
  propOfPath,
  propertyPathOf,
  readCanvasText,
  supportsTypography,
  writeCanvasText,
  type TypographyKind,
} from './typography'
import type { TextObject } from '@/types/document'

const text = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: 'H2O',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  x: 0,
  y: 0,
  w: 20,
  h: 8,
  ...over,
})

const KINDS: TypographyKind[] = ['figureText', 'canvasText']

describe('能力表', () => {
  it('每种对象都有一张支持表，且表里的每一条都说得出 property path', () => {
    for (const kind of KINDS) {
      for (const prop of TYPOGRAPHY_PROPS) {
        if (!supportsTypography(kind, prop)) continue
        const path = propertyPathOf(kind, prop)
        expect(path, `${kind}.${prop}`).toBeTruthy()
        // 支持了却报不出字段名 = 检查能定位到对象、定位不到字段
        expect(TYPOGRAPHY_PROPERTY_PATHS.has(path!)).toBe(true)
      }
    }
  })

  it('property path 与规范属性名可以互查（报字段名的和挂锚点的读同一张表）', () => {
    for (const kind of KINDS) {
      for (const prop of TYPOGRAPHY_PROPS) {
        const path = propertyPathOf(kind, prop)
        if (!path) continue
        expect(propOfPath(kind, path)).toBe(prop)
      }
    }
  })

  it('不支持的属性不给 property path——不是给一个用不上的名字', () => {
    // 画布文字的框就是它的框，没有垂直对齐这回事
    expect(supportsTypography('canvasText', 'valign')).toBe(false)
    expect(propertyPathOf('canvasText', 'valign')).toBeNull()
    // 图内文字的行距归 matplotlib 的 Text 自己，manifest 不发这一维
    expect(supportsTypography('figureText', 'lineHeight')).toBe(false)
    expect(propertyPathOf('figureText', 'lineHeight')).toBeNull()
  })

  it('横跨两类的选择取交集，不是并集', () => {
    const both = commonSupport(['figureText', 'canvasText'])
    // 六条排版核心两类都有——这正是「一起选中也能改」的structural保证
    for (const p of ['fontFamily', 'sizePt', 'weight', 'style', 'color', 'halign'] as const) {
      expect(both.has(p), p).toBe(true)
    }
    // 只有一侧有的两条落在交集之外
    expect(both.has('valign')).toBe(false)
    expect(both.has('lineHeight')).toBe(false)
    expect(commonSupport([]).size).toBe(0)
  })

  it('科学文本的能力两类各有各的名字（Prompt 14 接手时不用先拆开）', () => {
    expect(mathTextModeOf('canvasText')).toBe('inline_markup')
    expect(mathTextModeOf('figureText')).toBe('engine_mathtext')
  })
})

describe('取值语义', () => {
  it('字重 / 字形在两侧是同一个枚举，画布那侧的 boolean 只活在磁盘上', () => {
    expect(readCanvasText(text({ bold: true }), 'weight')).toBe('bold')
    expect(readCanvasText(text({ bold: false }), 'weight')).toBe('normal')
    expect(readCanvasText(text({ italic: true }), 'style')).toBe('italic')
    // 没设过 italic 与设成 false 是两个答案：前者可以「恢复」，后者已经是选择
    expect(readCanvasText(text(), 'style')).toBeUndefined()
    expect(readCanvasText(text({ italic: false }), 'style')).toBe('normal')

    const o = text()
    writeCanvasText(o, 'weight', 'bold')
    expect(o.bold).toBe(true)
    writeCanvasText(o, 'style', 'italic')
    expect(o.italic).toBe(true)
    writeCanvasText(o, 'style', 'normal')
    expect('italic' in o).toBe(false)
  })

  it('字体族没设过 = 继承默认，写回默认值时把字段删掉而不是写一个等价值', () => {
    const o = text()
    expect(o.fontFamily).toBeUndefined()
    expect(effectiveCanvasFamily(o)).toBe(CANVAS_TEXT_DEFAULT_FAMILY)
    expect(inheritedCanvasValue('fontFamily')).toBe(CANVAS_TEXT_DEFAULT_FAMILY)

    writeCanvasText(o, 'fontFamily', 'monospace')
    expect(o.fontFamily).toBe('monospace')
    writeCanvasText(o, 'fontFamily', CANVAS_TEXT_DEFAULT_FAMILY)
    // 留一个显式的 'serif' 会让「没设过」变成「设过、正好一样」——
    // 导出载荷凭空多一个字段，而语义完全相同
    expect('fontFamily' in o).toBe(false)
  })

  it('不认识的族按继承处理，绝不当成一个新的族存进文档', () => {
    const o = text()
    writeCanvasText(o, 'fontFamily', 'Times New Roman')
    expect('fontFamily' in o).toBe(false)
    expect(effectiveCanvasFamily({ fontFamily: 'Comic Sans' as never })).toBe(
      CANVAS_TEXT_DEFAULT_FAMILY,
    )
  })

  it('默认族的 CSS 栈仍然是文档字体——老文档的画面一个像素不许变', () => {
    expect(canvasFontStack(CANVAS_TEXT_DEFAULT_FAMILY)).toBe('var(--font-doc)')
    for (const f of CANVAS_TEXT_FAMILIES) expect(canvasFontStack(f)).toBeTruthy()
    expect(new Set(CANVAS_TEXT_FAMILIES.map(canvasFontStack)).size).toBe(
      CANVAS_TEXT_FAMILIES.length,
    )
  })

  it('新建时的默认值只有一处，且**不含**字体族', () => {
    const d = canvasTextDefaults()
    expect(d).toEqual({
      sizePt: 10,
      weight: 'normal',
      style: 'normal',
      color: '#000000',
      halign: 'left',
    })
    expect('fontFamily' in d).toBe(false)
  })
})

describe('校验与规整', () => {
  it('数值：越界与非数各有各的成因，都不给出一个"修正后"的值', () => {
    expect(coerceTypography('sizePt', 9)).toEqual({ ok: true, value: 9 })
    expect(coerceTypography('sizePt', '9')).toEqual({ ok: true, value: 9 })
    expect(coerceTypography('sizePt', 'abc')).toEqual({ ok: false, reason: 'not_a_number' })
    expect(coerceTypography('sizePt', NaN)).toEqual({ ok: false, reason: 'not_a_number' })
    expect(coerceTypography('sizePt', 0)).toEqual({ ok: false, reason: 'out_of_range' })
    // **绝不 clamp**：把 500 悄悄改成 400 等于替用户按了一个他没按的键
    expect(coerceTypography('sizePt', 500)).toEqual({ ok: false, reason: 'out_of_range' })
  })

  it('数值区间取「本表与目标值域的更紧者」——宽的那个会让某个目标收到越界值', () => {
    const field = canvasFieldOf('sizePt')!
    expect(coerceTypography('sizePt', 200, field)).toEqual({ ok: false, reason: 'out_of_range' })
    // 不带 field 时 200 在兜底区间内，两个答案不同 = 确实按 field 收紧了
    expect(coerceTypography('sizePt', 200)).toEqual({ ok: true, value: 200 })
  })

  it('枚举：选项表之外的值一律拒绝，不落到"最接近的那个"', () => {
    expect(coerceTypography('weight', 'bold')).toEqual({ ok: true, value: 'bold' })
    expect(coerceTypography('weight', 'bolder')).toEqual({ ok: false, reason: 'not_an_option' })
    expect(coerceTypography('halign', 'justify')).toEqual({ ok: false, reason: 'not_an_option' })
    expect(coerceTypography('fontFamily', 'monospace')).toEqual({ ok: true, value: 'monospace' })
    expect(coerceTypography('fontFamily', 'Arial')).toEqual({ ok: false, reason: 'not_an_option' })
    // 图内那侧的选项由 manifest 给：同一个名字在两侧的答案可以不同
    expect(coerceTypography('fontFamily', 'Arial', { options: ['serif', 'Arial'] })).toEqual({
      ok: true,
      value: 'Arial',
    })
  })

  it('颜色只认 hex', () => {
    expect(coerceTypography('color', '#abc')).toEqual({ ok: true, value: '#abc' })
    expect(coerceTypography('color', '#AABBCC')).toEqual({ ok: true, value: '#AABBCC' })
    expect(coerceTypography('color', 'red')).toEqual({ ok: false, reason: 'not_a_color' })
    expect(coerceTypography('color', 123)).toEqual({ ok: false, reason: 'not_a_color' })
  })
})
