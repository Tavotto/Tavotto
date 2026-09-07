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

/**
 * 两侧判据必须是**同一把尺子**：谁的语言内建函数都不许先碰那个串。
 *
 * `String.trim()` 与 Python 的 `str.strip()` 认的空白字符集不一样——
 * `\u001c`–`\u001f` 只有 Python 认，`\ufeff` 只有 JS 认。所以
 * `checkProjectName` 不 trim，后端 `app._unsafe_new_project_part()` 也拿**没被
 * `strip()` 动过**的末位分量去判（`tests/test_projects.py` 那条同名用例是这
 * 一条的另一半）。两边都把判断交给 `checkFilename` 自带的那份写死的空白集合。
 */
describe('两侧同一把尺子：不靠各自语言的 trim / strip', () => {
  it.each([
    ['figs\u001c', 'control_char'], // 只有 Python 的 strip 认这一档
    ['figs\ufeff', 'whitespace_edge'], // 只有 JS 的 trim 认这一档
  ])('%j → %s', (name, reason) => {
    expect(checkProjectName(name)).toBe(reason)
  })

  it('两个字符各自的内建函数确实只认一半（前提成立才谈得上口径）', () => {
    expect('figs\u001c'.trim()).toBe('figs\u001c') // JS 的 trim 认不出它
    expect('figs\ufeff'.trim()).toBe('figs') // JS 的 trim 会吃掉它
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
