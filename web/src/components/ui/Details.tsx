import type { DetailsHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ICON_SIZE } from './Icon'

/**
 * 原生 `<details>` 的统一外观：折叠箭头用 lucide 的 ChevronRight（xs 档，
 * 展开时转 90°），而不是浏览器自带的那个实心三角——它每个浏览器长得都不一样，
 * 也和树、检查器里的折叠箭头对不上。
 *
 * 只是给 `<details>` / `<summary>` 加类名，语义、键盘行为、`open` / `onToggle`
 * 全是原生的；要点击区更大 / 更小自己在 className 里改。
 */
export function Details({ className, ...props }: DetailsHTMLAttributes<HTMLDetailsElement>) {
  return <details {...props} className={cn('group/details', className)} />
}

export function Summary({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLElement> & { children: ReactNode }) {
  return (
    <summary
      {...props}
      className={cn(
        'flex cursor-pointer select-none list-none items-center gap-1 outline-none focus-visible:focus-ring',
        '[&::-webkit-details-marker]:hidden',
        className,
      )}
    >
      <ChevronRight
        size={ICON_SIZE.xs}
        aria-hidden
        className="transition-transform group-open/details:rotate-90"
      />
      {children}
    </summary>
  )
}
