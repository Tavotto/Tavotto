import { useEffect } from 'react'
import { isPickerEntry } from '@/lib/pickerHistory'
import { useProjectStore } from '@/store/projectStore'

/**
 * 浏览器后退 / 前进 ↔ Project Picker（记号与四条规则见 `lib/pickerHistory.ts`）。
 * 去 Picker 的那一格由 `showPicker` 自己 push；这里只接 popstate 与「从 Picker 走别的路
 * 回到编辑器」时把那一格退掉。回编辑器一律走 `returnToCurrent`，去 Picker 一律走
 * `showPicker`——收尾与冲刷只在那一处，这里不另写一份。挂在 App 根上（Picker 与工作台
 * 共同的那一层），两边都要听得到。
 */
export function usePickerHistory(): void {
  const phase = useProjectStore((s) => s.phase)
  useEffect(() => {
    // 「返回当前项目」或在 Picker 上打开了一个项目：Picker 那一格已经没有意义了
    if (phase === 'open' && isPickerEntry()) window.history.back()
  }, [phase])
  useEffect(() => {
    const onPop = (e: PopStateEvent) => {
      const s = useProjectStore.getState()
      if (isPickerEntry(e.state)) {
        if (s.phase === 'open') s.showPicker()
      } else if (s.phase === 'none') {
        s.returnToCurrent()
      }
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
}
