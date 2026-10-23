import { useEffect, useRef, useState } from 'react'
import { useUiStore } from '@/store/uiStore'

/** 舞台灯整段多长（ms）：淡入 · 停一秒 · 淡出。与 `index.css` 的 `--animate-spotlight` 同长 */
export const SPOTLIGHT_MS = 1400

/** 光圈比目标框各边多出多少（px）：贴边的洞会把描边本身也切进模糊里 */
const HOLE_PAD = 8

type Hole = { x: number; y: number; w: number; h: number; W: number; H: number }

/**
 * 定位之后的「舞台灯」（2026-10-23 用户提出）：问题面板点「定位」，画布上除了那个对象 / 图内元素
 * 以外的地方高斯模糊并压暗、目标那一块稍微提亮，约一秒后淡出——一眼看出被带到了哪儿。
 *
 * **光圈不自己算几何**：读 `OverlaySvg` 画出来的那个定位高亮框（`[data-issue-highlight]`，图内元素时由
 * `IssueOverlay.IssueElementHighlight` 按与悬停轮廓同一份换算画）的屏幕外接框。旋转 / 翻转过的图、
 * 视口正在平移到目标（`revealRect`）的那几帧，洞都跟着高亮框走——每帧读一次，直到整段放完。
 *
 * 只动画面、不吃指针（`pointer-events-none`），不碰文档 / 选区 / 历史。挂在覆盖层**之下**：选择框与
 * 高亮框画在它上面，始终清晰。reduced motion 下全局把动画压到 0.01 ms，`both` 停在末帧（透明），
 * 即不放舞台灯，只留高亮框。
 */
export function IssueSpotlight() {
  const hl = useUiStore((s) => s.issueHighlight)
  const host = useRef<HTMLDivElement>(null)
  const [hole, setHole] = useState<Hole | null>(null)

  useEffect(() => {
    setHole(null)
    if (!hl?.objectId) return
    const t0 = performance.now()
    let raf = 0
    let last = ''
    const tick = () => {
      const box = host.current?.getBoundingClientRect()
      const target = document.querySelector('[data-issue-highlight]')?.getBoundingClientRect()
      if (box && target) {
        const next: Hole = {
          x: target.left - box.left - HOLE_PAD,
          y: target.top - box.top - HOLE_PAD,
          w: target.width + 2 * HOLE_PAD,
          h: target.height + 2 * HOLE_PAD,
          W: box.width,
          H: box.height,
        }
        const key = JSON.stringify(next)
        if (key !== last) {
          last = key
          setHole(next)
        }
      }
      if (performance.now() - t0 < SPOTLIGHT_MS) raf = requestAnimationFrame(tick)
      else setHole(null)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [hl?.token, hl?.objectId])

  return (
    <div ref={host} data-issue-spotlight={hole ? '' : undefined} className="pointer-events-none absolute inset-0">
      {hole && hl && (
        <div key={hl.token} className="absolute inset-0 animate-spotlight">
          {/* 光圈外：模糊 + 压暗。evenodd 的两层矩形 = 整块减去光圈 */}
          <div
            className="absolute inset-0 bg-spotlight-shade"
            style={{
              backdropFilter: 'blur(3px)',
              WebkitBackdropFilter: 'blur(3px)',
              clipPath: `path(evenodd, 'M0 0H${hole.W}V${hole.H}H0Z M${hole.x} ${hole.y}h${hole.w}v${hole.h}h${-hole.w}Z')`,
            }}
          />
          {/* 光圈里：稍微提亮，四周一圈 sel 色的光晕 */}
          <div
            className="absolute rounded-sm shadow-spotlight"
            style={{
              left: hole.x,
              top: hole.y,
              width: hole.w,
              height: hole.h,
              backdropFilter: 'brightness(1.12)',
              WebkitBackdropFilter: 'brightness(1.12)',
            }}
          />
        </div>
      )}
    </div>
  )
}
