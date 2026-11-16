import { createContext, useContext, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/** 浮层底栏的两个落点：左 = 次要信息 / 次要按钮，右 = 主按钮（永远在右端）。 */
export const OverlayFooterContext = createContext<{ left: HTMLElement | null; right: HTMLElement | null } | null>(null)

/**
 * 各场景把自己的按钮交给浮层的底栏（portal）；不在浮层里渲染（单独挂场景的用例）时退回内联，按钮照样在。
 * 主按钮放 `right`，次要的放 `left`。
 */
export function OverlayFooter({ left, right }: { left?: ReactNode; right?: ReactNode }) {
  const c = useContext(OverlayFooterContext)
  if (!c)
    return (
      <div className="mt-4 flex items-center justify-between gap-2">
        <span className="flex items-center gap-2">{left}</span>
        <span className="flex items-center gap-2">{right}</span>
      </div>
    )
  return (
    <>
      {c.left && left ? createPortal(left, c.left) : null}
      {c.right && right ? createPortal(right, c.right) : null}
    </>
  )
}
