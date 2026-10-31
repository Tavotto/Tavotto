import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Card } from '@/components/ui/Card'
import { TruncateMiddle } from '@/components/ui/TruncateMiddle'
import { formatOf, nameTail } from '@/lib/assetCardText'
import { panelSrc, type PanelInfo } from '@/lib/api'
import { statusLabel } from '@/lib/readinessText'
import { formatCm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { addFigureToLayout } from '@/store/workspace'

/** 空画布上最多摆几张 */
export const PICK_LIMIT = 4

const pk = (key: string, values?: Record<string, unknown>) =>
  translate(`stage.${key}`, { ns: 'workspace', ...(values ?? {}) })

/**
 * 空画布上的选图卡（2026-10-31 UI 审计 B5）：项目里有图时，起步提示直接摆出前几张图，点一下放上画布。
 * 卡片依次浮入（`motion-safe` 才有位移，减少动态效果时直接在位）；点过的卡变淡，直到提示随第一个对象出现而消失。
 *
 * 「这张图能不能改图内」只讲后端判的接入状态（`panel.capability`）：editable = 「可改图内」，其余 = 状态名；悬停在
 * 不可改图内的卡上说明它只能整体移动、缩放、裁剪。素材在放上画布之前没有元素清单（manifest 要渲染之后才有），
 * 所以这里**不画**「能改哪些元素」的框——没有真实数据就不画假框。
 */
export function EmptyPickCards({ panels }: { panels: PanelInfo[] }) {
  useTranslation('workspace')
  const [placed, setPlaced] = useState<ReadonlySet<string>>(new Set())
  return (
    <div data-empty-pick className="grid justify-items-center gap-2.5 p-4">
      <h4 className="type-title">{pk('pickTitle')}</h4>
      <div className="flex justify-center gap-2.5">
        {panels.slice(0, PICK_LIMIT).map((p, i) => (
          <PickCard
            key={p.id}
            panel={p}
            index={i}
            placed={placed.has(p.id)}
            onPlace={() => {
              setPlaced((s) => new Set(s).add(p.id))
              addFigureToLayout(p.id)
            }}
          />
        ))}
      </div>
      <p className="type-caption">{pk('pickNote')}</p>
    </div>
  )
}

function PickCard({
  panel,
  index,
  placed,
  onPlace,
}: {
  panel: PanelInfo
  index: number
  placed: boolean
  onPlace: () => void
}) {
  const name = panel.id.split('/').pop() ?? panel.id
  const cap = panel.capability
  const editable = cap?.status === 'editable'
  const src = panelSrc(panel.id, panel.kind, 400, panel.mtime)
  return (
    <div
      className="motion-safe:animate-rise-in"
      style={{ animationDelay: `${index * 70}ms`, animationFillMode: 'both' }}
    >
      <Card
        appearance="raised"
        padding="sm"
        interactive
        className={cn('w-28 transition-opacity', placed && 'pointer-events-none opacity-40')}
      >
        <button
          type="button"
          data-pick-card={panel.id}
          title={panel.id}
          onClick={onPlace}
          className="group grid w-full min-w-0 gap-1 rounded-md text-left outline-none focus-visible:focus-ring"
        >
          <span className="relative flex h-16 items-center justify-center overflow-hidden rounded-md bg-paper">
            {src && (
              <img
                loading="lazy"
                src={src}
                alt=""
                draggable={false}
                className="h-full w-full object-contain p-1"
              />
            )}
          </span>
          <span className="block min-w-0 text-sm font-medium text-ink">
            <TruncateMiddle text={name} tail={nameTail(name)} />
          </span>
          <span className="block truncate type-meta tabular-nums text-ink-3">
            {formatOf(panel)} · {translate('measure.cmSize', { w: formatCm(panel.native_w_mm), h: formatCm(panel.native_h_mm) })}
          </span>
          {cap && (
            <span
              className={cn(
                'w-fit max-w-full truncate rounded-full bg-surface-2 px-1.5 type-meta',
                editable ? 'text-ink-2' : 'text-ink-3',
              )}
            >
              {editable ? pk('pickEditable') : statusLabel(cap.status)}
            </span>
          )}
          {cap && !editable && (
            <span className="hidden min-w-0 type-meta text-ink-3 group-hover:block group-focus-visible:block">
              {pk('pickLayoutOnly')}
            </span>
          )}
        </button>
      </Card>
    </div>
  )
}
