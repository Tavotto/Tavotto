import { useCallback, useEffect, useRef, useState } from 'react'
import { backendErrorText, fetchAiLoginStatus, startAiLogin, type AiAgentCaps, type AiCapabilities } from '@/lib/api'

type OnRefreshed = (next?: AiCapabilities) => Promise<boolean | void> | boolean | void

export type LoginPhase = 'idle' | 'waiting' | 'terminal' | 'manual' | 'retry'

/** 后台登录最多等多久（与后端 `LOGIN_TIMEOUT_S` 同量级）；到点不再轮询，按钮回到可点 */
const LOGIN_MAX_WAIT_MS = 10 * 60 * 1000
const LOGIN_POLL_MS = 3000

/**
 * 官方登录按钮的状态机。**Tavotto 只启动官方命令**（codex 后台、claude 开在系统终端里），
 * 不读、不存任何登录凭据；是否登录成功仍只看重新探测出来的就绪检查结论。
 *
 *   * background：轮询后端「登录进程还在不在」（3 s，最多 10 分钟，卸载即停），进程一结束就
 *     重新探测 capabilities 一次；
 *   * terminal：不轮询——用户回来点「我已登录」，或窗口重新聚焦时重新探测。
 */
export function useAgentLogin(
  agent: Pick<AiAgentCaps, 'id'>,
  onRefreshed: OnRefreshed,
  onError: (message: string | null) => void,
) {
  const [phase, setPhase] = useState<LoginPhase>('idle')
  const [command, setCommand] = useState('')
  const [authUrl, setAuthUrl] = useState<string | null>(null)
  const refreshRef = useRef(onRefreshed)
  useEffect(() => {
    refreshRef.current = onRefreshed
  }, [onRefreshed])
  /** 刷新是否成功：父级 `reload` 自己吞掉失败，只用返回值 `false` 表达；抛错同样算失败。 */
  const refreshOk = async () => {
    try {
      return (await refreshRef.current()) !== false
    } catch {
      return false
    }
  }

  useEffect(() => {
    if (phase !== 'waiting') return
    const started = Date.now()
    let stopped = false
    const timer = window.setInterval(() => {
      void (async () => {
        if (stopped) return
        if (Date.now() - started > LOGIN_MAX_WAIT_MS) {
          stopped = true
          window.clearInterval(timer)
          setPhase('idle')
          return
        }
        try {
          const s = await fetchAiLoginStatus(agent.id)
          if (stopped) return
          if (s.status === 'running') {
            if (s.auth_url) setAuthUrl(s.auth_url)
            return
          }
          stopped = true
          window.clearInterval(timer)
          setAuthUrl(null)
          // 登录进程已结束；刷新失败就停在「没能刷新」+ 重试，不回到初始登录按钮
          setPhase((await refreshOk()) ? 'idle' : 'retry')
        } catch {
          /* 网络抖动：下一轮再问 */
        }
      })()
    }, LOGIN_POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [phase, agent.id])

  // 终端模式：用户从终端切回 Tavotto 时重新探测一次
  useEffect(() => {
    if (phase !== 'terminal' && phase !== 'manual') return
    const onFocus = () => void refreshRef.current()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [phase])

  const begin = useCallback(async () => {
    onError(null)
    try {
      const r = await startAiLogin(agent.id)
      if (r.mode === 'background') {
        setPhase('waiting') // started 与 already_running 都是「等它结束」
      } else if (r.started) {
        setPhase('terminal')
      } else {
        setCommand(r.command ?? '')
        setPhase('manual')
      }
    } catch (e) {
      onError(backendErrorText(e))
    }
  }, [agent.id, onError])

  const confirm = async () => {
    setPhase((await refreshOk()) ? 'idle' : 'retry')
  }

  return { phase, command, authUrl, begin: () => void begin(), confirm: () => void confirm() }
}
