import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { versionThumbUrl, type LayoutVersionMeta } from '@/lib/api'
import { versionDisplayName } from '@/lib/versionSummary'
import { versionKind } from '@/lib/timelineGroups'
import { prefersReducedMotion } from '@/lib/motion'
import { formatDate, formatTime } from '@/i18n/format'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { CanvasThumb } from './CanvasThumb'

const vd = (key: string, values?: Record<string, unknown>) =>
  translate(`versions.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 条的高度与竖线的中线（px） */
const ZONE_H = 40
const MID = ZONE_H / 2
/** 条两端留白、换天处多留的空隙（px） */
const PAD = 8
const DAY_GAP = 8
/** 浮卡宽度（px）：右栏最窄时也放得下 */
const PEEK_W = 224
/** 放大影响到几根邻居（以节点为单位的高斯宽度） */
const SPREAD = 2.2
/** 一根只有一两像素宽时的粘性：指针离开当前节点超过这么多个节点才换 */
const STICKY = 0.65

const label = (v: LayoutVersionMeta) => versionDisplayName(v) || formatTime(v.ts)
const dayKey = (ts: number) => new Date(ts).toDateString()
/** 竖线的颜色档：选中的（卡片开着的那一版）> 命名节点 > 其余 */
const toneOf = (v: LayoutVersionMeta, selected: boolean) =>
  selected ? 'selected' : versionKind(v) === 'named' ? 'named' : 'auto'

interface Box {
  x: number
  w: number
  h: number
  o: number
  /** 0 = 竖线，1 = 标签 */
  c: number
}

/**
 * 时间线页签顶上的版本竖线条：一根竖线一个节点，左旧右新。
 *
 * 只做「快速找到那一版」：划过时只显示**拍节点那一刻存好的缩略图**（`versionThumbUrl`，
 * 没有就是列表一并带回的草图），不取正文、不按 overrides 出图——那条路一次几百毫秒到几秒，
 * 跟不上指针。点下去交给调用方（打开行上那张对比小卡片），出图仍只在那里发生。
 *
 * 几何：收起时竖线均匀铺满整条；展开时总宽不变（右栏放不下更宽的），指着的那根变成标签、
 * 邻居按距离放大。「指针 → 第几个节点」一律按**收起时的均匀几何**换算，放大不反过来
 * 改换算，否则标签一让位、指针下的节点就跟着换，来回抖。位置每帧直接写 style（不经 React），
 * React 只在节点集合或浮卡指向的那一版变了时重渲。
 */
export function TimelineScrubber({
  docId,
  versions,
  selectedId,
  summaryOf,
  onPeek,
  onPick,
}: {
  docId: string
  /** 列表此刻显示的那些节点，**左旧右新** */
  versions: LayoutVersionMeta[]
  selectedId: string | null
  /** 这一版比上一版变了什么（列表行第二行同一句） */
  summaryOf: (v: LayoutVersionMeta) => string
  /** 划过到哪一版（null = 离开）：调用方据此把列表滚到那一行 */
  onPeek: (id: string | null) => void
  /** 点下去 / 回车 */
  onPick: (id: string) => void
}) {
  const zoneRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const peekRef = useRef<HTMLDivElement>(null)
  const measureRef = useRef<HTMLSpanElement>(null)
  const tickRefs = useRef<(HTMLDivElement | null)[]>([])
  const chipW = useRef<number[]>([])
  const cur = useRef<Box[]>([])
  const trackCur = useRef<{ h: number } | null>(null)
  const hover = useRef({ on: false, f: 0, active: -1 })
  const preloaded = useRef(new Set<string>())
  const [peek, setPeek] = useState<number>(-1)
  const peekIndex = useRef(-1)
  const raf = useRef(0)
  const n = versions.length
  const selectedIdx = selectedId ? versions.findIndex((v) => v.id === selectedId) : -1

  // 回调与选中放进 ref：动画帧与指针处理读的是此刻的值，不因父组件重渲重挂监听
  const live = useRef({ versions, selectedIdx, onPeek, onPick })
  live.current = { versions, selectedIdx, onPeek, onPick }

  const dayStart = (vs: LayoutVersionMeta[], i: number) => i > 0 && dayKey(vs[i].ts) !== dayKey(vs[i - 1].ts)

  /** 收起时的均匀几何：每根竖线的中心 x（也是指针换算的依据） */
  const rest = useCallback(() => {
    const vs = live.current.versions
    const W = zoneRef.current?.clientWidth ?? 0
    const gaps = vs.reduce((k, _, i) => k + (dayStart(vs, i) ? 1 : 0), 0)
    const slot = vs.length > 1 ? (W - PAD * 2 - gaps * DAY_GAP) / (vs.length - 1) : 0
    const xs: number[] = []
    let x = vs.length > 1 ? PAD : W / 2
    for (let i = 0; i < vs.length; i++) {
      if (dayStart(vs, i)) x += DAY_GAP
      xs.push(x)
      x += slot
    }
    return { W, xs, gaps }
  }, [])

  const targets = useCallback((): { boxes: Box[]; trackH: number } => {
    const { versions: vs, selectedIdx: sel } = live.current
    const { W, xs, gaps } = rest()
    const h = hover.current
    if (!h.on) {
      return {
        trackH: 18,
        boxes: vs.map((v, i) => {
          const named = versionKind(v) === 'named'
          const s = i === sel
          return { x: xs[i], w: s || named ? 2.5 : 1.5, h: s ? 12 : named ? 10 : 7, o: 1, c: 0 }
        }),
      }
    }
    const a = h.active
    const sz = vs.map((_, i) => {
      const d = Math.abs(i - h.f)
      const s = Math.exp(-(d * d) / (2 * SPREAD * SPREAD))
      if (i === a) {
        const cw = chipW.current[i] ?? 48
        return { slot: cw + 8, w: cw, h: 22, o: 1, c: 1 }
      }
      return { slot: 1 + 7 * s, w: 1.5 + 2.5 * s, h: 8 + 12 * s, o: 0.55 + 0.45 * s, c: 0 }
    })
    // 总宽不变：标签占它自己那么宽，其余按比例分剩下的
    const fixed = sz[a].slot
    const want = sz.reduce((k, b) => k + b.slot, 0)
    const k = Math.max(0.2, (W - PAD * 2 - gaps * DAY_GAP - fixed) / Math.max(1, want - fixed))
    const slot = (i: number) => (i === a ? fixed : sz[i].slot * k)
    const boxes: Box[] = []
    let x = PAD + (a === 0 ? fixed / 2 : 0)
    for (let i = 0; i < vs.length; i++) {
      if (i > 0) x += (slot(i - 1) + slot(i)) / 2 + (dayStart(vs, i) ? DAY_GAP : 0)
      boxes.push({ x, w: sz[i].w, h: sz[i].h, o: sz[i].o, c: sz[i].c })
    }
    // 整条平移，让标签尽量落在指针下，但不出条
    const lo = boxes[0].x - boxes[0].w / 2 - PAD
    const hi = boxes[vs.length - 1].x + boxes[vs.length - 1].w / 2 + PAD
    let shift = xs[a] - boxes[a].x
    shift = hi - lo > W ? -lo : Math.max(-lo, Math.min(W - hi, shift))
    for (const b of boxes) b.x += shift
    return { boxes, trackH: 32 }
  }, [rest])

  const frame = useCallback(() => {
    raf.current = 0
    const zone = zoneRef.current
    if (!zone || !live.current.versions.length) return
    const { boxes, trackH } = targets()
    const k = prefersReducedMotion() ? 1 : 0.26
    let moving = false
    const lerp = (a: number, b: number) => {
      const v = a + (b - a) * k
      if (Math.abs(v - b) < 0.05) return b
      moving = true
      return v
    }
    const tc = (trackCur.current ??= { h: trackH })
    tc.h = lerp(tc.h, trackH)
    if (trackRef.current) {
      trackRef.current.style.height = `${tc.h}px`
      trackRef.current.style.top = `${MID - tc.h / 2}px`
    }
    const sel = live.current.selectedIdx
    boxes.forEach((t, i) => {
      const c = (cur.current[i] ??= { ...t })
      c.x = lerp(c.x, t.x)
      c.w = lerp(c.w, t.w)
      c.h = lerp(c.h, t.h)
      c.o = lerp(c.o, t.o)
      c.c = lerp(c.c, t.c)
      const el = tickRefs.current[i]
      if (!el) return
      el.style.transform = `translate(${c.x - c.w / 2}px, ${MID - c.h / 2}px)`
      el.style.width = `${c.w}px`
      el.style.height = `${c.h}px`
      el.style.opacity = String(c.o)
      ;(el.firstElementChild as HTMLElement).style.opacity = String(1 - c.c)
      ;(el.lastElementChild as HTMLElement).style.opacity = String(c.c)
      el.dataset.tone = toneOf(live.current.versions[i], i === sel)
    })
    const a = hover.current.active
    if (hover.current.on && a >= 0 && peekRef.current && cur.current[a]) {
      const left = Math.max(0, Math.min(zone.clientWidth - PEEK_W, cur.current[a].x - PEEK_W / 2))
      peekRef.current.style.transform = `translateX(${left}px)`
    }
    if (moving) raf.current = requestAnimationFrame(frame)
  }, [targets])

  const kick = useCallback(() => {
    if (!raf.current) raf.current = requestAnimationFrame(frame)
  }, [frame])
  useEffect(() => () => cancelAnimationFrame(raf.current), [])

  // 节点集合变了（新节点、筛选、换范围）：量标签宽度、动画从头来
  useLayoutEffect(() => {
    const m = measureRef.current
    chipW.current = versions.map((v) => {
      if (!m) return 48
      m.textContent = label(v)
      return Math.ceil(m.offsetWidth) + 16
    })
    cur.current = []
    trackCur.current = null
    if (hover.current.active >= versions.length) hover.current = { on: false, f: 0, active: -1 }
    kick()
  }, [versions, kick])
  useEffect(() => kick(), [selectedIdx, kick])
  useEffect(() => {
    const zone = zoneRef.current
    if (!zone || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      cur.current = []
      kick()
    })
    ro.observe(zone)
    return () => ro.disconnect()
  }, [kick])

  const setHover = useCallback(
    (on: boolean, px?: number) => {
      const vs = live.current.versions
      const h = hover.current
      if (!vs.length) return
      if (!on) {
        if (!h.on) return
        hover.current = { on: false, f: h.f, active: -1 }
        peekIndex.current = -1
        setPeek(-1)
        live.current.onPeek(null)
        kick()
        return
      }
      if (!h.on) {
        // 第一次展开：把这些节点的缩略图都先取回来，之后划过去都是现成的
        for (const v of vs) {
          if (!v.thumb) continue
          const url = versionThumbUrl(docId, v)
          if (preloaded.current.has(url)) continue
          preloaded.current.add(url)
          new Image().src = url
        }
      }
      h.on = true
      if (px !== undefined) {
        const { xs } = rest()
        let f = vs.length - 1
        if (px <= xs[0]) f = 0
        else {
          for (let i = 1; i < xs.length; i++) {
            if (px <= xs[i]) {
              f = i - 1 + (px - xs[i - 1]) / (xs[i] - xs[i - 1] || 1)
              break
            }
          }
        }
        h.f = f
      }
      const nearest = Math.max(0, Math.min(vs.length - 1, Math.round(h.f)))
      if (h.active < 0 || px === undefined || Math.abs(h.f - h.active) > STICKY) h.active = nearest
      if (h.active !== peekIndex.current) {
        peekIndex.current = h.active
        setPeek(h.active)
        live.current.onPeek(vs[h.active].id)
      }
      kick()
    },
    [docId, kick, rest],
  )
  const pick = useCallback(() => {
    const a = hover.current.active
    const v = live.current.versions[a]
    setHover(false)
    if (v) live.current.onPick(v.id)
  }, [setHover])

  if (!n) return null
  const pv = peek >= 0 ? versions[peek] : null
  const localX = (e: React.PointerEvent) => e.clientX - e.currentTarget.getBoundingClientRect().left

  return (
    <div className="relative mx-3 mb-1.5 shrink-0">
      <div
        ref={zoneRef}
        role="slider"
        tabIndex={0}
        aria-label={vd('scrubberLabel')}
        aria-valuemin={1}
        aria-valuemax={n}
        aria-valuenow={(peek >= 0 ? peek : selectedIdx >= 0 ? selectedIdx : n - 1) + 1}
        aria-valuetext={pv ? `${formatDate(pv.ts)} ${formatTime(pv.ts)} ${label(pv)}` : undefined}
        data-timeline-scrubber
        className="relative touch-none rounded-full outline-none focus-visible:focus-ring"
        style={{ height: ZONE_H }}
        onPointerMove={(e) => setHover(true, localX(e))}
        onPointerLeave={(e) => {
          if (e.pointerType === 'mouse') setHover(false)
        }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          setHover(true, localX(e))
        }}
        onPointerUp={() => {
          if (hover.current.on) pick()
        }}
        onFocus={() => {
          if (hover.current.on) return
          const vs = live.current.versions
          const sel = live.current.selectedIdx
          hover.current = { on: false, f: sel >= 0 ? sel : vs.length - 1, active: -1 }
          setHover(true)
        }}
        onBlur={() => setHover(false)}
        onKeyDown={(e) => {
          const h = hover.current
          const last = n - 1
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight' || e.key === 'Home' || e.key === 'End') {
            e.preventDefault()
            e.stopPropagation()
            const from = h.active >= 0 ? h.active : selectedIdx >= 0 ? selectedIdx : last
            h.f =
              e.key === 'Home' ? 0 : e.key === 'End' ? last : Math.max(0, Math.min(last, from + (e.key === 'ArrowLeft' ? -1 : 1)))
            h.active = -1
            setHover(true)
          } else if ((e.key === 'Enter' || e.key === ' ') && h.on) {
            e.preventDefault()
            pick()
          } else if (e.key === 'Escape' && h.on) {
            // 先收起竖线条；再按一次 Esc 才轮到页签自己的逐层退出
            e.stopPropagation()
            setHover(false)
          }
        }}
      >
        <div ref={trackRef} aria-hidden className="absolute inset-x-0 rounded-full bg-field" />
        {versions.map((v, i) => (
          <div
            key={v.id}
            ref={(el) => {
              tickRefs.current[i] = el
            }}
            aria-hidden
            data-timeline-scrubber-tick={v.id}
            data-tone={toneOf(v, i === selectedIdx)}
            className="group/tick absolute left-0 top-0 rounded-full"
          >
            <span
              className={cn(
                'absolute inset-0 rounded-full bg-ink-faint',
                'group-data-[tone=named]/tick:bg-accent group-data-[tone=selected]/tick:bg-ink',
              )}
            />
            <span className="absolute inset-0 flex items-center justify-center overflow-hidden whitespace-nowrap rounded-full bg-ink text-xs font-medium tabular-nums text-surface opacity-0">
              {label(v)}
            </span>
          </div>
        ))}
        {/* 量标签宽度用：与标签同一副字 */}
        <span
          ref={measureRef}
          aria-hidden
          className="invisible absolute left-0 top-0 whitespace-nowrap text-xs font-medium tabular-nums"
        />
      </div>
      {pv && (
        <div
          ref={peekRef}
          aria-hidden
          data-timeline-scrubber-peek={pv.id}
          className="pointer-events-none absolute left-0 top-full z-popover flex flex-col gap-1.5 rounded-lg bg-surface p-2 shadow-pop"
          style={{ width: PEEK_W }}
        >
          <PeekThumb docId={docId} meta={pv} />
          <span className="flex items-baseline gap-1.5 text-sm">
            <span className="tabular-nums text-ink">
              {formatDate(pv.ts)} {formatTime(pv.ts)}
            </span>
            {versionDisplayName(pv) && (
              <span className="min-w-0 truncate font-medium text-accent">{versionDisplayName(pv)}</span>
            )}
          </span>
          {summaryOf(pv) && <span className="type-caption">{summaryOf(pv)}</span>}
        </div>
      )}
    </div>
  )
}

/** 浮卡里缩略图那一格的宽（px）：浮卡宽减去两边内边距 */
const PEEK_FRAME_W = PEEK_W - 16
/** 那一格最矮 / 最高（px）：很扁或很高的内容也不把浮卡撑变形 */
const PEEK_FRAME_MIN_H = 96
const PEEK_FRAME_MAX_H = 168

/**
 * 页面上有东西的那块（mm，夹在页面内、四周留一点白）。存好的缩略图拍的是整页，
 * 图只占页面一角时缩成一小块；浮卡要回答的是「那一版长什么样」，所以对准内容放大。
 * 内容本来就差不多占满页面、或者草图里没有对象时不裁（null）。
 */
export function contentBox(
  page: { w: number; h: number },
  objects: readonly { x: number; y: number; w: number; h: number; hidden?: boolean }[],
): { x: number; y: number; w: number; h: number } | null {
  const shown = objects.filter((o) => !o.hidden && o.w > 0 && o.h > 0)
  if (!shown.length || !(page.w > 0) || !(page.h > 0)) return null
  const x0 = Math.max(0, Math.min(...shown.map((o) => o.x)))
  const y0 = Math.max(0, Math.min(...shown.map((o) => o.y)))
  const x1 = Math.min(page.w, Math.max(...shown.map((o) => o.x + o.w)))
  const y1 = Math.min(page.h, Math.max(...shown.map((o) => o.y + o.h)))
  if (x1 <= x0 || y1 <= y0) return null
  const pad = Math.min(page.w, page.h) * 0.03
  const box = {
    x: Math.max(0, x0 - pad),
    y: Math.max(0, y0 - pad),
    w: Math.min(page.w, x1 + pad) - Math.max(0, x0 - pad),
    h: Math.min(page.h, y1 + pad) - Math.max(0, y0 - pad),
  }
  return box.w * box.h > page.w * page.h * 0.8 ? null : box
}

/** 浮卡里的大一号缩略图：同一张存好的位图，对准页面上有内容的那块；没有位图画草图；都没有留占位 */
function PeekThumb({ docId, meta }: { docId: string; meta: LayoutVersionMeta }) {
  const page = meta.sketch?.page ?? meta.page
  const ratio = page && page.w > 0 && page.h > 0 ? `${page.w} / ${page.h}` : '4 / 3'
  if (meta.thumb) {
    const box = page && meta.sketch ? contentBox(page, meta.sketch.objects) : null
    if (page && box) {
      const fw = PEEK_FRAME_W
      const fh = Math.max(PEEK_FRAME_MIN_H, Math.min(PEEK_FRAME_MAX_H, (fw * box.h) / box.w))
      const k = Math.min(fw / box.w, fh / box.h)
      return (
        <span
          className="relative block overflow-hidden rounded-xs border border-border-strong bg-paper"
          style={{ width: fw, height: fh }}
          data-timeline-scrubber-peek-crop
        >
          <img
            src={versionThumbUrl(docId, meta)}
            alt=""
            className="absolute max-w-none"
            style={{
              width: page.w * k,
              height: page.h * k,
              left: fw / 2 - (box.x + box.w / 2) * k,
              top: fh / 2 - (box.y + box.h / 2) * k,
            }}
          />
        </span>
      )
    }
    return (
      <img
        src={versionThumbUrl(docId, meta)}
        alt=""
        className="w-full rounded-xs border border-border-strong bg-paper object-contain"
        style={{ aspectRatio: ratio }}
      />
    )
  }
  if (meta.sketch) {
    return <CanvasThumb page={meta.sketch.page} objects={meta.sketch.objects} className="w-full" />
  }
  return <span className="w-full rounded-xs border border-dashed border-border" style={{ aspectRatio: ratio }} />
}
