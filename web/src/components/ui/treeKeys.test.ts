import { describe, expect, it } from 'vitest'
import { isTreeNavKey, treeKeyAction, type TreeNavRow } from './treeKeys'

// 0 A (展开)
//   1 B (折叠)
//   1 C (展开)
//     2 D (叶)
//   1 E (叶)
// 0 F (叶)
const ROWS: TreeNavRow[] = [
  { level: 0, expanded: true },
  { level: 1, expanded: false },
  { level: 1, expanded: true },
  { level: 2 },
  { level: 1 },
  { level: 0 },
]

describe('treeKeyAction（WAI-ARIA tree 键位）', () => {
  it('↑↓ 走一行，到头停在原行（仍算处理过）', () => {
    expect(treeKeyAction('ArrowDown', ROWS, 0)).toEqual({ type: 'focus', index: 1 })
    expect(treeKeyAction('ArrowUp', ROWS, 3)).toEqual({ type: 'focus', index: 2 })
    expect(treeKeyAction('ArrowUp', ROWS, 0)).toEqual({ type: 'focus', index: 0 })
    expect(treeKeyAction('ArrowDown', ROWS, 5)).toEqual({ type: 'focus', index: 5 })
  })

  it('Home / End 到第一行 / 最后一行', () => {
    expect(treeKeyAction('Home', ROWS, 3)).toEqual({ type: 'focus', index: 0 })
    expect(treeKeyAction('End', ROWS, 1)).toEqual({ type: 'focus', index: 5 })
  })

  it('→：已折叠展开；已展开进第一个子节点；叶子不处理', () => {
    expect(treeKeyAction('ArrowRight', ROWS, 1)).toEqual({ type: 'expand' })
    expect(treeKeyAction('ArrowRight', ROWS, 0)).toEqual({ type: 'focus', index: 1 })
    expect(treeKeyAction('ArrowRight', ROWS, 2)).toEqual({ type: 'focus', index: 3 })
    expect(treeKeyAction('ArrowRight', ROWS, 3)).toBeNull()
  })

  it('→：已展开却没有可见子行（过滤后）不处理', () => {
    expect(treeKeyAction('ArrowRight', [{ level: 0, expanded: true }, { level: 0 }], 0)).toBeNull()
    expect(treeKeyAction('ArrowRight', [{ level: 0, expanded: true }], 0)).toBeNull()
  })

  it('←：已展开折叠；叶子 / 已折叠回父节点；根层不处理', () => {
    expect(treeKeyAction('ArrowLeft', ROWS, 2)).toEqual({ type: 'collapse' })
    expect(treeKeyAction('ArrowLeft', ROWS, 3)).toEqual({ type: 'focus', index: 2 })
    expect(treeKeyAction('ArrowLeft', ROWS, 1)).toEqual({ type: 'focus', index: 0 })
    expect(treeKeyAction('ArrowLeft', ROWS, 4)).toEqual({ type: 'focus', index: 0 })
    expect(treeKeyAction('ArrowLeft', ROWS, 5)).toBeNull()
    expect(treeKeyAction('ArrowLeft', ROWS, 0)).toEqual({ type: 'collapse' }) // 根已展开：先收起
    expect(treeKeyAction('ArrowLeft', [{ level: 0, expanded: false }], 0)).toBeNull() // 根已收起：无处可去
  })

  it('父节点是向上数第一个层级更浅的行（跳过同级与更深的兄弟子树）', () => {
    expect(treeKeyAction('ArrowLeft', ROWS, 4)).toEqual({ type: 'focus', index: 0 })
  })

  it('不是树键位 / 越界行 / 空树：null', () => {
    expect(treeKeyAction('Enter', ROWS, 0)).toBeNull()
    expect(treeKeyAction('ArrowDown', ROWS, 99)).toBeNull()
    expect(treeKeyAction('ArrowDown', [], 0)).toBeNull()
    expect(isTreeNavKey('Home')).toBe(true)
    expect(isTreeNavKey('Enter')).toBe(false)
  })
})
