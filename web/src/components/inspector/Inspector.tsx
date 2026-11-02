import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { useMemo, useState } from 'react'
import { ChevronRight, Pin, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { drawerMotion, type PresenceState } from '@/lib/motion'
import { t as translate } from '@/i18n'
import { formatMm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { ResizeHandle } from '@/components/ui/ResizeHandle'
import { tryRegisterPointerCancel } from '@/store/gestureCoordinator'
import { useDocumentStore } from '@/store/documentStore'
import { RIGHT_DEFAULT, RIGHT_MAX, RIGHT_MIN, isRightPinned, useUiStore, type RightTab } from '@/store/uiStore'
import type { ArrowObject, PanelObject, ShapeObject, TextObject } from '@/types/document'
import { useAiStore } from '@/store/aiStore'
import { TimelinePanel } from '../VersionDialog'
import { assistantTabLabel, AssistantPanel } from '../ai/AiPanel'
import { IconButton } from '../ui/Button'
import { ColorFieldContext } from '../ui/colorPalette'
import { Tab, TabList, TabPanel } from '../ui/Tabs'
import { Tip } from '../ui/Tooltip'
import { ArrangeSection } from './ArrangeSection'
import { CanvasPage } from './CanvasPage'
import { useDocumentColors } from './documentColors'
import { PanelElementPage } from './GroupPage'
import { IdentityHeader } from './IdentityHeader'
import { INSPECTOR_CONTAINER } from './layout'
import { PageBoundsHint } from './PageBoundsHint'
import { PanelSection } from './PanelSection'
import { ArrowSection, ShapeSection } from './StrokeSection'
import { TextSection } from './TextSection'
import { TransformSection } from './TransformSection'
import { useSelectedObjects } from './common'

/**
 * 右栏三个模式在同一个 tablist 里：属性（当前选中对象）· 画布（当前文档）· 改图助手。
 * 顺序按「对象 → 文档 → 独立工作流」由近及远排（2026-10-07 设计审计 §9.2 拍板②，ADR 0010 §3 当日修订）：
 * 此前助手夹在两个上下文页签中间。
 * ADR 0010 §3 曾把助手移出 tab 行做成头部的独立按钮（2026-09-14 修订）：那一版助手打开时
 * tablist 里**没有任何选中项**、方向键也到不了助手——三个互斥视图却用了两种控件。
 * ADR 0010 要的两件事都还在：选中对象一律切回属性页（`autoShowProperties`）、助手会话状态
 * 在 aiStore 里切走不丢；运行状态点留在助手页签上。
 */
const TABS: RightTab[] = ['properties', 'canvas', 'timeline', 'assistant']
/** 内容区 id（`TabPanel`）：`<id>-tab` 是对应页签的 id */
const PANEL_ID: Record<RightTab, string> = {
  properties: 'inspector-panel-properties',
  assistant: 'inspector-panel-assistant',
  canvas: 'inspector-panel-canvas',
  timeline: 'inspector-panel-timeline',
}

const tabLabel = (id: RightTab): string =>
  id === 'assistant' ? assistantTabLabel() : translate(`tab.${id}`, { ns: 'inspector' })

export function Inspector({
  overlay = false,
  state = 'open',
}: {
  overlay?: boolean
  /** 开合动效由 App 的 usePresence 驱动：收起时先播完退场再卸载 */
  state?: PresenceState
}) {
  perfCount('render.Inspector')
  const { t } = useTranslation('inspector')
  const tab = useUiStore((s) => s.rightTab)
  const setTab = useUiStore((s) => s.setRightTab)
  const width = useUiStore((s) => s.rightWidth)
  const pinned = useUiStore(isRightPinned)
  const layout = useUiStore((s) => s.layout)
  const runningAi = useAiStore((s) => s.sessions.some((x) => x.status === 'running'))

  const motion = drawerMotion({ state, overlay, width, side: 'right' })
  // 属性栏里的 ColorField 带可编辑 hex 与取色面板（文档颜色 / 最近 / 系统取色器）；浮动栏不挂这层
  const documentColors = useDocumentColors()
  const colorHost = useMemo(() => ({ rich: true, documentColors }), [documentColors])

  return (
    <aside
      {...motion}
      /* `data-inspector-panel` 是右侧检查器栏的稳定锚点。裸 `aside` 指代不了它
         ——左抽屉（`data-left-drawer`）、版本面板、快捷任务卡也都是 `aside`
         （issue #307）。**一个对象只留一个锚点名**：#299 与 #307 曾各给这个
         元素加过一个（`data-inspector-panel` / `data-inspector`），先到的那个
         赢；两个都留下的话，下一个人不知道该用哪个，而两个都不会被维护。 */
      data-inspector-panel
      aria-label={t('panelLabel')}
      className={cn(
        // overflow-hidden 是动效的一部分，见 drawerMotion 的注释
        'relative shrink-0 overflow-hidden border-l border-border bg-surface',
        overlay && 'absolute inset-y-0 right-0 z-drawer shadow-pop',
        motion.className,
      )}
    >
      <ColorFieldContext.Provider value={colorHost}>
      {/* 行网格的标签列宽 `--insp-label` 按这一层的宽度算（`@container` + cqi，layout.ts） */}
      <div className={cn('flex h-full flex-col', INSPECTOR_CONTAINER)} style={{ width }}>
      {/* 页签条（2026-09-30 重设计，学 OpenBitFun 的面板头）：与左边画布标签行同高 44、底边同一条
          hairline，两条线在工作面板里连成一条 */}
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3 @max-[379px]:gap-1 @max-[379px]:px-2">
        {/* 四个页签是一条等宽分段控件（2026-10-22 用户裁决：原文字页签间距小、易点错）：四格 minmax(0,1fr)，
            英文（Linux 的 DejaVu Sans 更宽）放不下的字在格内省略号截断，绝不撑开槽；整格可点，title 给全名。
            图钉另在 < 350px 让位 */}
        <TabList variant="segmented" label={t('tabsLabel')} className="min-w-0 flex-1">
          {TABS.map((id) => (
            <Tab
              key={id}
              variant="segmented"
              data-inspector-tab={id}
              panelId={PANEL_ID[id]}
              active={tab === id}
              onClick={() => setTab(id)}
              // 助手页签：可达名带上「有任务在运行」，视觉上是页签右上角的一颗点
              aria-label={
                id === 'assistant' && runningAi ? `${assistantTabLabel()} · ${t('aiRunning')}` : undefined
              }
              title={id === 'assistant' && runningAi ? t('assistantRunningTip') : tabLabel(id)}
            >
              {tabLabel(id)}
              {id === 'assistant' && runningAi && (
                <span
                  aria-hidden
                  className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-ink"
                />
              )}
            </Tab>
          ))}
        </TabList>
        {/* 右侧图标钮簇：与下方属性头部一样收成一个 ml-auto 的簇（簇内 4px），不再用 flex-1 撑开 +
            三段 12px 间距——320px 英文（Linux 的 DejaVu Sans）下页签行本来只剩 1px 余量，页签选中态改
            600 并按加粗宽度预留之后（打磨批次 A）恰好撑破 4px（e2e/inspector-overflow 量到 214 > 210）。
            贴右缘的 -mr-1.5 放在簇上而不是关闭钮上：负外边距落在簇内会让簇自己 scrollWidth 多 6px，
            那把尺子照样红（同上面 header 注释里说的那 4px）。 */}
        <span className="-mr-1.5 ml-auto flex shrink-0 items-center gap-1">
          {layout !== 'narrow' ? (
            /* 只留图钉，不写「常驻 / 自动收起」：即便右栏最窄 320px，两个标签页 +
               助手入口 + 带词的开关 + 关闭按钮在英文下也排不下（e2e/i18n.spec.ts
               量横向溢出）。状态靠**图形**说，不靠底色（打磨 S3，用户拍板）：默认就是钉住的，
               `active` 的 ink 10% 灰块会常驻在每一页右上角——整栏唯一一块常亮的底。现在
               钉住 = 实心图钉 + ink，未钉 = 线框图钉 + ink-3（图标集的实心孪生，ADR 0052）；
               aria-pressed 与气泡文案不变。 */
            <IconButton
              label={t(pinned ? 'pinnedAria' : 'autoHideAria')}
              side="bottom"
              iconSize="sm"
              aria-pressed={pinned}
              // 四个页签（含「时间线」）在英文（Linux 字体 281px）下，压缩间距后簇里带图钉要 ≥ 350px 才排得下：
              // 容器 < 350px 时图钉让位给页签行（钉住是偏好，不丢；栏拉宽到 350 就回来）
              className="@max-[349px]:hidden"
              onClick={() => useUiStore.getState().setRightPinned(!pinned)}
            >
              <Pin size={ICON_SIZE.sm} filled={pinned} className={pinned ? 'text-ink' : 'text-ink-3'} />
            </IconButton>
          ) : (
            <Tip label={t('overlayTip')} side="bottom">
              <span className="text-xs text-ink-3">{t('overlay')}</span>
            </Tip>
          )}
          <IconButton
            label={t('closePanel')}
            tip={translate('actions.close')}
            side="bottom"
            iconSize="sm"
            className="text-ink-2 hover:text-ink"
            // 稳定定位（e2e）：不认 aria-label 文案
            data-inspector-close
            onClick={() => useUiStore.getState().toggleRight()}
          >
            <X size={ICON_SIZE.sm} />
          </IconButton>
        </span>
      </div>

      <PageBoundsHint />
      {tab === 'assistant' ? (
        <TabPanel id={PANEL_ID.assistant} className="flex min-h-0 flex-1 flex-col">
          <AssistantPanel />
        </TabPanel>
      ) : tab === 'timeline' ? (
        // 排版时间线（ADR 0101 2026-10-21 修订）：右栏的一个页签，不再是盖在画布上的浮卡
        <TabPanel id={PANEL_ID.timeline} className="flex min-h-0 flex-1 flex-col">
          <TimelinePanel />
        </TabPanel>
      ) : tab === 'canvas' ? (
        <TabPanel id={PANEL_ID.canvas} className="min-h-0 flex-1 overflow-y-auto">
          <CanvasPage />
        </TabPanel>
      ) : (
        <TabPanel id={PANEL_ID.properties} className="flex min-h-0 flex-1 flex-col">
          <PropertiesPage />
        </TabPanel>
      )}
      </div>
      </ColorFieldContext.Provider>
      <WidthHandle />
    </aside>
  )
}

function WidthHandle() {
  const { t } = useTranslation('inspector')
  const width = useUiStore((s) => s.rightWidth)
  return (
    <ResizeHandle
      name="inspector"
      registerCancel={tryRegisterPointerCancel}
      side="left"
      value={width}
      min={RIGHT_MIN}
      max={RIGHT_MAX}
      defaultValue={RIGHT_DEFAULT}
      onChange={(px) => useUiStore.getState().setRightWidth(px)}
      label={t('resize', { min: RIGHT_MIN, max: RIGHT_MAX })}
    />
  )
}

/* -------------------------------------------------------------------------- */
/*  属性页                                                                     */
/* -------------------------------------------------------------------------- */

function PropertiesPage() {
  const objs = useSelectedObjects()
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const elementPanel = useDocumentStore((s) =>
    s.doc.objects.find((o) => o.id === elementPanelId && o.type === 'panel'),
  ) as PanelObject | undefined

  const panels = objs.filter((o): o is PanelObject => o.type === 'panel')
  const texts = objs.filter((o): o is TextObject => o.type === 'text')
  const arrows = objs.filter((o): o is ArrowObject => o.type === 'arrow')
  const shapes = objs.filter((o): o is ShapeObject => o.type === 'shape')
  const onlyType = (n: number) => n > 0 && objs.length === n
  // 面板选区的位置与尺寸由 PanelSection 自己出（含宽高比锁），
  // 这里再来一份 TransformSection 就重复了
  const panelsOnly = onlyType(panels.length)
  /**
   * 文字选区把「内容 + 排版」提到最前，位置与尺寸退成一行折叠摘要（审计 T27）。
   * 改一段标注最常做的是改字、改字号、改对齐；旧顺序让这三件事排在一整段
   * 变换之后，每次都得往下找。折叠不减能力，展开还是同一批字段。
   */
  const textsOnly = onlyType(texts.length)
  const arrowsOnly = onlyType(arrows.length)

  if (elementPanel) {
    return (
      <>
        <IdentityHeader panel={elementPanel} />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <PanelElementPage panel={elementPanel} />
        </div>
      </>
    )
  }

  if (objs.length === 0) {
    // 钉住时面板留着；未钉住时选择清空后面板本来就收起了。空着的属性页说这份文档本身
    // （2026-10-07 设计审计 §9.3：文档摘要卡），而不是一句「没有选中对象」
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <DocumentSummary />
      </div>
    )
  }

  return (
    <>
      <IdentityHeader objs={objs} />
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {/* 第一层：位置与尺寸等高频属性（文字除外，见下） */}
        {!panelsOnly && !textsOnly && !arrowsOnly && <TransformSection objs={objs} />}
        {/* 第二层：类型专属 */}
        {onlyType(panels.length) && <PanelSection objs={panels} />}
        {textsOnly && <TextSection objs={texts} />}
        {textsOnly && <TransformSection objs={objs} foldKey="text-transform" />}
        {onlyType(arrows.length) && <ArrowSection objs={arrows} />}
        {arrowsOnly && <TransformSection objs={objs} foldKey="arrow-transform" />}
        {onlyType(shapes.length) && <ShapeSection objs={shapes} />}
        {/* 第三层：排列（对齐 + 层级）。单选面板的排列由 PanelSection 自己摆在
            图片适配之后、更多之前（变换 → 内容适配 → 排列 → 源文件，审计 B09） */}
        {!(panelsOnly && objs.length === 1) && (
          <ArrangeSection count={objs.length} multi={objs.length > 1} />
        )}
      </div>
    </>
  )
}

/** 摘要里的毫米数：整数不带 .0（「150 × 100 mm」），有小数才留一位 */
const mmShort = (v: number) => formatMm(v).replace(/\.0$/, '')

/**
 * 没有选中时的属性页：这份文档的摘要卡（redesign-3 B2，手帐线稿）。
 *
 * 上面是页面比例的发丝预览（画布灰托盘里一张纸，上边与左边各一条尺寸刻度「150」「100」）：没有图时纸上是一格虚线空位，
 * 有图时每张图按**它在文档里的真实位置与大小**按页面比例缩进预览（排线框，与缩略图占位同一种语言）；
 * 纸永远是白的（`--color-paper`，暗色里不反相）。下面依次是标题、一行事实（尺寸 · 几张图，等宽数字，数字与单位之间是
 * 窄空格）、「画布设置 ›」；「详情」只在悬停 / 键盘聚焦这张卡时显现，展开后是起步提示与标注 / 修改计数。
 * 只读事实，不重复画布页的控件。
 */
function DocumentSummary() {
  const { t } = useTranslation('inspector')
  const page = useDocumentStore((s) => s.doc.page)
  const objects = useDocumentStore((s) => s.doc.objects)
  const [open, setOpen] = useState(false)
  const figures = objects.filter((o): o is PanelObject => o.type === 'panel')
  const changes = figures.reduce((n, p) => n + p.overrides.length, 0)
  const extra = [
    ...(objects.length > figures.length
      ? [t('docSummary.annotations', { count: objects.length - figures.length })]
      : []),
    ...(changes > 0 ? [t('element.modifiedCount', { count: changes })] : []),
  ]
  const meta = [
    t('docSummary.size', { w: mmShort(page.w), h: mmShort(page.h) }),
    t('summaryPanels', { count: figures.length }),
  ].join(' · ')
  const ratio = page.h > 0 && page.w > 0 ? page.w / page.h : 1.5
  // 纸宽：随栏宽（cqi，Inspector 内层是 @container）伸缩，封顶 164px；竖版页再按 120px 高封顶，不把栏撑高。
  // 左边留 22px 给纵向刻度、右边留同样 22px：纸本身才落在托盘的正中（刻度是挂在纸两边的）
  const previewW = `calc(min(52cqi, 164px, ${Math.round(120 * ratio)}px) + 44px)`
  const pct = (v: number, of: number) => `${(Math.max(0, Math.min(1, v / of)) * 100).toFixed(2)}%`
  return (
    <div className="p-3">
      <section
        data-document-summary
        aria-label={t('docSummary.title')}
        className="group/doc rounded-lg bg-surface"
      >
        <div data-document-summary-preview className="flex justify-center rounded-md bg-canvas pb-3 pt-4">
          <div className="relative px-[22px] pt-3.5" style={{ width: previewW }} aria-hidden>
            {/* 上边：横向刻度「—— 150 ——」 */}
            <div className="absolute inset-x-[22px] top-0 flex h-3 items-center gap-1.5 text-xs tabular-nums text-ink-3">
              <i className="h-px flex-1 bg-border-strong" />
              <span>{mmShort(page.w)}</span>
              <i className="h-px flex-1 bg-border-strong" />
            </div>
            {/* 左边：纵向刻度 */}
            <div className="absolute bottom-0 left-0 top-3.5 flex w-[18px] flex-col items-center gap-1 text-xs tabular-nums text-ink-3">
              <i className="w-px flex-1 bg-border-strong" />
              <span>{mmShort(page.h)}</span>
              <i className="w-px flex-1 bg-border-strong" />
            </div>
            <div data-document-summary-page className="paper-sheet relative w-full" style={{ aspectRatio: `${page.w} / ${page.h}` }}>
              {figures.length === 0 ? (
                <span className="paper-slot-empty absolute inset-2 rounded-xs" />
              ) : (
                figures
                  .filter((f) => !f.hidden)
                  // 只画图与纸的**交集**（Codex #1471 P2）：起点和宽高各自夹到纸内会把出界的图整块挪进纸里，
                  // 例如 x = −20、宽 40 的图该只占纸上 0–20，而不是 0–40；完全在纸外的不画
                  .map((f) => ({ f, x0: Math.max(0, f.x), y0: Math.max(0, f.y), x1: Math.min(page.w, f.x + f.w), y1: Math.min(page.h, f.y + f.h) }))
                  .filter(({ x0, y0, x1, y1 }) => x1 > x0 && y1 > y0)
                  .map(({ f, x0, y0, x1, y1 }) => (
                    <span
                      key={f.id}
                      data-document-summary-slot
                      className="hatch-box hatch-box-paper rounded-xs"
                      // .hatch-box 自带 position: relative（未分层样式，压过 Tailwind 的 absolute 工具类）：绝对定位写在 style 里
                      style={{
                        position: 'absolute',
                        left: pct(x0, page.w),
                        top: pct(y0, page.h),
                        width: pct(x1 - x0, page.w),
                        height: pct(y1 - y0, page.h),
                      }}
                    />
                  ))
              )}
            </div>
          </div>
        </div>
        <div className="mt-3 flex items-baseline justify-between gap-2 px-0.5">
          <h3 className="type-title min-w-0 truncate">{t('docSummary.title')}</h3>
          <button
            type="button"
            data-document-summary-details
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className={cn(
              'shrink-0 rounded-xs type-caption outline-none transition-opacity duration-fast hover:text-ink focus-visible:focus-ring',
              open ? 'opacity-100' : 'opacity-0 focus-visible:opacity-100 group-focus-within/doc:opacity-100 group-hover/doc:opacity-100',
            )}
          >
            {t('docSummary.details')}
          </button>
        </div>
        <p className="mt-0.5 truncate px-0.5 type-caption tabular-nums">{meta}</p>
        {open && (
          <div data-document-summary-detail className="mt-2 px-0.5 type-caption">
            {extra.length > 0 && <p className="tabular-nums">{extra.join(' · ')}</p>}
            <p className={extra.length > 0 ? 'mt-1' : undefined}>{t('emptyHint')}</p>
          </div>
        )}
        <div className="mt-2 border-t border-border pt-2">
          <button
            type="button"
            data-document-summary-canvas
            onClick={() => useUiStore.getState().setRightTab('canvas')}
            className="-ml-0.5 inline-flex h-6 items-center gap-0.5 rounded-xs px-0.5 text-sm text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
          >
            {t('docSummary.canvasSettings')}
            <ChevronRight size={ICON_SIZE.xs} aria-hidden className="text-ink-faint" />
          </button>
        </div>
      </section>
    </div>
  )
}
