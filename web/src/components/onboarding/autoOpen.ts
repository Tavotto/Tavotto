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
 *   * 第一次打开 Tavotto（没开始过教程、欢迎页没出现过、启动后停在主页、最近列表里没有自己的
 *     项目）→ 欢迎页，只这一次。已经在正常使用的人（有自己的项目，或启动时项目就开着）不打扰。
 * 暂停 / 已完成 / 已跳过的一律不自己出现——入口还在主页、帮助菜单、命令面板、设置里。
 *
 * 「没有自己的项目」要以**真的取到过**项目列表为准（`listsLoaded`）：启动时后端暂时不可达，
 * 列表也是空的，那不是新用户的证据——先不判，等列表取到再判（不把老用户的 `welcomeSeenAt` 记掉）。
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
    const { recent, pinned } = useProjectStore.getState()
    if ([...recent, ...pinned].some((r) => !r.tutorial)) return
    ob.openWelcome()
  }, [phase, listsLoaded])
}
