import { useCallback, useEffect, useRef, useState } from 'react'
import { backendErrorText, fetchAiInstallStatus, startAiInstall, type AiAgentId, type AiInstallState } from '@/lib/api'
import { t as translate } from '@/i18n'
import { ag } from '../agentState'
import { cx } from './copy'

const INSTALL_POLL_MS = 2000

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
 * 「安装 / 重新安装」：POST 起装，2 秒一轮 GET 轮询（running / done / error）。装好后调 `onDone`（调用方接登录）。
 * 接口形状不变——后端怎么装（npm 或应用内下载）这里不管。卸载即停。
 */
export function useAgentInstall(agent: AiAgentId, onDone: () => void) {
  const [state, setState] = useState<AiInstallState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const running = state?.status === 'running'
  const doneRef = useRef(onDone)
  useEffect(() => {
    doneRef.current = onDone
  }, [onDone])

  useEffect(() => {
    if (!running) return
    let stopped = false
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const s = await fetchAiInstallStatus(agent)
          if (stopped) return
          if (s.status === 'running') return setState(s)
          stopped = true
          window.clearInterval(timer)
          setState(s)
          if (s.status === 'error') setError(installErrorText(s.code))
          else if (s.status === 'done') doneRef.current()
        } catch {
          /* 网络抖动：下一轮再问 */
        }
      })()
    }, INSTALL_POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [running, agent])

  const begin = useCallback(async () => {
    setError(null)
    setState({ status: 'running' })
    try {
      const s = await startAiInstall(agent)
      setState(s)
      if (s.status === 'done') doneRef.current()
      else if (s.status === 'error') setError(installErrorText(s.code))
    } catch (e) {
      setState({ status: 'error' })
      setError(backendErrorText(e))
    }
  }, [agent])

  return { running, progress: state?.progress, error, begin: () => void begin() }
}
