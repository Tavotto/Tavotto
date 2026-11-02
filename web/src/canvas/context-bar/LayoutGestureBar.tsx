import { ALT, cn } from '@/lib/utils'
import { round1 } from '@/lib/units'
import type { LayoutReadout } from '@/store/interactionStore'
import { qb } from './text'

/**
 * 多图排版里拖面板的角（缩放）/ 边（裁剪）期间，胶囊里显示这一下拖动的含义（ADR 0161）。
 * **只翻译、不判断**：停靠状态与语气（低于下限）都由手势算好放在 `interaction.layoutReadout`，
 * 这里一个比较都不做。选择器用 `data-layout-readout`（kind）/ `data-tone`。
 */
export type ReadoutTone = 'neutral' | 'warn' | 'danger'

const SIDE_KEY = { l: 'layoutSideL', r: 'layoutSideR', t: 'layoutSideT', b: 'layoutSideB' } as const

/** 一条读数 → 句子与语气（纯函数，测试直接断言它） */
export function readoutSentence(r: LayoutReadout): { text: string; tone: ReadoutTone } {
  if (r.kind === 'scale') {
    const parts = [qb('layoutScale', { from: r.fromPct, to: r.toPct })]
    if (r.fromPt != null && r.toPt != null) {
      parts.push(qb('layoutScalePt', { from: round1(r.fromPt).toFixed(1), to: round1(r.toPt).toFixed(1) }))
    }
    if (r.follow.on && r.follow.count > 0) parts.push(qb('layoutFollow', { count: r.follow.count }))
    else if (!r.follow.on) parts.push(qb('layoutFollowNone'))
    return { text: parts.join(' · '), tone: r.low ? 'warn' : 'neutral' }
  }
  const side = qb(SIDE_KEY[r.side])
  const mm = round1(r.mm).toFixed(1)
  switch (r.state) {
    case 'full':
      return { text: qb('layoutCropFull', { side }), tone: 'neutral' }
    case 'trim':
      return { text: qb('layoutCropTrim', { side, mm }), tone: 'neutral' }
    case 'tight':
      return { text: qb('layoutCropTight', { side }), tone: 'neutral' }
    case 'blocked':
      return { text: qb('layoutCropBlocked', { alt: ALT }), tone: 'warn' }
    case 'content':
      return { text: qb('layoutCropContent', { side, mm }), tone: 'danger' }
  }
}

export function LayoutGestureReadout({ readout }: { readout: LayoutReadout }) {
  const { text, tone } = readoutSentence(readout)
  return (
    <span
      data-layout-readout={readout.kind}
      data-tone={tone}
      role="status"
      className={cn(
        'px-2 text-sm whitespace-nowrap',
        tone === 'neutral' && 'text-ink',
        tone === 'warn' && 'text-warn-content',
        tone === 'danger' && 'text-danger-content',
      )}
    >
      {text}
    </span>
  )
}
