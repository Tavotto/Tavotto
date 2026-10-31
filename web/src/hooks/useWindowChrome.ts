import { useEffect, useState } from 'react'
import { getFullscreen, onFullscreenChange, windowChrome } from '@/lib/desktop'

/**
 * macOS 主窗口此刻是否全屏（红绿灯随之收起）。非 `overlay` 窗框恒 false——浏览器、Windows、
 * 远程实例窗口、Codex 内嵌画布都不受影响。先装好监听再回读：回读只在没收到
 * 事件时补初值。
 */
export function useWindowFullscreen(): boolean {
  const [fullscreen, setFullscreen] = useState(false)
  useEffect(() => {
    if (windowChrome() !== 'overlay') return
    let live = true
    let heard = false
    let off = () => {}
    void onFullscreenChange((v) => {
      heard = true
      if (live) setFullscreen(v)
    }).then(async (fn) => {
      if (!live) return fn()
      off = fn
      // 监听装好之后再回读：两者并发时，回读完成与监听就绪之间的那次切换两边都接不到
      const v = await getFullscreen()
      if (live && !heard) setFullscreen(v)
    })
    return () => {
      live = false
      off()
    }
  }, [])
  return fullscreen
}
