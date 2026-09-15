import { Fragment } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ClipboardList,
  Images,
  Layers,
  LayoutGrid,
  Settings,
  TriangleAlert,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { EditableFigureIcon } from '@/components/ui/semanticIcons'
import { cn } from '@/lib/utils'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { RAIL_W, useUiStore, type LeftTab } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { Tip } from '../ui/Tooltip'

/**
 * 标签名走 workspace:rail.<id>，图标与顺序留在代码里。
 *
 * 「问题」（Prompt 11）**常驻**：它在没有问题时也要在——「一个问题都没有」
 * 本身就是用户要的答案，而按需出现的入口会让人以为功能坏了。角标只在真的
 * 有问题时出现，抽屉收起时它是唯一的提示。
 */
const ITEMS: { id: LeftTab; icon: typeof Images }[] = [
  { id: 'canvases', icon: LayoutGrid },
  { id: 'assets', icon: Images },
  { id: 'layers', icon: Layers },
  { id: 'elements', icon: EditableFigureIcon },
  { id: 'problems', icon: TriangleAlert },
]

/**
 * 常驻图标轨道：三个上下文各占一格，点击打开对应抽屉，再点一次收起。
 * 选中态用浅灰底色标记（比 hover 深一档），不用品牌蓝；状态语义靠 aria-expanded。
 */
export function LeftRail() {
  const { t } = useTranslation('workspace')
  const tab = useUiStore((s) => s.leftTab)
  const open = useUiStore((s) => s.leftOpen)
  const railClick = useUiStore((s) => s.railClick)
  const problems = useValidationStore((s) => s.issues.length)
  const blocking = useValidationStore((s) => s.issues.some((i) => i.severity === 'error'))

  return (
    <nav
      aria-label={t('rail.navLabel')}
      style={{ width: RAIL_W }}
      // 轨与抽屉之间不画线：两个同色面之间的 hairline 只是第三条竖线（抽屉右缘已有一条）；
      // 抽屉关着时轨对着纸色画布，明度差已经够（左栏审计 L16）
      className="flex shrink-0 flex-col items-center gap-1 bg-surface pb-2 pt-2"
    >
      {ITEMS.map(({ id, icon: Icon }) => {
        const active = open && tab === id
        // 角标只写进无障碍名，不再单独挂一个 aria-live——轨道是导航，不是播报区
        const label = id === 'problems' && problems > 0
          ? t('rail.problemsCount', { count: problems })
          : t(`rail.${id}`)
        const button = (
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
                ? 'bg-selected text-ink'
                : 'text-ink-2 hover:bg-surface-hover hover:text-ink',
            )}
          >
            <Icon size={ICON_SIZE.md} filled={active} />
            {id === 'problems' && problems > 0 && (
              /* 折叠时唯一的提示。**不挡画布**：它就在轨道自己的格子里，
                 而且用形状（实心点）+ 数字两重表达，不只靠颜色 */
              <span
                aria-hidden
                className={cn(
                  'absolute -right-0.5 -top-0.5 flex h-3 min-w-3 items-center justify-center',
                  'rounded-full px-0.5 text-[9px] leading-none tabular-nums',
                  blocking ? 'bg-danger text-white' : 'bg-ink-3 text-white',
                )}
              >
                {problems > 99 ? '99+' : problems}
              </span>
            )}
          </button>
        )
        // 抽屉开着时不出气泡：它会落在抽屉第二行上、盖住内容，而面板头已经写着这个名字
        // （左栏审计 L15）；开合状态由 aria-expanded 说，可达名不变（e2e 按它找这颗钮）
        return active ? (
          <Fragment key={id}>{button}</Fragment>
        ) : (
          <Tip key={id} label={label} side="right">
            {button}
          </Tip>
        )
      })}

      {/* 项目级入口与上面四个上下文分组：它开的是对话框不是抽屉，所以不进
          ITEMS，也不参与「再点一次收起」那套语义。
          **`data-rail` 照给**：这两颗在循环外单独写，2026-09-07 之前漏了这个属性，
          于是三个 spec 只能退回按 `aria-label` 的中文文案找它们（#299 那一族的
          同一个赌注在三处各下了一次）。id 与 rail 文案键的末段对齐。
          （这里不写成完整的翻译调用形态：i18n 检查是按字面量扫的，注释里出现
          一个带通配的 key 会被当成真的用到了，构建当场红。） */}
      {/* 分区之间只靠留白（`mt-auto` 把这两颗推到底），不画分隔线（左栏审计 L17） */}
      <Tip label={t('rail.readiness')} side="right">
        <button
          data-rail="readiness"
          onClick={() => useProjectReadinessStore.getState().openCenter({ source: 'panel' })}
          aria-label={t('rail.readiness')}
          className={cn(
            'mt-auto flex h-8 w-8 items-center justify-center rounded-sm outline-none',
            'text-ink-2 transition-colors hover:bg-surface-hover hover:text-ink',
            'focus-visible:focus-ring',
          )}
        >
          <ClipboardList size={ICON_SIZE.md} />
        </button>
      </Tip>
      <Tip label={t('rail.settings')} side="right">
        <button
          data-rail="settings"
          onClick={() => useUiStore.getState().setSettingsOpen(true)}
          aria-label={t('rail.settings')}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-sm outline-none',
            'text-ink-2 transition-colors hover:bg-surface-hover hover:text-ink',
            'focus-visible:focus-ring',
          )}
        >
          <Settings size={ICON_SIZE.md} />
        </button>
      </Tip>
    </nav>
  )
}
