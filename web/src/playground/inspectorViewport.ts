import { useEffect, useState } from 'react'

/**
 * 属性页（`ElementInspector`）在哪个宽度以上才出现。
 *
 * 首次引导的第 2 步指的就是属性页里的字号控件，所以**引导的显隐必须跟着属性页
 * 走**，不能自己另定一个宽度。窄屏是刻意的受限形态（ADR 0007）：画布可看可拖，
 * 树与属性页收起——在那儿让用户「去改一个不存在于可见 UI 的控件」，引导就从帮忙
 * 变成了骗人。
 *
 * **严格同源对**：这条查询与 `PlaygroundApp.tsx` 里属性页那个 aside 的
 * `hidden … md:flex` 是同一条判据的两侧（Tailwind 默认 `md` = 48rem = 768px）。
 * 改一侧必须改另一侧，看护在 `inspectorViewport.test.ts`。
 */
export const INSPECTOR_BREAKPOINT = '(min-width: 48rem)'

/**
 * 属性页此刻可见吗？跟随视口变化（窄屏转宽屏时引导会重新出现）。
 *
 * 没有 `matchMedia` 的环境（老 WebView / SSR）当作宽屏——探测不到不该把功能
 * 整个关掉，那与本修复之前的行为一致。
 */
export function inspectorVisible(): boolean {
  if (typeof matchMedia === 'undefined') return true
  return matchMedia(INSPECTOR_BREAKPOINT).matches
}

export function useInspectorVisible(): boolean {
  const [visible, setVisible] = useState(inspectorVisible)
  useEffect(() => {
    if (typeof matchMedia === 'undefined') return
    const mql = matchMedia(INSPECTOR_BREAKPOINT)
    const sync = () => setVisible(mql.matches)
    sync()
    mql.addEventListener?.('change', sync)
    return () => mql.removeEventListener?.('change', sync)
  }, [])
  return visible
}
