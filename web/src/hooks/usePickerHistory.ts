import { useEffect } from 'react'
import { isPickerEntry } from '@/lib/pickerHistory'
import { useProjectStore } from '@/store/projectStore'

/**
 * 浏览器后退 / 前进 ↔ Project Picker（记号与规则见 `lib/pickerHistory.ts`）。
 * 去 Picker 的那一格由 `showPicker` 自己 push；这里只接 popstate 与 phase 的两种迁移。
 * 回编辑器一律走 `returnToCurrent`，去 Picker 一律走 `showPicker`——收尾与冲刷只在那一处，
 * 这里不另写一份。挂在 App 根上（Picker 与工作台共同的那一层），两边都要听得到。
 */
export function usePickerHistory(): void {
  useEffect(
    () =>
      // 订阅 store 而不是 useEffect([phase])：启动探测（loading → open）要在 React 画出
      // 工作台**之前**改判，否则刷新时先闪一下编辑器；两种迁移也必须按「从哪来」区分
      useProjectStore.subscribe((s, prev) => {
        if (s.phase !== 'open' || !isPickerEntry()) return
        if (prev.phase === 'loading') {
          // 停在 Picker 那一格上刷新了：init() 按「项目还开着」把 phase 定成 open，
          // 但用户刷新前看的是 Picker——留在 Picker（项目照旧开着，「返回当前项目」可回）。
          // 此刻工作台还没挂载、文档还没恢复，没有要收尾或冲刷的东西
          useProjectStore.setState({ phase: 'none' })
        } else if (prev.phase === 'none') {
          // 在 Picker 上「返回当前项目」或打开了一个项目：Picker 那一格已经没有意义了
          window.history.back()
        }
      }),
    [],
  )
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
