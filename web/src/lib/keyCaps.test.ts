import { describe, expect, it } from 'vitest'
import { keyCaps } from './keyCaps'

describe('keyCaps', () => {
  it('修饰键 + 一个键', () => {
    expect(keyCaps('⇧CtrlS')).toEqual([['⇧', 'Ctrl', 'S']])
    expect(keyCaps('Ctrl]')).toEqual([['Ctrl', ']']])
  })
  it('具名键整颗，不被拆成字母', () => {
    expect(keyCaps('Delete')).toEqual([['Delete']])
    expect(keyCaps('Esc')).toEqual([['Esc']])
    expect(keyCaps('Space+拖动')).toEqual([['Space', '拖动']])
  })
  it('「或」按 " / " 分组；末尾的 + 与 − 自己是键', () => {
    expect(keyCaps('Ctrl+ / Ctrl−')).toEqual([['Ctrl', '+'], ['Ctrl', '−']])
    expect(keyCaps('V / T / A')).toEqual([['V'], ['T'], ['A']])
  })
  it('连接符 + 后面还有字符时只是连接', () => {
    expect(keyCaps('Alt+方向键')).toEqual([['Alt', '方向键']])
  })
})
