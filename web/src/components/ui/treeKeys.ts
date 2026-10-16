/**
 * WAI-ARIA tree 的键位（APG「Tree View」）做成一个纯函数，元素树与图层树共用：
 *
 *   ↑ ↓          上一行 / 下一行（到头停住）
 *   Home / End   第一行 / 最后一行
 *   →            已折叠 → 展开；已展开 → 进第一个子节点；叶子 → 不处理
 *   ←            已展开 → 折叠；叶子 / 已折叠 → 回父节点；根层 → 不处理
 *
 * 输入是**当前可见行**的扁平序列（每行只要层级与展开态），所以「第一个子节点」就是下一行
 * （层级更深一级）、「父节点」就是向上数第一个层级更浅的行。返回 null = 这个键不归树管，
 * 调用方别 preventDefault。Enter / Delete / F2 这些行自己的主操作不在这里。
 */
export interface TreeNavRow {
  /** 层级，从 0 或 1 起都行，只比较大小 */
  level: number
  /** undefined = 叶子；true / false = 已展开 / 已折叠 */
  expanded?: boolean
}

export type TreeKeyAction =
  | { type: 'focus'; index: number }
  | { type: 'expand' }
  | { type: 'collapse' }

export const TREE_NAV_KEYS = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'] as const

export function isTreeNavKey(key: string): boolean {
  return (TREE_NAV_KEYS as readonly string[]).includes(key)
}

export function treeKeyAction(key: string, rows: readonly TreeNavRow[], index: number): TreeKeyAction | null {
  const row = rows[index]
  if (!row || rows.length === 0) return null
  const last = rows.length - 1
  switch (key) {
    case 'ArrowDown':
      return { type: 'focus', index: Math.min(last, index + 1) }
    case 'ArrowUp':
      return { type: 'focus', index: Math.max(0, index - 1) }
    case 'Home':
      return { type: 'focus', index: 0 }
    case 'End':
      return { type: 'focus', index: last }
    case 'ArrowRight': {
      if (row.expanded === false) return { type: 'expand' }
      if (row.expanded === true) {
        const next = rows[index + 1]
        return next && next.level > row.level ? { type: 'focus', index: index + 1 } : null
      }
      return null
    }
    case 'ArrowLeft': {
      if (row.expanded === true) return { type: 'collapse' }
      for (let i = index - 1; i >= 0; i--) {
        if (rows[i].level < row.level) return { type: 'focus', index: i }
      }
      return null
    }
    default:
      return null
  }
}
