import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { useMemo } from 'react'
import { ChevronRight, Pin, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { drawerMotion, type PresenceState } from '@/lib/motion'
import { t as translate } from '@/i18n'
import { formatMm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { RIGHT_MAX, RIGHT_MIN, useUiStore, type RightTab } from '@/store/uiStore'
import type { ArrowObject, PanelObject, ShapeObject, TextObject } from '@/types/document'
import { useAiStore } from '@/store/aiStore'
import { assistantTabLabel, AssistantPanel } from '../ai/AiPanel'
import { IconButton } from '../ui/Button'
import { Card } from '../ui/Card'
import { ColorFieldContext } from '../ui/colorPalette'
import { Tab, TabList, TabPanel } from '../ui/Tabs'
import { Tip } from '../ui/Tooltip'
import { ArrangeSection } from './ArrangeSection'
import { CanvasPage } from './CanvasPage'
import { useDocumentColors } from './documentColors'
import { PanelElementPage } from './GroupPage'
import { IdentityHeader } from './IdentityHeader'
import { INSPECTOR_CONTAINER } from './layout'
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
const TABS: RightTab[] = ['properties', 'canvas', 'assistant']
/** 内容区 id（`TabPanel`）：`<id>-tab` 是对应页签的 id */
const PANEL_ID: Record<RightTab, string> = {
  properties: 'inspector-panel-properties',
  assistant: 'inspector-panel-assistant',
  canvas: 'inspector-panel-canvas',
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
  const pinned = useUiStore((s) => s.rightPinned)
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
      <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border px-3">
        <TabList label={t('tabsLabel')} className="min-w-0 self-stretch">
          {TABS.map((id) => (
            <Tab
              key={id}
              data-inspector-tab={id}
              panelId={PANEL_ID[id]}
              active={tab === id}
              onClick={() => setTab(id)}
              // 助手页签：可达名带上「有任务在运行」，视觉上是页签右上角的一颗点
              aria-label={
                id === 'assistant' && runningAi ? `${assistantTabLabel()} · ${t('aiRunning')}` : undefined
              }
              title={id === 'assistant' && runningAi ? t('assistantRunningTip') : undefined}
              // 页签一律纯文字（打磨 S4）：三家的页签 / 分段项都没有图标，助手的身份
              // 由页内空态那颗图标说；右上角的运行点已经回答「有没有在跑」
              className={id === 'assistant' ? 'inline-flex items-center pr-1' : undefined}
            >
              {tabLabel(id)}
              {id === 'assistant' && runningAi && (
                <span
                  aria-hidden
                  className="absolute right-0 top-1.5 h-1.5 w-1.5 rounded-full bg-ink"
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

      {tab === 'assistant' ? (
        <TabPanel id={PANEL_ID.assistant} className="flex min-h-0 flex-1 flex-col">
          <AssistantPanel />
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
  // 可聚焦的 separator 在 ARIA 里是个真控件：必须报出当前值与值域，
  // 否则屏幕阅读器只会念一句「分隔条」，用户不知道自己在调什么、调到了哪
  const width = useUiStore((s) => s.rightWidth)
  const start = (e: React.PointerEvent) => {
    e.preventDefault()
    const from = useUiStore.getState().rightWidth
    const x0 = e.clientX
    const move = (ev: PointerEvent) => useUiStore.getState().setRightWidth(from - (ev.clientX - x0))
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={t('resize', { min: RIGHT_MIN, max: RIGHT_MAX })}
      aria-valuenow={Math.round(width)}
      aria-valuemin={RIGHT_MIN}
      aria-valuemax={RIGHT_MAX}
      tabIndex={0}
      onPointerDown={start}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
        e.preventDefault()
        const ui = useUiStore.getState()
        ui.setRightWidth(ui.rightWidth + (e.key === 'ArrowLeft' ? 16 : -16))
      }}
      // 整条都在抽屉内侧：外层 overflow-hidden（开合动效要用）会把伸到外面的部分剪掉
      // 蓝色不做任何大块背景（第一节）：hover 只在内侧描一条 1px 的竖线；
      // 键盘聚焦是一条 2px 的 accent 竖线（2026-10-07 设计审计 §9.2：此前是 8px 宽的 accent/30 蓝带）
      className="absolute inset-y-0 left-0 z-canvas-chrome w-2 cursor-col-resize border-l border-transparent outline-none hover:border-border-strong focus-visible:border-l-2 focus-visible:border-accent"
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
        {!panelsOnly && !textsOnly && <TransformSection objs={objs} />}
        {/* 第二层：类型专属 */}
        {onlyType(panels.length) && <PanelSection objs={panels} />}
        {textsOnly && <TextSection objs={texts} />}
        {textsOnly && <TransformSection objs={objs} foldKey="text-transform" />}
        {onlyType(arrows.length) && <ArrowSection objs={arrows} />}
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

/**
 * 没有选中时的属性页：这份文档的摘要卡（尺寸、几张图、几处修改）+「画布设置 ›」。
 * 只读事实，不重复画布页的控件；一句提示告诉用户点什么开始编辑。
 */
function DocumentSummary() {
  const { t } = useTranslation('inspector')
  const page = useDocumentStore((s) => s.doc.page)
  const objects = useDocumentStore((s) => s.doc.objects)
  const figures = objects.filter((o): o is PanelObject => o.type === 'panel')
  const changes = figures.reduce((n, p) => n + p.overrides.length, 0)
  const facts = [
    t('docSummary.size', { w: formatMm(page.w), h: formatMm(page.h) }),
    t('summaryPanels', { count: figures.length }),
    ...(objects.length > figures.length
      ? [t('docSummary.annotations', { count: objects.length - figures.length })]
      : []),
    ...(changes > 0 ? [t('element.modifiedCount', { count: changes })] : []),
  ]
  return (
    <div className="p-3">
      <Card appearance="subtle" padding="md" data-document-summary className="flex flex-col gap-2">
        <p className="type-title">{t('docSummary.title')}</p>
        <ul className="flex flex-col gap-0.5">
          {facts.map((f) => (
            <li key={f} className="type-number text-ink-2">
              {f}
            </li>
          ))}
        </ul>
        <p className="type-caption">{t('emptyHint')}</p>
        <button
          type="button"
          data-document-summary-canvas
          onClick={() => useUiStore.getState().setRightTab('canvas')}
          className="-mx-1 flex h-7 items-center gap-1 self-start rounded-md px-1 text-sm text-ink-2 outline-none hover:bg-surface-hover hover:text-ink focus-visible:focus-ring"
        >
          {t('docSummary.canvasSettings')}
          <ChevronRight size={ICON_SIZE.xs} aria-hidden />
        </button>
      </Card>
    </div>
  )
}
