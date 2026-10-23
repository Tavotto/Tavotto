import { useEffect, useRef, useState } from 'react'
import { useUiStore } from '@/store/uiStore'

/** 舞台灯整段多长（ms）：渐亮 · 停一秒 · 渐暗。与 `index.css` 的 `--animate-spotlight-*` 同长 */
export const SPOTLIGHT_MS = 1800

type Spot = { cx: number; cy: number; rx: number; ry: number }

/**
 * 光圈的椭圆半径（px）：把目标框整个包进去再留一圈余量。柔边从半径的 55% 起往外渐变，
 * 所以目标本身落在完全清晰的那一段里，往外慢慢变糊变暗——像一束手电打在那儿，没有硬边。
 */
const radius = (half: number) => half * 1.5 + 28

/**
 * 定位之后的「舞台灯」（2026-10-23 用户提出）：问题面板点「定位」，画布上目标以外的地方渐渐
 * 高斯模糊、压暗，目标那一块稍微提亮，像一束手电打在那儿；约一秒后复原。不画任何描边。
 *
 * **光圈不自己算几何**：读 `OverlaySvg` 里那个定位高亮框（`[data-issue-highlight]`，图内元素时由
 * `IssueOverlay.IssueElementHighlight` 按与悬停轮廓同一份换算画；动效开着时它不可见，只当锚点）的
 * 屏幕外接框。视口正平移到目标（`revealRect`）的那几帧，光圈每帧跟着走。
 *
 * **动画只在带 `backdrop-filter` 的那一层自己身上**（关键帧改的是 blur / brightness 本身）：祖先上只要
 * 有 opacity / filter / mask / clip-path，那个祖先就成了 backdrop root，背后的画布取不到，模糊整个失效
 * ——第一版把淡入淡出做在父层的 opacity 上，结果只剩压暗、看不到模糊（用户反馈）。
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
          rx: Math.round(radius(target.width / 2)),
          ry: Math.round(radius(target.height / 2)),
        }
        const key = `${next.cx},${next.cy},${next.rx},${next.ry}`
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

  const at = spot ? `ellipse ${spot.rx}px ${spot.ry}px at ${spot.cx}px ${spot.cy}px` : ''
  return (
    <div ref={host} data-issue-spotlight={spot ? '' : undefined} className="pointer-events-none absolute inset-0">
      {spot && hl && (
        <>
          {/* 光圈外：模糊 + 压暗，柔边遮罩（中心透明 → 外圈满） */}
          <div
            key={`shade-${hl.token}`}
            data-spotlight-shade
            className="absolute inset-0 animate-spotlight-shade"
            style={{
              maskImage: `radial-gradient(${at}, transparent 55%, black 100%)`,
              WebkitMaskImage: `radial-gradient(${at}, transparent 55%, black 100%)`,
            }}
          />
          {/* 光圈里：稍微提亮，同样是柔边（中心满 → 外圈透明） */}
          <div
            key={`lift-${hl.token}`}
            className="absolute inset-0 animate-spotlight-lift"
            style={{
              maskImage: `radial-gradient(${at}, black 40%, transparent 75%)`,
              WebkitMaskImage: `radial-gradient(${at}, black 40%, transparent 75%)`,
            }}
          />
        </>
      )}
    </div>
  )
}
