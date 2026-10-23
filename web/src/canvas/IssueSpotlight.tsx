import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { useUiStore } from '@/store/uiStore'

/** 舞台灯整段多长（ms）：光束落下 · 停一秒 · 散开。与 `index.css` 的 `--animate-spotlight-*` 同长 */
export const SPOTLIGHT_MS = 2200

type Spot = { cx: number; cy: number; r: number; W: number; H: number }

/**
 * 光圈半径（px）：把目标框整个包进去再留一圈余量。光的衰减从半径的四成起一直拖到半径外，
 * 目标落在完全亮的那一段里，往外慢慢没入暗处——没有边。
 */
const radius = (w: number, h: number) => Math.hypot(w, h) / 2 + 36

/**
 * 定位之后的「舞台灯」（2026-10-23 用户提出，参照 iMessage 的「聚光灯」效果）：问题面板点「定位」，
 * 画布整体暗下去、轻微失焦，一束柔光从斜上方落到目标上、收拢；停一秒后光散开、画面复原。不画任何描边。
 *
 * 两层，各管各的：
 * - **景深**（`data-spotlight-blur`）：光外轻微高斯模糊。动画只在带 `backdrop-filter` 的那一层自己身上
 *   （关键帧直接动 blur）——祖先上只要有 opacity / transform / mask，背后的画布就取不到，模糊整个失效；
 * - **光束**（`data-spotlight-beam`）：一个以光圈为中心、远大于画布的径向渐变（中心透明带一点暖光，
 *   向外压到近黑）。落下 = 从斜上方、放大着收拢到目标；散开 = 原地放大淡出。它不带 backdrop-filter，
 *   动 transform / opacity 不影响模糊层。
 *
 * **光圈不自己算几何**：读 `OverlaySvg` 里那个定位高亮框（`[data-issue-highlight]`，图内元素时由
 * `IssueOverlay.IssueElementHighlight` 按与悬停轮廓同一份换算画；动效开着时不可见，只当锚点）的屏幕
 * 外接框，视口正平移到目标（`revealRect`）的那几帧每帧跟着走。
 *
 * 只动画面、不吃指针，不碰文档 / 选区 / 历史；挂在选框覆盖层之下，选择框始终清晰。
 * reduced motion 下不放（全局把动画压到 0.01 ms，`both` 停在末帧 = 原样），由高亮框代替。
 */
export function IssueSpotlight() {
  const hl = useUiStore((s) => s.issueHighlight)
  const host = useRef<HTMLDivElement>(null)
  const [spot, setSpot] = useState<Spot | null>(null)

  useEffect(() => {
    setSpot(null)
    if (!hl?.objectId) return
    const t0 = performance.now()
    let raf = 0
    let last = ''
    const tick = () => {
      const box = host.current?.getBoundingClientRect()
      const target = document.querySelector('[data-issue-highlight]')?.getBoundingClientRect()
      if (box && target) {
        const next: Spot = {
          cx: Math.round(target.left - box.left + target.width / 2),
          cy: Math.round(target.top - box.top + target.height / 2),
          r: Math.round(radius(target.width, target.height)),
          W: Math.round(box.width),
          H: Math.round(box.height),
        }
        const key = `${next.cx},${next.cy},${next.r},${next.W},${next.H}`
        if (key !== last) {
          last = key
          setSpot(next)
        }
      }
      if (performance.now() - t0 < SPOTLIGHT_MS) raf = requestAnimationFrame(tick)
      else setSpot(null)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [hl?.token, hl?.objectId])

  if (!spot || !hl) return <div ref={host} className="pointer-events-none absolute inset-0" />
  const at = `circle ${spot.r}px at ${spot.cx}px ${spot.cy}px`
  // 光束层的边长：从光圈中心到画布最远角的两倍再放大，落下前放大 1.8 倍时也盖得住整块画布
  const side = 2 * Math.hypot(Math.max(spot.cx, spot.W - spot.cx), Math.max(spot.cy, spot.H - spot.cy)) + 4 * spot.r
  return (
    <div ref={host} data-issue-spotlight className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* 景深：光外轻微失焦（柔边遮罩：光圈里清晰） */}
      <div
        key={`blur-${hl.token}`}
        data-spotlight-blur
        className="absolute inset-0 animate-spotlight-blur"
        style={{
          maskImage: `radial-gradient(${at}, transparent 45%, black 130%)`,
          WebkitMaskImage: `radial-gradient(${at}, transparent 45%, black 130%)`,
        }}
      />
      {/* 光束：中心一点暖光、向外渐渐压到近黑；从斜上方落下收拢，最后原地散开 */}
      <div
        key={`beam-${hl.token}`}
        data-spotlight-beam
        className="absolute animate-spotlight-beam bg-spotlight-beam"
        style={
          {
            left: spot.cx - side / 2,
            top: spot.cy - side / 2,
            width: side,
            height: side,
            '--spot-r': `${spot.r}px`,
          } as CSSProperties
        }
      />
    </div>
  )
}
