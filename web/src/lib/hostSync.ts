import { isDesktop, runHostIntegration } from "@/lib/desktop"

/**
 * 桌面版启动时把已接入编辑器的技能同步一次（ADR 0170 裁决 6）：`tavotto-cli hosts sync --json`，只补缺的、
 * 过期的技能（本机复制，不联网），用户改过的不动。**后台自动下载开着**才发 `sync_plugins`，让引擎顺带更新
 * Codex 插件；开关关着、或状态没拿到（`autoDownloadOn` 为 false）就只发 `sync`——不替用户做没确认过的下载。
 *
 * 不阻塞界面、不弹任何东西、失败静默（结果只在「接入 AI 编辑器」面板里以版本和「有新版」体现）。
 * 整个应用只发一次；它不读也不写任何项目数据，所以不是「跨项目存活的 store」，没有项目代际要管。
 * 浏览器版没有壳，不发。
 */
let started = false

export function startHostSync(autoDownload: boolean): void {
  if (started || !isDesktop()) return
  started = true
  void runHostIntegration(autoDownload ? "sync_plugins" : "sync").catch(
    () => undefined,
  )
}

/** 仅测试用：让「整个应用只发一次」的守卫可重置 */
export function resetHostSyncForTest(): void {
  started = false
}
