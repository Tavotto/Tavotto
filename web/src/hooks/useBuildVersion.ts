import { useEffect, useState } from 'react'
import { currentBuildId, fetchBuildVersion } from '@/lib/api'

const POLL_MS = 60_000

/**
 * 构建版本自检：本页面跑的 bundle 与服务器上的对不上，说明这个标签页
 * 停在旧版本上（浏览器缓存、或页面开了很久没刷新）。
 *
 * 只提示、绝不自动 reload——用户可能正在图内编辑或跑 AI 会话，
 * 刷新会把这些状态全部丢掉。发现不一致后就停止轮询，提示条常驻到用户处理。
 */
export function useBuildVersion(): boolean {
  const [outdated, setOutdated] = useState(false)

  useEffect(() => {
    const mine = currentBuildId()
    if (!mine) return // dev server 下没有 hash 文件名，跳过自检

    let stopped = false
    let timer: number | undefined

    const check = async () => {
      if (stopped) return
      try {
        const { build } = await fetchBuildVersion()
        if (!stopped && build && build !== mine) {
          setOutdated(true)
          stopped = true
          window.clearTimeout(timer)
          return
        }
      } catch {
        /* 后端没起或临时失败：下一轮再说，不打扰用户 */
      }
      if (!stopped) timer = window.setTimeout(check, POLL_MS)
    }

    void check()
    // SSE 重连成功往往意味着后端刚重启过（很可能同时换了构建），立刻复查一次
    window.addEventListener('mm:sse-open', check)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      window.removeEventListener('mm:sse-open', check)
    }
  }, [])

  return outdated
}
