import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'

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
export function TreeBranch({ open, children }: { open: boolean; children: ReactNode }) {
  const last = useRef(children)
  if (open) last.current = children
  const { mounted, state } = usePresence(open, DURATION.exit)
  const [animate, setAnimate] = useState(false)
  const first = useRef(true)
  useLayoutEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    setAnimate(true)
  }, [open])
  if (!mounted) return null
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
        if (e.target === e.currentTarget && open) setAnimate(false)
      }}
    >
      <div className={cn('min-h-0', animate && 'overflow-hidden')}>
        <ul role="group">{open ? children : last.current}</ul>
      </div>
    </li>
  )
}
