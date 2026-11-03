/**
 * 导出预览的缩放舞台：内容按比例 contain 铺满舞台（上下或左右顶满、不留边距），滚轮 / 触控板捏合以指针为中心缩放，
 * 放大后可拖动平移，双击或右下角小按钮回到适合大小。
 *
 * 缩放是 CSS transform（视图状态，不进文档、不进撤销、不发后端）。位图内容要跟着放大变清晰：舞台把
 * 「当前显示宽度 × 缩放 × 设备像素比」向上取档后交给 `children(px)`，调用方据此换 `/api/render` 的更高档
 * （`lib/previewBuckets`）。`px` 只增不减（缩回去不再回退到低档，省一次重取和一次闪动）；舞台还没量到尺寸
 * （测不到 ResizeObserver 的环境）时 `px` 是 0，调用方用自己的默认档。
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { IconButton } from './ui/Button'
import { ICON_SIZE } from './ui/Icon'
import { Maximize2 } from './ui/icons'
import { bucketFor } from '@/lib/previewBuckets'
import { t as translate } from '@/i18n'

const MAX_SCALE = 12
/** 滚轮每个像素的 delta 折成多少指数缩放；捏合（ctrlKey）事件的 delta 小得多，系数大一档 */
const WHEEL_STEP = 0.0015
const PINCH_STEP = 0.01

interface View {
  s: number
  x: number
  y: number
}
const FIT: View = { s: 1, x: 0, y: 0 }

/** 内容在舞台里 contain 之后的像素尺寸；舞台尺寸未知时返回 null */
function fitSize(box: { w: number; h: number }, ratio: number) {
  if (box.w <= 0 || box.h <= 0 || !(ratio > 0)) return null
  return box.w / box.h <= ratio ? { w: box.w, h: box.w / ratio } : { w: box.h * ratio, h: box.h }
}

/** 平移夹在「内容盖住舞台」的范围内；某一轴上内容比舞台小就固定居中 */
function clampView(v: View, box: { w: number; h: number }, fit: { w: number; h: number } | null): View {
  const s = Math.min(MAX_SCALE, Math.max(1, v.s))
  if (!fit || s === 1) return s === 1 ? FIT : { ...v, s }
  const mx = Math.max(0, (fit.w * s - box.w) / 2)
  const my = Math.max(0, (fit.h * s - box.h) / 2)
  return { s, x: Math.min(mx, Math.max(-mx, v.x)), y: Math.min(my, Math.max(-my, v.y)) }
}

export function ExportZoomStage({
  ratio,
  children,
}: {
  /** 内容宽高比 */
  ratio: number
  children: (px: number) => ReactNode
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  const [view, setView] = useState<View>(FIT)
  const [peak, setPeak] = useState(0)
  const sizeRef = useRef({ box, ratio })
  sizeRef.current = { box, ratio }
  const drag = useRef<{ id: number; px: number; py: number; vx: number; vy: number } | null>(null)

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight })
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 滚轮必须是非被动监听才能 preventDefault（React 的 onWheel 是被动的）
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const qx = e.clientX - rect.left - rect.width / 2
      const qy = e.clientY - rect.top - rect.height / 2
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? rect.height : 1
      const k = Math.exp(-e.deltaY * unit * (e.ctrlKey ? PINCH_STEP : WHEEL_STEP))
      setView((v) => {
        const { box: b, ratio: r } = sizeRef.current
        const s = Math.min(MAX_SCALE, Math.max(1, v.s * k))
        const f = s / v.s
        return clampView({ s, x: qx - (qx - v.x) * f, y: qy - (qy - v.y) * f }, b, fitSize(b, r))
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const fit = fitSize(box, ratio)
  const dpr = typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1
  const need = fit ? bucketFor(fit.w * view.s * dpr) : 0
  useEffect(() => {
    if (need > peak) setPeak(need)
  }, [need, peak])
  const px = Math.max(need, peak)

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0 || view.s <= 1) return
      e.currentTarget.setPointerCapture(e.pointerId)
      drag.current = { id: e.pointerId, px: e.clientX, py: e.clientY, vx: view.x, vy: view.y }
    },
    [view],
  )
  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    const { box: b, ratio: r } = sizeRef.current
    setView((v) => clampView({ s: v.s, x: d.vx + e.clientX - d.px, y: d.vy + e.clientY - d.py }, b, fitSize(b, r)))
  }, [])
  const endDrag = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id === e.pointerId) drag.current = null
  }, [])

  const zoomed = view.s > 1.001
  const contentStyle: CSSProperties = {
    ...(fit
      ? { width: fit.w, height: fit.h }
      : { aspectRatio: String(ratio), width: '100%', maxHeight: '100%' }),
    transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})`,
  }
  return (
    <div
      ref={boxRef}
      data-export-preview-zoom={String(+view.s.toFixed(4))}
      className="absolute inset-0 flex touch-none select-none items-center justify-center overflow-hidden"
      style={{ cursor: zoomed ? (drag.current ? 'grabbing' : 'grab') : undefined }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={() => setView(FIT)}
    >
      <div
        data-export-preview-content
        // 内缩的发丝边（不占位、不留边距）：页面白与舞台灰很接近，没有它看不出页面边界
        className="shrink-0 outline outline-1 -outline-offset-1 outline-border"
        style={contentStyle}
      >
        {children(fit ? px : 0)}
      </div>
      {zoomed && (
        <IconButton
          data-export-preview-reset
          label={translate('export.previewFit', { ns: 'dialogs' })}
          className="absolute bottom-2 right-2 bg-surface/80 backdrop-blur"
          onPointerDown={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
          onClick={() => setView(FIT)}
        >
          <Maximize2 size={ICON_SIZE.sm} />
        </IconButton>
      )}
    </div>
  )
}
