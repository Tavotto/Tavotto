import { useEffect } from 'react'
import { useAiStore } from '@/store/aiStore'

/** 对话框开着时多久强刷一次（用户可能正在别处装 / 登录） */
export const CAPS_WATCH_MS = 3000

let inflight: Promise<void> | null = null

/** 强刷 capabilities；同一时刻只一个在飞（并发调用共用同一个 promise），失败静默（caps 不动） */
export function refreshCaps(): Promise<void> {
  if (!inflight) {
    inflight = useAiStore
      .getState()
      .loadCaps(true)
      .catch(() => undefined)
      .finally(() => {
        inflight = null
      })
  }
  return inflight
}

/**
 * 「连接 AI」状态自动刷新：`active` 期间每 3 秒一次（页面不可见时暂停）；
 * 回到前台（window focus / visibilitychange 变可见）立即刷一次。卸载 / `active=false` 即停。
 * 设置页「改图助手」只要 focus 刷新，传 `{ poll: false }`。
 */
export function useCapsWatch(active: boolean, opts: { poll?: boolean } = {}): void {
  const poll = opts.poll ?? true
  useEffect(() => {
    if (!active) return
    const visible = () => document.visibilityState !== 'hidden'
    const timer = poll
      ? window.setInterval(() => {
          if (visible()) void refreshCaps()
        }, CAPS_WATCH_MS)
      : null
    const onFocus = () => {
      if (visible()) void refreshCaps()
    }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      if (timer !== null) window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [active, poll])
}
