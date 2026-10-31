import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'

/**
 * 按 `data-*` 钩子找一行，**只认可见的**：收起中的子树还保活着（退场动画，整棵 inert + aria-hidden），
 * 那里面的行不算存在。元素树 / 图层树的「选中项滚入视野」共用这一个出口——查到保活着的旧行就
 * `scrollIntoView`，会把长树滚走，而该停在折叠后的组头上。
 */
export function findVisibleRow(root: ParentNode, attr: string, key: string): Element | null {
  const el = root.querySelector(`[${attr}="${CSS.escape(key)}"]`)
  return el && !el.closest('[inert], [aria-hidden="true"]') ? el : null
}

/**
 * 树里一个节点的子树容器：展开 / 收起时高度走 `grid-template-rows` 0fr ↔ 1fr + 淡入
 * （与 `ui/Field.Reveal` 同一对 keyframes：`animate-reveal-in` / `-out`，时长是 motion token，
 * 退场短一档），下面的行跟着长高 / 收拢，而不是瞬间跳出（2026-10-16 UI 2.0 第二轮 04）。
 *
 * 树是**扁平行**渲染的（键位、roving、aria-level / posinset 都按扁平序列算），这里只是在渲染时
 * 把「紧跟在父行后面、更深一级的那一段行」收进一个容器：
 *   `ul[role=tree] › li[role=none] › div › ul[role=group] › li[role=treeitem]…`
 * 行本身（`data-el` / `data-layer`、tabIndex、aria-*）一个字没变。
 *
 * - 动画只在 `open` **翻转**时播：首次挂载（整棵树出现、换图、搜索过滤出新节点）直接就位，不播。
 * - 收起：`usePresence` 保活一个退场时长，期间容器里放的是**上一次展开时的行**（缓存），
 *   且 `inert` + `aria-hidden`：不可聚焦、不被读屏、不被点中。reduced-motion 直接卸载。
 * - 裁切（overflow-hidden）只在动画期间生效，播完撤掉，免得贴边的内凹焦点环被切。
 * - `prefers-reduced-motion` 由 index.css 的全局兜底把动画压到 0.01ms。
 */
export function TreeBranch({
  open,
  children,
  onSettled,
}: {
  open: boolean
  children: ReactNode
  /** 展开 / 收起的过渡走完了（展开：动画结束；收起：保活期过去、子树卸载；reduced-motion 下立即）。
   *  图层树用它让 `useFlip` 重新量基准（过渡期间别的提交只记了半路位置） */
  onSettled?: () => void
}) {
  const last = useRef(children)
  if (open) last.current = children
  const { mounted, state } = usePresence(open, DURATION.exit)
  const [animate, setAnimate] = useState(false)
  const first = useRef(true)
  const settled = useRef(onSettled)
  settled.current = onSettled
  // 收起：保活期过去、子树真的卸载那一刻 = 过渡结束
  const wasMounted = useRef(mounted)
  useEffect(() => {
    if (wasMounted.current && !mounted && !open) settled.current?.()
    wasMounted.current = mounted
  }, [mounted, open])
  useLayoutEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    setAnimate(true)
  }, [open])
  // 展开的那一刻同一次提交里就把行画出来（`usePresence` 的 mounted 要晚一个 effect）：
  // 选中项滚入视野的 effect 紧跟着这次提交跑，行得已经在 DOM 里
  if (!open && !mounted) return null
  return (
    <li
      role="none"
      data-tree-branch={state}
      inert={!open || undefined}
      aria-hidden={!open || undefined}
      className={cn(
        'grid grid-cols-[minmax(0,1fr)]',
        animate && 'data-[tree-branch=open]:animate-reveal-in data-[tree-branch=closed]:animate-reveal-out',
      )}
      onAnimationEnd={(e) => {
        if (e.target === e.currentTarget && open) {
          setAnimate(false)
          settled.current?.()
        }
      }}
    >
      <div className={cn('min-h-0', animate && 'overflow-hidden')}>
        <ul role="group">{open ? children : last.current}</ul>
      </div>
    </li>
  )
}
