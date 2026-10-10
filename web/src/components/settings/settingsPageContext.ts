import { createContext, useContext, useEffect, useRef } from 'react'

/**
 * 钻入页（改图助手 › Codex）的面包屑（2026-10-07 设计审计 §9.1）：设置外壳的页头平时是「分区名 + 一句说明」，
 * 子页面挂着时换成「分区名 › 子页名」，分区名那一截是返回按钮。子页面用 `useSettingsCrumb` 报上来、卸载时撤掉。
 * 独立成文件：组件文件只导出组件，fast refresh 才生效。
 */
export interface SettingsCrumb {
  /** 子页名（「Codex」） */
  current: string
  onBack: () => void
  /** 返回按钮的可达名（与左侧导航的同名项区分开） */
  backLabel: string
  /** 落在返回按钮上的稳定锚点（e2e 认它） */
  backAnchor?: string
}

export const SettingsCrumbContext = createContext<(crumb: SettingsCrumb | null) => void>(() => {})

export function useSettingsCrumb(crumb: SettingsCrumb | null) {
  const set = useContext(SettingsCrumbContext)
  // onBack 每次渲染换一个也不重报：读 ref 里最新的那个
  const latest = useRef(crumb)
  latest.current = crumb
  const current = crumb?.current ?? null
  const backLabel = crumb?.backLabel ?? ''
  const backAnchor = crumb?.backAnchor
  useEffect(() => {
    if (current == null) return
    set({ current, backLabel, backAnchor, onBack: () => latest.current?.onBack() })
    return () => set(null)
  }, [set, current, backLabel, backAnchor])
}
