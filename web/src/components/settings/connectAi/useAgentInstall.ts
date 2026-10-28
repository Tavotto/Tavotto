import { useEffect, useRef } from 'react'
import type { AiAgentId, AiInstallState } from '@/lib/api'
import { useAgentInstallStore } from '@/store/agentInstallStore'
import { t as translate } from '@/i18n'
import { ag } from '../agentState'
import { cx } from './copy'
import { refreshCaps } from './useCapsWatch'

/** 应用内安装的失败码（后端 `managedcli.ERROR_*`），文案在 errors:engine.managedCliError.* */
const MANAGED_CODES = new Set([
  'managed_cli_offline',
  'managed_cli_tls',
  'managed_cli_source_unavailable',
  'managed_cli_hash_mismatch',
  'managed_cli_unsupported_platform',
  'managed_cli_disk_full',
  'managed_cli_invalid_archive',
  'managed_cli_write_failed',
  'managed_cli_launch_failed',
  'managed_cli_cancelled',
])

/** 安装失败码 → 一句话；应用内安装的码走后端那块文案，旧 npm 的码用原有文案，未知码一句通用兜底 */
export function installErrorText(code: AiInstallState['code'] | string | undefined): string {
  if (code && MANAGED_CODES.has(code)) return translate(`engine.managedCliError.${code}`, { ns: 'errors' })
  switch (code) {
    case 'npm_missing':
      return ag('install.error.npm_missing')
    case 'installed_but_not_found':
      return ag('install.error.installed_but_not_found')
    case 'timeout':
      return ag('install.error.timeout')
    default:
      return ag('install.error.other')
  }
}

/** 进度一句话：有 0–1 的 progress 显示百分比，否则「正在安装…」 */
export const installProgressText = (progress: number | undefined): string =>
  typeof progress === 'number' && progress >= 0 && progress <= 1
    ? cx('step.installingPct', { pct: Math.round(progress * 100) })
    : ag('install.running')

/**
 * 「安装 / 重新安装」的组件侧句柄：状态与轮询都住在 `agentInstallStore`（组件被 capabilities 刷新卸载也不丢），
 * 这里只订阅。装好后「接着登录」：发起安装的那一行（`owner`，它的登录流程一直挂着）用 `useLoginAfterInstall` 消费。
 */
export function useAgentInstall(agent: AiAgentId, owner: string) {
  const entry = useAgentInstallStore((s) => s.byAgent[agent])
  const error = entry?.status === 'error' ? (entry.message ?? installErrorText(entry.code)) : null
  return {
    running: entry?.status === 'running',
    progress: entry?.progress,
    error,
    begin: () => useAgentInstallStore.getState().begin(agent, owner, refreshCaps),
  }
}

/** 常驻的那一行调用：安装完成留下登录意图时，发起登录（不依赖安装按钮还在不在） */
export function useLoginAfterInstall(agent: AiAgentId, owner: string, beginLogin: () => void): void {
  const due = useAgentInstallStore((s) => s.loginDue[agent] === owner)
  const ref = useRef(beginLogin)
  useEffect(() => {
    ref.current = beginLogin
  })
  useEffect(() => {
    if (due && useAgentInstallStore.getState().takeLoginDue(agent, owner)) ref.current()
  }, [due, agent, owner])
}
