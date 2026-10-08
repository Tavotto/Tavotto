/**
 * 非模态浮层的 Tab 焦点陷阱（2026-10-07 设计审计 §10.1：快速编辑弹层 `role="dialog"`，Tab 出去就找不回来、Esc 也没人接）。
 * 在 keydown（Tab）里调：走到最后一个往前绕回第一个，Shift+Tab 反之；焦点不在里面时拉回第一个 / 最后一个。
 * Radix 的弹层（下拉 Select 的选项列表）在自己的 portal 里、自己管键盘：焦点在那里时不插手。
 * 模态对话框不用它——`ui/Dialog` 由 Radix 管焦点。
 */
export function trapTab(e: KeyboardEvent, box: HTMLElement | null): void {
  if (e.key !== 'Tab' || !box) return
  if ((e.target as Element | null)?.closest?.('[data-radix-popper-content-wrapper]')) return
  const items = [
    ...box.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ]
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement as HTMLElement | null
  const inside = !!active && box.contains(active)
  if (!inside || (e.shiftKey ? active === first || active === box : active === last)) {
    e.preventDefault()
    ;(e.shiftKey ? last : first).focus()
  }
}
