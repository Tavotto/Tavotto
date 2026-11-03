import { useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { formatMessage } from '@/i18n'
import { cn } from '@/lib/utils'
import { useInteractionStore } from '@/store/interactionStore'
import { useNoticeRailStore } from '@/store/noticeRailStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore, type ViewTransform } from '@/store/viewportStore'
import { bottomSafeFor, placeStatus, selectionScreenRect, sidebarInsets, type Placement } from './position'

/**
 * 拖动中的状态胶囊（ADR 0161）：读 `interaction.dragStatus`，**只读不判**——语气、句子、框都由手势算好。
 * 不受 `ContextBar` 的"拖动即藏"规则影响（它是另一只胶囊）；`dragStatus == null` 时什么都不画。
 * 选择器：`data-drag-status`，语气 `data-drag-tone`（normal / warn / danger）。
 */
export function DragStatusBar() {
  useTranslation('workspace')
  const status = useInteractionStore((s) => s.dragStatus)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const originX = useViewportStore((s) => s.originX)
  const originY = useViewportStore((s) => s.originY)
  const layout = useUiStore((s) => s.layout)
  const leftOpen = useUiStore((s) => s.leftOpen)
  const leftWidth = useUiStore((s) => s.leftWidth)
  const rightOpen = useUiStore((s) => s.rightOpen)
  const rightWidth = useUiStore((s) => s.rightWidth)
  const noticeTop = useNoticeRailStore((s) => s.top)
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<Placement | null>(null)

  useLayoutEffect(() => {
    if (!status) {
      setPos(null)
      return
    }
    const t: ViewTransform = { zoom, panX, panY, originX, originY }
    const anchor = selectionScreenRect(status.box, t)
    const size = { w: ref.current?.offsetWidth ?? 240, h: ref.current?.offsetHeight ?? 36 }
    const viewport = { width: window.innerWidth, height: window.innerHeight }
    const insets = sidebarInsets({ layout, leftOpen, leftWidth, rightOpen, rightWidth })
    const next = placeStatus(anchor, size, viewport, insets, bottomSafeFor(noticeTop, viewport.height))
    setPos((prev) => (prev && prev.x === next.x && prev.y === next.y ? prev : next))
  }, [status, zoom, panX, panY, originX, originY, layout, leftOpen, leftWidth, rightOpen, rightWidth, noticeTop])

  if (!status) return null
  const main = formatMessage(status.main)
  const detail = status.detail ? formatMessage(status.detail) : ''
  return createPortal(
    <div
      ref={ref}
      data-drag-status=""
      data-drag-tone={status.tone}
      data-placement={pos?.placement}
      style={pos ? { left: pos.x, top: pos.y } : { left: -9999, top: -9999 }}
      className={cn(
        // 只读读数：不吃指针（不挡住正在拖的手柄）
        'pointer-events-none fixed z-overlay flex w-max items-center gap-1.5 rounded-full bg-surface px-3 py-2 text-sm shadow-pop',
        pos ? 'animate-pop-in' : 'invisible',
      )}
    >
      <span
        className={cn(
          'whitespace-nowrap',
          status.tone === 'normal' && 'text-ink',
          status.tone === 'warn' && 'text-warn-content',
          status.tone === 'danger' && 'text-danger-content',
        )}
      >
        {main}
      </span>
      {detail && <span className="whitespace-nowrap text-ink-3">· {detail}</span>}
    </div>,
    document.body,
  )
}
