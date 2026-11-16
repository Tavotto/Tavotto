import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import type { PickItem } from '@/store/dataFigureStore'
import { FigureThumb } from './FigureThumb'
import { OverlayFooter } from './OverlayFooter'
import { useDrawingLine } from './ResultScenes'

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
  drawing = false,
  onDraw,
  onBack,
}: {
  items: PickItem[]
  selectedId: string
  /** 来源数据文件名（小标签） */
  source: string
  onChoose: (id: string) => void
  /** 出图中：版面不动，没选中的淡到 35% 不可点，选中的带扫光，底栏左边是状态句、主按钮禁用 */
  drawing?: boolean
  onDraw: () => void
  onBack: () => void
}) {
  const { t } = useTranslation('ai')
  const line = useDrawingLine(drawing)
  const known = (c: string) => KNOWN_CANDIDATES.includes(c)
  const title = (c: string) => (known(c) ? t(`dataFigureV2.candidate.${c}.title`) : t('dataFigureV2.candidate.other.title'))
  const why = (c: string) => (known(c) ? t(`dataFigureV2.candidate.${c}.why`) : t('dataFigureV2.candidate.other.why'))
  // 1 个画法单列（卡片贴着它收窄）；其余一律 2 列：3 个画法 = 上面两张、下面一张占一格，磁贴尺寸 2 / 3 / 4 个时一致
  const cols = items.length === 1 ? 'grid-cols-1' : 'grid-cols-1 min-[560px]:grid-cols-2'
  return (
    <div data-studio-pick {...(drawing ? { 'data-studio-working': '' } : {})} className="flex flex-col gap-3">
      <span className="type-section text-ink-2" id="studio-pick-methods" data-studio-source title={source}>
        {t('dataFigureV2.pick.methods')}
      </span>
      <div role="radiogroup" aria-labelledby="studio-pick-methods" className={cn('grid auto-rows-fr gap-3', cols)}>
        {items.map((it, i) => {
          const on = it.id === selectedId
          return (
            // 无边的浅底磁贴：选中 = 一圈描边环；悬停换深一档的底（仓库规矩：交互态不位移）；双击 = 画这张
            <label
              key={it.id}
              data-studio-option
              data-checked={on || undefined}
              onDoubleClick={() => {
                if (drawing) return
                onChoose(it.id)
                onDraw()
              }}
              className={cn(
                'flex h-full cursor-default animate-rise-in flex-col gap-2 rounded-lg bg-field p-2 outline-2 -outline-offset-2 transition-colors duration-fast ease-standard',
                'hover:bg-field-hover has-[:focus-visible]:focus-ring-inset',
                on ? 'outline outline-ink-3' : 'outline-transparent',
                drawing && !on && 'pointer-events-none opacity-35',
              )}
              style={{ animationDelay: `calc(var(--duration-exit) * ${i})`, animationFillMode: 'backwards' }}
            >
              <input
                type="radio"
                name="studio-pick"
                className="sr-only"
                checked={on}
                disabled={drawing}
                onChange={() => onChoose(it.id)}
                aria-label={title(it.title_code)}
              />
              <span className="relative block overflow-hidden rounded-md">
                <FigureThumb src={it.thumb} alt={title(it.title_code)} />
                {drawing && on && (
                  <span aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-1/3 animate-sweep bg-linear-to-r from-transparent via-paper/70 to-transparent" />
                )}
              </span>
              <span className="flex items-start gap-2 px-1">
                <span className={cn('min-w-0 flex-1 text-base', on ? 'font-medium' : 'font-normal')}>{title(it.title_code)}</span>
                {it.recommended && <Badge>{t('dataFigureV2.pick.recommended')}</Badge>}
              </span>
              <span className="px-1 pb-1 text-sm text-ink-2">{why(it.why_code)}</span>
            </label>
          )
        })}
      </div>
      <OverlayFooter
        left={
          drawing ? (
            <span className="text-sm text-ink-2" role="status">
              {line}
            </span>
          ) : (
            <Button variant="ghost" size="lg" data-studio-back onClick={onBack}>
              {t('dataFigureV2.back')}
            </Button>
          )
        }
        right={
          <Button variant="primary" size="lg" data-studio-primary disabled={drawing} onClick={onDraw}>
            {t('dataFigureV2.pick.draw')}
          </Button>
        }
      />
    </div>
  )
}
