import { useTranslation } from 'react-i18next'
import { Braces, Images, Layers, LayoutGrid, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'
import { RAIL_W, useUiStore, type LeftTab } from '@/store/uiStore'
import { Tip } from '../ui/Tooltip'

/** 标签名走 workspace:rail.<id>，图标与顺序留在代码里 */
const ITEMS: { id: LeftTab; icon: typeof Images }[] = [
  { id: 'canvases', icon: LayoutGrid },
  { id: 'assets', icon: Images },
  { id: 'layers', icon: Layers },
  { id: 'elements', icon: Braces },
]

/**
 * 常驻图标轨道：三个上下文各占一格，点击打开对应抽屉，再点一次收起。
 * 选中态用左侧 2px 竖条 + 底色双重标记，不单靠颜色。
 */
export function LeftRail() {
  const { t } = useTranslation('workspace')
  const tab = useUiStore((s) => s.leftTab)
  const open = useUiStore((s) => s.leftOpen)
  const railClick = useUiStore((s) => s.railClick)

  return (
    <nav
      aria-label={t('rail.navLabel')}
      style={{ width: RAIL_W }}
      className="flex shrink-0 flex-col items-center gap-1 border-r border-border bg-surface pb-2 pt-2"
    >
      {ITEMS.map(({ id, icon: Icon }) => {
        const active = open && tab === id
        const label = t(`rail.${id}`)
        return (
          <Tip key={id} label={active ? t('rail.collapse', { label }) : label} side="right">
            <button
              onClick={() => railClick(id)}
              // 焦点救援的落点（`lib/focusRescue.ts`）：aria-label 是本地化文案，
              // 不能当选择器用
              data-rail={id}
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
      <Tip label={t('rail.settings')} side="right">
        <button
          onClick={() => useUiStore.getState().setSettingsOpen(true)}
          aria-label={t('rail.settings')}
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
