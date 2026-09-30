import { Fragment } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ClipboardList,
  Folder,
  Images,
  Layers,
  LayoutGrid,
  Paintbrush,
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
 * 「样式」（2026-09-24）排在「问题」前面：先看这张图长什么样，再看哪里不合规——
 * 两者互相跳转（样式里不合规的那一格直达问题清单里的那一条）。
 *
 * 「问题」（Prompt 11）**常驻**：它在没有问题时也要在——「一个问题都没有」
 * 本身就是用户要的答案，而按需出现的入口会让人以为功能坏了。图标上的中性小点
 * 只在有**阻断项**时出现（2026-09-28 起不再挂红底数字）；问题数在可达名里。
 */
const ITEMS: { id: LeftTab; icon: typeof Images }[] = [
  // 工作区（项目一级）排最上：范围从外到内——项目 → 画布 → 素材 → 图层 → 图内
  { id: 'workspace', icon: Folder },
  { id: 'canvases', icon: LayoutGrid },
  { id: 'assets', icon: Images },
  { id: 'layers', icon: Layers },
  { id: 'elements', icon: EditableFigureIcon },
  { id: 'style', icon: Paintbrush },
  { id: 'problems', icon: TriangleAlert },
]

/**
 * 轨钮（2026-09-30 重设计，参照 OpenBitFun）：图标下面写一个短名（`rail.short.*`，两个字 / 一个
 * 英文词），小白不用悬停去猜图标；选中 = 白底 + 卡片投影 + 实心图标，落在灰色桌面上。
 * 可达名仍是完整名（`rail.<id>`），短名 aria-hidden，读屏不会念两遍。
 */
const RAIL_BUTTON =
  'relative flex w-14 flex-col items-center gap-1 rounded-md pb-1.5 pt-2 outline-none transition-colors focus-visible:focus-ring'
const RAIL_IDLE = 'text-ink-2 hover:bg-surface-hover hover:text-ink'

/**
 * 常驻图标轨道：每个上下文各占一格，点击打开对应抽屉，再点一次收起。
 * 选中态用浅灰底色标记（比 hover 深一档），不用品牌蓝；状态语义靠 aria-expanded。
 *
 * 钮 28×28、图标仍 16（2026-09-15 全面打磨拍板）：轨钮原先是全产品唯一的 32px
 * 控件，与顶栏 / 面板头的图标钮差一档；现在同档，选中底更贴着图标。轨宽仍 44。
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
      className="flex shrink-0 flex-col items-center gap-1 pb-2 pt-1"
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
            className={cn(RAIL_BUTTON, active ? 'bg-surface text-ink shadow-card' : RAIL_IDLE)}
          >
            <Icon size={ICON_SIZE.md} filled={active} />
            <span aria-hidden className="max-w-full truncate px-0.5 text-xs leading-none">
              {t(`rail.short.${id}`)}
            </span>
            {id === 'problems' && blocking && (
              /* 折叠时唯一的提示，而且**只为阻断项亮**（2026-09-28 用户反馈：红底数字
                 角标一直在余光里报警，数字随每次编辑跳，却不说该做什么）。现在是一颗
                 6px 的中性墨点：警告与建议不打扰，有会拦住导出的问题时才出现。
                 问题数没有丢——在可达名与悬停提示里（`rail.problemsCount`）。
                 点在 28px 钮自己的格子里，不挡画布；`ring-surface` 把它从图标上切开 */
              <span
                aria-hidden
                data-rail-blocking
                className="absolute right-3 top-1 h-1.5 w-1.5 rounded-full bg-ink-2 ring-2 ring-bg"
              />
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
          className={cn(RAIL_BUTTON, RAIL_IDLE, 'mt-auto')}
        >
          <ClipboardList size={ICON_SIZE.md} />
          <span aria-hidden className="max-w-full truncate px-0.5 text-xs leading-none">
            {t('rail.short.readiness')}
          </span>
        </button>
      </Tip>
      <Tip label={t('rail.settings')} side="right">
        <button
          data-rail="settings"
          onClick={() => useUiStore.getState().setSettingsOpen(true)}
          aria-label={t('rail.settings')}
          className={cn(RAIL_BUTTON, RAIL_IDLE)}
        >
          <Settings size={ICON_SIZE.md} />
          <span aria-hidden className="max-w-full truncate px-0.5 text-xs leading-none">
            {t('rail.short.settings')}
          </span>
        </button>
      </Tip>
    </nav>
  )
}
