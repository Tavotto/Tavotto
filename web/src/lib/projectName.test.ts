/**
 * 项目名 = 一个平台安全的路径分量（评审 #299-2）。
 *
 * 这批用例先钉住**危险的那几个**：它们是这条判据存在的理由——`..` /
 * `../other` / `nested/name` 之前会被原样拼进 `parent/name` 发给后端，
 * `mkdir(parents=True, exist_ok=True)` 把它们解析掉之后，项目就落在了
 * 对话框上写着的目录之外。
 */
import { describe, expect, it } from 'vitest'

import { checkProjectName } from './projectName'

describe('拒绝：不是一个路径分量', () => {
  it.each([
    ['..', 'dot_only'],
    ['.', 'dot_only'],
    ['../other', 'illegal_char'],
    ['..\\other', 'illegal_char'],
    ['nested/name', 'illegal_char'],
    ['nested\\name', 'illegal_char'],
    ['/abs', 'illegal_char'],
    ['C:\\Users', 'illegal_char'],
  ])('%s → %s', (name, reason) => {
    expect(checkProjectName(name)).toBe(reason)
  })
})

describe('拒绝：空 / 纯空白 / 首尾空白', () => {
  it.each([
    ['', 'empty'],
    ['   ', 'whitespace_edge'],
    ['\t', 'whitespace_edge'],
    ['figs ', 'whitespace_edge'],
    [' figs', 'whitespace_edge'],
  ])('%j → %s', (name, reason) => {
    expect(checkProjectName(name)).toBe(reason)
  })
})

describe('拒绝：Windows 上建不出来的名字', () => {
  it.each([
    ['CON', 'reserved_name'],
    ['con', 'reserved_name'],
    ['NUL', 'reserved_name'],
    ['PRN', 'reserved_name'],
    ['AUX', 'reserved_name'],
    ['COM1', 'reserved_name'],
    ['COM9', 'reserved_name'],
    ['LPT1', 'reserved_name'],
    ['LPT9', 'reserved_name'],
    ['CON.figs', 'reserved_name'],
    ['figs.', 'trailing_dot'],
    ['fi:gs', 'illegal_char'],
    ['fi*gs', 'illegal_char'],
    ['fi?gs', 'illegal_char'],
    ['fi"gs', 'illegal_char'],
    ['fi<gs', 'illegal_char'],
    ['fi>gs', 'illegal_char'],
    ['fi|gs', 'illegal_char'],
    ['fi\u0001gs', 'control_char'],
    ['fi\u007fgs', 'control_char'],
  ])('%j → %s', (name, reason) => {
    expect(checkProjectName(name)).toBe(reason)
  })

  it('太长的名字也拒（Windows 路径长度）', () => {
    expect(checkProjectName('x'.repeat(121))).toBe('too_long')
  })
})

describe('接受：正常的中英文名字', () => {
  it.each([
    'my_paper_figures',
    'figs',
    'Figures 2026',
    'fig-1.v2',
    '论文插图',
    '第二章 图',
    'COM10', // 只有 COM1–COM9 是保留名
    '.hidden',
  ])('%j', (name) => {
    expect(checkProjectName(name)).toBeNull()
  })
})
