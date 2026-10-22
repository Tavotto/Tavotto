import { useTranslation } from 'react-i18next'
import { Numbers } from '@sfinterface/numbers'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { dimSpan } from './pageEdgeReadout'

/** 端点短竖线的长度；文字沿标尺方向的长度估计 = 基础 + 吸栏宽名（只用来判放里面还是外侧，不决定文字位置） */
const CAP_LEN = 12
const TEXT_BASE = 70
const TEXT_H = 14

const MM_SUFFIX = '\u00a0mm'
const mmText = (v: number) => String(Math.round(v * 10) / 10)

/**
 * 拖页面边时**标尺里**的尺寸标注：`|----- 157.3 mm -----|`——无框、无胶囊。
 * 拖左 / 右边画在水平标尺（`data-ruler="x"`）里，拖上 / 下边画在竖直标尺（`data-ruler="y"`）里，文字转 -90° 与竖直
 * 标尺刻度数字同向。线从页面起边画到止边，两端短竖线，线在文字处断开（文字垫一块与标尺底同色、无边框的底）。
 * 颜色是选中蓝 `--color-sel`（与吸附线同一个 token，亮暗两套都有）。位置只随页面边走，文字居中于线段中点，
 * tabular-nums，数字变化不抖；几何是纯函数 `pageEdgeReadout.dimSpan`。
 * 叠在标尺 canvas 之上的 DOM 层、`pointer-events: none`，只在拖动中挂，不进文档 / 导出。
 */
export function PageEdgeRulerDim({ axis, size, length }: { axis: 'x' | 'y'; size: number; length: number }) {
  const { t, i18n } = useTranslation('workspace')
  const pageEdge = useInteractionStore((s) => s.pageEdge)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const pageW = useDocumentStore((s) => s.doc.page.w)
  const pageH = useDocumentStore((s) => s.doc.page.h)
  if (!pageEdge) return null
  const showsW = pageEdge.edge === 'l' || pageEdge.edge === 'r'
  if (showsW !== (axis === 'x')) return null
  const k = mmToWorld(1) * zoom
  const mm = showsW ? pageW : pageH
  const start = showsW ? panX : panY
  const end = start + mm * k
  const snap = showsW ? pageEdge.snapLabel : null
  const textLen = TEXT_BASE + (snap ? 16 + snap.length * 7 : 0)
  const g = dimSpan(pageEdge.edge, start, end, length, textLen)
  if (!g) return null
  const kind = showsW ? 'width' : 'height'
  const horiz = axis === 'x'
  const mid = size / 2
  const color = 'var(--color-sel)'
  // 标尺底：页面范围内是 surface、范围外是 bg（`Rulers.draw` 的两层底），垫底块取文字所在处的那一种
  const under = g.outside ? 'bg-bg' : 'bg-surface'
  const lineStyle = (from: number, to: number) =>
    horiz
      ? { left: from, width: to - from, top: mid - 0.5, height: 1, background: color }
      : { top: from, height: to - from, left: mid - 0.5, width: 1, background: color }
  const capStyle = (at: number) =>
    horiz
      ? { left: at - 0.5, width: 1, top: mid - CAP_LEN / 2, height: CAP_LEN, background: color }
      : { top: at - 0.5, height: 1, left: mid - CAP_LEN / 2, width: CAP_LEN, background: color }
  const label = (
    <div
      data-dim-readout={kind}
      data-readout-edge={pageEdge.edge}
      className={`absolute whitespace-nowrap px-[5px] text-center text-xs font-medium tabular-nums ${under}`}
      style={{
        color,
        height: TEXT_H,
        lineHeight: `${TEXT_H}px`,
        ...(horiz
          ? { left: g.center, top: mid, transform: 'translate(-50%, -50%)' }
          : { left: mid, top: g.center, transform: 'translate(-50%, -50%) rotate(-90deg)' }),
      }}
    >
      {/* 前缀 / 单位在 Numbers 外面写：Numbers 会吃掉首尾空格，用不换行空格留出「宽 157.3 mm」的间隔 */}
      <span aria-hidden>{t(`pageEdge.${kind}Prefix`).replace(/\s+$/, '\u00a0')}</span>
      <Numbers
        value={Math.round(mm * 10) / 10}
        duration={pageEdge.rolls ? undefined : 0}
        format={{ useGrouping: false, maximumFractionDigits: 1 }}
        locale={i18n.language}
        label={t(`pageEdge.${kind}`, { mm: mmText(mm) })}
      />
      <span aria-hidden>{MM_SUFFIX}</span>
      {snap && (
        <span data-dim-snap="" className="ml-1">
          · {snap}
        </span>
      )}
    </div>
  )
  return (
    <div
      data-ruler-dim={axis}
      className="pointer-events-none absolute z-sticky overflow-hidden"
      style={
        horiz
          ? { left: size, top: 0, width: length, height: size }
          : { left: 0, top: size, width: size, height: length }
      }
    >
      {/* 整条线一次画满；文字自带的同色底压在上面，线就在文字处断开（不依赖文字宽度的估计） */}
      <div data-dim-line="" className="absolute" style={lineStyle(g.a, g.b)} />
      {g.startCap && <div data-dim-cap="start" className="absolute" style={capStyle(g.a)} />}
      {g.endCap && <div data-dim-cap="end" className="absolute" style={capStyle(g.b)} />}
      {label}
    </div>
  )
}
