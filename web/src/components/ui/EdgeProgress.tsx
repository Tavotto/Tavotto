import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { DURATION } from '@/lib/motion'

/**
 * 沿对话框外框走一圈的进度描边（外部审阅「分享审阅」，ADR 0187 §八 修订 2026-11-21）。
 *
 * 挂在 `Dialog` 的 `edge` 槽里：铺满 `RD.Content`、不占位、不接事件，所以卡片尺寸一点不变。
 * 从顶边正中出发顺时针走，圆角读外壳的计算值，线内缩半个线宽，贴着外框不被裁。
 *
 * **不是百分比**：拿不到真进度的地方不报 `aria-valuenow`、不出数字（ADR 0168 的「不做假百分比」）。
 * 调用方只给 `phase`，每一段要走到哪、走多慢都在 `index.css` 的 `edge-progress` 里：
 *   - `start` / `mid`：先快后慢地逼近一个停靠点，永远不自己走满；阶段跳了（复用画面时直接 done）
 *     就从当前位置接着走，CSS 过渡天然从计算值出发。
 *   - `full`：合拢一圈（slow 档）。
 *   - `off`：淡出（失败 / 完成后让位），位置留在原地不倒退。
 * 减少动态效果：全局兜底把过渡压到 0.01ms，描边直接到位；信息在调用方的状态文字里，不靠动画。
 */
export type EdgePhase = 'idle' | 'start' | 'mid' | 'full' | 'off'

/** 从顶边正中出发、顺时针、圆角 r 的圆角矩形路径（内缩 i） */
export function edgePath(w: number, h: number, r: number, i: number): string {
  const R = Math.max(0, Math.min(r - i, (w - 2 * i) / 2, (h - 2 * i) / 2))
  const L = i
  const T = i
  const Rt = w - i
  const B = h - i
  const cx = w / 2
  return [
    `M${cx} ${T}`,
    `H${Rt - R}`,
    `A${R} ${R} 0 0 1 ${Rt} ${T + R}`,
    `V${B - R}`,
    `A${R} ${R} 0 0 1 ${Rt - R} ${B}`,
    `H${L + R}`,
    `A${R} ${R} 0 0 1 ${L} ${B - R}`,
    `V${T + R}`,
    `A${R} ${R} 0 0 1 ${L + R} ${T}`,
    'Z',
  ].join(' ')
}

/**
 * 合拢一圈的时长：两档 slow。合拢常常从很靠前的位置出发（复用画面时一次轮询就 done），slow 一档扫完整圈像闪了一下。
 * 经内联 `--edge-seal` 交给 CSS，调用方的合拢闸也用它——一个出处。
 */
export const EDGE_SEAL_MS = DURATION.slow * 2
const STROKE = 2 // 最粗的那条（笔头）；线内缩它的一半，贴着外框不被圆角裁掉

export function EdgeProgress({ phase, label }: { phase: EdgePhase; label: string }) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ w: number; h: number; r: number } | null>(null)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const read = () => {
      const w = el.clientWidth
      const h = el.clientHeight
      // 圆角读外壳自己的计算值（rounded-panel），不在这里另写一份
      const host = el.parentElement
      const r = host ? parseFloat(getComputedStyle(host).borderTopLeftRadius) || 0 : 0
      setSize((s) => (s && s.w === w && s.h === h && s.r === r ? s : { w, h, r }))
    }
    read()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const active = phase !== 'idle' && phase !== 'off'
  return (
    <div
      ref={box}
      aria-hidden={!active || undefined}
      className="edge-progress pointer-events-none absolute inset-0 z-sticky"
      data-edge-progress={phase}
      style={{ '--edge-seal': `${EDGE_SEAL_MS}ms` } as CSSProperties}
      {...(active ? { role: 'progressbar', 'aria-label': label } : {})}
    >
      {size && size.w > 0 && size.h > 0 && (
        // 画的是卡片外框本身，不是图标：iconography.test 的内联 svg 豁免表登记 1 个
        <svg width={size.w} height={size.h} viewBox={`0 0 ${size.w} ${size.h}`} className="block">
          <path className="edge-progress-track" d={edgePath(size.w, size.h, size.r, STROKE / 2)} pathLength={1} />
          <path className="edge-progress-run" d={edgePath(size.w, size.h, size.r, STROKE / 2)} pathLength={1} />
          <path className="edge-progress-head" d={edgePath(size.w, size.h, size.r, STROKE / 2)} pathLength={1} />
        </svg>
      )}
    </div>
  )
}
