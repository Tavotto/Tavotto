import { Images, Layers, LayoutGrid, Settings, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import { RAIL_W, useUiStore, type LeftTab } from '@/store/uiStore'
import { Tip } from '../ui/Tooltip'

const ITEMS: { id: LeftTab; icon: typeof Images; label: string }[] = [
  { id: 'canvases', icon: LayoutGrid, label: '画布' },
  { id: 'assets', icon: Images, label: '素材' },
  { id: 'layers', icon: Layers, label: '结构' },
  { id: 'elements', icon: Zap, label: '图内元素' },
]

/**
 * 常驻图标轨道：三个上下文各占一格，点击打开对应抽屉，再点一次收起。
 * 选中态用左侧 2px 竖条 + 底色双重标记，不单靠颜色。
 */
export function LeftRail() {
  const tab = useUiStore((s) => s.leftTab)
  const open = useUiStore((s) => s.leftOpen)
  const railClick = useUiStore((s) => s.railClick)

  return (
    <nav
      aria-label="工作区侧栏"
      style={{ width: RAIL_W }}
      className="flex shrink-0 flex-col items-center gap-1 border-r border-border bg-surface pb-2 pt-2"
    >
      {ITEMS.map(({ id, icon: Icon, label }) => {
        const active = open && tab === id
        return (
          <Tip key={id} label={active ? `收起${label}` : label} side="right">
            <button
              onClick={() => railClick(id)}
              aria-label={label}
              aria-expanded={active}
              className={cn(
                'relative flex h-8 w-8 items-center justify-center rounded-sm outline-none',
                'transition-colors focus-visible:focus-ring',
                active
                  ? 'bg-accent-subtle text-accent'
                  : 'text-ink-2 hover:bg-ink/[.05] hover:text-ink',
              )}
            >
              {active && (
                <span
                  aria-hidden
                  className="absolute -left-1.5 top-1.5 h-5 w-0.5 rounded-full bg-accent"
                />
              )}
              <Icon size={16} />
            </button>
          </Tip>
        )
      })}

      {/* 设置与主导航分组：贴底、上方留一条分隔线 */}
      <span className="mt-auto h-px w-6 bg-border" aria-hidden />
      <Tip label="设置" side="right">
        <button
          onClick={() => useUiStore.getState().setSettingsOpen(true)}
          aria-label="设置"
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-sm outline-none',
            'text-ink-2 transition-colors hover:bg-ink/[.05] hover:text-ink',
            'focus-visible:focus-ring',
          )}
        >
          <Settings size={16} />
        </button>
      </Tip>
    </nav>
  )
}
