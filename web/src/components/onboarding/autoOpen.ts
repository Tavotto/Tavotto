import { useEffect } from 'react'
import { ackReinstallMarker, fetchReinstallMarker } from '@/lib/api'
import { isDesktop } from '@/lib/desktop'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'

/** 一次页面会话里只判一次（App 的两个分支各挂一份，换分支会重挂） */
let autoChecked = false

/** 卸载记号一次页面会话里只问一次 */
let reinstallChecked = false

/** 测试用：让下一次挂载重新判 */
export function resetTutorialAutoOpenForTest(): void {
  autoChecked = false
  reinstallChecked = false
}

/**
 * Windows 卸载后重装（NSIS 卸载记号，ADR 0136 修订 2026-10-21）：卸载不删 WebView 的 localStorage，
 * 重装后前端仍记得「教程已完成」。后端看到记号就回 true → 这里把 onboarding 迁回新手态（与升级迁移同一状态），
 * 再 ack 让后端删记号。先改状态再 ack：处理中途失败记号还在，下次启动接着来；应用内更新不写记号，永远是 false。
 * 取不到（旧后端 / 非 Windows 恒 false）一律当没有，不打扰。
 * **只有桌面客户端（`isDesktop()`，壳拉起的本机 sidecar）消费**：同一个 `%APPDATA%\Tavotto` 记号浏览器模式的后端也读得到，
 * 浏览器里（pip / 源码安装、远程实例窗口）取了或 ack 了，会把记号耗掉而真正重装的桌面 WebView 拿到 false（Codex #1272）——
 * 所以浏览器不取、不 ack、不重置。
 */
async function applyReinstallMarker(): Promise<void> {
  if (!isDesktop()) return
  try {
    const { reinstalled } = await fetchReinstallMarker()
    if (!reinstalled) return
    useOnboardingStore.getState().resetToNewcomer()
    autoChecked = false // 让下面的 effect（订阅了 status / welcomeSeenAt）按新手态重判一次
    await ackReinstallMarker()
  } catch {
    /* 没拿到就当没有；记号留着，下次启动再试 */
  }
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
  // 订阅这两项：重装重置把它们改回新手态后，下面的判定要重跑
  const status = useOnboardingStore((s) => s.status)
  const welcomeSeenAt = useOnboardingStore((s) => s.welcomeSeenAt)
  useEffect(() => {
    if (reinstallChecked) return
    reinstallChecked = true
    void applyReinstallMarker()
  }, [])
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
  }, [phase, listsLoaded, status, welcomeSeenAt])
}
