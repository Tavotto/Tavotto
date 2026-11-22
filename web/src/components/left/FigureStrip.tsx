import { useMemo, useRef, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { readingOrder } from '@/lib/geometry'
import { isSubLabel } from '@/lib/stylePresets'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { focusLayoutPanel } from '@/store/workspace'
import type { CanvasObject, PanelObject, TextObject } from '@/types/document'
import { CanvasThumb } from '@/components/CanvasThumb'
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

const byLetter = (a: string, b: string) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0)

/**
 * 当前画布上的图的排法：有子图序号的按序号（a、b、c…）排在前面，没有序号的接在后面按阅读顺序
 * （`readingOrder`，与 (a)(b)(c) 自动编号同一个顺序）。
 */
export function figuresInOrder(objects: readonly CanvasObject[]): { panel: PanelObject; letter: string | null }[] {
  const texts = objects.filter((o): o is TextObject => o.type === 'text')
  const panels = objects.filter((o): o is PanelObject => o.type === 'panel' && !o.hidden)
  const all = readingOrder(panels).map((panel) => ({ panel, letter: subLabelOf(panel, texts) }))
  const lettered = all.filter((f) => f.letter).sort((a, b) => byLetter(a.letter!, b.letter!))
  return [...lettered, ...all.filter((f) => !f.letter)]
}

/**
 * 单图模式顶上的一排缩略图方块（一组切换按钮，`aria-pressed` 标当前）：每张图一块，上面是这张图自己的小图
 * （`CanvasThumb`，与画布列表同一份预览源，不另渲染），下面一行小字——有子图序号写序号，没有写图名（超长省略，全名在 title）。
 * 当前图是浮起的卡（thumb 底 + shadow-thumb + border-strong 描边；不用彩色竖线，也不铺色块）。放不下时横向滚动，不换行。
 * 点一个 = 在画布上选中它；← → 在图之间走。
 */
export function FigureStrip({ currentId }: { currentId: string | null }) {
  useTranslation('workspace')
  const objects = useDocumentStore((s) => s.doc.objects)
  const figures = useMemo(() => figuresInOrder(objects), [objects])
  const rootRef = useRef<HTMLDivElement>(null)
  if (!figures.length) return null
  const stop = figures.find((f) => f.panel.id === currentId) ?? figures[0]

  // 走「聚焦这张图」：顺带退出上一张图的图内编辑，否则样式仍改着那一张（Codex #2083 P2）
  const select = (id: string) => focusLayoutPanel(id)
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
      className="flex shrink-0 gap-1.5 overflow-x-auto px-3 pb-1.5 pt-0.5 [scrollbar-width:none]"
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
            data-figure-active={active || undefined}
            className={cn(
              'flex w-14 shrink-0 flex-col items-center gap-0.5 rounded-md p-1 outline-1 -outline-offset-1 outline-transparent transition-[outline-color,background-color,box-shadow] duration-fast focus-visible:focus-ring',
              active ? 'bg-thumb shadow-thumb outline-border-strong' : 'hover:outline-border',
            )}
          >
            <span className="flex h-[34px] w-11 shrink-0 overflow-hidden rounded-sm bg-paper ring-1 ring-inset ring-border" data-figure-thumb>
              <CanvasThumb
                flush
                page={{ w: panel.w, h: panel.h }}
                objects={[{ ...panel, x: 0, y: 0 }]}
                className="size-full"
              />
            </span>
            <span data-figure-caption className={cn('w-full truncate text-center text-xs', active ? 'font-medium text-ink' : 'text-ink-3')}>
              {letter ?? name}
            </span>
          </button>
        )
      })}
    </div>
  )
}
