import { describe, expect, it } from 'vitest'
import type { ManifestElement } from './api'
import type { PanelObject } from '@/types/document'
import { mathFontFollow } from './figureMathFont'

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
