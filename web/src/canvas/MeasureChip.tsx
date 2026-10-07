import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { t as translate } from '@/i18n'
import { boundsOf } from '@/lib/geometry'
import { formatMm } from '@/lib/units'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { mmToPx, mmToViewX, mmToViewY, useViewportStore } from '@/store/viewportStore'

/** 改的是大小的那几种拖动：芯片说 W × H；移动说对象的 X, Y；方向键微调说这一段挪了多少（Δ） */
const SIZE_KINDS = new Set(['resize', 'draw', 'crop', 'endpoint'])
const MOVE_KINDS = new Set(['move'])

/** 芯片与它所贴的框之间的缝；放不下（贴到视口底）就翻到框的上面 */
const GAP = 8
const CHIP_H = 22

export type MeasureMode = 'size' | 'position' | 'offset'

/**
 * 贴着选区的尺寸芯片（2026-10-07 设计审计 §10.1）：拖动 / 缩放 / 画框 / 方向键微调进行中，在被改的那个
 * 框正下方居中显示一个读数——W × H、对象的 X, Y、或这一段微调的 Δ。
 *
 * 此前读数在画布左下角的 HUD 里，而且移动时报的是**指针**坐标，不是对象的位置：用户要的是「这张图现在
 * 在哪」，不是「我的鼠标在哪」。读数贴着它说的那个东西，视线不用在画布与左下角之间来回跳。
 *
 * 只是读数：不吃指针事件、不进 Tab 顺序。几何来自文档里的对象（画布对象的拖动在事务里实时改文档）或
 * 正在画的草稿框；图内元素的拖动（预览平面，不改文档）不在这里报。
 */
export function MeasureChip() {
  perfCount('render.MeasureChip')
  useTranslation('workspace')
  const kind = useInteractionStore((s) => s.kind)
  const nudge = useInteractionStore((s) => s.nudge)
  const draft = useInteractionStore((s) => s.draft)
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const viewH = useViewportStore((s) => s.viewH)

  const mode: MeasureMode | null = nudge
    ? 'offset'
    : SIZE_KINDS.has(kind)
      ? 'size'
      : MOVE_KINDS.has(kind)
        ? 'position'
        : null
  if (!mode) return null

  // 被改的那个框：画新对象时是草稿框，其余是选区里看得见的对象的联合包围盒
  const box =
    kind === 'draw' && draft
      ? { x: draft.x, y: draft.y, w: draft.w, h: draft.h }
      : (() => {
          const sel = objects.filter((o) => ids.includes(o.id) && !o.hidden)
          return sel.length ? boundsOf(sel) : null
        })()
  if (!box) return null

  const text =
    mode === 'offset'
      ? `Δ ${translate('measure.mmPair', { a: signedMm(nudge!.dx), b: signedMm(nudge!.dy) })}`
      : mode === 'size'
        ? translate('measure.mmSize', { w: formatMm(Math.abs(box.w)), h: formatMm(Math.abs(box.h)) })
        : translate('measure.mmPair', { a: formatMm(box.x), b: formatMm(box.y) })

  const t = { zoom, panX, panY, originX: 0, originY: 0 }
  const left = mmToViewX(box.x, t) + mmToPx(box.w, t) / 2
  const top = mmToViewY(box.y, t)
  const bottom = top + mmToPx(box.h, t)
  const below = bottom + GAP + CHIP_H <= viewH || !viewH
  return (
    <div
      data-measure-chip={mode}
      className="pointer-events-none absolute z-sticky w-max -translate-x-1/2 rounded-full bg-ink px-2 py-0.5 text-xs font-medium tabular-nums text-surface shadow-pop"
      style={{ left, top: below ? bottom + GAP : top - GAP - CHIP_H }}
    >
      {text}
    </div>
  )
}

/** 位移读数带正负号：「+1.5」「−0.5」「0.0」 */
function signedMm(v: number): string {
  const s = formatMm(Math.abs(v))
  if (s === formatMm(0)) return s
  return v > 0 ? `+${s}` : `−${s}`
}
