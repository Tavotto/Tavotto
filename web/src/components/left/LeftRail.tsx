import { Fragment } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ClipboardList,
  Folder,
  Images,
  Layers,
  LayoutGrid,
  ListChecks,
  Paintbrush,
  Settings,
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
 * 本身就是用户要的答案，而按需出现的入口会让人以为功能坏了。图标上的小点
 * 只在有**阻断项**时出现（2026-09-28 起不再挂红底数字；2026-10-07 起点是 danger 色——
 * 它只为阻断亮，颜色就是它唯一要说的话）；问题数在可达名与气泡里（「3 项阻断（共 20）」）。
 * 图标是 `ListChecks`（检查清单），不再与「警告」同形（审计 §9.4）。
 */
const ITEMS: { id: LeftTab; icon: typeof Images }[] = [
  // 工作区（项目一级）排最上：范围从外到内——项目 → 画布 → 素材 → 图层 → 图内
  { id: 'workspace', icon: Folder },
  { id: 'canvases', icon: LayoutGrid },
  { id: 'assets', icon: Images },
  { id: 'layers', icon: Layers },
  { id: 'elements', icon: EditableFigureIcon },
  { id: 'style', icon: Paintbrush },
  { id: 'problems', icon: ListChecks },
]

/**
 * 轨钮（2026-09-30 重设计，参照 OpenBitFun）：图标下面写一个短名（`rail.short.*`，两个字 / 一个
 * 英文词），小白不用悬停去猜图标；选中 = 白底 + 1px 轮廓 + 实心图标，落在灰色桌面上（2026-10-07
 * 审计 §4.1：此前是卡片投影，4% 的投影在桌面上等于没有；卡片投影只属于 `ui/Card`）。
 * 可达名仍是完整名（`rail.<id>`），短名 aria-hidden，读屏不会念两遍。
 */
const RAIL_BUTTON =
  'relative flex w-14 flex-col items-center gap-1 rounded-md pb-1.5 pt-2 outline-none transition-colors focus-visible:focus-ring'
const RAIL_IDLE = 'text-ink-2 hover:bg-surface-hover hover:text-ink'
/** 选中：白底 + 1px border 轮廓（outline，几何不变）——不靠投影 */
const RAIL_ACTIVE = 'bg-surface text-ink outline-1 -outline-offset-1 outline-border'

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
  const blocking = useValidationStore((s) => s.issues.reduce((n, i) => n + (i.severity === 'error' ? 1 : 0), 0))

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
        const label =
          id !== 'problems' || problems === 0
            ? t(`rail.${id}`)
            : blocking > 0
              ? t('rail.problemsBlocking', { count: blocking, total: problems })
              : t('rail.problemsCount', { count: problems })
        const button = (
          <button
            onClick={() => railClick(id)}
            // 焦点救援的落点（`lib/focusRescue.ts`）：aria-label 是本地化文案，
            // 不能当选择器用
            data-rail={id}
            aria-label={label}
            aria-expanded={active}
            className={cn(RAIL_BUTTON, active ? RAIL_ACTIVE : RAIL_IDLE)}
          >
            <Icon size={ICON_SIZE.md} filled={active} />
            <span aria-hidden className="max-w-full truncate px-0.5 text-xs leading-none">
              {t(`rail.short.${id}`)}
            </span>
            {id === 'problems' && blocking > 0 && (
              /* 折叠时唯一的提示，而且**只为阻断项亮**（2026-09-28 用户反馈：红底数字
                 角标一直在余光里报警，数字随每次编辑跳，却不说该做什么）。一颗 6px 的点，
                 **贴在图标上**、不贴在钮角上（审计 §9.4）；颜色是 danger 锚点（非文字 ≥3:1）——
                 它只在有阻断时出现，红就是它唯一要说的事（此前的中性墨点约 2.5:1，几乎看不见）。
                 警告与建议不打扰。数字在可达名与悬停提示里（`rail.problemsBlocking`）。
                 环的颜色跟着钮底走：选中是白底，未选中是桌面 */
              <span
                aria-hidden
                data-rail-blocking
                className={cn(
                  'absolute left-1/2 top-1.5 ml-1 h-1.5 w-1.5 rounded-full bg-danger ring-2',
                  active ? 'ring-surface' : 'ring-bg',
                )}
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
