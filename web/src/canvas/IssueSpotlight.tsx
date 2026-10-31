import { useEffect, useId, useRef, useState, type CSSProperties } from 'react'
import { useUiStore } from '@/store/uiStore'

/** 舞台灯整段多长（ms）：光打过来 ≈0.3 s · 停 ≈0.4 s · 散开 ≈0.3 s（用户：整体一秒）。与 `index.css` 的 `--animate-spotlight-*` 同长 */
export const SPOTLIGHT_MS = 1000

/** 暗幕的颜色：语义 token（界面代码不写颜色字面量） */
const SHADE = 'var(--color-spotlight-shade)'

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
 * 两层，各管各的：
 * - **景深**（`data-spotlight-blur`）：光外轻微高斯模糊，光圈里清晰。动画只在带 `backdrop-filter` 的那一层
 *   自己身上（关键帧直接动 blur）——祖先上只要有 opacity / transform / mask，背后的画布就取不到，模糊失效；
 * - **暗幕**（`data-spotlight-beam`，SVG）：只有一层暗幕，按「受光」挖亮——遮罩里光圈（一条 smoothstep
 *   曲线，外缘一段很缓的长尾 = 微弱光晕）与光柱（`data-spotlight-shaft`，两条边与光圈相切的柔边光锥，
 *   越近目标越亮、一直伸到光心）是同一张遮罩里的两块受光区，天然连成一片。不往画面上叠白：照亮的
 *   内容保持原样（用户：上一版像雾球、光柱与光圈接不上）。光圈从光源一侧收拢过来、光柱从光源射出。
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
  // 景深层：以光心为中心的一块方形（边长盖得住整块画布），遮罩让光圈里清晰
  const side = 2 * Math.hypot(Math.max(spot.cx, spot.W - spot.cx), Math.max(spot.cy, spot.H - spot.cy)) + 4 * spot.r
  const blurBox = {
    left: spot.cx - side / 2,
    top: spot.cy - side / 2,
    width: side,
    height: side,
    '--spot-r': `${spot.r}px`,
  } as CSSProperties
  // 光柱：一束光锥，两条边与光圈**相切**（光圈可见亮区半径 ≈ 1.25r）——锥在切点处与光圈一样宽、顺着
  // 光圈的轮廓收进去，没有「细棍顶个球」的收腰。光源一端留一点灯口宽度
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
  // 锥一直伸到光心（不止切点那条弦），与光圈重叠的部分本来就全亮，接缝就不存在
  const cone = [
    [src.x + px * mouth, src.y + py * mouth],
    tangent(1),
    [spot.cx, spot.cy],
    tangent(-1),
    [src.x - px * mouth, src.y - py * mouth],
  ]
    .map(([x, y]) => `${Math.round(x)},${Math.round(y)}`)
    .join(' ')
  const showShaft = dist > lit * 1.4
  // 光圈的受光曲线（遮罩里黑 = 挖掉暗幕）：0.7r 内全亮，到 1.45r 按 smoothstep 降到 0.16，再缓缓拖到 2.3r
  // 归零——后面那段长尾就是「四周一点微弱的光晕」：只是暗幕在那儿薄一点，不往画面上加白
  const R = spot.r * 2.3
  const glow: [number, number][] = []
  for (let k = 0; k <= 8; k++) {
    const t = k / 8
    const e = t * t * (3 - 2 * t)
    glow.push([(0.7 + 0.75 * t) / 2.3, 1 - 0.84 * e])
  }
  for (let k = 1; k <= 4; k++) {
    const t = k / 4
    glow.push([(1.45 + 0.85 * t) / 2.3, 0.16 * (1 - t * t * (3 - 2 * t))])
  }

  return (
    <div ref={host} data-issue-spotlight className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* 景深：光外轻微失焦，光圈里清晰（`mask-spotlight`） */}
      <div key={`blur-${hl.token}`} data-spotlight-blur className="absolute animate-spotlight-blur mask-spotlight" style={blurBox} />
      {/* 暗幕：一层，光圈与光柱都是在它上面「挖亮」（同一张遮罩里的两块受光区），光柱经过的地方就是亮一些、
          天然与光圈连成一片；不往画面上叠白（用户：像个雾球、光柱接不上） */}
      <svg
        key={`shade-${hl.token}`}
        data-spotlight-beam
        className="absolute inset-0 animate-spotlight-fade"
        width={spot.W}
        height={spot.H}
      >
        <defs>
          <radialGradient id={`lit-${uid}`} gradientUnits="userSpaceOnUse" cx={spot.cx} cy={spot.cy} r={R}>
            {glow.map(([o, a]) => (
              <stop key={o} offset={o} stopColor="black" stopOpacity={a} />
            ))}
          </radialGradient>
          <linearGradient
            id={`shaft-${uid}`}
            gradientUnits="userSpaceOnUse"
            x1={src.x}
            y1={src.y}
            x2={spot.cx}
            y2={spot.cy}
          >
            <stop offset="0" stopColor="black" stopOpacity="0.12" />
            <stop offset="1" stopColor="black" stopOpacity="0.5" />
          </linearGradient>
          <filter id={`soft-${uid}`} x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation={Math.max(6, Math.round(spot.r * 0.25))} />
          </filter>
          <mask id={`mask-${uid}`} maskUnits="userSpaceOnUse" x={0} y={0} width={spot.W} height={spot.H}>
            <rect width={spot.W} height={spot.H} fill="white" />
            {showShaft && (
              <g
                data-spotlight-shaft
                className="animate-spotlight-shaft"
                style={{ transformBox: 'view-box', transformOrigin: `${Math.round(src.x)}px ${Math.round(src.y)}px` }}
              >
                <polygon points={cone} fill={`url(#shaft-${uid})`} filter={`url(#soft-${uid})`} />
              </g>
            )}
            <g
              className="animate-spotlight-land"
              style={
                {
                  transformBox: 'view-box',
                  transformOrigin: `${spot.cx}px ${spot.cy}px`,
                  '--spot-from-x': `${Math.round(ux * spot.r * 1.2)}px`,
                  '--spot-from-y': `${Math.round(uy * spot.r * 1.2)}px`,
                } as CSSProperties
              }
            >
              <circle cx={spot.cx} cy={spot.cy} r={R} fill={`url(#lit-${uid})`} />
            </g>
          </mask>
        </defs>
        <rect width={spot.W} height={spot.H} style={{ fill: SHADE }} fillOpacity={0.72} mask={`url(#mask-${uid})`} />
      </svg>
    </div>
  )
}
