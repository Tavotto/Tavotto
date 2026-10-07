import { useCallback, useState, type FocusEvent, type KeyboardEvent, type MouseEvent } from 'react'

export type RowMenuMode = null | 'button' | { x: number; y: number }

export interface RowMenuState {
  /** 摊在行元素上：右键、⇧F10 / ContextMenu 键、焦点进出（决定 ⋯ 在不在 Tab 顺序里） */
  rowProps: {
    onContextMenu?: (e: MouseEvent) => void
    onKeyDown?: (e: KeyboardEvent) => void
    onFocus: (e: FocusEvent) => void
    onBlur: (e: FocusEvent) => void
  }
  /** null = 关；'button' = 从 ⋯ 垂下；{x,y} = 贴着光标（右键） */
  mode: RowMenuMode
  setMode: (m: RowMenuMode) => void
  /** 焦点此刻在这一行里（行本身或行内任何控件） */
  focusWithin: boolean
  enabled: boolean
}

/**
 * 一行的「⋯」菜单的三个入口共用一份状态（2026-10-07 设计审计 §10.3，`ui/RowMenu`）：
 *   ⋯ 按钮 · 右键（在光标处开出同一份菜单）· ⇧F10 / ContextMenu 键（从 ⋯ 垂下）。
 * 行的 onKeyDown 若自己也有处理，先调 `rowProps.onKeyDown` 再接自己的（它只认那两个键）。
 * `enabled=false`（菜单里一项都没有）时不拦右键、不拦键。
 */
export function useRowMenu({ enabled = true }: { enabled?: boolean } = {}): RowMenuState {
  const [mode, setMode] = useState<RowMenuMode>(null)
  const [focusWithin, setFocusWithin] = useState(false)

  const onContextMenu = useCallback((e: MouseEvent) => {
    // 菜单本身 portal 在别处，React 事件却照着组件树冒上来：在菜单里右键不算「在行上右键」
    if (!e.currentTarget.contains(e.target as Node)) return
    e.preventDefault()
    setMode({ x: e.clientX, y: e.clientY })
  }, [])
  const onKeyDown = useCallback((e: KeyboardEvent) => {
    if (!e.currentTarget.contains(e.target as Node)) return
    if ((e.key === 'F10' && e.shiftKey) || e.key === 'ContextMenu') {
      e.preventDefault()
      e.stopPropagation()
      setMode('button')
    }
  }, [])
  const onFocus = useCallback(() => setFocusWithin(true), [])
  const onBlur = useCallback((e: FocusEvent) => {
    const next = e.relatedTarget as Node | null
    if (next && e.currentTarget.contains(next)) return
    setFocusWithin(false)
  }, [])

  return {
    rowProps: {
      onContextMenu: enabled ? onContextMenu : undefined,
      onKeyDown: enabled ? onKeyDown : undefined,
      onFocus,
      onBlur,
    },
    mode: enabled ? mode : null,
    setMode,
    focusWithin,
    enabled,
  }
}
