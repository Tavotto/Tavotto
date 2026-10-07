/**
 * 「文档颜色」只读文档里写下来、会画出来的颜色（`documentColorsOf`）。
 */
import { describe, expect, it } from 'vitest'
import type { TextObject } from '@/types/document'
import { documentColorsOf } from './documentColors'

const note = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: '标注',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  x: 10,
  y: 10,
  w: 20,
  h: 8,
  ...over,
})

describe('documentColorsOf', () => {
  it('文字框的描边色也是文档颜色——只用在描边上的颜色不能漏（Codex #829）', () => {
    const colors = documentColorsOf({
      objects: [note({ bg: '#ffeedd', borderColor: '#13579b', borderPt: 0.5 })],
      page: { w: 150, h: 100, transparent: true },
    })
    expect(colors).toContain('#13579b')
    expect(colors).toContain('#ffeedd')
    expect(colors).toContain('#000000')
  })

  it('没有描边（borderColor 缺席 / null）不凭空添色', () => {
    const colors = documentColorsOf({
      objects: [note({ borderColor: null })],
      page: { w: 150, h: 100, transparent: true },
    })
    expect(colors).toEqual(['#000000'])
  })
})
