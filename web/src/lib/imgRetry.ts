/**
 * `<img src=/api/render…>` 的有界重试（统一实施包 U08，ADR 0067）。
 *
 * 候选后端下 `/api/render` 在 render child 队列满时回 **503 + `Retry-After: 1`**——那是背压，
 * 不是故障（renderhost 的有界队列）。可 `<img>` 看不见状态码，只知道「加载失败」，画布上
 * 就会挂一个碎图标，而一秒后同一个地址本来就能出图。这里把那一次失败变成按固定退避
 * 重试同一地址（cache-bust 参数 `r=<n>`，最多 `RENDER_RETRY_DELAYS_MS.length` 次）；`src`
 * 变了计数归零、挂着的定时器取消。
 *
 * **只对 `/api/render` 的地址重试**：blob / data URL、`/api/file`（磁盘原件）失败不是背压，
 * 重试三次只会把一个真错误晚报几秒。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export const RENDER_RETRY_DELAYS_MS: readonly number[] = [1000, 2000, 4000]

export function isRenderUrl(src: string): boolean {
  return src.includes('/api/render?')
}

/** 第 n 次重试的地址：同一资源、不同查询串（浏览器不会复用那次失败的缓存条目） */
export function retrySrc(src: string, attempt: number): string {
  if (attempt <= 0) return src
  return `${src}${src.includes('?') ? '&' : '?'}r=${attempt}`
}

export function useRetryingSrc(src: string): { src: string; onError: () => void } {
  const [attempt, setAttempt] = useState(0)
  const timer = useRef<number | null>(null)
  const lastSrc = useRef(src)
  if (lastSrc.current !== src) {
    // 渲染期间发现 src 换了：计数立即归零（不等 effect），免得把上一张的重试地址套到新地址上
    lastSrc.current = src
    if (attempt !== 0) setAttempt(0)
  }
  useEffect(() => {
    return () => {
      if (timer.current != null) window.clearTimeout(timer.current)
    }
  }, [src])
  const onError = useCallback(() => {
    if (!isRenderUrl(src) || attempt >= RENDER_RETRY_DELAYS_MS.length) return
    if (timer.current != null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      timer.current = null
      setAttempt((a) => a + 1)
    }, RENDER_RETRY_DELAYS_MS[attempt])
  }, [src, attempt])
  return { src: retrySrc(src, attempt), onError }
}
