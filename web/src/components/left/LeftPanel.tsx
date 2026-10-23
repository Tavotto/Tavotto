import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { Pin } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { drawerMotion, type PresenceState } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { ResizeHandle } from '@/components/ui/ResizeHandle'
import { tryRegisterPointerCancel } from '@/store/gestureCoordinator'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { LEFT_DEFAULT, LEFT_MAX, LEFT_MIN, RAIL_W, useUiStore } from '@/store/uiStore'
import { IconButton } from '../ui/Button'
import { DESK_FIELD_SCOPE } from '../ui/fieldBox'
import { AssetBrowser } from './AssetBrowser'
import { CanvasList } from './CanvasList'
import { DrawerCount } from './DrawerCount'
import { DrawerSlotsContext } from './drawerSlots'
import { ElementTree } from './ElementTree'
import { LayerTree } from './LayerTree'
import { ProblemPanel } from './ProblemPanel'
import { StylePanel } from './StylePanel'
import { WorkspaceList } from './WorkspaceList'

/**
 * 左侧上下文抽屉：内容由图标轨道决定，一次只有一个上下文。
 * 标题行给出上下文名 + 计数；宽屏可钉住（选中对象时不自动让位）。
 */
export function LeftPanel({
  overlay = false,
  state = 'open',
}: {
  overlay?: boolean
  /** 开合动效由 App 的 usePresence 驱动：收起时先播完退场再卸载 */
  state?: PresenceState
}) {
  perfCount('render.LeftPanel')
  const { t } = useTranslation('workspace')
  const tab = useUiStore((s) => s.leftTab)
  const width = useUiStore((s) => s.leftWidth)
  const pinned = useUiStore((s) => s.leftPinned)
  const wide = useUiStore((s) => s.layout === 'wide')
  const objectCount = useDocumentStore((s) => s.doc.objects.length)
  // 标题行的两个槽（`DrawerHeader`）：节点进 state，抽屉在下一帧把自己的动作 portal 进来
  const [metaSlot, setMetaSlot] = useState<HTMLElement | null>(null)
  const [actionsSlot, setActionsSlot] = useState<HTMLElement | null>(null)
  const slots = useMemo(() => ({ meta: metaSlot, actions: actionsSlot }), [metaSlot, actionsSlot])

  const motion = drawerMotion({ state, overlay, width, side: 'left' })

  return (
    <aside
      {...motion}
      style={{ ...motion.style, left: overlay ? RAIL_W : undefined }}
      data-left-drawer
      aria-label={t(`rail.${tab}`)}
      className={cn(
        // overflow-hidden 是动效的一部分：停靠态动的是外层 width，内容包在下面
        // 那层定宽 div 里，所以展开收起时抽屉自己的子树一次都不重排
        // isolate：抽屉自己是一个层叠上下文。否则问题面板吸顶组头的 z-sticky 会直接落在根层叠上下文里，
        // 压过右边画布面板（无 z-index）在抽屉一侧的投影 / 描边——组头那几行的底色把画布左缘「切」断（b3 反馈）
        'relative isolate shrink-0 overflow-hidden',
        // 停靠时抽屉就坐在灰色桌面上（2026-09-30 重设计）：与右边的白色工作面板之间靠明度差分开，
        // 不画线。覆盖式是浮层：白底 + 浮层投影——投影自带 1px 环，再画一条实边就是双描边
        // （宪法第一节「浮层不再画实色 border」；左栏审计 L20）
        // 可编辑框在桌面上换成白底（field 比桌面还浅，放在灰上就看不出是个框）：只改这一棵子树里的
        // 两个 token，fieldBox 原语不动。`--drawer-bg` = 抽屉此刻的底色，给吸顶的组头这类
        // 「必须与抽屉同色才不露缝」的子元素读（问题面板的组头），不另造设计 token
        overlay
          ? 'absolute inset-y-0 z-drawer bg-surface shadow-pop [--drawer-bg:var(--color-surface)]'
          : `bg-bg [--drawer-bg:var(--color-bg)] ${DESK_FIELD_SCOPE}`,
        motion.className,
      )}
    >
      <div className="flex h-full flex-col" style={{ width }}>
      {/* 标题行：名字 + 低权重计数 + 钉住。计数只是一个数字（type-meta），
          单位进读屏用的隐藏文本——审计 T07 / T08 要的是**可达名**里分得清对象 /
          元素 / 修改，不是让视觉上多四个字。
          标题是分区标题那一档（type-section 12/500）：此前 11px，比它下面的
          「图 3」分区标题还小一号，层级倒挂（左栏审计 L01） */}
      <div className="flex h-9 shrink-0 items-center gap-1.5 pl-3 pr-1.5">
        <h2 className="type-section shrink-0">{t(`rail.${tab}`)}</h2>
        {tab === 'layers' && objectCount > 0 && (
          <DrawerCount value={objectCount} label={t('layerTree.count', { count: objectCount })} />
        )}
        {tab === 'elements' && <ElementCount />}
        {/* meta 槽：抽屉自己的计数 / 范围（问题面板的「当前图 13 ▾」胶囊，审计 §9.4：范围并入标题行） */}
        <span ref={setMetaSlot} data-drawer-meta className="flex min-w-0 items-center gap-1.5 empty:hidden" />
        <span className="flex-1" />
        {/* actions 槽（审计 §10.3）：「+」「刷新」「⋯」进标题行，搜索行只放搜索。
            面板头的图标钮走默认档（16px 图标）：`sm` 只给与 11–12px 文字并排的行内小钮（宪法第四节） */}
        <span ref={setActionsSlot} data-drawer-actions className="flex shrink-0 items-center gap-0.5 empty:hidden" />
        {wide && (
          <IconButton
            side="bottom"
            label={pinned ? t('drawer.unpin') : t('drawer.pin')}
            active={pinned}
            aria-pressed={pinned}
            onClick={() => useUiStore.getState().setLeftPinned(!pinned)}
          >
            <Pin size={ICON_SIZE.md} filled={pinned} className={pinned ? 'text-ink' : 'text-ink-3'} />
          </IconButton>
        )}
      </div>
      <DrawerSlotsContext.Provider value={slots}>
      {tab === 'workspace' ? (
        <WorkspaceList />
      ) : tab === 'canvases' ? (
        <CanvasList />
      ) : tab === 'assets' ? (
        <AssetBrowser />
      ) : tab === 'layers' ? (
        <LayerTree />
      ) : tab === 'style' ? (
        <StylePanel />
      ) : tab === 'problems' ? (
        <ProblemPanel />
      ) : (
        <ElementTree />
      )}
      </DrawerSlotsContext.Provider>
      </div>
      <WidthHandle />
    </aside>
  )
}

/** 元素计数进标题：树里不再重复统计行 */
function ElementCount() {
  const { t } = useTranslation('workspace')
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedIds = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  const byId = (id: string | null) => {
    const o = id ? objects.find((x) => x.id === id) : undefined
    return o?.type === 'panel' && o.script ? o : null
  }
  const panel = byId(elementPanelId) ?? byId(selectedIds.at(-1) ?? null)
  const n = usePanelDisplayManifest(panel)?.elements.length ?? 0
  if (!n) return null
  return <DrawerCount value={n - 1} label={t('elementTree.count', { count: n - 1 })} />
}

/** 右边缘的拖拽把手：卡片网格的列宽由它决定，所以宽度值得可调且记住 */
function WidthHandle() {
  const { t } = useTranslation('workspace')
  const width = useUiStore((s) => s.leftWidth)
  return (
    <ResizeHandle
      name="left-drawer"
      registerCancel={tryRegisterPointerCancel}
      side="right"
      value={width}
      min={LEFT_MIN}
      max={LEFT_MAX}
      defaultValue={LEFT_DEFAULT}
      onChange={(px) => useUiStore.getState().setLeftWidth(px)}
      label={t('drawer.resize', { min: LEFT_MIN, max: LEFT_MAX })}
    />
  )
}
