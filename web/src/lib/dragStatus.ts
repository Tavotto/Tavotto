import { msg, type UiMessage } from '@/i18n'
import type { DragStatus } from '@/store/interactionStore'
import { ALT } from '@/lib/utils'
import { round1 } from '@/lib/units'
import type { EdgeCropState, CropSide } from '@/lib/panelEdgeCrop'
import type { PadEdgeState } from '@/lib/framePad'

/**
 * 多图排版里面板角缩放 / 边裁剪、单图编辑拖画布边 / 角的拖动状态（ADR 0161）→ `interaction.dragStatus`。
 * 语气与停靠状态在手势里算好传进来，这里只把它们拼成描述符（`workspace:contextBar.*`）。
 */
const cb = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`contextBar.${key}`, values, 'workspace')

type Box = DragStatus['box']

export function scaleDragStatus(i: {
  fromPct: number
  toPct: number
  /** 图内文字等效 pt；位图源没有 = null */
  fromPt: number | null
  toPt: number | null
  /** 低于当前期刊 / 规范下限（读不到下限 = false） */
  low: boolean
  /** 整个落在图里的标注：`count` 个；`on` = 这一帧跟着缩（非等比那一帧留在原处） */
  follow: { on: boolean; count: number }
  box: Box
}): DragStatus {
  const pct = { from: i.fromPct, to: i.toPct }
  const main =
    i.fromPt != null && i.toPt != null
      ? cb('layoutScalePtFull', {
          ...pct,
          ptFrom: round1(i.fromPt).toFixed(1),
          ptTo: round1(i.toPt).toFixed(1),
        })
      : cb('layoutScale', pct)
  const detail =
    i.follow.count === 0
      ? null
      : i.follow.on
        ? cb('layoutFollow', { count: i.follow.count })
        : cb('layoutFollowNone')
  return { tone: i.low ? 'warn' : 'normal', main, detail, box: i.box }
}

const SIDE_KEY: Record<CropSide, string> = {
  l: 'layoutSideL',
  r: 'layoutSideR',
  t: 'layoutSideT',
  b: 'layoutSideB',
}

export function cropDragStatus(i: {
  side: CropSide
  state: EdgeCropState
  /** 这条边此刻裁掉的宽度 / 高度（mm） */
  mm: number
  /** 顶在图边还想往外、但改不了图幅时说原因（ADR 0161 修订 2026-11-06） */
  growBlocked?: 'cropped' | 'unavailable' | null
  box: Box
}): DragStatus {
  const side = cb(SIDE_KEY[i.side])
  const mm = round1(i.mm).toFixed(1)
  switch (i.state) {
    case 'full':
      return {
        tone: 'normal',
        main: cb('layoutCropFull', { side }),
        detail: i.growBlocked
          ? cb(i.growBlocked === 'cropped' ? 'layoutGrowCropped' : 'layoutGrowUnavailable')
          : null,
        box: i.box,
      }
    case 'trim':
      return { tone: 'normal', main: cb('layoutCropTrim', { side, mm }), box: i.box }
    case 'tight':
      return { tone: 'normal', main: cb('layoutCropTight', { side }), box: i.box }
    case 'blocked':
      return { tone: 'warn', main: cb('layoutCropBlocked', { alt: ALT }), box: i.box }
    case 'content':
      return { tone: 'danger', main: cb('layoutCropContent', { side, mm }), box: i.box }
  }
}

/** 多图排版里把图边往外拖 = 改图幅（ADR 0161 修订 2026-11-06）：只有被拖的那一维变 */
export function edgeGrowDragStatus(i: {
  side: CropSide
  from: readonly [number, number]
  to: readonly [number, number]
  box: Box
}): DragStatus {
  const horizontal = i.side === 'l' || i.side === 'r'
  const axis = horizontal ? 0 : 1
  return {
    tone: 'normal',
    main: cb(horizontal ? 'layoutGrowWidth' : 'layoutGrowHeight', {
      from: String(round1(i.from[axis])),
      to: String(round1(i.to[axis])),
    }),
    detail: cb('frameCornerDetail'),
    box: i.box,
  }
}

/** 单图编辑拖画布的一条边（留白，ADR 0161）：停靠状态由 `lib/framePad.planPadEdge` 算好 */
export function framePadDragStatus(i: {
  side: CropSide
  state: PadEdgeState
  /** 这条边留白的绝对值（mm） */
  mm: number
  /** 此刻的画布（图幅 + 留白，导出 mm） */
  canvas: readonly [number, number]
  /** 画布宽吸上的栏宽（已译好的「规范 栏名」） */
  col: string | null
  box: Box
}): DragStatus {
  const side = cb(SIDE_KEY[i.side])
  const mm = String(round1(i.mm))
  const size = cb('frameCanvasSize', { w: String(round1(i.canvas[0])), h: String(round1(i.canvas[1])) })
  const withCol = (m: UiMessage): UiMessage => (i.col ? cb('frameWithCol', { base: m, col: i.col }) : m)
  switch (i.state) {
    case 'grow':
      return { tone: 'normal', main: withCol(cb('frameGrow', { side, mm })), detail: cb('frameFigureStaysSize', { size }), box: i.box }
    case 'edge':
      return { tone: 'normal', main: cb('frameAtEdge', { side }), detail: size, box: i.box }
    case 'trim':
      return { tone: 'normal', main: withCol(cb('frameTrim', { side, mm })), detail: size, box: i.box }
    case 'tight':
      return { tone: 'normal', main: withCol(cb('frameTight', { side })), detail: size, box: i.box }
    case 'blocked':
      return { tone: 'warn', main: cb('layoutCropBlocked', { alt: ALT }), box: i.box }
    case 'crop':
      return { tone: 'danger', main: cb('frameCrop', { side, mm }), detail: size, box: i.box }
  }
}

/** 单图编辑拖画布的角（等比改图幅；`free` = ⇧ 自由比例，ADR 0161） */
export function frameCornerDragStatus(i: {
  free: boolean
  from: readonly [number, number]
  to: readonly [number, number]
  col: string | null
  box: Box
}): DragStatus {
  const f = (v: number) => String(round1(v))
  const main = i.free
    ? cb('frameCornerFree', { fw: f(i.from[0]), fh: f(i.from[1]), tw: f(i.to[0]), th: f(i.to[1]) })
    : cb('frameCornerUniform', { from: f(i.from[0]), to: f(i.to[0]) })
  return {
    tone: 'normal',
    main: i.col ? cb('frameWithCol', { base: main, col: i.col }) : main,
    detail: cb('frameCornerDetail'),
    box: i.box,
  }
}
