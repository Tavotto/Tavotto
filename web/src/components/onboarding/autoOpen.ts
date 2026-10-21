import { useEffect } from 'react'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'

/** 一次页面会话里只判一次（App 的两个分支各挂一份，换分支会重挂） */
let autoChecked = false

/** 测试用：让下一次挂载重新判 */
export function resetTutorialAutoOpenForTest(): void {
  autoChecked = false
}

/**
 * 什么时候自己出现：
 *   * 上次关应用时教程正进行到一半（`active`）→ 接着那一步；
 *   * 新手态（`not_started`）、欢迎页没出现过、启动后停在主页 → 欢迎页，只这一次。
 *     **有没有自己的项目不再判**（2026-10-21 用户裁决，ADR 0136 修订）：升级前用过 Tavotto 的老用户
 *     被 `migratePersisted` 迁回新手态（`welcomeSeenAt` 清空）后，要和新用户走同一条路径看一次新教程，
 *     而老用户恰恰有自己的项目。之后 `welcomeSeenAt` 记下了，不会再自动出现。
 * 暂停 / 已完成 / 已跳过的一律不自己出现——入口还在主页、帮助菜单、命令面板、设置里。
 * 启动时项目就开着（`phase !== 'none'`）也不弹：此刻用户正在工作，主页的新手版与入口仍在。
 *
 * 仍以**真的取到过**项目列表（`listsLoaded`）为准再判：启动期的自动恢复结束后才知道是不是停在主页。
 */
export function useAutoOpen(): void {
  const phase = useProjectStore((s) => s.phase)
  const listsLoaded = useProjectStore((s) => s.listsLoaded)
  useEffect(() => {
    if (autoChecked || phase === 'loading') return
    const ob = useOnboardingStore.getState()
    if (ob.status === 'active') {
      autoChecked = true
      ob.openTutorial()
      return
    }
    if (phase === 'none' && !listsLoaded) return
    autoChecked = true
    if (phase !== 'none' || ob.status !== 'not_started' || ob.welcomeSeenAt !== null) return
    ob.openWelcome()
  }, [phase, listsLoaded])
}
