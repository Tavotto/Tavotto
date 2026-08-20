import { useTranslation } from 'react-i18next'
import {
  Copy,
  Eye,
  EyeOff,
  Image as ImageIcon,
  Lock,
  LockOpen,
  MoreHorizontal,
  MousePointerClick,
  MoveUpRight,
  Pin,
  Square,
  Trash2,
  Type as TypeIcon,
  X,
} from 'lucide-react'
import { drawerMotion, type PresenceState } from '@/lib/motion'
import { msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { cn, MOD } from '@/lib/utils'
import { deleteSelected, duplicateSelected, hideElement, updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelManifest } from '@/store/renderStore'
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
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem, MenuSeparator } from '../ui/Menu'
import { Tip } from '../ui/Tooltip'
import { ArrangeSection } from './ArrangeSection'
import { CanvasPage } from './CanvasPage'
import { ElementInspector } from './ElementInspector'
import { identityCrumbs } from './identityCrumbs'
import { PanelSection } from './PanelSection'
import { ArrowSection, ShapeSection } from './StrokeSection'
import { TextSection } from './TextSection'
import { TransformSection } from './TransformSection'
import { useSelectedObjects } from './common'

/** 三个模式的标签文案：助手那条来自 ai 命名空间，另外两条在 inspector 里 */
const TABS: RightTab[] = ['properties', 'assistant', 'canvas']

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
        <div role="tablist" aria-label={t('tabsLabel')} className="flex h-full items-center gap-3">
          {TABS.map((id) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={cn(
                'relative h-full text-xs outline-none transition-colors focus-visible:focus-ring',
                tab === id
                  ? 'font-medium text-ink after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:rounded-full after:bg-ink'
                  : 'text-ink-3 hover:text-ink-2',
              )}
            >
              {tabLabel(id)}
              {id === 'assistant' && runningAi && (
                <span
                  className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-accent"
                  aria-label={t('aiRunning')}
                />
              )}
            </button>
          ))}
        </div>
        <span className="flex-1" />
        {layout !== 'narrow' ? (
          /* 只留图钉，不写「常驻 / 自动收起」：这一行宽度是 296–320px 定死的，
             三个标签页 + 一个带词的开关 + 关闭按钮在英文下要 321px，超出 17px
             把关闭按钮顶到面板外面（e2e/i18n.spec.ts 量的就是它）。状态本身
             由填色（active）+ aria-pressed 表达，说明留在 tooltip 与无障碍名里，
             那两处不占版面。 */
          <Tip label={t(pinned ? 'pinnedTip' : 'autoHideTip')} side="bottom">
            <Button
              size="icon-sm"
              active={pinned}
              aria-pressed={pinned}
              aria-label={t(pinned ? 'pinnedAria' : 'autoHideAria')}
              onClick={() => useUiStore.getState().setRightPinned(!pinned)}
            >
              <Pin size={11} className={pinned ? undefined : 'text-ink-3'} />
            </Button>
          </Tip>
        ) : (
          <Tip label={t('overlayTip')} side="bottom">
            <span className="text-xs text-ink-3">{t('overlay')}</span>
          </Tip>
        )}
        <Tip label={translate('actions.close')} side="bottom">
          <Button
            size="icon-sm"
            className="-mr-1.5"
            aria-label={t('closePanel')}
            onClick={() => useUiStore.getState().toggleRight()}
          >
            <X size={13} className="text-ink-3" />
          </Button>
        </Tip>
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
        {/* 第一层：位置与尺寸等高频属性 */}
        {!panelsOnly && <TransformSection objs={objs} />}
        {/* 第二层：类型专属 */}
        {onlyType(panels.length) && <PanelSection objs={panels} />}
        {onlyType(texts.length) && <TextSection objs={texts} />}
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
  const manifest = usePanelManifest(panel)

  if (panel) {
    const gid = selectedGids.at(-1)
    const el = gid ? manifest?.elements.find((e) => e.gid === gid) : undefined
    // gid 形如 axes_1.images_0：中段就是宿主子图，拼出「面板 / 子图 / 元素」
    const axesGid = gid?.includes('.') ? gid.split('.')[0] : undefined
    const axes = axesGid ? manifest?.elements.find((e) => e.gid === axesGid) : undefined
    const crumbs = identityCrumbs(
      panel.name ?? panel.fileId,
      axes && axes.gid !== gid ? axes.label : undefined,
      el?.label,
      selectedGids.length,
    )
    const hideable =
      el && el.gid !== 'figure' && el.editable.some((f) => f.prop === 'visible')

    return (
      <header className="shrink-0 px-3 pb-2">
        <div className="flex items-center gap-1.5">
          <ImageIcon size={13} className="shrink-0 text-ink-3" />
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
                  <EyeOff size={12} className="text-ink-3" />
                </Button>
              </Tip>
            )}
            <Tip label={t('exitElementEdit')} side="bottom">
              <Button
                size="icon-sm"
                className="-mr-1"
                onClick={() => useUiStore.getState().setElementPanel(null)}
                aria-label={t('exitElementEdit')}
              >
                <X size={12} className="text-ink-3" />
              </Button>
            </Tip>
          </span>
        </div>
        {crumbs.length > 1 && (
          <p className="mt-0.5 truncate text-xs text-ink-3" title={crumbs.join(' / ')}>
            {crumbs.slice(0, -1).join(' / ')}
          </p>
        )}
      </header>
    )
  }

  const one = objs.length === 1 ? objs[0] : null
  const kinds = [...new Set(objs.map((o) => o.type))]
  const Icon = one ? TYPE_ICON[one.type] : kinds.length === 1 ? TYPE_ICON[kinds[0]] : Copy
  const locked = objs.length > 0 && objs.every((o) => o.locked)
  const hidden = objs.length > 0 && objs.every((o) => o.hidden)
  const ids = objs.map((o) => o.id)

  return (
    <header className="shrink-0 px-3 pb-2">
      <div className="flex items-center gap-1.5">
        <Icon size={13} className="shrink-0 text-ink-3" />
        <h2 className="min-w-0 truncate text-xs font-medium text-ink">
          {one ? objectLabel(one) : translate('count.selectedObjects', { count: objs.length })}
        </h2>
        {locked && <Lock size={11} className="shrink-0 text-ink-3" aria-label={t('locked')} />}
        {hidden && <EyeOff size={11} className="shrink-0 text-ink-3" aria-label={t('hiddenState')} />}
        {!one && <span className="shrink-0 text-xs text-ink-3">{summarize(objs)}</span>}
        <Menu
          width={172}
          align="end"
          trigger={
            <Button size="icon-sm" className="-mr-1 ml-auto" aria-label={t('objectActions')}>
              <MoreHorizontal size={13} className="text-ink-3" />
            </Button>
          }
        >
          <MenuItem shortcut={`${MOD}D`} onSelect={duplicateSelected}>
            <span className="flex items-center gap-2">
              <Copy size={13} className="text-ink-3" />
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
              {hidden ? <Eye size={13} className="text-ink-3" /> : <EyeOff size={13} className="text-ink-3" />}
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
              {locked ? <LockOpen size={13} className="text-ink-3" /> : <Lock size={13} className="text-ink-3" />}
              {t(locked ? 'unlock' : 'lock')}
            </span>
          </MenuItem>
          <MenuSeparator />
          <MenuItem danger shortcut="⌫" onSelect={deleteSelected}>
            <span className="flex items-center gap-2">
              <Trash2 size={13} />
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
