import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { ArrowLeftRight, GradientLinear, GradientRadial, Square } from '@/components/ui/icons'
import { IconButton } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Row } from '@/components/ui/Field'
import { Select } from '@/components/ui/Select'
import {
  addStop,
  defaultGradient,
  insertStopAt,
  linearAngle,
  moveStop,
  readGradient,
  removeStop,
  reverseStops,
  sortedStops,
  stopsCss,
  switchKind,
  updateStop,
  withLinearAngle,
} from '@/lib/gradient'
import { cn } from '@/lib/utils'
import { updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useGradientEditStore } from '@/store/gradientEditStore'
import { registerGesture, tryRegisterPointerCancel } from '@/store/gestureCoordinator'
import type { CanvasObject, Gradient, PathObject, ShapeObject } from '@/types/document'
import { INSPECTOR_LABEL_W } from '../layout'

/**
 * 渐变的属性栏控件（ADR 0186 §四，第一期：只在属性栏调，画布控制柄第二期）。2026-11-15 用户返工：做成 PPT 那样的横向色带。
 *
 * - 「填充」「描边」那一行：渐变时是「开关 + 类型下拉 + 角度（线性才有）+ 反向」，纯色色块与十六进制不再显示，也没有预览条
 *   （与下面的色带重复）；
 * - 紧跟其下常显一条色带，占满整个控件列；再下面一行只编辑选中的那个色标
 *   （色块 · 位置 % · 不透明度 %）。径向的中心、半径在「更多」里（`RadialRows`）；
 * - 色带交互：点空白处加色标（插值色）、左右拖（不越过邻居）、往下拖出色带或 Delete 删（至少留两个）；色标是 slider：
 *   ←/→ 1%、⇧ 10%、Delete 删、Enter 开取色器；色带本身聚焦时 Enter 在最大空档中点加一个；
 * - 降级纯色不显示：每次写渐变都把**位置最靠前那个色标**的颜色同步进 `fill` / `color`（旧版本、切回纯色都用它）。
 *
 * 多选时以第一个对象的渐变为准、写给这一侧生效的全部对象。字段读写只经 `lib/gradient`。填充与描边共用这一套。
 */

type Mark = ShapeObject | PathObject
export type GradientTarget = 'fill' | 'stroke'
type Kind = 'solid' | 'linear' | 'radial'

const gk = (key: string, values?: Record<string, unknown>) => translate(`gradient.${key}`, { ns: 'inspector', ...(values ?? {}) })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')
const field = (t: GradientTarget) => (t === 'fill' ? 'fillGradient' : 'strokeGradient')
/** 往下拖出色带多远算「删掉」（px） */
const REMOVE_DRAG_PX = 24

/** 这一侧的渐变（读得成才算；读不成的字段当作没有） */
export function gradientOf(o: Mark, t: GradientTarget): Gradient | null {
  const g = o[field(t)]
  return readGradient(g) ? (g as Gradient) : null
}

export function kindOf(objs: readonly Mark[], t: GradientTarget): Kind | undefined {
  const kinds = new Set(objs.map((o) => gradientOf(o, t)?.kind ?? 'solid'))
  return kinds.size === 1 ? ([...kinds][0] as Kind) : undefined
}

/** 写一个对象的这一侧：有渐变时把位置最靠前那个色标的颜色同步进降级纯色；null = 回纯色（降级色留着） */
function setGradient(o: CanvasObject, t: GradientTarget, g: Gradient | null) {
  if (o.type !== 'shape' && o.type !== 'path') return
  if (!g) {
    delete o[field(t)]
    return
  }
  o[field(t)] = g
  const first = sortedStops(g)[0]?.color
  if (first) {
    if (t === 'fill') o.fill = first
    else o.color = first
  }
}

function write(objs: readonly Mark[], t: GradientTarget, label: UiMessage, next: (o: Mark) => Gradient | null) {
  updateObjects(
    objs.map((o) => o.id),
    label,
    (o) => {
      if (o.type === 'shape' || o.type === 'path') setGradient(o, t, next(o))
    },
  )
}

const KIND_ICON = { solid: Square, linear: GradientLinear, radial: GradientRadial } as const
function KindIcon({ kind }: { kind: Kind }) {
  const Icon = KIND_ICON[kind]
  return <Icon size={ICON_SIZE.sm} data-gradient-kind={kind} aria-hidden />
}

/** 类型下拉：放在「填充」/「描边」那一行的最后 */
export function GradientKindSelect({ objs, target }: { objs: readonly Mark[]; target: GradientTarget }) {
  useTranslation('inspector')
  const kind = kindOf(objs, target)
  return (
    <Select<Kind>
      value={kind ?? ('' as Kind)}
      placeholder={gk('mixed')}
      ariaLabel={gk(target === 'fill' ? 'fillKind' : 'strokeKind')}
      title={gk('kindTip')}
      // 触发器只画一枚图标（纯色 / 线性 / 径向）；下拉里是完整的字
      className="w-auto min-w-0 shrink-0 gap-0.5 px-1"
      triggerLabel={kind ? <KindIcon kind={kind} /> : undefined}
      options={(['solid', 'linear', 'radial'] as const).map((k) => ({ value: k, label: gk(`kind.${k}`) }))}
      onChange={(k) =>
        write(objs, target, hist('setGradientKind'), (o) => {
          if (k === 'solid') return null
          const cur = gradientOf(o, target)
          return cur ? switchKind(cur, k) : defaultGradient(k, (target === 'fill' ? o.fill : null) ?? o.color)
        })
      }
    />
  )
}

/** 棋盘格底：色标带透明时看得出来（颜色取纸面 token） */
const CHECKER =
  'conic-gradient(var(--color-paper-checker) 25%, var(--color-paper) 0 50%, var(--color-paper-checker) 0 75%, var(--color-paper) 0) 0 0 / 8px 8px'

/**
 * 「填充」/「描边」那一行渐变时的工具：角度框（线性才有）+ 反向。跟在类型下拉后面，不新增行。
 */
export function GradientTools({ objs, target }: { objs: readonly Mark[]; target: GradientTarget }) {
  useTranslation('inspector')
  const live = objs.filter((o) => gradientOf(o, target))
  const first = live[0]
  const g = first ? gradientOf(first, target) : null
  if (!first || !g) return null
  const commit = (next: Gradient) => write(live, target, hist('setGradient'), () => next)
  return (
    <>
      {g.kind === 'linear' && (
        <NumberField
          ariaLabel={gk('angle')}
          title={gk('angleTip')}
          value={linearAngle(g, first.w, first.h)}
          step={15}
          min={-360}
          max={360}
          precision={0}
          unit="°"
          onChange={(v) =>
            // 每个对象按自己的宽高换算端点（宽高比不同时同一个角度才是同一个角度）
            write(live, target, hist('setGradient'), (o) => {
              return withLinearAngle(g, v, o.w, o.h)
            })
          }
        />
      )}
      <IconButton label={gk('reverse')} side="left" onClick={() => commit(reverseStops({ ...g, stops: sortedStops(g) }))}>
        <ArrowLeftRight size={ICON_SIZE.sm} />
      </IconButton>
    </>
  )
}

/**
 * 色带 + 选中色标那一行（填充与描边共用）。放在「填充」/「描边」那一行正下方，没有渐变就什么都不画。
 */
export function GradientEditor({ objs, target }: { objs: readonly Mark[]; target: GradientTarget }) {
  useTranslation('inspector')
  const live = objs.filter((o) => gradientOf(o, target))
  const first = live[0]
  const g = first ? gradientOf(first, target) : null
  // 选中的色标与画布上的渐变柄共用（`gradientEditStore`）：这边点了，画布上那个点跟着亮，反之亦然
  const edit = useGradientEditStore()
  const [removing, setRemoving] = useState(false)
  const barRef = useRef<HTMLDivElement | null>(null)
  const rowRef = useRef<HTMLDivElement | null>(null)
  const drag = useRef<{ i: number; release: () => void } | null>(null)
  /** 结束拖动色标（只认 ref，不依赖闭包）：discard = 放弃这一拖（Esc / 指针被系统取消），否则收成一条历史 */
  const endDrag = (discard: boolean) => {
    const d = drag.current
    drag.current = null
    if (!d) return false
    d.release()
    setRemoving(false)
    useDocumentStore.getState().endTxn(discard ? { discard: true } : undefined)
    return true
  }
  const endDragRef = useRef(endDrag)
  endDragRef.current = endDrag
  // 拖到一半组件卸载（选区变了等）：事务不能悬着
  useEffect(() => () => void endDragRef.current(false), [])
  if (!first || !g) return null
  const sel = edit.objectId === first.id && edit.target === target ? edit.stop : 0
  const setSel = (i: number) => edit.set(first.id, { target, stop: i })
  const stops = sortedStops(g)
  const sorted: Gradient = { ...g, stops }
  const at = Math.min(sel, stops.length - 1)
  const ids = live.map((o) => o.id)

  const commit = (next: Gradient) => write(live, target, hist('setGradient'), () => next)
  /** 拖动途中：同一个事务里改，松手收成一条历史 */
  const liveUpdate = (next: Gradient) =>
    useDocumentStore.getState().txnUpdate((d) => {
      for (const o of d.objects) if (ids.includes(o.id)) setGradient(o, target, next)
    })
  /** 拖动途中读的是文档里的最新值（每一帧都从它算，不从闭包里的旧值算） */
  const current = (): Gradient | null => {
    const o = useDocumentStore.getState().doc.objects.find((x) => x.id === first.id)
    return o && (o.type === 'shape' || o.type === 'path') ? gradientOf(o, target) : null
  }
  const tAt = (clientX: number) => {
    const r = barRef.current!.getBoundingClientRect()
    return r.width > 0 ? (clientX - r.left) / r.width : 0
  }
  const focusStop = (i: number) =>
    requestAnimationFrame(() => barRef.current?.querySelector<HTMLElement>(`[data-gradient-stop="${i}"]`)?.focus())
  const remove = (i: number) => {
    if (stops.length <= 2) return
    commit(removeStop(sorted, i))
    setSel(Math.max(0, i - 1))
    focusStop(Math.max(0, i - 1))
  }
  const openPicker = () => {
    const row = rowRef.current
    const btn = row?.querySelector<HTMLElement>('[data-color-swatch]') ?? row?.querySelector<HTMLElement>('input[type=color]')
    btn?.click()
  }

  const onBarPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || e.target !== e.currentTarget) return
    const { g: next, index } = insertStopAt(sorted, tAt(e.clientX))
    commit(next)
    setSel(index)
    focusStop(index)
  }
  const onStopPointerDown = (e: ReactPointerEvent<HTMLButtonElement>, i: number) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.stopPropagation()
    setSel(i)
    e.currentTarget.focus()
    e.currentTarget.setPointerCapture?.(e.pointerId)
    endDrag(false)
    useDocumentStore.getState().beginTxn(hist('setGradient'))
    // 与手势协调器登记：保存 / 离散动作前会把这一拖收成一条历史；Esc 走指针取消出口放弃这一拖
    const offFinish = registerGesture(() => void endDragRef.current(false))
    const offCancel = tryRegisterPointerCancel(() => void endDragRef.current(true))
    drag.current = {
      i,
      release: () => {
        offFinish()
        offCancel?.()
      },
    }
  }
  const onStopPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current
    const cur = current()
    if (!d || !cur) return
    const r = barRef.current!.getBoundingClientRect()
    const out = sortedStops(cur).length > 2 && e.clientY > r.bottom + REMOVE_DRAG_PX
    setRemoving(out)
    if (out) return
    liveUpdate(moveStop(cur, d.i, tAt(e.clientX)))
  }
  const onStopPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current
    if (!d) return
    const cur = current()
    const r = barRef.current!.getBoundingClientRect()
    if (cur && sortedStops(cur).length > 2 && e.clientY > r.bottom + REMOVE_DRAG_PX) {
      liveUpdate(removeStop({ ...cur, stops: sortedStops(cur) }, d.i))
      setSel(Math.max(0, d.i - 1))
    }
    endDrag(false)
  }
  const onStopKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const step = e.shiftKey ? 0.1 : 0.01
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault()
      e.stopPropagation()
      commit(moveStop(sorted, i, stops[i].offset + (e.key === 'ArrowRight' ? step : -step)))
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault()
      e.stopPropagation()
      remove(i)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      openPicker()
    }
  }
  const onBarKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Enter' || e.target !== e.currentTarget) return
    e.preventDefault()
    e.stopPropagation()
    const next = sortedStops(addStop(sorted))
    const i = next.findIndex((s, k) => s !== stops[k])
    commit({ ...g, stops: next })
    const idx = i < 0 ? stops.length : i
    setSel(idx)
    focusStop(idx)
  }

  const s = stops[at]
  const pct = (v: number) => Math.round(v * 1000) / 10
  return (
    <>
      <Row label="" labelWidth={INSPECTOR_LABEL_W}>
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <div
            ref={barRef}
            data-gradient-bar={target}
            role="group"
            tabIndex={0}
            aria-label={gk(target === 'fill' ? 'fillBar' : 'strokeBar')}
            title={gk('barTip')}
            onPointerDown={onBarPointerDown}
            onKeyDown={onBarKey}
            className="relative mx-1.5 h-5 min-w-0 flex-1 cursor-copy rounded-sm border border-border outline-none focus-visible:focus-ring"
            style={{ background: `${stopsCss(sorted)}, ${CHECKER}` }}
          >
            {stops.map((st, i) => (
              <button
                key={i}
                type="button"
                data-gradient-stop={i}
                data-selected={i === at || undefined}
                role="slider"
                aria-label={gk('stop', { n: i + 1 })}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={pct(st.offset)}
                aria-valuetext={`${pct(st.offset)}%`}
                onPointerDown={(e) => onStopPointerDown(e, i)}
                onPointerMove={onStopPointerMove}
                onPointerUp={onStopPointerUp}
                onPointerCancel={() => void endDrag(true)}
                onFocus={() => setSel(i)}
                onKeyDown={(e) => onStopKey(e, i)}
                className={cn(
                  'absolute top-1/2 h-6 w-3 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-sm border-2 border-surface outline-none',
                  'focus-visible:focus-ring',
                  // 白边 + 一圈细线：浅色 / 深色的色带上都看得出节点；选中的那个换成强调色
                  i === at ? 'ring-2 ring-accent' : 'ring-1 ring-border-strong',
                  i === at && removing && 'opacity-30',
                )}
                style={{ left: `${pct(st.offset)}%`, background: st.color }}
              />
            ))}
          </div>
        </div>
      </Row>
      <Row label="" labelWidth={INSPECTOR_LABEL_W}>
        <div ref={rowRef} data-gradient-selected={target} className="flex min-w-0 flex-1 items-center gap-1">
          <ColorField
            swatchOnly
            ariaLabel={gk('stopColor', { n: at + 1 })}
            value={s.color}
            onChange={(v) => commit(updateStop(sorted, at, { color: v }))}
          />
          <NumberField
            fill
            ariaLabel={gk('stopOffset', { n: at + 1 })}
            value={pct(s.offset)}
            step={1}
            min={0}
            max={100}
            precision={0}
            unit="%"
            onChange={(v) => commit(moveStop(sorted, at, v / 100))}
          />
          <NumberField
            fill
            ariaLabel={gk('stopOpacity', { n: at + 1 })}
            title={gk('stopOpacityTip')}
            value={pct(s.opacity ?? 1)}
            step={5}
            min={0}
            max={100}
            precision={0}
            unit="%"
            onChange={(v) => commit(updateStop(sorted, at, { opacity: v / 100 }))}
          />
        </div>
      </Row>
    </>
  )
}

/** 「更多」里只剩径向的中心与半径（线性的角度在色带那一行） */
export function RadialRows({ objs, target }: { objs: readonly Mark[]; target: GradientTarget }) {
  useTranslation('inspector')
  const live = objs.filter((o) => gradientOf(o, target)?.kind === 'radial')
  const g = live[0] ? gradientOf(live[0], target) : null
  if (!g || g.kind !== 'radial') return null
  const set = (next: Gradient) => write(live, target, hist('setGradient'), () => next)
  const pct = (v: number) => Math.round(v * 1000) / 10
  const pre = target === 'fill' ? 'fill' : 'stroke'
  return (
    <div data-gradient-radial={target} className="flex flex-col gap-1.5">
      <Row label={gk(`${pre}Center`)} labelWidth={INSPECTOR_LABEL_W}>
        <NumberField
          half
          prefix="X"
          ariaLabel={gk(`${pre}CenterX`)}
          value={pct(g.center[0])}
          step={5}
          min={-100}
          max={200}
          precision={0}
          unit="%"
          onChange={(v) => set({ ...g, center: [v / 100, g.center[1]] })}
        />
        <NumberField
          half
          prefix="Y"
          ariaLabel={gk(`${pre}CenterY`)}
          value={pct(g.center[1])}
          step={5}
          min={-100}
          max={200}
          precision={0}
          unit="%"
          onChange={(v) => set({ ...g, center: [g.center[0], v / 100] })}
        />
      </Row>
      <Row label={gk(`${pre}Radius`)} labelWidth={INSPECTOR_LABEL_W}>
        <NumberField
          half
          ariaLabel={gk(`${pre}Radius`)}
          title={gk('radiusTip')}
          value={pct(g.radius)}
          step={5}
          min={1}
          max={300}
          precision={0}
          unit="%"
          onChange={(v) => set({ ...g, radius: Math.max(v, 1) / 100 })}
        />
      </Row>
    </div>
  )
}
