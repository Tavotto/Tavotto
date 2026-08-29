import { useTranslation } from 'react-i18next'
import { Braces, ClipboardList, Images, Layers, LayoutGrid, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { RAIL_W, useUiStore, type LeftTab } from '@/store/uiStore'
import { Tip } from '../ui/Tooltip'

/**
 * 标签名走 workspace:rail.<id>，图标与顺序留在代码里。
 *
 * Prompt 11 的「问题」入口会加在这里（`{ id: 'problems', icon: … }` + 一个
 * `LeftTab` 取值 + 一份抽屉内容）。**在它真的有内容之前不放一个占位按钮**
 * ——按了什么都不发生的入口比没有入口更坏，用户会以为功能坏了。
 */
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

      {/* 项目级入口与上面四个上下文分组：它开的是对话框不是抽屉，所以不进
          ITEMS，也不参与「再点一次收起」那套语义 */}
      <span className="mt-auto h-px w-6 bg-border" aria-hidden />
      <Tip label={t('rail.readiness')} side="right">
        <button
          onClick={() => useProjectReadinessStore.getState().openCenter()}
          aria-label={t('rail.readiness')}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-sm outline-none',
            'text-ink-2 transition-colors hover:bg-ink/[.05] hover:text-ink',
            'focus-visible:focus-ring',
          )}
        >
          <ClipboardList size={16} />
        </button>
      </Tip>
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
