import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { ChevronRight } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { Kbd } from '../ui/Kbd'
import { Segmented } from '../ui/Segmented'
import { cp, type GroupView, type PlanBox } from './changePlanModel'

export type CompareMode = 'slide' | 'side' | 'toggle'

const pct = (n: number) => `${n * 100}%`
const boxStyle = (b: PlanBox) => ({ left: pct(b[0]), top: pct(b[1]), width: pct(b[2]), height: pct(b[3]) })

/** 按住空格 = 看修改前（输入框 / 按钮上的空格是它们自己的，不抢） */
function useHoldSpace(enabled: boolean): boolean {
  const [hold, setHold] = useState(false)
  useEffect(() => {
    if (!enabled) return
    const own = (t: EventTarget | null) => t instanceof HTMLElement && !!t.closest('input, textarea, select, button, [role="slider"], [contenteditable="true"]')
    const down = (e: KeyboardEvent | globalThis.KeyboardEvent) => {
      if (e.code !== 'Space' || own(e.target)) return
      e.preventDefault()
      setHold(true)
    }
    const up = (e: globalThis.KeyboardEvent) => {
      if (e.code === 'Space') setHold(false)
    }
    const off = () => setHold(false)
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', off)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', off)
      setHold(false)
    }
  }, [enabled])
  return hold
}

/** 一张图的画框：纸底是兄弟（纸上只画纸墨），改动框与批注点叠在图上 */
function Frame({
  width,
  aspect,
  className,
  innerRef,
  children,
}: {
  width: string
  aspect: number
  className?: string
  innerRef?: (el: HTMLDivElement | null) => void
  children: React.ReactNode
}) {
  return (
    <div ref={innerRef} className={cn('relative shrink-0 rounded-sm shadow-page', className)} style={{ width, aspectRatio: aspect }} data-change-plan-frame>
      <span className="absolute inset-0 rounded-sm bg-paper" />
      {children}
    </div>
  )
}

const Img = ({ src, className, style }: { src: string; className?: string; style?: React.CSSProperties }) => (
  <img src={src} alt="" draggable={false} className={cn('absolute inset-0 size-full select-none rounded-sm object-contain', className)} style={style} />
)

/**
 * 修改方案弹窗的对比舞台：滑动对比（可拖的分隔线，把手 ‹ ›）· 并排 · 切换，按住空格看修改前（只在「切换」出现）。
 * 图按舞台可用空间等比放到最大；图面默认干净，改动处的细框只在右侧清单悬停 / 选中那一组时淡入（`activeId`）。
 */
export function CompareStage({
  before,
  after,
  aspect = 744 / 392,
  mode,
  groups,
  activeId,
}: {
  before: string
  after: string
  aspect?: number
  mode: CompareMode
  /** 勾选了的改动（没勾的这一处不改，也就不画框） */
  groups: GroupView[]
  activeId: string | null
}) {
  useTranslation('ai')
  // 按住空格看修改前只在「切换」里：滑动 / 并排本来就同时看得到两边
  const hold = useHoldSpace(mode === 'toggle')
  const [pos, setPos] = useState(50)
  const [dragging, setDragging] = useState(false)
  const [view, setView] = useState<'before' | 'after'>('after')
  const frameRef = useRef<HTMLDivElement>(null)

  const fit = `calc((100cqh - 4.75rem) * ${aspect})`
  const one = `min(100%, ${fit})`
  const half = `min(calc((100% - 1rem) / 2), ${fit})`

  const place = (e: ReactPointerEvent) => {
    const r = frameRef.current?.getBoundingClientRect()
    if (r && r.width > 0) setPos(Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100)))
  }
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    const step = e.shiftKey ? 10 : 2
    if (e.key === 'ArrowLeft') setPos((p) => Math.max(0, p - step))
    else if (e.key === 'ArrowRight') setPos((p) => Math.min(100, p + step))
    else if (e.key === 'Home') setPos(0)
    else if (e.key === 'End') setPos(100)
    else return
    e.preventDefault()
  }

  /** 改动框：after 侧画修改后的位置，before 侧画同一处改前的位置 */
  const boxes = (side: 'before' | 'after', visible: (b: PlanBox) => boolean = () => true) =>
    groups.flatMap((g) =>
      g.boxes.map((pair, i) => {
        const b = side === 'before' ? pair.before : pair.after
        // 改前没有这一处（新长出来的东西）：修改前那一侧不画框
        if (!b || !visible(b)) return null
        const on = g.key === activeId
        return (
          <span
            key={`${g.key}-${i}`}
            data-change-plan-box={g.key}
            data-active={on || undefined}
            // 图面默认纯净：只有清单里选中 / 悬停的那一项，它对应的那一处才淡入（1px 低饱和描边 + 约 6% 填充）
            className={cn(
              'pointer-events-none absolute rounded-xs bg-sel/6 inset-ring inset-ring-sel/60 transition-opacity duration-fast',
              on ? 'opacity-100' : 'opacity-0',
            )}
            style={boxStyle(b)}
          />
        )
      }),
    )

  const cap = (text: string) => <span className="type-meta">{text}</span>
  const effectiveView = hold ? 'before' : view
  // 三种模式共用一条纵向骨架：上一行说明（占位等高）· 图面 · 下一行控制（占位等高），图面顶边与大小不随模式跳
  const row = 'flex h-5 items-center'

  let body: React.ReactNode
  if (mode === 'slide') {
    const p = pos
    body = (
      <div className="flex flex-col gap-1.5" style={{ width: one }}>
        <div className={cn(row, 'justify-between')}>
          {cap(cp('dialog.before'))}
          {cap(cp('dialog.after'))}
        </div>
        <div ref={frameRef} className="relative w-full">
          <Frame width="100%" aspect={aspect}>
            <Img src={before} />
            <Img src={after} className={cn(!dragging && 'transition-[clip-path] duration-base')} style={{ clipPath: `inset(0 0 0 ${p}%)` }} />
            <div className="absolute inset-0">
              {boxes('before', (b) => b[0] + b[2] / 2 < p / 100)}
              {boxes('after', (b) => b[0] + b[2] / 2 >= p / 100)}
            </div>
            <div
              className={cn('absolute inset-y-0 -ml-4 flex w-8 touch-none justify-center', dragging ? 'cursor-grabbing' : 'cursor-grab')}
              style={{ left: `${p}%` }}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId)
                setDragging(true)
                place(e)
              }}
              onPointerMove={(e) => dragging && place(e)}
              onPointerUp={() => setDragging(false)}
              onPointerCancel={() => setDragging(false)}
              data-change-plan-divider
            >
              {/* 中轴线：深线 + 微白边，压在任何图上都读得出 */}
              <span aria-hidden className="w-px bg-shadow/80 ring-1 ring-paper/60" />
              {/* 把手：亮暖白圆钮（纸色，两套主题同值）+ 细描边 + 轻投影 */}
              <span
                role="slider"
                tabIndex={0}
                aria-label={cp('dialog.dragHandle')}
                aria-orientation="horizontal"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(p)}
                onKeyDown={onKey}
                className="absolute top-1/2 flex size-[26px] -translate-y-1/2 items-center justify-center rounded-full bg-paper text-paper-ink inset-ring inset-ring-paper-ink-2 shadow-pop outline-none focus-visible:focus-ring"
              >
                <ChevronRight size={ICON_SIZE.xs} className="rotate-180" />
                <ChevronRight size={ICON_SIZE.xs} />
              </span>
            </div>
          </Frame>
        </div>
        <div className={row} />
      </div>
    )
  } else if (mode === 'side') {
    body = (
      <div className="flex w-full flex-col gap-1.5">
        <div className="flex w-full items-center justify-center gap-4">
          <div className={row} style={{ width: half }}>
            {cap(cp('dialog.before'))}
          </div>
          <div className={row} style={{ width: half }}>
            {cap(cp('dialog.after'))}
          </div>
        </div>
        <div className="flex w-full items-start justify-center gap-4">
          <Frame width={half} aspect={aspect}>
            <Img src={before} />
            {boxes('before')}
          </Frame>
          <Frame width={half} aspect={aspect}>
            <Img src={after} />
            {boxes('after')}
          </Frame>
        </div>
        <div className={row} />
      </div>
    )
  } else {
    body = (
      <div className="flex flex-col gap-1.5" style={{ width: one }}>
        <div className={cn(row, 'justify-center')}>{cap(cp(effectiveView === 'before' ? 'dialog.before' : 'dialog.after'))}</div>
        <Frame width="100%" aspect={aspect}>
          <Img src={effectiveView === 'before' ? before : after} />
          {boxes(effectiveView)}
        </Frame>
        <div className="flex justify-center pt-1">
          <Segmented<'before' | 'after'>
            value={effectiveView}
            onChange={setView}
            ariaLabel={cp('dialog.compareLabel')}
            className="w-44"
            items={[
              { value: 'before', label: cp('dialog.before') },
              { value: 'after', label: cp('dialog.after') },
            ]}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-change-plan-stage={mode}>
      <div className={cn('flex min-h-0 flex-1 justify-center px-5 pb-1 pt-3', mode === 'side' ? 'items-center' : 'items-start')} style={{ containerType: 'size' }}>
        {body}
      </div>
      <p className="flex h-8 shrink-0 items-center gap-1 px-5 type-meta">
        {mode === 'toggle' && (
          <>
            {cp('dialog.hold')}
            <Kbd className="inset-ring inset-ring-border">{cp('dialog.space')}</Kbd>
            {cp('dialog.holdBefore')}
          </>
        )}
      </p>
    </div>
  )
}
