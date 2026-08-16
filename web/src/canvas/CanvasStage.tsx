import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Images } from 'lucide-react'
import { EmptyState } from '@/components/ui/EmptyState'
import { useAssetStore } from '@/store/assetStore'
import { addPanel } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, mmToWorld, useViewportStore } from '@/store/viewportStore'
import { shouldFitOnDoubleClick } from '@/lib/fitGuard'
import { ObjectView } from './ObjectView'
import { OverlaySvg } from './OverlaySvg'
import { PageSheet } from './PageSheet'
import { QuickEdit } from './QuickEdit'
import { Rulers, RULER_SIZE } from './Rulers'
import { startDraw, startMarquee, startPan } from './interactions'

export function CanvasStage() {
  const outerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<HTMLDivElement>(null)
  const [outer, setOuter] = useState({ w: 0, h: 0 })

  const showRulers = useUiStore((s) => s.showRulers)
  const showGrid = useUiStore((s) => s.showGrid)
  const gridSize = useUiStore((s) => s.gridSize)
  const showSafeArea = useUiStore((s) => s.showSafeArea)
  const tool = useUiStore((s) => s.tool)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  const spaceDown = useViewportStore((s) => s.spaceDown)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const setViewRect = useViewportStore((s) => s.setViewRect)
  const page = useDocumentStore((s) => s.doc.page)
  const objects = useDocumentStore((s) => s.doc.objects)
  const dragging = useInteractionStore((s) => s.kind !== 'none')

  const pad = showRulers ? RULER_SIZE : 0

  // 视口尺寸 / 位置上报：面板折叠、窗口缩放都会触发
  useLayoutEffect(() => {
    const el = outerRef.current
    const view = viewRef.current
    if (!el || !view) return
    const sync = () => {
      const r = el.getBoundingClientRect()
      setOuter({ w: r.width, h: r.height })
      const v = view.getBoundingClientRect()
      setViewRect({ left: v.left, top: v.top, width: v.width, height: v.height })
    }
    sync()
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    window.addEventListener('resize', sync)
    window.addEventListener('scroll', sync, true)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', sync)
      window.removeEventListener('scroll', sync, true)
    }
  }, [setViewRect, pad])

  // 首次拿到尺寸后自动适应页面
  const fittedRef = useRef(false)
  useEffect(() => {
    if (fittedRef.current) return
    const { viewW, viewH, fit } = useViewportStore.getState()
    if (viewW && viewH) {
      fit(page.w, page.h)
      fittedRef.current = true
    }
  }, [outer.w, outer.h, page.w, page.h])

  // React 的 onWheel 是被动监听，缩放必须手动挂非被动监听器
  useEffect(() => {
    const el = viewRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const vp = useViewportStore.getState()
      const ax = e.clientX - vp.originX
      const ay = e.clientY - vp.originY
      if (e.ctrlKey || e.metaKey) {
        // 0.0022 让鼠标滚轮一格约 1.3×，触控板捏合（deltaY 很小）也保持连续
        vp.zoomAt(Math.exp(-e.deltaY * 0.0022), ax, ay)
      } else {
        vp.setPan(vp.panX - e.deltaX, vp.panY - e.deltaY)
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button === 1 || (e.button === 0 && spaceDown)) {
      e.preventDefault()
      startPan(e)
      return
    }
    if (e.button !== 0) return
    // 点空白处：退出文字编辑 / 裁剪模式
    if (useUiStore.getState().editingTextId) useUiStore.getState().setEditingText(null)
    if (cropTargetId) {
      useUiStore.getState().setCropTarget(null)
      return
    }
    if (tool !== 'select') {
      startDraw(e, tool)
      return
    }
    startMarquee(e)
  }

  // 光标 mm 坐标：按帧节流，避免状态栏拖慢拖动
  const rafRef = useRef(0)
  const onPointerMove = (e: React.PointerEvent) => {
    const { clientX, clientY } = e
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0
      useInteractionStore.getState().setCursor(clientToMm(clientX, clientY))
    })
  }

  // 双击画布外侧灰色工作区 → 适应当前画布（对象/页面内容/各种编辑态不误触）
  const onDoubleClick = (e: React.MouseEvent) => {
    const ui = useUiStore.getState()
    const ok = shouldFitOnDoubleClick({
      tool,
      spaceDown,
      editingText: !!ui.editingTextId,
      cropping: !!ui.cropTargetId,
      interacting: useInteractionStore.getState().kind !== 'none',
      onObject: !!(e.target as HTMLElement).closest('[data-object-id]'),
      point: clientToMm(e.clientX, e.clientY),
      page: { w: page.w, h: page.h },
    })
    if (ok) useViewportStore.getState().fitAnimated(page.w, page.h)
  }

  const cursor = spaceDown
    ? dragging
      ? 'grabbing'
      : 'grab'
    : tool !== 'select'
      ? 'crosshair'
      : 'default'

  return (
    <div ref={outerRef} className="relative min-h-0 min-w-0 flex-1 overflow-hidden bg-canvas">
      {showRulers && outer.w > 0 && (
        <Rulers viewW={Math.max(outer.w - pad, 0)} viewH={Math.max(outer.h - pad, 0)} />
      )}
      <div
        ref={viewRef}
        className="absolute overflow-hidden bg-canvas"
        style={{ left: pad, top: pad, right: 0, bottom: 0, cursor }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onDoubleClick={onDoubleClick}
        onPointerLeave={() => useInteractionStore.getState().setCursor(null)}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes('application/x-panel-id')) {
            e.preventDefault()
            e.dataTransfer.dropEffect = 'copy'
          }
        }}
        onDrop={(e) => {
          const pid = e.dataTransfer.getData('application/x-panel-id')
          if (!pid) return
          e.preventDefault()
          const info = useAssetStore.getState().byId[pid]
          if (!info) return
          const p = clientToMm(e.clientX, e.clientY)
          addPanel(info, p.x, p.y)
        }}
      >
        {/* 唯一的世界变换 */}
        <div
          className="absolute left-0 top-0 origin-top-left"
          style={{
            transform: `translate(${panX}px, ${panY}px) scale(${zoom})`,
            width: mmToWorld(page.w),
            height: mmToWorld(page.h),
            pointerEvents: spaceDown || tool !== 'select' ? 'none' : 'auto',
          }}
        >
          <PageSheet
            w={page.w}
            h={page.h}
            zoom={zoom}
            showGrid={showGrid}
            gridSize={gridSize}
            bg={page.bg}
            transparent={page.transparent}
            margin={page.margin}
            showSafeArea={showSafeArea}
          />
          {objects.map((o) => (
            <ObjectView key={o.id} obj={o} />
          ))}
        </div>

        <OverlaySvg />

        {objects.length === 0 && <EmptyHint />}
      </div>

      <ElementEditBar />

      {/* 右键快捷编辑：自己 portal 到 body，不受世界变换影响 */}
      <QuickEdit />
    </div>
  )
}

/**
 * 图内编辑态的浮动出口：Esc 之外的显式按钮。退出时顺手选中该面板，
 * 属性页落在面板上，「写回原始文件」就在手边。
 */
function ElementEditBar() {
  const panelId = useUiStore((s) => s.elementPanelId)
  const name = useDocumentStore((s) => {
    const o = s.doc.objects.find((x) => x.id === panelId)
    return o?.type === 'panel' ? (o.name ?? o.fileId) : null
  })
  if (!panelId) return null

  const exit = () => {
    useUiStore.getState().setElementPanel(null)
    useSelectionStore.getState().set([panelId])
  }

  return (
    <div className="absolute left-1/2 top-2 z-30 -translate-x-1/2">
      <div className="flex h-7 items-center gap-2 rounded-md border border-border bg-surface pl-2.5 pr-1 shadow-pop">
        <span className="max-w-64 truncate text-xs text-ink-2">
          图内编辑{name ? <span className="text-ink">：{name}</span> : null}
        </span>
        <button
          onClick={exit}
          title="退出图内编辑，回到画布层（Esc）"
          className="flex h-5 items-center gap-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink transition-colors hover:bg-ink/[.055]"
        >
          返回画布
          <span className="font-mono text-xs text-ink-3">Esc</span>
        </button>
      </div>
    </div>
  )
}

function EmptyHint() {
  const setLeftTab = useUiStore((s) => s.setLeftTab)
  const selectionEmpty = useSelectionStore((s) => s.ids.length === 0)
  if (!selectionEmpty) return null
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
      <div className="pointer-events-auto">
        <EmptyState
          icon={Images}
          title="画布是空的"
          hint="从素材库拖入面板，或双击列表项加入画布。"
          action={{ label: '打开素材库', onClick: () => setLeftTab('assets') }}
        />
      </div>
    </div>
  )
}
