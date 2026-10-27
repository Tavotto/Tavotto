import { useEffect, useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { SpanIcon } from './SpanIcon'
import { MorphText } from './MorphText'
import { endOf, formatCost, formatSeconds, formatTokens, type TraceNode } from './utils'

interface Props {
  node: TraceNode
  anchor: HTMLElement
  running: boolean
  now: number
  slowest: boolean
  onClose: () => void
}

type Tone = 'neutral' | 'accent' | 'danger' | 'ok'

/**
 * 浮在条下方、与条左对齐（靠近视口底边时翻到上方）。列表滚动时跟着条走——
 * 位置直接写进元素样式，不走 React state，所以滚动 / 跟随不会重渲染。Esc 或点外面关闭，焦点还给触发它的条。
 */
export function SpanPopover({ node, anchor, running, now, slowest, onClose }: Props) {
  const { t } = useTranslation('ai')
  const ref = useRef<HTMLDivElement>(null)
  const { span } = node

  useLayoutEffect(() => {
    let frame = 0
    let last = ''
    const place = () => {
      const el = ref.current
      if (el && anchor.isConnected) {
        const a = anchor.getBoundingClientRect()
        const { width, height } = el.getBoundingClientRect()
        const flip = a.bottom + 6 + height > window.innerHeight - 8 && a.top - 6 - height > 8
        const left = Math.min(Math.max(8, a.left), Math.max(8, window.innerWidth - width - 8))
        const top = flip ? a.top - 6 - height : a.bottom + 6
        const key = `${left}|${top}`
        if (key !== last) {
          last = key
          el.style.left = `${left}px`
          el.style.top = `${top}px`
          el.style.visibility = ''
          el.dataset.side = flip ? 'top' : 'bottom'
        }
      }
      frame = requestAnimationFrame(place)
    }
    place()
    return () => cancelAnimationFrame(frame)
  }, [anchor])

  useEffect(() => {
    ref.current?.focus({ preventScroll: true })
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
      anchor.focus({ preventScroll: true })
    }
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node
      if (!ref.current?.contains(target) && !anchor.contains(target)) onClose()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('pointerdown', onDown, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onDown, true)
    }
  }, [anchor, onClose])

  const statusKey = running ? 'running' : (span.status ?? (span.timing === 'unmeasured' ? 'unknown' : 'ok'))
  const tone: Tone = running ? 'accent' : statusKey === 'ok' ? 'ok' : statusKey === 'error' ? 'danger' : 'neutral'
  const dash = t('trace.ui.unknown')

  const rows: { id: string; label: string; value: string }[] = []
  if (span.timing === 'measured' || span.timing === 'reported') {
    rows.push({ id: 'duration', label: t('trace.ui.field.duration'), value: formatSeconds(Math.max(0, endOf(span, now) - span.start)) })
  } else if (span.timing === 'unmeasured') {
    rows.push({ id: 'duration', label: t('trace.ui.field.duration'), value: dash })
  }
  rows.push({ id: 'started', label: t('trace.ui.field.started'), value: t('trace.ui.startOffset', { value: formatSeconds(span.start) }) })
  if (node.cost !== null) rows.push({ id: 'cost', label: t('trace.ui.field.cost'), value: formatCost(node.cost) })
  if (span.kind === 'agent') {
    if (node.tokens !== null) rows.push({ id: 'tokens', label: t('trace.ui.field.tokens'), value: formatTokens(node.tokens) })
    rows.push({ id: 'steps', label: t('trace.ui.field.steps'), value: String(node.children.length) })
  } else {
    if (span.tokensIn !== null) rows.push({ id: 'tokensIn', label: t('trace.ui.field.tokensIn'), value: formatTokens(span.tokensIn) })
    if (span.tokensOut !== null) rows.push({ id: 'tokensOut', label: t('trace.ui.field.tokensOut'), value: formatTokens(span.tokensOut) })
  }
  if (span.attempt > 1) rows.push({ id: 'attempt', label: t('trace.ui.field.attempt'), value: String(span.attempt) })

  const note =
    span.timing === 'reported' ? t('trace.ui.note.reported')
    : span.timing === 'point' ? t('trace.ui.note.point')
    : span.timing === 'unmeasured' ? t('trace.ui.note.unmeasured')
    : null

  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-label={span.name}
      tabIndex={-1}
      data-trace-popover
      data-trace-span-id={span.id}
      style={{ left: -9999, top: 0, visibility: 'hidden' }}
      className="fixed z-popover flex w-80 flex-col gap-3 rounded-lg bg-surface p-3 text-base text-ink shadow-pop outline-none motion-safe:animate-pop-in data-[side=top]:origin-bottom-left data-[side=bottom]:origin-top-left"
    >
      <div className="flex items-center gap-2">
        <SpanIcon icon={span.icon} size="lg" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h2 className="type-title truncate">{span.name}</h2>
          <div className="flex flex-wrap items-center gap-1">
            <Badge tone={tone} data-trace-status={statusKey}>
              <MorphText value={t(`trace.ui.status.${statusKey}`)} />
            </Badge>
            {span.firstFailure && (
              <Badge tone="danger" data-trace-badge="first-failure">
                {t('trace.ui.badge.firstFailure')}
              </Badge>
            )}
            {slowest && <Badge data-trace-badge="slowest">{t('trace.ui.badge.slowest')}</Badge>}
            {span.retry && <Badge data-trace-badge="retry">{t('trace.ui.badge.attempt', { n: span.attempt })}</Badge>}
          </div>
        </div>
        <Button
          size="icon-xs"
          data-trace-popover-close
          aria-label={t('trace.ui.close')}
          onClick={() => {
            onClose()
            anchor.focus({ preventScroll: true })
          }}
          className="shrink-0 self-start text-ink-3 hover:text-ink"
        >
          <X size={ICON_SIZE.sm} />
        </Button>
      </div>

      <dl className="grid grid-cols-3 gap-3 rounded-md bg-surface-2 p-3">
        {rows.map((r) => (
          <div key={r.id} data-trace-fact={r.id} className="flex min-w-0 flex-col gap-0.5">
            <dt className="truncate text-xs text-ink-3">{r.label}</dt>
            <dd className="font-medium tabular-nums">{r.value}</dd>
          </div>
        ))}
      </dl>

      {note && (
        <p data-trace-note={span.timing} className="text-sm text-ink-2">
          {note}
        </p>
      )}

      {span.facts.length > 0 && (
        <dl className="flex flex-col gap-1.5">
          {span.facts.map((f, i) => (
            <div key={`${i}:${f.label}`} data-trace-adapter-fact className="flex flex-col gap-0.5">
              <dt className="text-xs text-ink-3">{f.label}</dt>
              <dd className="text-pretty break-words">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>,
    document.body,
  )
}
