import { useRef, type ButtonHTMLAttributes, type HTMLAttributes, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { useSlidingIndicator } from './slidingIndicator'
import { tabClass } from './tabClass'
import { useBoldWidthLock } from './useBoldWidthLock'

/**
 * 下划线标签页：整行只有文字与一条 2px 的近黑下划线，没有框、没有底色。
 * 选中态 = ink 色 + 下划线（两重线索，不单靠颜色）；未选中 ink-3，hover 提到 ink-2。
 * 下划线是 **tablist 上唯一的一条**，切页签时滑到新位置（`useSlidingIndicator`，2026-09-14
 * 二审 E2）——此前每个页签自己画一条，切换是旧的消失、新的出现，中间没有轨迹。
 *
 * 右栏的「属性 / 改图助手 / 画布」与画布标签栏共用同一条下划线（`TAB_UNDERLINE`）；
 * 画布标签有拖拽 / 重命名 / 关闭钮，结构不同，只借用视觉，不借用组件。
 *
 * 键盘（WAI-ARIA tabs 模式；2026-09-14 审计 S4）：tablist 只占**一个** Tab 停靠点
 * （当前页），← → 换到相邻页并当场切换（自动激活——切视图不昂贵），Home / End 到
 * 首尾。此前每个页签都是一个停靠点、方向键不动。`Tab` 传 `panelId` 就同时得到
 * `id="<panelId>-tab"` 与 `aria-controls`，内容区用 `TabPanel` 包起来即可对上。
 */
export function TabList({
  label,
  className,
  children,
  onKeyDown,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { label: string; children: ReactNode }) {
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(e)
    if (e.defaultPrevented) return
    const tabs = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)')]
    if (tabs.length === 0) return
    const cur = tabs.findIndex((t) => t === e.target || t.contains(e.target as Node))
    let next: number
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = Math.min(tabs.length - 1, cur + 1)
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = Math.max(0, cur - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = tabs.length - 1
    else return
    e.preventDefault()
    const target = tabs[next]
    target.focus()
    // 自动激活：焦点到哪一页就切到哪一页（点击与键盘走同一个 onClick）
    if (target.getAttribute('aria-selected') !== 'true') target.click()
  }
  const rootRef = useRef<HTMLDivElement>(null)
  const indicator = useSlidingIndicator(rootRef, '[role="tab"][aria-selected="true"]')
  return (
    <div
      {...rest}
      ref={rootRef}
      role="tablist"
      aria-label={label}
      onKeyDown={handleKeyDown}
      // 页签间 12：右栏最窄 320px 英文（Linux 的 DejaVu Sans）下三个页签按加粗宽度预留是 71 + 85 + 49 = 205，
      // tablist 只有 230 可用——两段 12 刚好 229；原语打磨那轮改成 16（两家参照都没量到这一格）多出的 8px 恰好撑破 7px
      className={cn('relative flex h-full items-center gap-3', className)}
    >
      {children}
      {indicator.style && (
        <span
          aria-hidden
          data-tab-indicator
          className={cn(
            'pointer-events-none absolute bottom-0 left-0 h-0.5 rounded-full bg-ink',
            indicator.animate && 'transition-[transform,width] duration-base ease-pop',
          )}
          style={indicator.style}
        />
      )}
    </div>
  )
}

export function Tab({
  active,
  panelId,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  active: boolean
  /** 对应内容区（`TabPanel`）的 id：给了它页签就带上 id 与 aria-controls */
  panelId?: string
  children: ReactNode
}) {
  const ref = useRef<HTMLButtonElement>(null)
  // 选中态 600 比 400 宽 2–3%：量一次加粗宽度当 min-width，切换时邻居不挪（画布页签共用同一个钩子）
  useBoldWidthLock(ref)
  return (
    <button
      {...rest}
      ref={ref}
      type="button"
      role="tab"
      id={panelId ? `${panelId}-tab` : rest.id}
      aria-selected={active}
      aria-controls={panelId}
      // roving tabindex：只有当前页在 Tab 顺序里，其余用方向键到达
      tabIndex={active ? 0 : -1}
      className={cn(tabClass(active), className)}
    >
      {children}
    </button>
  )
}

/** 页签对应的内容区：`role="tabpanel"`，由同名页签命名。只是语义外壳，不带样式 */
export function TabPanel({
  id,
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { id: string; children: ReactNode }) {
  return (
    <div {...rest} id={id} role="tabpanel" aria-labelledby={`${id}-tab`} className={className}>
      {children}
    </div>
  )
}
