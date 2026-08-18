import { Pin } from 'lucide-react'
import { drawerMotion, type PresenceState } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { LEFT_MAX, LEFT_MIN, RAIL_W, useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Tip } from '../ui/Tooltip'
import { AssetBrowser } from './AssetBrowser'
import { CanvasList } from './CanvasList'
import { ElementTree } from './ElementTree'
import { LayerTree } from './LayerTree'

const TITLES = {
  canvases: '画布',
  assets: '素材',
  layers: '结构',
  elements: '图内元素',
} as const

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
      aria-label={TITLES[tab]}
      className={cn(
        // overflow-hidden 是动效的一部分：停靠态动的是外层 width，内容包在下面
        // 那层定宽 div 里，所以展开收起时抽屉自己的子树一次都不重排
        'relative shrink-0 overflow-hidden border-r border-border bg-surface',
        overlay && 'absolute inset-y-0 z-30 shadow-pop',
        motion.className,
      )}
    >
      <div className="flex h-full flex-col" style={{ width }}>
      <div className="flex h-9 shrink-0 items-center gap-1.5 px-3">
        <h2 className="text-xs font-medium text-ink">{TITLES[tab]}</h2>
        {tab === 'layers' && objectCount > 0 && (
          <span className="font-mono text-xs text-ink-3">{objectCount}</span>
        )}
        {tab === 'elements' && <ElementCount />}
        <span className="flex-1" />
        {wide && (
          <Tip label={pinned ? '取消钉住' : '钉住：选中对象时不自动收起'} side="bottom">
            <Button
              size="icon-sm"
              active={pinned}
              aria-pressed={pinned}
              aria-label={pinned ? '取消钉住侧栏' : '钉住侧栏'}
              onClick={() => useUiStore.getState().setLeftPinned(!pinned)}
            >
              <Pin size={12} className={pinned ? undefined : 'text-ink-3'} />
            </Button>
          </Tip>
        )}
      </div>
      {tab === 'canvases' ? (
        <CanvasList />
      ) : tab === 'assets' ? (
        <AssetBrowser />
      ) : tab === 'layers' ? (
        <LayerTree />
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
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedIds = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  const byId = (id: string | null) => {
    const o = id ? objects.find((x) => x.id === id) : undefined
    return o?.type === 'panel' && o.script ? o : null
  }
  const panel = byId(elementPanelId) ?? byId(selectedIds.at(-1) ?? null)
  const n = usePanelManifest(panel)?.elements.length ?? 0
  if (!n) return null
  return <span className="font-mono text-xs text-ink-3">{n - 1}</span>
}

/** 右边缘的拖拽把手：卡片网格的列宽由它决定，所以宽度值得可调且记住 */
function WidthHandle() {
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
      aria-label={`调整侧栏宽度（${LEFT_MIN}–${LEFT_MAX}px）`}
      tabIndex={0}
      onPointerDown={start}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
        e.preventDefault()
        const ui = useUiStore.getState()
        ui.setLeftWidth(ui.leftWidth + (e.key === 'ArrowRight' ? 16 : -16))
      }}
      // 整条都在抽屉内侧：外层 overflow-hidden（开合动效要用）会把伸到外面的部分剪掉
      className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize outline-none hover:bg-accent/20 focus-visible:bg-accent/30"
    />
  )
}
