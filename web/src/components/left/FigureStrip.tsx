import { useMemo, useRef, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { readingOrder } from '@/lib/geometry'
import { isSubLabel } from '@/lib/stylePresets'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import type { CanvasObject, PanelObject, TextObject } from '@/types/document'
import { sp } from './styleRowParts'

/** 这张图的子图序号（落在图框里的 (a)(b)…，取最靠左上角的那个）；没有 = null */
export function subLabelOf(panel: PanelObject, texts: readonly TextObject[]): string | null {
  let best: { d: number; letter: string } | null = null
  for (const t of texts) {
    if (!isSubLabel(t) || t.hidden) continue
    if (t.x < panel.x - 1 || t.x > panel.x + panel.w || t.y < panel.y - 1 || t.y > panel.y + panel.h) continue
    const d = Math.hypot(t.x - panel.x, t.y - panel.y)
    if (!best || d < best.d) best = { d, letter: t.text.trim().replace(/[()]/g, '').toLowerCase() }
  }
  return best?.letter ?? null
}

/** 当前画布上的图，按阅读顺序（`readingOrder`，与 (a)(b)(c) 自动编号同一个顺序） */
export function figuresInOrder(objects: readonly CanvasObject[]): { panel: PanelObject; letter: string | null }[] {
  const texts = objects.filter((o): o is TextObject => o.type === 'text')
  const panels = objects.filter((o): o is PanelObject => o.type === 'panel' && !o.hidden)
  return readingOrder(panels).map((panel) => ({ panel, letter: subLabelOf(panel, texts) }))
}

/**
 * 单图模式顶上的一排小方块（一组切换按钮，`aria-pressed` 标当前）：每张图一个，按阅读顺序，有子图序号就写「a 图名」，没有就只写图名。
 * 当前图是浮起的实心块（不用彩色竖线，也不铺底色提示块）。放不下时横向滚动，不换行。
 * 点一个 = 在画布上选中它；← → 在图之间走。
 */
export function FigureStrip({ currentId }: { currentId: string | null }) {
  useTranslation('workspace')
  const objects = useDocumentStore((s) => s.doc.objects)
  const figures = useMemo(() => figuresInOrder(objects), [objects])
  const rootRef = useRef<HTMLDivElement>(null)
  if (!figures.length) return null
  const stop = figures.find((f) => f.panel.id === currentId) ?? figures[0]

  const select = (id: string) => useSelectionStore.getState().set([id])
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const delta = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!delta) return
    e.preventDefault()
    const at = figures.findIndex((f) => f.panel.id === currentId)
    const next = figures[Math.max(0, Math.min(figures.length - 1, (at < 0 ? 0 : at) + delta))]
    select(next.panel.id)
    rootRef.current?.querySelector<HTMLElement>(`[data-figure-chip="${CSS.escape(next.panel.id)}"]`)?.focus()
  }

  return (
    <div
      ref={rootRef}
      role="group"
      aria-label={sp('figuresLabel')}
      data-style-figures
      onKeyDown={onKeyDown}
      className="flex shrink-0 gap-1 overflow-x-auto px-3 pb-1.5 pt-0.5 [scrollbar-width:none]"
    >
      {figures.map(({ panel, letter }) => {
        const active = panel.id === currentId
        const name = panel.name ?? panel.fileId
        return (
          <button
            key={panel.id}
            type="button"
            aria-pressed={active}
            data-figure-chip={panel.id}
            tabIndex={panel === stop.panel ? 0 : -1}
            title={letter ? `(${letter}) ${name}` : name}
            onClick={() => select(panel.id)}
            className={cn(
              'flex h-6 max-w-[9rem] shrink-0 items-center gap-1 rounded-md px-2 text-xs outline-none transition-colors duration-fast focus-visible:focus-ring',
              active ? 'bg-thumb font-medium text-ink shadow-thumb' : 'text-ink-2 hover:bg-surface-hover hover:text-ink',
            )}
          >
            {letter && <span className="shrink-0 font-medium">{letter}</span>}
            <span className="min-w-0 truncate">{name}</span>
          </button>
        )
      })}
    </div>
  )
}
