import { useEffect, useId, useRef, useState, type CSSProperties } from 'react'
import { useUiStore } from '@/store/uiStore'

/** 舞台灯整段多长（ms）：光打过来 ≈0.3 s · 停 ≈0.4 s · 散开 ≈0.3 s（用户：整体一秒）。与 `index.css` 的 `--animate-spotlight-*` 同长 */
export const SPOTLIGHT_MS = 1000

/** 光柱的颜色：语义 token（界面代码不写颜色字面量） */
const LIGHT = 'var(--color-spotlight-light)'

type Spot = { cx: number; cy: number; r: number; W: number; H: number }

/**
 * 光圈半径（px）：把目标框整个包进去再留一圈余量。衰减从半径的七成起、一条曲线拖到 1.9 倍半径，
 * 目标落在完全亮的那一段里，往外慢慢没入暗处——没有边、只有一层。
 */
const radius = (w: number, h: number) => Math.hypot(w, h) / 2 + 36

/** 光柱从哪儿打过来：离光心最近的画布角，再往外推一点（光源在画外） */
function sourceOf(s: Spot): { x: number; y: number } {
  const corners = [
    { x: 0, y: 0 },
    { x: s.W, y: 0 },
    { x: 0, y: s.H },
    { x: s.W, y: s.H },
  ]
  const near = corners.reduce((a, b) => (Math.hypot(b.x - s.cx, b.y - s.cy) < Math.hypot(a.x - s.cx, a.y - s.cy) ? b : a))
  const dx = near.x - s.cx
  const dy = near.y - s.cy
  const len = Math.hypot(dx, dy) || 1
  return { x: near.x + (dx / len) * 40, y: near.y + (dy / len) * 40 }
}

/**
 * 定位之后的「舞台灯」（2026-10-23 用户提出，参照 iMessage 的「聚光灯」效果）：问题面板点「定位」，
 * 一道光柱从离目标最近的画布角斜射过来，画布整体暗下去、轻微失焦，目标落在一个光圈里（外缘一圈很淡的光晕）；
 * 一秒内打过来、停住、散开。不画任何描边。
 *
 * 三层，各管各的：
 * - **景深**（`data-spotlight-blur`）：光外轻微高斯模糊，遮罩与光圈同一条衰减曲线（只有一层光圈）。
 *   动画只在带 `backdrop-filter` 的那一层自己身上（关键帧直接动 blur）——祖先上只要有 opacity / transform /
 *   mask，背后的画布就取不到，模糊整个失效；
 * - **光圈**（`data-spotlight-beam`）：以光心为中心、远大于画布的径向渐变（光圈里全透、往外一条 smoothstep
 *   曲线压到近黑，外缘叠一圈很淡的光晕）。打过来 = 从光源方向放大着收拢到目标；散开 = 原地放大淡出；
 * - **光柱**（`data-spotlight-shaft`）：从光源到光圈的一道柔边光锥（SVG，高斯模糊），两条边与光圈相切、
 *   越近目标越亮、一路接进光圈；从光源一端「射」出去。目标离光源太近（光源落在光圈里）时不画光柱。
 *
 * **光心不自己算几何**：读 `OverlaySvg` 里那个定位高亮框（`[data-issue-highlight]`，图内元素时由
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
  const uid = useId().replace(/:/g, '')

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
  const src = sourceOf(spot)
  const dist = Math.hypot(src.x - spot.cx, src.y - spot.cy) || 1
  const ux = (src.x - spot.cx) / dist
  const uy = (src.y - spot.cy) / dist
  // 两层同一块方形：以光心为中心，边长 = 光心到画布最远角的两倍再放大（打过来前放大时也盖得住）
  const side = 2 * Math.hypot(Math.max(spot.cx, spot.W - spot.cx), Math.max(spot.cy, spot.H - spot.cy)) + 4 * spot.r
  const geometry = {
    left: spot.cx - side / 2,
    top: spot.cy - side / 2,
    width: side,
    height: side,
    '--spot-r': `${spot.r}px`,
    // 光圈从光源那一侧收拢过来
    '--spot-from-x': `${Math.round(ux * spot.r * 1.2)}px`,
    '--spot-from-y': `${Math.round(uy * spot.r * 1.2)}px`,
  } as CSSProperties
  // 光柱：一束光锥，两条边与光圈**相切**（光圈可见亮区半径 ≈ 1.25r）——锥在切点处与光圈一样宽、顺着
  // 光圈的轮廓收进去，没有「细棍顶个球」的收腰（用户：顶部像棒棒糖）。光源一端也留一点宽度（灯口，不是针尖）
  const px = -uy
  const py = ux
  const lit = spot.r * 1.25
  const cos = Math.min(1, lit / dist)
  const sin = Math.sqrt(1 - cos * cos)
  const mouth = spot.r * 0.3
  const tangent = (side: 1 | -1) => [
    spot.cx + lit * (cos * ux + side * sin * px),
    spot.cy + lit * (cos * uy + side * sin * py),
  ]
  const cone = [[src.x + px * mouth, src.y + py * mouth], tangent(1), tangent(-1), [src.x - px * mouth, src.y - py * mouth]]
    .map(([x, y]) => `${Math.round(x)},${Math.round(y)}`)
    .join(' ')
  // 渐变从灯口一路渐强到切点那条弦，接进光圈，不先淡掉
  const chord = Math.max(0, Math.min(1, (dist - lit * cos) / dist))
  const showShaft = dist > lit * 1.4

  return (
    <div ref={host} data-issue-spotlight className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* 景深：光外轻微失焦。遮罩与光圈同一条衰减曲线（`mask-spotlight`），只有一层光圈 */}
      <div
        key={`blur-${hl.token}`}
        data-spotlight-blur
        className="absolute animate-spotlight-blur mask-spotlight"
        style={geometry}
      />
      {/* 光圈：光圈里全透、往外一条 smoothstep 曲线压到近黑，外缘一圈很淡的光晕 */}
      <div
        key={`beam-${hl.token}`}
        data-spotlight-beam
        className="absolute animate-spotlight-beam bg-spotlight-beam"
        style={geometry}
      />
      {/* 光柱：从光源射向光圈，柔边、越近越亮 */}
      <svg
        key={`shaft-${hl.token}`}
        data-spotlight-shaft
        className="absolute inset-0 animate-spotlight-shaft"
        width={spot.W}
        height={spot.H}
        style={{ transformOrigin: `${Math.round(src.x)}px ${Math.round(src.y)}px` }}
      >
        <defs>
          <linearGradient
            id={`shaft-fill-${uid}`}
            gradientUnits="userSpaceOnUse"
            x1={src.x}
            y1={src.y}
            x2={spot.cx}
            y2={spot.cy}
          >
            <stop offset="0" style={{ stopColor: LIGHT }} stopOpacity="0.03" />
            <stop offset={chord * 0.6} style={{ stopColor: LIGHT }} stopOpacity="0.09" />
            <stop offset={chord} style={{ stopColor: LIGHT }} stopOpacity="0.16" />
            <stop offset="1" style={{ stopColor: LIGHT }} stopOpacity="0.16" />
          </linearGradient>
          <filter id={`shaft-soft-${uid}`} x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation={Math.max(6, Math.round(spot.r * 0.22))} />
          </filter>
        </defs>
        {showShaft && <polygon points={cone} fill={`url(#shaft-fill-${uid})`} filter={`url(#shaft-soft-${uid})`} />}
      </svg>
    </div>
  )
}
