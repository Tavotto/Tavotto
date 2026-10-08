import { perfCount } from '@/perf/core'
import { mmToWorld } from '@/store/viewportStore'

/** 纸的默认底色：**页面内容**（导出时就是这个白），不是界面的 surface——`--color-paper` 两套主题同值 */
const PAPER = 'var(--color-paper)'
/**
 * 透明页面棋盘格的深格：**不透明**、从纸派生（`--color-paper-checker` = paper-ink 6% 混进纸）。不能是
 * `GRID_INK(6)`——那是半透明的，页面这一层自己就是底，格子会透出下面的画布色：暗色里画布近黑，
 * 棋盘格就成了黑白相间、盖过透明图的内容（Codex P2，2026-10-07）。
 */
const CHECKER = 'var(--color-paper-checker)'
/**
 * 网格线的墨：纸上的墨（`--color-paper-ink`）的 N%。不是 `--color-ink`——那是界面的字色，
 * 暗色里是浅色，画在不变的白纸上就看不见了（2026-10-07 暗色主题）。
 */
const GRID_INK = (pct: number) => `color-mix(in srgb, var(--color-paper-ink) ${pct}%, transparent)`

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
 * 白色纸面：画布的视觉中心。**没有投影**——持久表面不用投影（宪法第一节）。
 *
 * **也不画轮廓**（2026-10-07 设计审计 §10.1）：页面那一圈 1px 由 `PageOutsideMask` 在屏幕空间画（压在内容之上、
 * 任何缩放下都是 1px）。此前这里在世界层里再画一圈 outline：它跟着缩放变粗（400% 时 4px），而且与
 * 遮罩那一圈画了两遍。纸本身是真白（页面内容，不是界面色）；网格线是 paper-ink 的半透明 color-mix（压在不透明的纸 / 棋盘格上）；
 * 透明棋盘格本身是不透明的 `--color-paper-checker`——页面这一层是最底下的纸，不能透出画布。
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
  perfCount('render.PageSheet')
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
      className="absolute left-0 top-0"
      style={{
        width: wPx,
        height: hPx,
        // 透明背景用棋盘格表示「导出时这里没有底色」
        background: transparent
          ? `repeating-conic-gradient(${CHECKER} 0% 25%, ${PAPER} 0% 50%) 0 0 / 12px 12px`
          : (bg ?? PAPER),
      }}
    >
      {showGrid && (
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage: `linear-gradient(to right, ${GRID_INK(7)} ${hair}px, transparent ${hair}px),
                              linear-gradient(to bottom, ${GRID_INK(7)} ${hair}px, transparent ${hair}px)`,
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
