import { describe, expect, it } from 'vitest'
import type { ManifestElement } from './api'
import type { PanelObject } from '@/types/document'
import { figureMathFamily, mathFontFollow } from './figureMathFont'

const el = (value: unknown = 'serif'): ManifestElement =>
  ({ gid: 'axes_0.title', editable: [{ prop: 'fontfamily', type: 'enum', value }] }) as unknown as ManifestElement
const panel = (overrides: { gid: string; prop: string; value: unknown }[] = []) =>
  ({ id: 'p1', overrides }) as unknown as PanelObject

describe('图内公式字体跟正文（ADR 0152 PR 5）', () => {
  it('用了样式 / 上下标且字体族没改过：写成当前正文族', () => {
    expect(mathFontFollow(panel(), el('Times New Roman'), 'Temperature $\\mathit{T}$ (K)')).toBe('Times New Roman')
    expect(mathFontFollow(panel(), el(), 'cm$^{-1}$')).toBe('serif')
  })
  it('没用样式、只有公式块、或字体族已经改过：不写', () => {
    expect(mathFontFollow(panel(), el(), 'Temperature T (K)')).toBeNull()
    expect(mathFontFollow(panel(), el(), '$\\alpha$ rate')).toBeNull()
    expect(mathFontFollow(panel([{ gid: 'axes_0.title', prop: 'fontfamily', value: 'serif' }]), el(), '$\\mathit{T}$')).toBeNull()
    expect(mathFontFollow(panel(), el(''), '$\\mathit{T}$')).toBeNull()
  })
})

describe('图内公式预览用哪个族（figureMathFamily）', () => {
  const withFace = (math_face?: string): ManifestElement => ({ ...el('Times New Roman'), math_face }) as ManifestElement
  it('manifest 的 math_face 优先于默认；都没有是 matplotlib 默认的 DejaVu Sans', () => {
    expect(figureMathFamily(panel(), withFace('STIXGeneral'), '$x$')).toBe('face:STIXGeneral')
    expect(figureMathFamily(panel(), withFace(undefined), '$x$')).toBe('face:DejaVu Sans')
  })
  it('这次编辑会让公式跟正文：预览直接用将写下的正文族，不等 manifest 刷新', () => {
    expect(figureMathFamily(panel(), withFace('DejaVu Sans'), '$\\mathit{T}$ (K)')).toBe('face:Times New Roman')
  })
  it('字体族已有 override：公式字体集随它（引擎同一条路），override 比 manifest 新', () => {
    const p = panel([{ gid: 'axes_0.title', prop: 'fontfamily', value: 'Arial' }])
    expect(figureMathFamily(p, withFace('DejaVu Sans'), '$\\mathit{T}$')).toBe('face:Arial')
  })
  it('族名带字体栈 / 引号 / 不合法字符：取第一个、不合法退默认脸（后端会拒整条请求）', () => {
    expect(figureMathFamily(panel(), withFace('"Source Serif 4", serif'), '$x$')).toBe('face:Source Serif 4')
    expect(figureMathFamily(panel(), withFace('a:italic'), '$x$')).toBe('face:DejaVu Sans')
    expect(figureMathFamily(panel(), withFace('x'.repeat(81)), '$x$')).toBe('face:DejaVu Sans')
  })
})
