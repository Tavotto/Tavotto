import { useTranslation } from 'react-i18next'
import type { PlanEntry } from '@/store/changePlanStore'
import { useUiStore } from '@/store/uiStore'
import { ReviewMark } from '../review/ReviewMark'
import { listJoin } from '@/i18n/format'
import { cp } from './changePlanModel'
import { FigureThumb } from './FigureThumb'
import { useImageAspect, usePanelThumbSrc, usePlanPanel } from './usePlanFigure'

/** 小圆标在缩略图上互相靠得太近就往下错开（14px 的圆在 ~230px 宽的图上，0.06 的距离就叠了） */
function spreadMarks<T extends { x: number; y: number }>(marks: T[]): T[] {
  const placed: T[] = []
  for (const m of marks) {
    let y = m.y
    while (placed.some((p) => Math.abs(p.x - m.x) < 0.07 && Math.abs(p.y - y) < 0.1)) y += 0.1
    placed.push({ ...m, y: Math.min(y, 0.95) })
  }
  return placed
}

/**
 * 助手里用户一侧的「审阅意见」卡：把审阅页签里那一批意见当作「用户说的话」递给助手（一次「交给 Agent 修改」的请求）。
 * 右对齐、收窄，底与用户气泡 / 附件气泡同一档（surface-hover）；缩略图是这张图，意见在图上的位置用与审阅页签同一套编号小圆标出
 * （整体意见没有位置，不画圆点）。「看意见 ›」是一行灰字文字钮，回到审阅页签。
 */
export function ReviewNoteCard({ entry }: { entry: PlanEntry }) {
  useTranslation('ai')
  const panel = usePlanPanel(entry)
  const src = usePanelThumbSrc(panel, !entry.beforeSrc)
  const aspect = useImageAspect(entry.beforeSrc ?? src)
  const comments = entry.comments
  const count = comments.length || entry.commentCount
  if (!count) return null
  const marks = spreadMarks(
    comments.flatMap((c, i) => {
      const a = c.anchor
      return a.kind === 'overall' ? [] : [{ n: i + 1, x: a.box[0] + a.box[2] / 2, y: a.box[1] + a.box[3] / 2, id: c.id }]
    }),
  )
  const who = listJoin([...new Set(comments.map((c) => c.author))])
  return (
    <div data-change-plan-note-card className="flex justify-end">
      <div className="w-[min(78%,15.5rem)] rounded-lg bg-surface-hover p-1.5">
        <FigureThumb src={entry.beforeSrc ?? src} alt={cp('card.beforeAlt')} aspect={aspect}>
          {marks.map((m) => (
            <ReviewMark
              key={m.id}
              n={m.n}
              onPaper
              solid
              className="absolute size-3.5 -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${m.x * 100}%`, top: `${m.y * 100}%` }}
            />
          ))}
        </FigureThumb>
        <div className="flex flex-col gap-0.5 px-1.5 pb-0.5 pt-2.5">
          <div className="flex items-baseline gap-2">
            <p className="min-w-0 flex-1 truncate text-sm text-ink">{cp('reviewNote.title', { count })}</p>
            <button
              type="button"
              data-change-plan-open-review
              onClick={() => useUiStore.getState().setRightTab('review')}
              className="shrink-0 rounded-sm text-xs text-ink-3 outline-none transition-colors duration-fast hover:text-ink focus-visible:focus-ring"
            >
              {cp('reviewNote.open')}
            </button>
          </div>
          {who && (
            <p className="truncate type-meta" title={who}>
              {who}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
