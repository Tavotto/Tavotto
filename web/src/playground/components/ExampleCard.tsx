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
 *   * reduced-motion 下卡片不位移不缩放，拖动状态只用轮廓与试验台文字表达
 *     （§21 的硬要求）。
 */
import { useRef, useState, type RefObject } from 'react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
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
  const rootRef = useRef<HTMLDivElement>(null)
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
    <Card
      ref={rootRef}
      role="article"
      appearance="raised"
      padding="none"
      interactive
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
      // 整卡可点是第 ⑤ 条路（触屏直接点卡片）。卡里的两颗按钮各自 stopPropagation，
      // 「查看代码」不会被整卡的启动吞掉（cardDrag.test / landing.test 看护）
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
        // Card interactive（2026-10-07 设计审计 §5 / §10.4）：外 12 圆角、hover 1px 轮廓、
        // 卡里任何控件聚焦时整卡一圈 accent 环。可拖（鼠标）的卡用抓手
        'group flex touch-manipulation flex-col text-left outline-none',
        'transition-[outline-color,box-shadow,transform] duration-fast ease-out',
        stageRef && 'cursor-grab active:cursor-grabbing',
        // 拖起：accent 轮廓（接收 / 拖动态用 accent；sel 只给画布）+ 浮层投影
        dragging && 'z-drawer shadow-pop outline-accent hover:outline-accent',
      )}
    >
      {dragging && (
        <span className="sr-only" role="status">
          {pg('draggingAnnounce', { name: title })}
        </span>
      )}

      {/* 封面：构建期从同一份源码真实执行生成（generate_playground_examples.py）。
          固定宽高比来自封面固有尺寸——不同图形不会让卡片跳动。
          图坐在画布灰上（与工作台里「纸在画布上」同一种关系）；外 12 = 内 8 + 4 */}
      <div className="p-1">
        <div className="rounded-md bg-canvas p-3">
          <img
            src={example.thumbnail}
            width={example.thumbWidth}
            height={example.thumbHeight}
            alt={pg('coverAlt', { name: title })}
            draggable={false}
            className="pointer-events-none h-auto w-full select-none"
          />
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-1.5 px-3 pb-3 pt-2">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-medium text-ink">{title}</h3>
          {example.difficulty === 'starter' && <Badge tone="accent">{pg('starterBadge')}</Badge>}
          <span className="type-meta ml-auto font-mono">{example.filename}</span>
        </div>
        <p className="text-xs leading-relaxed text-ink-2">{pg(example.descriptionKey)}</p>
        <p className="text-xs text-ink-2">
          <span className="text-ink-3">{pg('editableLabel')}</span>{' '}
          {pg(example.editableKey)}
        </p>

        <div className="mt-1.5 flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            data-example-view-code
            onClick={(e) => {
              e.stopPropagation()
              onViewCode(example)
            }}
          >
            {pg('viewCode')}
          </Button>
          {/* 一屏一个填色主动作：只有主推案例（featured）的「开始体验」是 primary，
              其余卡片同一句话降为次按钮——三颗黑钮并排就没有「先点哪个」了 */}
          <Button
            variant={example.featured ? 'primary' : 'secondary'}
            size="sm"
            onClick={(e) => {
              e.stopPropagation()
              onLaunch(example)
            }}
          >
            {pg('startExample')}
          </Button>
        </div>
      </div>
    </Card>
  )
}
