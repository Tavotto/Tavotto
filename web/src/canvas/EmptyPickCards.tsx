import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Card } from '@/components/ui/Card'
import { MiddleEllipsis } from '@/components/ui/MiddleEllipsis'
import { formatOf, nameTail } from '@/lib/assetCardText'
import { panelSrc, type PanelInfo } from '@/lib/api'
import { statusLabel } from '@/lib/readinessText'
import { formatCm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { addFigureToLayout } from '@/store/workspace'

/** 空画布上最多摆几张 */
export const PICK_LIMIT = 4
/** 一张卡的宽（px）：要完整放下「PDF · 7.3 × 5.8 cm」 */
const CARD_W = 136
const GAP = 10
/** 提示面板的内边距（px） */
const PAD = 16

const pk = (key: string, values?: Record<string, unknown>) =>
  translate(`stage.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 一张卡的高（px，缩略图 64 + 名字 + 元数据 + 内边距）与提示面板其余部分（内边距 + 标题 + 说明 + 间距）的高 */
const CARD_H = 128
const CHROME_H = 112

/** 纸面放得下几行（至少一行） */
export function pickRows(paperH: number): number {
  return Math.max(1, Math.floor((paperH - CHROME_H + GAP) / (CARD_H + GAP)))
}

/** 纸面放得下几列：4 张铺成一排放得下就一排，否则两列（再窄一列）；面板整块落在纸面内 */
export function pickColumns(paperW: number, count: number): number {
  const fit = (cols: number) => cols * CARD_W + (cols - 1) * GAP + PAD * 2 <= paperW
  if (count >= 4 && fit(4)) return 4
  if (count >= 2 && fit(2)) return 2
  return 1
}

/**
 * 空画布上的选图卡（2026-10-31 UI 审计 B5）：项目里有图时，起步提示直接摆出前几张图，点一下放上画布。
 * 卡片依次浮入（`motion-safe` 才有位移，减少动态效果时直接在位）；点过的卡变淡，直到提示随第一个对象出现而消失。
 *
 * 接入状态与素材卡同一条规则：可编辑是默认，不标；只有不可编辑的图在缩略图角上打状态小标签。悬停 / 聚焦在这种卡上时，
 * 卡片尺寸不变，下方那行说明换成「只能整体移动、缩放、裁剪」。素材在放上画布之前没有元素清单（manifest 要渲染之后才有），
 * 所以这里**不画**「能改哪些元素」的框——没有真实数据就不画假框。
 */
export function EmptyPickCards({ panels, paperW, paperH }: { panels: PanelInfo[]; paperW: number; paperH: number }) {
  useTranslation('workspace')
  const [placed, setPlaced] = useState<ReadonlySet<string>>(new Set())
  const [layoutOnlyHover, setLayoutOnlyHover] = useState(false)
  // 面板整块落在纸面内：列数按纸宽、行数按纸高，放不下的卡不摆（素材库里都在）
  const cols = pickColumns(paperW, Math.min(panels.length, PICK_LIMIT))
  const shown = panels.slice(0, Math.min(PICK_LIMIT, cols * pickRows(paperH)))
  return (
    <div data-empty-pick className="grid justify-items-center gap-2.5" style={{ padding: PAD }}>
      <h4 className="type-title">{pk('pickTitle')}</h4>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${cols}, ${CARD_W}px)`, gap: GAP }}>
        {shown.map((p, i) => (
          <PickCard
            key={p.id}
            panel={p}
            index={i}
            placed={placed.has(p.id)}
            onHint={setLayoutOnlyHover}
            onPlace={() => {
              setPlaced((s) => new Set(s).add(p.id))
              addFigureToLayout(p.id)
            }}
          />
        ))}
      </div>
      <p className="type-caption" data-empty-pick-note>
        {layoutOnlyHover ? pk('pickLayoutOnly') : pk('pickNote')}
      </p>
    </div>
  )
}

function PickCard({
  panel,
  index,
  placed,
  onHint,
  onPlace,
}: {
  panel: PanelInfo
  index: number
  placed: boolean
  onHint: (on: boolean) => void
  onPlace: () => void
}) {
  const name = panel.id.split('/').pop() ?? panel.id
  const cap = panel.capability
  const abnormal = !!cap && cap.status !== 'editable'
  const src = panelSrc(panel.id, panel.kind, 400, panel.mtime)
  const hint = (on: boolean) => abnormal && onHint(on)
  return (
    <div className="motion-safe:animate-rise-in" style={{ animationDelay: `${index * 70}ms`, animationFillMode: 'both' }}>
      <Card
        appearance="raised"
        padding="sm"
        interactive
        className={cn('transition-opacity', placed && 'pointer-events-none opacity-40')}
      >
        <button
          type="button"
          data-pick-card={panel.id}
          title={panel.id}
          onClick={onPlace}
          onMouseEnter={() => hint(true)}
          onMouseLeave={() => hint(false)}
          onFocus={() => hint(true)}
          onBlur={() => hint(false)}
          className="grid w-full min-w-0 gap-1 rounded-md text-left outline-none focus-visible:focus-ring"
        >
          <span className="relative flex h-16 items-center justify-center overflow-hidden rounded-md bg-paper">
            {src && (
              <img loading="lazy" src={src} alt="" draggable={false} className="h-full w-full object-contain p-1" />
            )}
            {abnormal && (
              <span
                data-card-status={cap.status}
                className="pointer-events-none absolute bottom-1 left-1 rounded-full bg-thumb px-1.5 type-meta text-paper-ink-2 shadow-thumb"
              >
                {statusLabel(cap.status)}
              </span>
            )}
          </span>
          <span className="block min-w-0 text-sm font-medium text-ink">
            <MiddleEllipsis text={name} tail={nameTail(name)} />
          </span>
          <span className="block whitespace-nowrap type-meta tabular-nums text-ink-3">
            {formatOf(panel)} · {translate('measure.cmSize', { w: formatCm(panel.native_w_mm), h: formatCm(panel.native_h_mm) })}
          </span>
        </button>
      </Card>
    </div>
  )
}
