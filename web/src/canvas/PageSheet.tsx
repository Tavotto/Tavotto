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

/**
 * 白色纸面：画布的视觉中心。**没有投影**——持久表面不用投影（宪法第一节），
 * 只有一圈 1px 的 border-strong/60 轮廓把纸从画布灰上托出来。
 */
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
      // 纸面是纯装饰（没有语义角色可以套），但"这一屏上有没有纸"正是
      // 快速编辑与画布排版的可见差别——留一个测试落点，与 CanvasStage 的
      // `data-canvas-stage` 同一条理由
      data-page-sheet=""
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
          // 画布层的彩色线只有 --color-sel 一种（2026-09-15 打磨 C2）：安全区与选框同色
          className="pointer-events-none absolute border border-dashed border-sel/45"
          style={{
            inset: mmToWorld(margin),
            borderWidth: hair,
          }}
        />
      )}
    </div>
  )
}
