/**
 * 案例卡片：一张整理好的科研 Figure 样张，不是 SaaS 模板卡。
 *
 * 启动路径**五条等价**（拖拽只是增强，绝不是门槛）：
 *   ① 拖到中央试验台（仅鼠标指针；触屏交给滚动）
 *   ② 点击「开始体验」
 *   ③ 卡片聚焦后 Enter
 *   ④ Code Sheet 里的「用这个案例开始」（ExampleCodeSheet）
 *   ⑤ 触屏直接点卡片
 *
 * 拖拽用 Pointer Events + pointer capture 自己实现（三张卡不值一个 DnD 框架）：
 *   * 只认 pointerType === 'mouse'——触屏拖动与页面滚动冲突，触屏走点击；
 *   * 超过 6px 才算拖起（否则是点击）；拖过的 pointerup **吞掉**随后的 click；
 *   * 是否落进试验台由 stageRef 的包围盒判定，结果实时回报给 Landing
 *     （试验台据此点亮）；
 *   * pointercancel / capture 丢失 = 取消：回原位、不启动；
 *   * reduced-motion 下卡片不位移不缩放，拖动状态只用边框与试验台文字表达
 *     （§21 的硬要求）。
 */
import { useRef, useState, type RefObject } from 'react'
import { prefersReducedMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'
import type { PlaygroundExample } from '../examples'
import { pg } from '../pgText'

/** 拖动的判定阈值（px）：小于它是点击，不是拖 */
const DRAG_THRESHOLD = 6

export interface CardDragEvent {
  example: PlaygroundExample
  /** 指针当前是否悬在中央试验台上 */
  overStage: boolean
}

export function ExampleCard({
  example,
  stageRef,
  onLaunch,
  onViewCode,
  onDragChange,
}: {
  example: PlaygroundExample
  /** 中央试验台的 DOM（drop 判定用）；触屏/窄屏可为 null */
  stageRef: RefObject<HTMLElement | null> | null
  onLaunch: (example: PlaygroundExample) => void
  onViewCode: (example: PlaygroundExample) => void
  /** 拖动状态回报（null = 拖动结束/取消） */
  onDragChange?: (drag: CardDragEvent | null) => void
}) {
  const rootRef = useRef<HTMLElement>(null)
  const [dragging, setDragging] = useState(false)
  const [offset, setOffset] = useState<{ x: number; y: number } | null>(null)
  // pointerdown 起点与「这轮手势拖过没有」
  const gesture = useRef<{ x: number; y: number; dragged: boolean; over: boolean } | null>(null)
  // 拖过的那一轮要吞掉随后的 click（事件顺序是 pointerup → lostpointercapture
  // → click，所以这个标记必须独立于 gesture，不能靠 gesture 是否还在）
  const suppressClick = useRef(false)

  const title = pg(example.titleKey)

  const overStageAt = (x: number, y: number): boolean => {
    const el = stageRef?.current
    if (!el) return false
    const r = el.getBoundingClientRect()
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
  }

  const endDrag = (launchOver: boolean) => {
    setDragging(false)
    setOffset(null)
    onDragChange?.(null)
    // drop 落在试验台上才启动，且**只启动一次**（gesture 已清空，重入无效）
    if (launchOver) onLaunch(example)
  }

  const onPointerDown = (e: React.PointerEvent) => {
    // 只有鼠标左键拖；触屏与笔留给滚动/点击。按在卡片内的按钮上不算拖。
    if (e.pointerType !== 'mouse' || e.button !== 0 || !stageRef) return
    if ((e.target as HTMLElement).closest('button, a')) return
    gesture.current = { x: e.clientX, y: e.clientY, dragged: false, over: false }
    rootRef.current?.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const g = gesture.current
    if (!g) return
    const dx = e.clientX - g.x
    const dy = e.clientY - g.y
    if (!g.dragged) {
      if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return
      g.dragged = true
      setDragging(true)
    }
    g.over = overStageAt(e.clientX, e.clientY)
    // reduced-motion：卡片不位移，拖动状态靠边框（下面的 className）与
    // 试验台文字表达——功能一样，只是不动
    if (!prefersReducedMotion()) setOffset({ x: dx, y: dy })
    onDragChange?.({ example, overStage: g.over })
  }

  const onPointerUp = () => {
    const g = gesture.current
    if (!g) return
    gesture.current = null
    if (g.dragged) suppressClick.current = true
    endDrag(g.dragged && g.over)
  }

  const onPointerCancel = () => {
    // 系统打断（切窗口 / 手势冲突 / capture 丢失）：回原位、不启动
    if (!gesture.current) return
    gesture.current = null
    endDrag(false)
  }

  const onClickCapture = (e: React.MouseEvent) => {
    if (suppressClick.current) {
      suppressClick.current = false
      e.preventDefault()
      e.stopPropagation()
    }
  }

  return (
    <article
      ref={rootRef}
      tabIndex={0}
      aria-label={`${title} — ${pg(example.descriptionKey)}`}
      data-example-card={example.id}
      data-dragging={dragging || undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onLostPointerCapture={onPointerCancel}
      onClickCapture={onClickCapture}
      onClick={() => onLaunch(example)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && e.target === rootRef.current) {
          e.preventDefault()
          onLaunch(example)
        }
      }}
      style={
        offset
          ? { transform: `translate(${offset.x}px, ${offset.y}px) scale(1.03)` }
          : undefined
      }
      className={cn(
        'group relative flex cursor-pointer touch-manipulation flex-col overflow-hidden rounded-[10px] border bg-surface text-left',
        'transition-[border-color,box-shadow,transform] duration-150 ease-out',
        'focus-visible:focus-ring outline-none',
        dragging
          ? 'z-30 border-sel shadow-pop'
          : 'border-border hover:border-ink-faint hover:shadow-pop',
      )}
    >
      {dragging && (
        <span className="sr-only" role="status">
          {pg('draggingAnnounce', { name: title })}
        </span>
      )}

      {/* 封面：构建期从同一份源码真实执行生成（generate_playground_examples.py）。
          固定宽高比来自封面固有尺寸——不同图形不会让卡片跳动 */}
      <div className="border-b border-border bg-white p-3">
        <img
          src={example.thumbnail}
          width={example.thumbWidth}
          height={example.thumbHeight}
          alt={pg('coverAlt', { name: title })}
          draggable={false}
          className="pointer-events-none h-auto w-full select-none"
        />
      </div>

      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold text-ink">{title}</h3>
          {example.difficulty === 'starter' && (
            <span className="rounded-sm bg-sel/10 px-1.5 py-0.5 text-[10px] font-medium text-sel">
              {pg('starterBadge')}
            </span>
          )}
          <span className="ml-auto font-mono text-[10px] text-ink-faint">{example.filename}</span>
        </div>
        <p className="text-xs leading-relaxed text-ink-2">{pg(example.descriptionKey)}</p>
        <p className="text-[11px] text-ink-3">
          <span className="text-ink-faint">{pg('editableLabel')}</span>{' '}
          {pg(example.editableKey)}
        </p>

        <div className="mt-1.5 flex items-center gap-2">
          <button
            onClick={(e) => {
              e.stopPropagation()
              onViewCode(example)
            }}
            className="h-7 rounded-[6px] border border-border px-2.5 text-xs text-ink-2 transition-colors hover:border-ink-faint hover:text-ink"
          >
            {pg('viewCode')}
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onLaunch(example)
            }}
            className="h-7 rounded-[6px] bg-ink px-3 text-xs font-medium text-white transition-opacity hover:opacity-90"
          >
            {pg('startExample')}
          </button>
        </div>
      </div>
    </article>
  )
}
