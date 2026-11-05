import { describe, expect, it } from 'vitest'
import {
  isBuiltinFace,
  restyleLocalFace,
  setBuiltinFamily,
  setLocalCjkFamily,
  setLocalFamily,
  styleMissing,
  type FontCatalog,
} from './fontCatalog'
import type { TextObject } from '@/types/document'

const sha = (c: string) => c.repeat(64)
const face = (c: string, bold = false, italic = false, n = 0) => ({ sha256: sha(c), face: n, bold, italic })

const catalog: FontCatalog = {
  families: [
    {
      name: 'Times New Roman',
      zh: null,
      cjk: false,
      generic: 'serif',
      faces: [face('a'), face('b', true), face('c', false, true), face('d', true, true)],
    },
    { name: 'SimSun', zh: '宋体', cjk: true, generic: null, faces: [face('e')] },
  ],
  builtin: {
    serif: { name: 'Liberation Serif', faces: [face('1'), face('2', true)] },
    'sans-serif': { name: 'Liberation Sans', faces: [face('3'), face('4', true)] },
  },
}

const text = (over: Partial<TextObject> = {}) => ({ id: 't', type: 'text', bold: false, ...over }) as TextObject

describe('选本机字体', () => {
  it('英文选本机字体：写身份和回退族，不带文件路径', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(o.sourceFont).toEqual({ family: 'Times New Roman', sha256: sha('a'), face: 0 })
    expect(o.fontFamily).toBe('serif')
  })

  it('已是粗体时选到这一族的粗体那张脸', () => {
    const o = text({ bold: true })
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(o.sourceFont).toMatchObject({ sha256: sha('b'), bold: true })
  })

  it('这一族没有粗体：选常规脸，对象的粗体跟着关掉，按钮不会说谎', () => {
    const o = text({ bold: true })
    setLocalFamily(o, catalog, 'SimSun')
    expect(o.sourceFont?.sha256).toBe(sha('e'))
    expect(o.bold).toBe(false)
  })

  it('换成自带中文字形的字体：之前单独选的中文脸清掉', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    setLocalFamily(o, catalog, 'SimSun')
    expect(o.sourceFont?.cjk).toBeUndefined()
  })
})

describe('中文那一格', () => {
  it('英文是本机字体：只加 cjk', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(o.sourceFont).toMatchObject({
      family: 'Times New Roman',
      cjk: { family: 'SimSun', sha256: sha('e'), face: 0 },
    })
  })

  it('英文留在内置字体：正文脸点名批准字体，界面仍是内置族', () => {
    const o = text({ fontFamily: 'serif' })
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(o.sourceFont).toMatchObject({ family: 'Liberation Serif', sha256: sha('1') })
    expect(isBuiltinFace(catalog, o.sourceFont!)).toBe(true)
    expect(o.fontFamily).toBe('serif')
  })

  it('回到自动：内置英文 + 自动 = 整条 sourceFont 清掉，文档回到没选过的样子', () => {
    const o = text({ fontFamily: 'serif' })
    setLocalCjkFamily(o, catalog, 'SimSun')
    setLocalCjkFamily(o, catalog, null)
    expect(o.sourceFont).toBeUndefined()
  })

  it('回到自动：本机英文字体留着', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    setLocalCjkFamily(o, catalog, null)
    expect(o.sourceFont?.family).toBe('Times New Roman')
    expect(o.sourceFont?.cjk).toBeUndefined()
  })

  it('英文选回内置：中文脸保留', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(setBuiltinFamily(o, catalog, 'sans-serif')).toBe(true)
    expect(o.sourceFont).toMatchObject({ family: 'Liberation Sans', cjk: { family: 'SimSun' } })
    expect(o.fontFamily).toBe('sans-serif')
  })

  it('没有中文脸时英文选回内置不走这里（调用方清掉 sourceFont）', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    expect(setBuiltinFamily(o, catalog, 'sans-serif')).toBe(false)
  })
})

describe('粗体 / 斜体', () => {
  it('按 B 换成同族的粗体文件，中文脸不动', () => {
    const o = text()
    setLocalFamily(o, catalog, 'Times New Roman')
    setLocalCjkFamily(o, catalog, 'SimSun')
    expect(restyleLocalFace(o, catalog, true, false)).toBe(true)
    expect(o.sourceFont).toMatchObject({ sha256: sha('b'), bold: true, cjk: { family: 'SimSun' } })
    expect(o.bold).toBe(true)
  })

  it('这一族没有那张脸：不动，按钮置灰', () => {
    const o = text()
    setLocalFamily(o, catalog, 'SimSun')
    expect(styleMissing(o, catalog, true, false)).toBe(true)
    expect(restyleLocalFace(o, catalog, true, false)).toBe(true)
    expect(o.bold).toBe(false)
    expect(o.sourceFont?.sha256).toBe(sha('e'))
  })

  it('清单不认识的脸（复制来的原图字体）走 ADR 0164 的失效规则', () => {
    const o = text({ sourceFont: { family: 'DejaVu Sans', sha256: sha('f'), face: 0 } })
    expect(styleMissing(o, catalog, true, false)).toBe(false)
    expect(restyleLocalFace(o, catalog, true, false)).toBe(false)
  })
})
