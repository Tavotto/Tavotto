import { useEffect } from 'react'
import { cssColorToHex, setDesktopTitlebarColors, windowChrome } from '@/lib/desktop'
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
