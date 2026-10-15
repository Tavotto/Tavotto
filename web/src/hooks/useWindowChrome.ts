import { useEffect, useState } from 'react'
import {
  cssColorToHex,
  getFullscreen,
  onFullscreenChange,
  setDesktopTitlebarColors,
  windowChrome,
} from '@/lib/desktop'
import { subscribeEffectiveTheme } from '@/lib/theme'

/** 页面此刻的顶栏底色 / 文字色（设计变量的计算值），认不出来回 null */
function chromeColors(): { background: string; text: string } | null {
  const style = getComputedStyle(document.documentElement)
  const background = cssColorToHex(style.getPropertyValue('--color-bg'))
  const text = cssColorToHex(style.getPropertyValue('--color-ink'))
  return background && text ? { background, text } : null
}

/**
 * Windows（窗框 `menu-button`）：系统标题栏与页面顶栏同色——启动时报一次，主题（显式偏好或
 * 跟随系统）一变再报。别的窗框什么都不做。挂在 App 根上，项目选择器与工作台共用。
 */
export function useWindowChrome() {
  useEffect(() => {
    if (windowChrome() !== 'menu-button') return
    const report = () => {
      const c = chromeColors()
      if (c) void setDesktopTitlebarColors(c.background, c.text)
    }
    report()
    return subscribeEffectiveTheme(report)
  }, [])
}

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
