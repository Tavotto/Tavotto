import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { Pin } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { drawerMotion, type PresenceState } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { LEFT_MAX, LEFT_MIN, RAIL_W, useUiStore } from '@/store/uiStore'
import { IconButton } from '../ui/Button'
import { AssetBrowser } from './AssetBrowser'
import { CanvasList } from './CanvasList'
import { DrawerCount } from './DrawerCount'
import { ElementTree } from './ElementTree'
import { LayerTree } from './LayerTree'
import { ProblemPanel } from './ProblemPanel'
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
        'relative shrink-0 overflow-hidden bg-surface',
        // 停靠时与画布之间一根 hairline；覆盖式只留浮层投影——投影自带 1px 环，
        // 再画一条实边就是双描边（宪法第一节「浮层不再画实色 border」；左栏审计 L20）
        overlay ? 'absolute inset-y-0 z-30 shadow-pop' : 'border-r border-border',
        motion.className,
      )}
    >
      <div className="flex h-full flex-col" style={{ width }}>
      {/* 标题行：名字 + 低权重计数 + 钉住。计数只是一个数字（type-meta），
          单位进读屏用的隐藏文本——审计 T07 / T08 要的是**可达名**里分得清对象 /
          元素 / 修改，不是让视觉上多四个字。
          标题是分区标题那一档（type-section 12/500）：此前 11px，比它下面的
          「图 3」分区标题还小一号，层级倒挂（左栏审计 L01） */}
      <div className="flex h-9 shrink-0 items-center gap-1.5 px-3">
        <h2 className="type-section">{t(`rail.${tab}`)}</h2>
        {tab === 'layers' && objectCount > 0 && (
          <DrawerCount value={objectCount} label={t('layerTree.count', { count: objectCount })} />
        )}
        {tab === 'elements' && <ElementCount />}
        {/* 问题面板的标题不再带计数（二审 C2，拍板「乙」）：页签「当前图 13 · 整个文档 14」
            已经把两个范围各说了一遍，轨道角标说的是整个文档；标题再来一个 13 就是
            一个概念三个数字 */}
        <span className="flex-1" />
        {/* 面板头的图标钮走默认档（16px 图标）：`sm` 只给与 11–12px 文字并排的行内小钮
            （宪法第四节）；此前面板头 14、画布页「+」16、版本 × 16 三处三样（左栏审计 L13） */}
        {wide && (
          <IconButton
            side="bottom"
            label={pinned ? t('drawer.unpin') : t('drawer.pin')}
            tip={pinned ? t('drawer.unpinHint') : t('drawer.pinHint')}
            active={pinned}
            aria-pressed={pinned}
            className="-mr-1.5"
            onClick={() => useUiStore.getState().setLeftPinned(!pinned)}
          >
            <Pin size={ICON_SIZE.md} filled={pinned} className={pinned ? 'text-ink' : 'text-ink-3'} />
          </IconButton>
        )}
      </div>
      {tab === 'workspace' ? (
        <WorkspaceList />
      ) : tab === 'canvases' ? (
        <CanvasList />
      ) : tab === 'assets' ? (
        <AssetBrowser />
      ) : tab === 'layers' ? (
        <LayerTree />
      ) : tab === 'problems' ? (
        <ProblemPanel />
      ) : (
        <ElementTree />
      )}
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
  // 可聚焦的 separator 是个真控件：必须报出当前值与值域（axe critical）
  const width = useUiStore((s) => s.leftWidth)
  const start = (e: React.PointerEvent) => {
    e.preventDefault()
    const from = useUiStore.getState().leftWidth
    const x0 = e.clientX
    const move = (ev: PointerEvent) => useUiStore.getState().setLeftWidth(from + ev.clientX - x0)
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
      aria-label={t('drawer.resize', { min: LEFT_MIN, max: LEFT_MAX })}
      aria-valuenow={Math.round(width)}
      aria-valuemin={LEFT_MIN}
      aria-valuemax={LEFT_MAX}
      tabIndex={0}
      onPointerDown={start}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
        e.preventDefault()
        const ui = useUiStore.getState()
        ui.setLeftWidth(ui.leftWidth + (e.key === 'ArrowRight' ? 16 : -16))
      }}
      // 整条都在抽屉内侧：外层 overflow-hidden（开合动效要用）会把伸到外面的部分剪掉。
      // hover 只把边界加深一档：accent 小面积只给焦点 / 链接 / AI / 选择框，一条 8px 的
      // 蓝带不在其中（宪法第一节；左栏审计 L38），键盘聚焦时才用 accent
      className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize outline-none hover:bg-border-strong focus-visible:bg-accent/30"
    />
  )
}
