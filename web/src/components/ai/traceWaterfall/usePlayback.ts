import { useCallback, useEffect, useRef, useState } from 'react'
import { prefersReducedMotion } from '@/lib/motion'

/** 回放速度：自动 = 把整段压缩到 `AUTO_SECONDS` 秒左右（太短的就按实际时间），1× = 实时，10× = 十倍速 */
export type PlaybackSpeed = 'auto' | 'realtime' | 'fast'

/** 「自动」档：不管真实跑了多久，整段大约这么多秒放完 */
export const AUTO_SECONDS = 4
/** 提交到界面的最短间隔（毫秒）：约 30 帧/秒，大 Trace 每帧都重画整行树吃不消 */
const FRAME_MS = 33

/** 回放速率（每个真实秒走过的 Trace 秒数）。纯函数，单测直接断言 */
export function playbackRate(speed: PlaybackSpeed, total: number): number {
  if (speed === 'realtime') return 1
  if (speed === 'fast') return 10
  return Math.max(1, total / AUTO_SECONDS)
}

/** `m:ss` */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export interface Playback {
  /** 回放到的时刻（秒）；在终点 = `total` */
  t: number
  /** 正在回放中的某一刻（不在终点）：这时画「那一刻的样子」，否则画完整结果 */
  replaying: boolean
  playing: boolean
  speed: PlaybackSpeed
  setSpeed: (s: PlaybackSpeed) => void
  toggle: () => void
  replay: () => void
  seek: (t: number) => void
}

/**
 * 已结束 Trace 的回放时钟。`enabled`（已结束且有时长）时：打开就从 0 自动播一遍，减少动效偏好下不自动播、直接停在终点；
 * 在终点按播放 = 重头来。`pos === null` 表示在终点（画完整结果）。拖动进度条会暂停。
 */
export function usePlayback(total: number, enabled: boolean): Playback {
  const [start] = useState(() => enabled && !prefersReducedMotion())
  const [pos, setPos] = useState<number | null>(start ? 0 : null)
  const [playing, setPlaying] = useState(start)
  const [speed, setSpeed] = useState<PlaybackSpeed>('auto')
  const posRef = useRef(pos)
  posRef.current = pos
  const rate = playbackRate(speed, total)

  useEffect(() => {
    if (!playing || !enabled) return
    let raf = 0
    let last = performance.now()
    let lastCommit = last
    let at = posRef.current ?? 0
    const tick = (now: number) => {
      at += ((now - last) / 1000) * rate
      last = now
      if (at >= total) {
        setPos(null)
        setPlaying(false)
        return
      }
      if (now - lastCommit >= FRAME_MS) {
        lastCommit = now
        setPos(at)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, enabled, rate, total])

  const replay = useCallback(() => {
    setPos(0)
    setPlaying(true)
  }, [])
  const toggle = useCallback(() => {
    if (playing) return setPlaying(false)
    if (posRef.current === null) setPos(0)
    setPlaying(true)
  }, [playing])
  const seek = useCallback(
    (t: number) => {
      setPlaying(false)
      setPos(t >= total ? null : Math.max(0, t))
    },
    [total],
  )

  const active = enabled && pos !== null
  return { t: active ? Math.min(pos, total) : total, replaying: active, playing: enabled && playing, speed, setSpeed, toggle, replay, seek }
}
