import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { cn } from '@/lib/utils'

/**
 * 抽屉 / 栏的**宽度把手**（左抽屉右缘、属性栏左缘各一个，此前是两份复制的 `WidthHandle`）。
 * WAI-ARIA window splitter：`role="separator"` + `aria-valuenow / min / max`，一个 Tab 停靠点。
 *
 * - 指针：按下抓住（pointer capture，指针出了 8px 命中条甚至出了窗口都还在拖），拖动实时调宽；
 *   **Esc 回滚**到按下时的宽度（经 `registerPointerCancel`：`useKeyboard` 的 Esc 先取消进行中的指针手势，与画布拖动同一条路）；`pointercancel`（系统打断）同样回滚；松手即定。
 * - **双击复位**到默认宽度。
 * - 键盘：←/→ 按 `step`（16px）步进，朝「变宽」的那个方向由 `side` 定；Home / End 到下限 / 上限。
 * - 拖拽态：`data-dragging` 期间发丝线保持 2px accent（不靠 hover——指针早就离开了 8px 条）。
 * - 视觉：命中区 8px、整条在容器内侧（外层 `overflow-hidden` 是开合动效要用的，伸到外面会被剪掉）；
 *   看得见的只有贴边的一根发丝线：hover 1px border-strong，键盘聚焦 / 拖拽 2px accent（蓝色不做大块背景）。
 *
 * 宽度本身不在这里存：`value` 进、`onChange(px)` 出，调用方（uiStore 的 `setLeftWidth` / `setRightWidth`）
 * 负责夹值与持久化。`data-resize-handle` 是把手本身、`data-drawer-resize-line` 是发丝线的稳定钩子。
 */
export function ResizeHandle({
  name,
  registerCancel,
  side,
  value,
  min,
  max,
  defaultValue,
  onChange,
  label,
  step = 16,
}: {
  /**
   * 登记「取消这次拖动」的出口，返回注销函数（调用方传 `store/gestureCoordinator.registerPointerCancel`：
   * 原语不 import store）。拖动期间 `useKeyboard` 的 Esc 经它回滚，ContextBar 等别的 Esc 监听据此让位
   */
  /** 登记 Esc 的取消出口；别的指针手势占着时回 null——这时不开始拖动（不抢它的出口） */
  registerCancel: (cancel: () => void) => (() => void) | null
  /** 稳定钩子 `data-resize-handle`（`left-drawer` / `inspector`）：e2e 按它找，不按 role + aria-value* */
  name: string
  /** 把手贴在容器的哪一边：右缘（左抽屉，向右拖 = 变宽）或左缘（属性栏，向左拖 = 变宽） */
  side: 'left' | 'right'
  value: number
  min: number
  max: number
  /** 双击复位到它 */
  defaultValue: number
  onChange: (px: number) => void
  /** 可达名（含值域，调用方走 i18n） */
  label: string
  step?: number
}) {
  const grow = side === 'right' ? 1 : -1
  const ref = useRef<HTMLDivElement>(null)
  const drag = useRef<{ id: number; x0: number; from: number } | null>(null)
  const unregister = useRef<(() => void) | null>(null)
  const [dragging, setDragging] = useState(false)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange

  const finish = (rollback: boolean) => {
    const d = drag.current
    if (!d) return
    drag.current = null
    unregister.current?.()
    unregister.current = null
    setDragging(false)
    try {
      ref.current?.releasePointerCapture?.(d.id)
    } catch {
      // 已经释放了
    }
    if (rollback) onChangeRef.current(d.from)
  }

  // 松手 / 打断只认发起这次拖动的那支指针；别的指针的 up / cancel 不结束它
  const finishFor = (e: PointerEvent, rollback: boolean) => {
    if (drag.current && e.pointerId !== drag.current.id) return
    finish(rollback)
  }

  // 卸载时若还在拖，只清掉本地状态（宽度保持当前值）
  useEffect(
    () => () => {
      drag.current = null
      unregister.current?.()
      unregister.current = null
    },
    [],
  )

  const onPointerDown = (e: PointerEvent) => {
    if (e.button !== 0) return
    e.preventDefault()
    // 已经在拖：第二根手指 / 第二支笔不接管（否则会覆盖起点、注销第一支的 Esc 回滚出口）
    if (drag.current) return
    // Esc 的归属走仓库现成的指针手势协议：登记取消出口，`useKeyboard` 在 Esc 时先 `cancelActivePointerGesture()`
    // 并吞掉这一下——不自己在 window 上再挂一个 Esc 监听（那挡不住 ContextBar 等其它捕获监听，Codex #1102）。
    // 别的指针手势（画布拖动）正占着出口时不开始：它的 Esc 回滚不能被我们顶掉
    const release = registerCancel(() => finish(true))
    if (!release) return
    unregister.current = release
    drag.current = { id: e.pointerId, x0: e.clientX, from: value }
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId)
    } catch {
      // 合成事件没有活动指针
    }
    ;(e.currentTarget as HTMLElement).focus({ preventScroll: true })
    setDragging(true)
  }
  const onPointerMove = (e: PointerEvent) => {
    const d = drag.current
    if (!d || e.pointerId !== d.id) return
    onChange(d.from + grow * (e.clientX - d.x0))
  }
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return
    let next: number | null = null
    if (e.key === 'ArrowRight') next = value + grow * step
    else if (e.key === 'ArrowLeft') next = value - grow * step
    else if (e.key === 'Home') next = min
    else if (e.key === 'End') next = max
    if (next === null) return
    e.preventDefault()
    onChange(next)
  }

  return (
    <div
      ref={ref}
      role="separator"
      data-resize-handle={name}
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      data-dragging={dragging || undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={(e) => finishFor(e, false)}
      onPointerCancel={(e) => finishFor(e, true)}
      onLostPointerCapture={(e) => finishFor(e, false)}
      onDoubleClick={() => onChange(defaultValue)}
      onKeyDown={onKeyDown}
      className={cn(
        'group/resize absolute inset-y-0 z-canvas-chrome flex w-2 cursor-col-resize outline-none',
        side === 'right' ? 'right-0 justify-end' : 'left-0 justify-start',
      )}
    >
      <span
        aria-hidden
        data-drawer-resize-line
        className={cn(
          'h-full w-px bg-transparent transition-colors duration-fast',
          'group-hover/resize:bg-border-strong group-focus-visible/resize:w-0.5 group-focus-visible/resize:bg-accent',
          'group-data-[dragging]/resize:w-0.5 group-data-[dragging]/resize:bg-accent',
        )}
      />
    </div>
  )
}
