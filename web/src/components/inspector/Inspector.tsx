import { useTranslation } from 'react-i18next'
import {
  Copy,
  Eye,
  EyeOff,
  Image as ImageIcon,
  Lock,
  LockOpen,
  Ellipsis,
  MousePointerClick,
  MoveUpRight,
  Pin,
  Sparkles,
  Square,
  Trash2,
  Type as TypeIcon,
  X,
} from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { switchKindOf } from '@/lib/shapeSwitch'
import { drawerMotion, type PresenceState } from '@/lib/motion'
import { msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { cn, MOD } from '@/lib/utils'
import { deleteSelected, duplicateSelected, hideElement, updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { RIGHT_MAX, RIGHT_MIN, useUiStore, type RightTab } from '@/store/uiStore'
import {
  objectLabel,
  type ArrowObject,
  type CanvasObject,
  type PanelObject,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { useAiStore } from '@/store/aiStore'
import { assistantTabLabel, AssistantPanel } from '../ai/AiPanel'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem, MenuSeparator } from '../ui/Menu'
import { Tab, TabList } from '../ui/Tabs'
import { Tip } from '../ui/Tooltip'
import { ArrangeSection } from './ArrangeSection'
import { CanvasPage } from './CanvasPage'
import { ElementInspector } from './ElementInspector'
import { identityCrumbs, untruncatedLabel } from './identityCrumbs'
import { KIND_SWITCH_ICON } from './kindSwitchIcons'
import { ObjectKindSwitch } from './ObjectKindSwitch'
import { RestoreMenu } from './RestoreMenu'
import { roleName } from './roles/registry'
import { roleIcon } from './roles/roleIcons'
import { PanelSection } from './PanelSection'
import { ArrowSection, ShapeSection } from './StrokeSection'
import { TextSection } from './TextSection'
import { TransformSection } from './TransformSection'
import { useSelectedObjects } from './common'

/**
 * tab 行只放「对象上下文」的两页：属性（当前选中对象）与画布（当前文档）。
 * 助手是独立工作流，不与它们同级——入口在右侧，带运行状态点（§ADR 0010）。
 */
const TABS: RightTab[] = ['properties', 'canvas']

const tabLabel = (id: RightTab): string =>
  id === 'assistant' ? assistantTabLabel() : translate(`tab.${id}`, { ns: 'inspector' })

const TYPE_ICON = {
  panel: ImageIcon,
  text: TypeIcon,
  arrow: MoveUpRight,
  shape: Square,
} as const

export function Inspector({
  overlay = false,
  state = 'open',
}: {
  overlay?: boolean
  /** 开合动效由 App 的 usePresence 驱动：收起时先播完退场再卸载 */
  state?: PresenceState
}) {
  const { t } = useTranslation('inspector')
  const tab = useUiStore((s) => s.rightTab)
  const setTab = useUiStore((s) => s.setRightTab)
  const width = useUiStore((s) => s.rightWidth)
  const pinned = useUiStore((s) => s.rightPinned)
  const layout = useUiStore((s) => s.layout)
  const runningAi = useAiStore((s) => s.sessions.some((x) => x.status === 'running'))

  const motion = drawerMotion({ state, overlay, width, side: 'right' })

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
        overlay && 'absolute inset-y-0 right-0 z-30 shadow-pop',
        motion.className,
      )}
    >
      <div className="flex h-full flex-col" style={{ width }}>
      <div className="flex h-9 shrink-0 items-center gap-3 px-3">
        <TabList label={t('tabsLabel')}>
          {TABS.map((id) => (
            <Tab key={id} data-inspector-tab={id} active={tab === id} onClick={() => setTab(id)}>
              {tabLabel(id)}
            </Tab>
          ))}
        </TabList>
        <span className="flex-1" />
        <Tip label={runningAi ? t('assistantRunningTip') : assistantTabLabel()} side="bottom">
          <Button
            size="sm"
            active={tab === 'assistant'}
            aria-pressed={tab === 'assistant'}
            aria-label={assistantTabLabel() + (runningAi ? ` · ${t('aiRunning')}` : '')}
            className="relative gap-1 px-1.5 text-xs"
            onClick={() => setTab(tab === 'assistant' ? 'properties' : 'assistant')}
          >
            <Sparkles size={ICON_SIZE.sm} className={tab === 'assistant' ? undefined : 'text-ink-3'} />
            {assistantTabLabel()}
            {runningAi && (
              <span
                aria-hidden
                className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-ink"
              />
            )}
          </Button>
        </Tip>
        {layout !== 'narrow' ? (
          /* 只留图钉，不写「常驻 / 自动收起」：即便右栏最窄 320px，两个标签页 +
             助手入口 + 带词的开关 + 关闭按钮在英文下也排不下（e2e/i18n.spec.ts
             量横向溢出）。状态本身由填色（active）+ aria-pressed 表达，说明留在
             tooltip 与无障碍名里，那两处不占版面。 */
          <IconButton
            label={t(pinned ? 'pinnedAria' : 'autoHideAria')}
            tip={t(pinned ? 'pinnedTip' : 'autoHideTip')}
            side="bottom"
            iconSize="sm"
            active={pinned}
            aria-pressed={pinned}
            onClick={() => useUiStore.getState().setRightPinned(!pinned)}
          >
            {/* 小 ghost 图标钮：常驻态只是轻 tint + 描成 ink，不是头部最显眼的东西 */}
            <Pin size={ICON_SIZE.sm} className={pinned ? 'text-ink' : 'text-ink-3'} />
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
          className="-mr-1.5 text-ink-3 hover:text-ink"
          onClick={() => useUiStore.getState().toggleRight()}
        >
          <X size={ICON_SIZE.sm} />
        </IconButton>
      </div>

      {tab === 'assistant' ? (
        <AssistantPanel />
      ) : tab === 'canvas' ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <CanvasPage />
        </div>
      ) : (
        <PropertiesPage />
      )}
      </div>
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
      className="absolute inset-y-0 left-0 z-20 w-2 cursor-col-resize outline-none hover:bg-accent/20 focus-visible:bg-accent/30"
    />
  )
}

/* -------------------------------------------------------------------------- */
/*  属性页                                                                     */
/* -------------------------------------------------------------------------- */

function PropertiesPage() {
  const { t } = useTranslation('inspector')
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
          <ElementInspector panel={elementPanel} />
        </div>
      </>
    )
  }

  if (objs.length === 0) {
    // 钉住时面板留着；未钉住时选择清空后面板本来就收起了
    return (
      <div className="flex min-h-0 flex-1 overflow-y-auto">
        <EmptyState icon={MousePointerClick} title={t('emptyTitle')} hint={t('emptyHint')} />
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
        {/* 第三层：排列与层级（紧凑工具带；单选面板的对齐已在位置组里） */}
        <ArrangeSection
          count={objs.length}
          multi={objs.length > 1}
          zOnly={panelsOnly && objs.length === 1}
        />
      </div>
    </>
  )
}

/**
 * 唯一的上下文头：现在改的是谁、它处于什么状态、对它还能做什么。
 * 复制 / 显隐 / 锁定 / 删除收进右侧更多菜单，锁定与隐藏状态本身常驻显示。
 */
function IdentityHeader({ objs = [], panel }: { objs?: CanvasObject[]; panel?: PanelObject }) {
  const { t } = useTranslation('inspector')
  const selectedGids = useUiStore((s) => s.selectedGids)
  const manifest = usePanelDisplayManifest(panel)

  if (panel) {
    const gid = selectedGids.at(-1)
    const el = gid ? manifest?.elements.find((e) => e.gid === gid) : undefined
    // gid 形如 axes_1.images_0：中段就是宿主子图，拼出「面板 / 子图 / 元素」
    const axesGid = gid?.includes('.') ? gid.split('.')[0] : undefined
    const axes = axesGid ? manifest?.elements.find((e) => e.gid === axesGid) : undefined
    const text = el?.editable.find((f) => f.prop === 'text')?.value
    const crumbs = identityCrumbs(
      panel.name ?? panel.fileId,
      axes && axes.gid !== gid ? axes.label : undefined,
      el ? untruncatedLabel(el.label, typeof text === 'string' ? text : undefined) : undefined,
      selectedGids.length,
    )
    const hideable =
      el && el.gid !== 'figure' && el.editable.some((f) => f.prop === 'visible')
    // 来源状态：选中元素时报它自己被改了几项，没选（整张图）时报面板总数。
    // 「多少项被 Tavotto 修改、怎么恢复」是右栏头部要直接回答的问题。
    const modified = el
      ? panel.overrides.filter((o) => o.gid === el.gid).length
      : panel.overrides.length
    const RoleIcon = roleIcon(el?.role ?? 'figure')

    return (
      <header className="shrink-0 pl-3 pr-2 pb-2">
        <div className="flex items-center gap-1.5">
          {/* 图标按角色查树里那张表（roles/roleIcons）：标题是 T、曲线是折线、图例是列表，
              与左栏元素树同一张脸；以前不管选了什么都是同一个图片图标 */}
          <RoleIcon size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
          {/* 没选元素时标题是面板名：标出「整张图」这一层，免得与画布上的面板混淆（审计 T01） */}
          {!el && (
            <span data-object-kind className="shrink-0 rounded-sm bg-surface-active px-1 text-xs text-ink-2">
              {roleName('figure')}
            </span>
          )}
          <h2 className="min-w-0 truncate text-xs font-medium text-ink">
            {crumbs.at(-1) ?? t('elementFallback')}
          </h2>
          <span className="ml-auto flex shrink-0 items-center">
            {hideable && el && (
              <Tip label={t('hideElementTip')} side="bottom">
                <Button
                  size="icon-sm"
                  onClick={() => {
                    hideElement(panel.id, el.gid, el.label)
                    useUiStore.getState().setSelectedGid(null)
                  }}
                  aria-label={t('hideElement')}
                >
                  <EyeOff size={ICON_SIZE.sm} className="text-ink-3" />
                </Button>
              </Tip>
            )}
            <Tip label={t('exitElementEdit')} side="bottom">
              <Button
                size="icon-sm"
                data-exit-element-edit
                onClick={() => useUiStore.getState().setElementPanel(null)}
                aria-label={t('exitElementEdit')}
              >
                <X size={ICON_SIZE.sm} className="text-ink-3" />
              </Button>
            </Tip>
          </span>
        </div>
        {(crumbs.length > 1 || modified > 0) && (
          <p className="mt-0.5 flex items-center gap-1.5 pr-1 text-xs text-ink-3">
            {crumbs.length > 1 && (
              <span className="min-w-0 truncate" title={crumbs.join(' / ')}>
                {crumbs.slice(0, -1).join(' / ')}
              </span>
            )}
            {/* 「n 项已修改」徽标本身就是恢复菜单（恢复此元素 / 恢复整张图）：
                改了几项与怎么撤回是同一个问题的两半，不另起一行 */}
            <RestoreMenu panel={panel} gid={el?.gid} count={modified} />
          </p>
        )}
      </header>
    )
  }

  const one = objs.length === 1 ? objs[0] : null
  const kinds = [...new Set(objs.map((o) => o.type))]
  // 标注的图标按**它自己那一种**画，不是所有形状都用一个方块：三角形旁边摆
  // 一个正方形，图标说的和徽标说的是两件事（与 MarkerPicker 同一条纪律——
  // 形状是事实，不该拿一个通用图形代替）
  const oneKind = one ? switchKindOf(one) : null
  const Icon = one
    ? oneKind
      ? KIND_SWITCH_ICON[oneKind]
      : TYPE_ICON[one.type]
    : kinds.length === 1
      ? TYPE_ICON[kinds[0]]
      : Copy
  /**
   * 标题 = **用户内容**。没起过名字的标注，`objectLabel` 的兜底正是类型名，
   * 而类型徽标已经在说它了——两格并排写着同一个词（「三角形 ⌄ 三角形」）看起来
   * 像个 bug。这一格没有新话要说时就整个不出现，让徽标独自承担（文字与面板不受
   * 影响：它们的名字是那句话 / 那个文件名，与类型不是一回事）。
   */
  const title = one
    ? oneKind && !one.name
      ? null
      : objectLabel(one)
    : translate('count.selectedObjects', { count: objs.length })
  const locked = objs.length > 0 && objs.every((o) => o.locked)
  const hidden = objs.length > 0 && objs.every((o) => o.hidden)
  const ids = objs.map((o) => o.id)

  return (
    <header className="shrink-0 pl-3 pr-2 pb-2">
      <div className="flex items-center gap-1.5">
        <Icon size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
        {/* 对象类型与名字分开写：名字是用户内容（文件名 / 文字），类型才回答
            「我在改的是文字、面板还是标注」（审计 T01）。这颗徽标同时是**类型
            切换**的入口——标注能换成同族的另一种时它就是下拉，换不了时还是那颗
            静态徽标（cap-shape-switch；判据在 lib/shapeSwitch，这里不判） */}
        <ObjectKindSwitch objs={objs} />
        {title != null && (
          <h2 className="min-w-0 truncate text-xs font-medium text-ink">{title}</h2>
        )}
        {locked && <Lock size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-label={t('locked')} />}
        {hidden && <EyeOff size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-label={t('hiddenState')} />}
        {!one && <span className="shrink-0 text-xs text-ink-3">{summarize(objs)}</span>}
        <Menu
          width={172}
          align="end"
          trigger={
            <Button size="icon-sm" className="ml-auto" aria-label={t('objectActions')}>
              <Ellipsis size={ICON_SIZE.sm} className="text-ink-3" />
            </Button>
          }
        >
          <MenuItem shortcut={`${MOD}D`} onSelect={duplicateSelected}>
            <span className="flex items-center gap-2">
              <Copy size={ICON_SIZE.sm} className="text-ink-3" />
              {translate('actions.copy')}
            </span>
          </MenuItem>
          <MenuItem
            onSelect={() =>
              updateObjects(ids, msg(hidden ? 'history.showObject' : 'history.hideObject', undefined, 'workspace'), (o) => {
                o.hidden = !hidden
              })
            }
          >
            <span className="flex items-center gap-2">
              {hidden ? <Eye size={ICON_SIZE.sm} className="text-ink-3" /> : <EyeOff size={ICON_SIZE.sm} className="text-ink-3" />}
              {t(hidden ? 'show' : 'hide')}
            </span>
          </MenuItem>
          <MenuItem
            onSelect={() =>
              updateObjects(ids, msg(locked ? 'history.unlockObject' : 'history.lockObject', undefined, 'workspace'), (o) => {
                o.locked = !locked
              })
            }
          >
            <span className="flex items-center gap-2">
              {locked ? <LockOpen size={ICON_SIZE.sm} className="text-ink-3" /> : <Lock size={ICON_SIZE.sm} className="text-ink-3" />}
              {t(locked ? 'unlock' : 'lock')}
            </span>
          </MenuItem>
          <MenuSeparator />
          <MenuItem danger shortcut="⌫" onSelect={deleteSelected}>
            <span className="flex items-center gap-2">
              <Trash2 size={ICON_SIZE.sm} />
              {translate('actions.delete')}
            </span>
          </MenuItem>
        </Menu>
      </div>
    </header>
  )
}

function summarize(objs: CanvasObject[]): string {
  const n = (type: CanvasObject['type']) => objs.filter((o) => o.type === type).length
  const parts: string[] = []
  if (n('panel')) parts.push(translate('summaryPanels', { ns: 'inspector', count: n('panel') }))
  if (n('text')) parts.push(translate('summaryTexts', { ns: 'inspector', count: n('text') }))
  const marks = n('arrow') + n('shape')
  if (marks) parts.push(translate('summaryMarks', { ns: 'inspector', count: marks }))
  return listJoin(parts)
}
