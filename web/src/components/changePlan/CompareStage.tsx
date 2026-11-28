import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { pickElementStack } from '@/canvas/interactions'
import type { Manifest, ManifestElement, ReviewAnchor } from '@/lib/api'
import type { PlanNote } from '@/store/changePlanStore'
import { cn } from '@/lib/utils'
import { geomPathD } from '@/lib/pathGeom'
import { ReviewMark } from '../review/ReviewMark'
import { ChevronRight, Pencil } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { Kbd } from '../ui/Kbd'
import { Popover } from '../ui/Popover'
import { Segmented } from '../ui/Segmented'
import { cp, type GroupView, type PlanBox } from './changePlanModel'
import { NoteEditor } from './NoteEditor'
import { elementTarget, type NoteTarget } from './noteTarget'

export type { NoteTarget }

export type CompareMode = 'slide' | 'side' | 'toggle'
/** 舞台的四种画法：三种对比 + 批注（只有「修改后」的大图，点元素 / 框选 / 整体写批注） */
export type StageMode = CompareMode | 'annotate'


/** 正在写 / 改的批注：`id` 有 = 改已有的 */
export interface NoteEditing {
  id?: string
  target: NoteTarget
}

/** 对比模式下，高级版在「修改后」一侧的元素上直接开始写批注 */
export interface QuickNote {
  manifest: Manifest | null
  /** 鼠标第一次进到图上：去取元素表 */
  onArm: () => void
  onPick: (target: NoteTarget) => void
}

export interface CompareNoting {
  manifest: Manifest | null
  notes: PlanNote[]
  editing: NoteEditing | null
  onEdit: (e: NoteEditing | null) => void
  onSave: (id: string | undefined, target: NoteTarget, text: string) => void
  onRemove: (id: string) => void
}

const ELEMENT_BOX_MIN = 0.004
const DRAG_PX = 4

/** 批注框在图上的锚位：元素 / 框选 = 它的框；整体 = 图顶边中点 */
const anchorBox = (a: ReviewAnchor): PlanBox => (a.kind === 'overall' ? [0.5, 0.02, 0, 0] : (a.box as PlanBox))

const pctOf = (r: number) => Math.round(Math.min(1, Math.max(0, r)) * 1e4) / 100
const pct = (n: number) => `${n * 100}%`
const boxStyle = (b: PlanBox) => ({ left: pct(b[0]), top: pct(b[1]), width: pct(b[2]), height: pct(b[3]) })

/**
 * 「修改后」的图上点选的一层（盖在画框里）：悬停 = 元素细描边轻高亮（`pickElementStack` 与画布同一套命中）；
 * `annotate`：点一下选元素、拖一下框一块（`onRegion`）；`quick`（对比模式）：高亮 + 一枚「写批注」小胶囊，点一下去批注。
 * `left`：只盖分隔线右侧的百分比（滑动对比里只有分隔线右边是「修改后」）。
 */
const hasPath = (el: ManifestElement | null): boolean => !!el?.geometry && geomPathD(el.geometry, ([x, y]) => ({ x, y })) !== ''

/**
 * 悬停描示沿元素的**真实路径**（与画布 `OverlaySvg` 的 GeometryOutline 同一个道理）：曲线的包围盒里大半是空白，
 * 画成框用户认不出点中的是哪条线（两条交叉曲线的框一模一样）。只描边、不着色；`clip` 是引擎给的子图裁剪框。
 * SVG 盖满整个画框（滑动对比里 PickLayer 只占分隔线右侧，按 `left` 往回伸），坐标就是图的分数坐标。
 */
const VB = 1000

function HoverPath({ el, left }: { el: ManifestElement; left: number }) {
  const geom = el.geometry
  if (!geom) return null
  // `geomPathD` 把坐标保留两位小数（它原本吃屏幕像素）：在 0–1000 的坐标系里画，精度 0.001%，小标记也不塌成一点
  const d = geomPathD(geom, ([x, y]) => ({ x: x * VB, y: y * VB }))
  if (!d) return null
  const c = geom.clip
  const clipId = `cp-hover-clip-${el.gid.replace(/[^A-Za-z0-9_-]/g, '_')}`
  const k = 1 - left / 100
  return (
    <svg
      aria-hidden
      data-change-plan-hover-path={el.gid}
      className="pointer-events-none absolute inset-y-0 h-full overflow-visible"
      style={{ left: `${-left / k}%`, width: `${100 / k}%` }}
      viewBox={`0 0 ${VB} ${VB}`}
      preserveAspectRatio="none"
    >
      {c && (
        <clipPath id={clipId}>
          <rect x={c[0] * VB} y={c[1] * VB} width={c[2] * VB} height={c[3] * VB} />
        </clipPath>
      )}
      <path
        d={d}
        clipPath={c ? `url(#${clipId})` : undefined}
        fill="none"
        stroke="var(--color-sel)"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

function PickLayer({
  manifest,
  mode,
  left = 0,
  onArm,
  onPick,
  onRegion,
}: {
  manifest: Manifest | null
  mode: 'annotate' | 'quick'
  left?: number
  onArm?: () => void
  onPick: (target: NoteTarget) => void
  onRegion?: (target: NoteTarget) => void
}) {
  const [hover, setHover] = useState<ManifestElement | null>(null)
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null)
  const down = useRef<{ x: number; y: number } | null>(null)
  const at = (e: ReactPointerEvent): [number, number] | null => {
    const r = e.currentTarget.closest('[data-change-plan-frame]')?.getBoundingClientRect()
    if (!r || r.width <= 0 || r.height <= 0) return null
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]
  }
  const pickAt = (f: [number, number]) => pickElementStack(manifest, f[0], f[1])[0] ?? null
  const hb = hover ? hover.bbox : null
  return (
    <div
      data-change-plan-pick={mode}
      className={cn('absolute inset-y-0 right-0 touch-none', mode === 'annotate' && 'cursor-crosshair')}
      style={{ left: `${left}%` }}
      onPointerEnter={() => onArm?.()}
      onPointerLeave={() => !down.current && setHover(null)}
      onPointerDown={(e) => {
        if (mode !== 'annotate' || e.button !== 0) return
        const f = at(e)
        if (!f) return
        e.currentTarget.setPointerCapture(e.pointerId)
        down.current = { x: e.clientX, y: e.clientY }
        setDrag({ x0: f[0], y0: f[1], x1: f[0], y1: f[1] })
      }}
      onPointerMove={(e) => {
        const f = at(e)
        if (!f) return
        if (down.current) {
          setDrag((d) => (d ? { ...d, x1: f[0], y1: f[1] } : d))
          return
        }
        setHover(pickAt(f))
      }}
      onPointerUp={(e) => {
        const start = down.current
        down.current = null
        const f = at(e)
        const d = drag
        setDrag(null)
        if (!start || !f || !d) return
        if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < DRAG_PX) {
          const el = pickAt(f)
          if (el) onPick(elementTarget(el))
          return
        }
        const x = Math.min(d.x0, f[0])
        const y = Math.min(d.y0, f[1])
        const w = Math.abs(f[0] - d.x0)
        const h = Math.abs(f[1] - d.y0)
        if (w >= ELEMENT_BOX_MIN && h >= ELEMENT_BOX_MIN) onRegion?.({ anchor: { kind: 'region', box: [x, y, w, h].map((v) => Math.round(v * 1e4) / 1e4) as PlanBox }, label: cp('dialog.noteRegion') })
      }}
      onPointerCancel={() => {
        down.current = null
        setDrag(null)
      }}
      onClick={(e) => {
        // 对比模式：点一下 = 去写批注（annotate 的点选在 pointerup 里做）
        if (mode !== 'quick' || !hover) return
        e.stopPropagation()
        onPick(elementTarget(hover))
      }}
    >
      {hover && !drag && <HoverPath el={hover} left={left} />}
      {hb && !drag && (
        <span
          aria-hidden
          data-change-plan-hover={hover?.gid}
          // 有真实路径的元素（曲线、多边形、填充…）沿路径描（`HoverPath`），这里只留透明的定位框给「写批注」小胶囊；没有路径的（文字等）照旧画框
          className={cn('pointer-events-none absolute rounded-xs', !hasPath(hover) && 'bg-sel/6 inset-ring inset-ring-sel/60')}
          style={{ left: `${(hb[0] * 100 - left) / (1 - left / 100)}%`, top: pct(hb[1]), width: `${(hb[2] * 100) / (1 - left / 100)}%`, height: pct(hb[3]) }}
        >
          {mode === 'quick' && (
            <span
              data-change-plan-quick={hover?.gid}
              className={cn(
                'absolute left-0 inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-full bg-surface px-2 text-xs text-ink shadow-pop',
                hb[1] > 0.08 ? '-top-6' : 'top-full mt-1',
              )}
            >
              <Pencil size={ICON_SIZE.sm} aria-hidden />
              {cp('dialog.writeNote')}
            </span>
          )}
        </span>
      )}
      {drag && (
        <span
          aria-hidden
          className="pointer-events-none absolute rounded-xs bg-sel/6 inset-ring inset-ring-sel/60"
          style={{
            left: `${(Math.min(drag.x0, drag.x1) * 100 - left) / (1 - left / 100)}%`,
            top: pct(Math.min(drag.y0, drag.y1)),
            width: `${(Math.abs(drag.x1 - drag.x0) * 100) / (1 - left / 100)}%`,
            height: pct(Math.abs(drag.y1 - drag.y0)),
          }}
        />
      )}
    </div>
  )
}

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
  afterBusy = false,
  noting,
  quick,
  labels,
}: {
  before: string
  after: string
  aspect?: number
  mode: StageMode
  /** 勾选了的改动（没勾的这一处不改，也就不画框） */
  groups: GroupView[]
  activeId: string | null
  /** 「修改后」正在按勾选重新出图：保留旧图，淡一档（不做呼吸） */
  afterBusy?: boolean
  /** 批注模式（`mode === 'annotate'`）：点选 / 框选写批注、已有批注的编号 */
  noting?: CompareNoting
  /** 对比模式：高级版悬停「修改后」一侧的元素 = 轻高亮 +「写批注」小胶囊 */
  quick?: QuickNote
  /** 两侧的叫法（第 2 版起左边是上一版：「第 N−1 版 / 第 N 版」）；不给 = 修改前 / 修改后 */
  labels?: { before: string; after: string }
}) {
  useTranslation('ai')
  // 批注框以图卡的边为界：靠近边沿向内翻转，不跨出图卡
  const [frameEl, setFrameEl] = useState<HTMLDivElement | null>(null)
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

  /**
   * 改动框：每个框取它所在那一侧的 manifest 的框（before 侧画改前的位置，after 侧画改后的位置）。
   * 滑动对比里 `split`（分隔线位置，0–1）把框按分隔线裁开：左边只画 before 框在线左的部分，右边只画 after 框在线右的部分——
   * 一个元素跨过分隔线时两侧各画各自的那一段（clip-path 裁，不会在分隔线上多出一道边）。
   */
  const boxes = (side: 'before' | 'after', split?: number) =>
    groups.flatMap((g) =>
      g.boxes.map((pair, i) => {
        const b = side === 'before' ? pair.before : pair.after
        // 改前没有这一处（新长出来的东西）：修改前那一侧不画框；只是自动留边距挪了子图的一处不画整块大框
        if (!b || pair.room) return null
        let clip: string | undefined
        if (split !== undefined) {
          const w = Math.max(b[2], 1e-6)
          if (side === 'before') {
            if (b[0] >= split) return null
            clip = `inset(0 ${pctOf((b[0] + b[2] - split) / w)}% 0 0)`
          } else {
            if (b[0] + b[2] <= split) return null
            clip = `inset(0 0 0 ${pctOf((split - b[0]) / w)}%)`
          }
        }
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
            style={clip ? { ...boxStyle(b), clipPath: clip } : boxStyle(b)}
          />
        )
      }),
    )

  /** 批注模式：已有批注的编号（点一下回到它的批注框改）+ 正在写的那一处的批注框（`Popover` 锚在那一处） */
  const noteLayer = () => {
    if (!noting) return null
    const { editing } = noting
    const existing = editing?.id ? noting.notes.find((n) => n.id === editing.id) : undefined
    const box = editing ? anchorBox(editing.target.anchor) : null
    return (
      <>
        {noting.notes.map((n, i) => {
          const a = n.anchor
          const open = () => noting.onEdit({ id: n.id, target: { anchor: a, label: n.label } })
          if (a.kind === 'overall')
            return (
              <button key={n.id} type="button" data-change-plan-mark={n.id} aria-label={n.label} title={n.label} onClick={open} className="absolute -left-2 -top-2 rounded-full outline-none focus-visible:focus-ring">
                <ReviewMark n={i + 1} overall onPaper solid />
              </button>
            )
          return (
            <span key={n.id} className="pointer-events-none absolute" style={boxStyle(a.box as PlanBox)}>
              {/* 框画在纸面上：用纸上的墨（两套主题同值），深色主题下也看得见 */}
              {a.kind === 'region' && <span aria-hidden data-change-plan-region-box className="absolute inset-0 rounded-xs inset-ring inset-ring-paper-ink-3" />}
              <button type="button" data-change-plan-mark={n.id} aria-label={n.label} title={n.label} onClick={open} className="pointer-events-auto absolute -right-2 -top-2 rounded-full outline-none focus-visible:focus-ring">
                <ReviewMark n={i + 1} onPaper solid />
              </button>
            </span>
          )
        })}
        {editing && box && (
          <Popover
            key={editing.id ?? JSON.stringify(editing.target.anchor)}
            width={240}
            arrow
            boundary={frameEl}
            side={box[0] + box[2] / 2 < 0.2 ? 'left' : 'bottom'}
            align="center"
            open
            onOpenChange={(v) => !v && noting.onEdit(null)}
            trigger={<span aria-hidden className="pointer-events-none absolute" style={boxStyle(box)} />}
          >
            <NoteEditor
              label={editing.target.label}
              initial={existing?.text}
              onSave={(text) => noting.onSave(editing.id, editing.target, text)}
              onRemove={existing ? () => noting.onRemove(existing.id) : undefined}
              onDone={() => noting.onEdit(null)}
            />
          </Popover>
        )}
      </>
    )
  }

  /** 对比模式：「修改后」一侧的点选层（`left`：滑动对比里只盖分隔线右边） */
  const quickLayer = (left = 0) => (quick ? <PickLayer manifest={quick.manifest} mode="quick" left={left} onArm={quick.onArm} onPick={quick.onPick} /> : null)

  const busyCls = afterBusy ? 'opacity-60' : undefined
  const cap = (text: string) => <span className="type-meta">{text}</span>
  const beforeText = labels?.before ?? cp('dialog.before')
  const afterText = labels?.after ?? cp('dialog.after')
  const effectiveView = hold ? 'before' : view
  // 三种模式共用一条纵向骨架：上一行说明（占位等高）· 图面 · 下一行控制（占位等高），图面顶边与大小不随模式跳
  const row = 'flex h-5 items-center'

  let body: React.ReactNode
  if (mode === 'slide') {
    const p = pos
    body = (
      <div className="flex flex-col gap-1.5" style={{ width: one }}>
        <div className={cn(row, 'justify-between')}>
          {cap(beforeText)}
          {cap(afterText)}
        </div>
        <div ref={frameRef} className="relative w-full">
          <Frame width="100%" aspect={aspect}>
            <Img src={before} />
            <Img src={after} className={cn(!dragging && 'transition-[clip-path] duration-base', busyCls)} style={{ clipPath: `inset(0 0 0 ${p}%)` }} />
            <div className="absolute inset-0">
              {boxes('before', p / 100)}
              {boxes('after', p / 100)}
            </div>
            {!hold && quickLayer(p)}
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
                className="absolute top-1/3 flex size-[26px] -translate-y-1/2 items-center justify-center rounded-full bg-paper text-paper-ink inset-ring inset-ring-paper-ink-2 shadow-pop outline-none focus-visible:focus-ring"
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
  } else if (mode === 'annotate') {
    body = (
      <div className="flex flex-col gap-1.5" style={{ width: one }}>
        <div className={row} />
        <Frame width="100%" aspect={aspect} innerRef={setFrameEl}>
          <Img src={after} />
          {noting && <PickLayer manifest={noting.manifest} mode="annotate" onPick={(t) => noting.onEdit({ target: t })} onRegion={(t) => noting.onEdit({ target: t })} />}
          {noteLayer()}
        </Frame>
        <div className={row} />
      </div>
    )
  } else if (mode === 'side') {
    body = (
      <div className="flex w-full flex-col gap-1.5">
        <div className="flex w-full items-center justify-center gap-4">
          <div className={row} style={{ width: half }}>
            {cap(beforeText)}
          </div>
          <div className={row} style={{ width: half }}>
            {cap(afterText)}
          </div>
        </div>
        <div className="flex w-full items-start justify-center gap-4">
          <Frame width={half} aspect={aspect}>
            <Img src={before} />
            {boxes('before')}
          </Frame>
          <Frame width={half} aspect={aspect}>
            <Img src={after} className={busyCls} />
            {boxes('after')}
            {quickLayer()}
          </Frame>
        </div>
        <div className={row} />
      </div>
    )
  } else {
    body = (
      <div className="flex flex-col gap-1.5" style={{ width: one }}>
        <div className={cn(row, 'justify-center')}>{cap(effectiveView === 'before' ? beforeText : afterText)}</div>
        <Frame width="100%" aspect={aspect}>
          <Img src={effectiveView === 'before' ? before : after} className={effectiveView === 'after' ? busyCls : undefined} />
          {boxes(effectiveView)}
          {effectiveView === 'after' && quickLayer()}
        </Frame>
        <div className="flex justify-center pt-1">
          <Segmented<'before' | 'after'>
            value={effectiveView}
            onChange={setView}
            ariaLabel={cp('dialog.compareLabel')}
            className="w-44"
            items={[
              { value: 'before', label: beforeText },
              { value: 'after', label: afterText },
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
            {labels ? cp('dialog.holdSee', { label: labels.before }) : cp('dialog.holdBefore')}
          </>
        )}
      </p>
    </div>
  )
}
