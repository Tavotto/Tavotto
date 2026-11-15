import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { File } from '@/components/ui/icons'
import { ICON_SIZE, IconSlot } from '@/components/ui/Icon'
import { cn } from '@/lib/utils'
import type { PickItem } from '@/store/dataFigureStore'
import { FigureThumb } from './FigureThumb'

const KNOWN_CANDIDATES = [
  'points_mean', 'box_points', 'violin_points', 'ecdf_groups', 'bar_error', 'line_points', 'line_band',
  'line_units', 'scatter_fit', 'scatter', 'hist', 'ecdf', 'density', 'box_points_single',
]

/**
 * 选画法：2–4 张用真数据画出来的缩略图，同一图幅，**单选**，默认选推荐的那张（后端排第一的）。名字与那一句话来自后端的码
 * （`title_code` / `why_code`）。一组原生单选（每张卡里一个看不见的 radio：方向键切换、Tab 只停一个）。
 */
export function PickScene({
  items,
  selectedId,
  source,
  onChoose,
  onDraw,
  onBack,
}: {
  items: PickItem[]
  selectedId: string
  /** 来源数据文件名（小标签） */
  source: string
  onChoose: (id: string) => void
  onDraw: () => void
  onBack: () => void
}) {
  const { t } = useTranslation('ai')
  const known = (c: string) => KNOWN_CANDIDATES.includes(c)
  const title = (c: string) => (known(c) ? t(`dataFigureV2.candidate.${c}.title`) : t('dataFigureV2.candidate.other.title'))
  const why = (c: string) => (known(c) ? t(`dataFigureV2.candidate.${c}.why`) : t('dataFigureV2.candidate.other.why'))
  return (
    <div data-studio-pick className="flex w-full max-w-[1040px] flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h2 className="type-heading">{t('dataFigureV2.pick.title')}</h2>
        <span data-studio-source title={source} className="inline-flex h-5 max-w-full items-center gap-1 self-start rounded-full bg-surface-2 pl-1.5 pr-2 text-xs text-ink-2">
          <IconSlot className="text-ink-3">
            <File size={ICON_SIZE.sm} aria-hidden />
          </IconSlot>
          <span className="min-w-0 truncate">{source}</span>
        </span>
      </div>
      <span className="type-section -mb-3 text-ink-2" id="studio-pick-methods">
        {t('dataFigureV2.pick.methods')}
      </span>
      <div
        role="radiogroup"
        aria-labelledby="studio-pick-methods"
        className={cn('grid grid-cols-1 gap-3 sm:grid-cols-2', items.length === 3 && 'lg:grid-cols-3')}
      >
        {items.map((it, i) => {
          const on = it.id === selectedId
          return (
            <Card
              key={it.id}
              interactive
              selected={on}
              padding="sm"
              className="animate-rise-in"
              style={{ animationDelay: `calc(var(--duration-exit) * ${i})`, animationFillMode: 'backwards' }}
            >
              <label data-studio-option data-checked={on || undefined} className="flex cursor-default flex-col gap-2 rounded-md">
                <input
                  type="radio"
                  name="studio-pick"
                  className="sr-only"
                  checked={on}
                  onChange={() => onChoose(it.id)}
                  aria-label={title(it.title_code)}
                />
                <FigureThumb src={it.thumb} alt={title(it.title_code)} />
                <span className="flex items-center gap-2 px-1">
                  <span className={cn('min-w-0 flex-1 truncate text-base', on ? 'font-medium' : 'font-normal')}>{title(it.title_code)}</span>
                  {it.recommended && <Badge>{t('dataFigureV2.pick.recommended')}</Badge>}
                </span>
                <span className="line-clamp-2 px-1 pb-1 text-sm text-ink-2">{why(it.why_code)}</span>
              </label>
            </Card>
          )
        })}
      </div>
      {/* 备选多（4 张两行）时按钮也不被推到屏外：贴着舞台底边 */}
      <div className="sticky -bottom-6 z-sticky -mx-8 -mb-6 flex gap-2 bg-canvas px-8 pb-6 pt-3">
        <Button variant="primary" size="lg" data-studio-primary onClick={onDraw}>
          {t('dataFigureV2.pick.draw')}
        </Button>
        <Button variant="secondary" size="lg" data-studio-back onClick={onBack}>
          {t('dataFigureV2.pick.another')}
        </Button>
      </div>
    </div>
  )
}
