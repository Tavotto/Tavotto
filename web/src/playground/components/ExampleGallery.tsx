/**
 * 案例库：三张真实 Figure 样张。布局职责在 Landing（≥1280 是左列，
 * 中屏两列网格，窄屏单列），这里只负责把卡片按 featured 优先排出来。
 */
import type { RefObject } from 'react'
import { EXAMPLES, type PlaygroundExample } from '../examples'
import { pg } from '../pgText'
import { ExampleCard, type CardDragEvent } from './ExampleCard'

export function ExampleGallery({
  stageRef,
  onLaunch,
  onViewCode,
  onDragChange,
  className,
}: {
  stageRef: RefObject<HTMLElement | null> | null
  onLaunch: (example: PlaygroundExample) => void
  onViewCode: (example: PlaygroundExample) => void
  onDragChange?: (drag: CardDragEvent | null) => void
  className?: string
}) {
  const ordered = [...EXAMPLES].sort((a, b) => Number(b.featured ?? false) - Number(a.featured ?? false))
  return (
    <section aria-label={pg('galleryLabel')} className={className}>
      {ordered.map((ex) => (
        <ExampleCard
          key={ex.id}
          example={ex}
          stageRef={stageRef}
          onLaunch={onLaunch}
          onViewCode={onViewCode}
          onDragChange={onDragChange}
        />
      ))}
    </section>
  )
}
