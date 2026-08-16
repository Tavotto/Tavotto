import { mmToWorld } from '@/store/viewportStore'

interface PageSheetProps {
  w: number
  h: number
  zoom: number
  showGrid: boolean
  gridSize: number
  bg?: string
  transparent?: boolean
  /** 安全区域页边距（mm）；>0 且 showSafeArea 时画一圈虚线 */
  margin?: number
  showSafeArea?: boolean
}

/** 白色纸面：唯一带 shadow 的常驻元素，画布的视觉中心 */
export function PageSheet({
  w,
  h,
  zoom,
  showGrid,
  gridSize,
  bg,
  transparent,
  margin = 0,
  showSafeArea,
}: PageSheetProps) {
  const wPx = mmToWorld(w)
  const hPx = mmToWorld(h)
  const cell = mmToWorld(Math.max(gridSize, 0.5))
  // 世界层被整体缩放，网格线要除以 zoom 才能保持 1px 观感
  const hair = 1 / zoom

  return (
    <div
      className="absolute left-0 top-0 outline outline-1 outline-border-strong/60"
      style={{
        width: wPx,
        height: hPx,
        // 透明背景用棋盘格表示「导出时这里没有底色」
        background: transparent
          ? 'repeating-conic-gradient(rgba(27,27,24,.06) 0% 25%, #fff 0% 50%) 0 0 / 12px 12px'
          : (bg ?? '#ffffff'),
      }}
    >
      {showGrid && (
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage: `linear-gradient(to right, rgba(27,27,24,.07) ${hair}px, transparent ${hair}px),
                              linear-gradient(to bottom, rgba(27,27,24,.07) ${hair}px, transparent ${hair}px)`,
            backgroundSize: `${cell}px ${cell}px`,
          }}
        />
      )}
      {showSafeArea && margin > 0 && (
        <div
          className="pointer-events-none absolute border border-dashed border-accent/45"
          style={{
            inset: mmToWorld(margin),
            borderWidth: hair,
          }}
        />
      )}
    </div>
  )
}
