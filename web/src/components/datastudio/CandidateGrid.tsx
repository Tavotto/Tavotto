import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import type { Candidate, ThumbState } from '@/lib/dataFigureAdapter'
import { FigureThumb } from './FigureThumb'

const KNOWN_CANDIDATES = [
  'points_mean', 'box_points', 'violin_points', 'ecdf_groups', 'bar_error', 'line_points', 'line_band',
  'line_units', 'scatter_fit', 'scatter', 'hist', 'ecdf', 'density', 'box_points_single',
]

/** 出图超过这么久还没回来：换一句话，让人知道没卡住 */
const SLOW_MS = 8000

/** 出图中的状态句：正常「正在画…」，超过 8 秒换成慢提示 */
export function useDrawingLine(active: boolean): string {
  const { t } = useTranslation('ai')
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    setSlow(false)
    if (!active) return
    const id = window.setTimeout(() => setSlow(true), SLOW_MS)
    return () => window.clearTimeout(id)
  }, [active])
  return t(slow ? 'dataFigureV2.working.slow' : 'dataFigureV2.working.drawing')
}

/**
 * 候选画法网格：1–2 个画法一行，其余一律两列；尺寸随可用高度收放（容器查询：内容区高度减去文字与间距，按缩略图 16:10 反算宽度），
 * 四张要一屏放得下。每张 = 白底缩略图 + 名字 + 「推荐」+ 一句说明，整张可点，没有单独的「画这张」按钮。
 * 出图期间：被点的那张带扫光，其余淡到 35% 不可点。方向键在网格里移动，Enter 选。
 * 候选没出来之前（`items` 为空、`pending`）画骨架：尺寸与最终的一致，换上去不跳。
 */
export function CandidateGrid({
  items,
  thumbs,
  pending,
  drawingId,
  onPick,
  onThumb,
}: {
  /** 还没被去掉的候选（取不到图的已经不在里面）；第一张带「推荐」 */
  items: Candidate[]
  /** 每张缩略图各自的就绪状态 */
  thumbs: Record<string, ThumbState>
  /** 候选的文字还没回来：画 4 张骨架 */
  pending?: boolean
  drawingId: string | null
  onPick: (id: string) => void
  /** 一张缩略图取到了 / 取不到 */
  onThumb: (id: string, ok: boolean) => void
}) {
  const { t } = useTranslation('ai')
  const grid = useRef<HTMLDivElement>(null)
  const known = (c: string) => KNOWN_CANDIDATES.includes(c)
  const title = (c: string) => (known(c) ? t(`dataFigureV2.candidate.${c}.title`) : t('dataFigureV2.candidate.other.title'))
  const why = (c: string) => (known(c) ? t(`dataFigureV2.candidate.${c}.why`) : t('dataFigureV2.candidate.other.why'))
  const count = pending ? 4 : items.length
  const oneRow = count <= 2

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']
    if (!keys.includes(e.key)) return
    const tiles = [...(grid.current?.querySelectorAll<HTMLElement>('[data-candidate]') ?? [])]
    const i = tiles.indexOf(document.activeElement as HTMLElement)
    if (i < 0) return
    const cols = count === 1 ? 1 : 2
    const next = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'ArrowDown' ? i + cols : i - cols
    if (next < 0 || next >= tiles.length) return
    e.preventDefault()
    tiles[next]!.focus()
  }

  return (
    <div
      ref={grid}
      onKeyDown={onKeyDown}
      data-candidate-grid
      className={cn('grid gap-3.5', count === 1 ? 'grid-cols-1' : 'grid-cols-2')}
      style={{ maxWidth: count === 1 ? 423 : oneRow ? 860 : 'clamp(460px, calc((100cqh - 262px) * 1.6 + 46px), 860px)' }}
    >
      {pending
        ? [0, 1, 2, 3].map((i) => (
            <div key={i} data-candidate-skeleton className="flex flex-col rounded-panel bg-surface-hover p-2 pb-3">
              <FigureThumb src={null} alt="" className="aspect-16/10" />
              <span aria-hidden className="mx-1.5 mt-3 block h-4 w-2/5 rounded-sm bg-surface-active" />
              <span aria-hidden className="mx-1.5 mt-2 block h-3 w-4/5 rounded-sm bg-surface-active" />
            </div>
          ))
        : items.map((it, i) => {
            const mine = drawingId === it.id
            const dim = drawingId != null && !mine
            const ready = thumbs[it.id] === 'ready'
            return (
              <button
                key={it.id}
                type="button"
                data-candidate={it.id}
                data-drawing={mine || undefined}
                tabIndex={i === 0 ? 0 : -1}
                // 图没到的磁贴不可点：点了也没有东西可看
                disabled={drawingId != null || !ready}
                onClick={() => onPick(it.id)}
                className={cn(
                  'flex animate-fade-in flex-col rounded-panel bg-surface-hover p-2 pb-3 text-left outline-none transition-colors duration-fast ease-standard',
                  'enabled:hover:bg-surface-active focus-visible:focus-ring disabled:cursor-default',
                  dim && 'opacity-35',
                )}
              >
                <span className="relative block overflow-hidden rounded-md">
                  <FigureThumb src={it.thumb} alt="" onSettled={(ok) => onThumb(it.id, ok)} className="aspect-16/10" />
                  {mine && <span aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-1/3 animate-sweep bg-linear-to-r from-transparent via-paper/70 to-transparent" />}
                </span>
                <span className="mx-1.5 mt-3 flex items-center gap-2">
                  <span className="min-w-0 text-base font-medium text-ink">{title(it.title_code)}</span>
                  {i === 0 && <Badge>{t('dataFigureV2.pick.recommended')}</Badge>}
                </span>
                <span className="mx-1.5 mt-1 text-sm text-ink-2">{why(it.why_code)}</span>
              </button>
            )
          })}
    </div>
  )
}
