import { msg, type UiMessage } from '@/i18n'
import type { DragStatus } from '@/store/interactionStore'
import { ALT } from '@/lib/utils'
import { round1 } from '@/lib/units'
import type { EdgeCropState, CropSide } from '@/lib/panelEdgeCrop'

/**
 * 多图排版里面板角缩放 / 边裁剪的拖动状态（ADR 0161）→ `interaction.dragStatus`。
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
  const detail = !i.follow.on
    ? cb('layoutFollowNone')
    : i.follow.count > 0
      ? cb('layoutFollow', { count: i.follow.count })
      : null
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
  box: Box
}): DragStatus {
  const side = cb(SIDE_KEY[i.side])
  const mm = round1(i.mm).toFixed(1)
  switch (i.state) {
    case 'full':
      return { tone: 'normal', main: cb('layoutCropFull', { side }), box: i.box }
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
