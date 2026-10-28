import { useRef, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { IconButton } from '@/components/ui/Button'
import { Pause, Play, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Segmented } from '@/components/ui/Segmented'
import { formatClock, type PlaybackSpeed } from './usePlayback'

export interface PlaybackProps {
  /** 总时长（秒） */
  total: number
  /** 回放到的时刻（秒） */
  t: number
  playing: boolean
  speed: PlaybackSpeed
  onToggle: () => void
  onReplay: () => void
  onSeek: (t: number) => void
  onSpeed: (s: PlaybackSpeed) => void
}

/**
 * 回放条：左边播放 / 重播 / `0:12 / 0:33` / 速度，右边的进度条与下面时间轴同一把尺——
 * 它的宽度 = 总时长占时间轴的比例（`fraction`），拇指在哪，现在线就在哪。放在 `data-trace-body` 里，沿用它的标签列宽。
 * 进度条自己画（不是原生 range）：点哪落哪，不被原生拇指的内缩带偏。
 */
export function PlaybackBar({ total, t, playing, speed, onToggle, onReplay, onSeek, onSpeed, fraction }: PlaybackProps & { fraction: number }) {
  const { t: tr } = useTranslation('ai')
  const trackRef = useRef<HTMLDivElement>(null)
  const share = total > 0 ? Math.min(1, Math.max(0, t / total)) : 0
  const clock = `${formatClock(t)} / ${formatClock(total)}`

  const seekFromPointer = (e: { clientX: number }) => {
    const rect = trackRef.current?.getBoundingClientRect()
    if (!rect || rect.width <= 0) return
    onSeek(Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)) * total)
  }
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.currentTarget.setPointerCapture(e.pointerId)
    seekFromPointer(e)
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) seekFromPointer(e)
  }
  const step = Math.max(0.1, total / 100)
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const to =
      e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? t - step
      : e.key === 'ArrowRight' || e.key === 'ArrowUp' ? t + step
      : e.key === 'Home' ? 0
      : e.key === 'End' ? total
      : null
    if (to === null) return
    e.preventDefault()
    e.stopPropagation()
    onSeek(Math.min(total, Math.max(0, to)))
  }

  return (
    <div data-trace-playback className="relative flex h-11 items-center border-b border-border bg-surface select-none">
      <div className="flex w-(--trace-label) shrink-0 items-center gap-1 ps-3 pe-3">
        <IconButton
          data-trace-playback-toggle={playing ? 'pause' : 'play'}
          label={tr(playing ? 'trace.ui.playback.pause' : 'trace.ui.playback.play')}
          iconSize="sm"
          onClick={onToggle}
        >
          {playing ? <Pause size={ICON_SIZE.sm} filled /> : <Play size={ICON_SIZE.sm} filled />}
        </IconButton>
        <IconButton data-trace-playback-replay label={tr('trace.ui.playback.replay')} iconSize="sm" onClick={onReplay}>
          <RotateCcw size={ICON_SIZE.sm} />
        </IconButton>
        <span data-trace-playback-clock className="ms-1 me-2 shrink-0 text-sm text-ink-2 tabular-nums">
          {clock}
        </span>
        <Segmented
          value={speed}
          onChange={onSpeed}
          ariaLabel={tr('trace.ui.playback.speedLabel')}
          data-testid="trace-playback-speed"
          className="ms-auto hidden w-auto shrink-0 @2xl/trace:inline-flex"
          items={(['auto', 'realtime', 'fast'] as const).map((v) => ({
            value: v,
            label: tr(`trace.ui.playback.speed.${v}`),
            tip: tr(`trace.ui.playback.speedTip.${v}`),
          }))}
        />
      </div>
      <div className="relative h-full flex-1">
        <div className="absolute inset-y-0 start-0" style={{ width: `${fraction * 100}%` }}>
          <div
            ref={trackRef}
            role="slider"
            tabIndex={0}
            data-trace-playback-scrub
            aria-label={tr('trace.ui.playback.scrub')}
            aria-valuemin={0}
            aria-valuemax={Math.round(total * 10) / 10}
            aria-valuenow={Math.round(t * 10) / 10}
            aria-valuetext={clock}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onKeyDown={onKeyDown}
            className="group/scrub relative h-full cursor-grab touch-none active:cursor-grabbing outline-none"
          >
            <span className="pointer-events-none absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-border" />
            <span
              className="pointer-events-none absolute start-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-accent"
              style={{ width: `${share * 100}%` }}
            />
            <span
              data-trace-playback-thumb
              className="pointer-events-none absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent group-focus-visible/scrub:focus-ring"
              style={{ left: `${share * 100}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
