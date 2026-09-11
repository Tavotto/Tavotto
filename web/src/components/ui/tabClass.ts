import { cn } from '@/lib/utils'

/** Tabs.tsx 的类名部分；单独成文件是为了让那边只导出组件（Fast refresh 的要求） */
export const TAB_UNDERLINE = 'after:absolute after:bottom-0 after:h-0.5 after:rounded-full after:bg-ink'

export const tabClass = (active: boolean) =>
  cn(
    'relative h-full text-xs outline-none transition-colors duration-fast focus-visible:focus-ring',
    active ? cn('font-medium text-ink', TAB_UNDERLINE, 'after:inset-x-0') : 'text-ink-3 hover:text-ink-2',
  )

