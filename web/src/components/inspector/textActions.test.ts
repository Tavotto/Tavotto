import { describe, expect, it } from 'vitest'
import { figureTextCase } from './TextActions'

describe('图内文字的大小写（Codex #1489）', () => {
  it('带样式的字照样改，公式块不动', () => {
    expect(figureTextCase('$\\mathit{temperature}$ (k)', 'upper')).toBe('$\\mathit{TEMPERATURE}$ (K)')
    expect(figureTextCase('$\\alpha$ rate', 'upper')).toBe('$\\alpha$ RATE')
  })
  it('句首按整句判断：样式段里的字不是句首', () => {
    expect(figureTextCase('RATE $\\mathit{K}$', 'sentence')).toBe('Rate $\\mathit{k}$')
  })
})
