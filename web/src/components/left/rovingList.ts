import { useLayoutEffect, useRef, type FocusEvent, type KeyboardEvent } from 'react'

/**
 * 一列行的**漫游焦点**（roving tabindex，2026-10-07 设计审计 §10.3 键位契约）：整列只有一个 Tab 停靠点，
 * ↑↓ / Home / End 在行与行之间走，Enter 是行自己的主操作（行的主按钮本来就是 button）。
 *
 * 行的主按钮标 `data-roving`；这个 hook 只改它们的 `tabIndex`（React 不管这几个按钮的 tabIndex），
 * 每次渲染之后保证恰好一个是 0：上次落过焦点的那一个还在就是它，否则第一个可用的。
 * 行里的其它控件（⋯、收藏开关）照 `ui/RowMenu` 的规矩：行有焦点时才进 Tab 顺序。
 */
export function useRovingList<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const last = useRef<HTMLElement | null>(null)

  const items = () =>
    [...(ref.current?.querySelectorAll<HTMLElement>('[data-roving]') ?? [])].filter(
      (el) => !(el as HTMLButtonElement).disabled,
    )

  useLayoutEffect(() => {
    const all = [...(ref.current?.querySelectorAll<HTMLElement>('[data-roving]') ?? [])]
    const live = items()
    const keep = last.current && live.includes(last.current) ? last.current : (live[0] ?? null)
    for (const el of all) el.tabIndex = el === keep ? 0 : -1
  })

  const onFocus = (e: FocusEvent) => {
    const hit = (e.target as HTMLElement).closest<HTMLElement>('[data-roving]')
    if (!hit || !ref.current?.contains(hit)) return
    last.current = hit
    for (const el of ref.current.querySelectorAll<HTMLElement>('[data-roving]')) el.tabIndex = el === hit ? 0 : -1
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.altKey || e.metaKey || e.ctrlKey) return
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    // 输入框里的方向键是光标的
    if ((e.target as HTMLElement).closest('input, textarea, [contenteditable="true"]')) return
    const list = items()
    if (!list.length) return
    const at = list.findIndex((el) => el.contains(document.activeElement) || el.closest('li')?.contains(document.activeElement))
    const next =
      e.key === 'Home' ? 0 : e.key === 'End' ? list.length - 1 : at < 0 ? 0 : at + (e.key === 'ArrowDown' ? 1 : -1)
    if (next < 0 || next >= list.length) return
    e.preventDefault()
    list[next].focus()
  }

  return { ref, onFocus, onKeyDown }
}
