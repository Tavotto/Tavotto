import { useEffect, useRef, useState, type ReactNode } from 'react'
import { backendErrorText, cancelSiwcFlow, fetchSiwcFlow, startSiwcLogin, type AiConnection, type AiSiwcFlow, type AiVendor, type AiVendorMethod } from '@/lib/api'
import { Button } from '../../ui/Button'
import { cx } from './copy'
import { refreshAndFind } from './useLoginFlow'

/** 轮询间隔：浏览器里点完「允许」到这边看到结果，最多慢这么久 */
const POLL_MS = 1000

type Phase = 'idle' | 'starting' | 'waiting' | 'done'

/** 结束但没连上时的一句话（流程状态 / 原因码 → 文案；每个键写成字面量，i18n 检查才数得到） */
function endedText(flow: AiSiwcFlow): string | null {
  switch (flow.state) {
    case 'plan_disabled':
      return cx('siwc.planDisabled')
    case 'denied':
    case 'cancelled':
      return null // 用户自己取消的：不报错，回到按钮
    case 'expired':
      return cx('siwc.expired')
    default:
      return cx('siwc.failed')
  }
}

/**
 * 「Continue with ChatGPT」一行的两个部件：按钮与它下面的一句提示（ADR 0209 §五）。
 * 点按钮 → 后端起本机回调、给授权地址 → 系统浏览器打开 → 轮询流程状态；登录成功后重读 capabilities，
 * 这种方式对应的连接 ready 了就调 `onConnected`。按钮文案是官方推荐的那一串，产品名不翻译。
 */
export function useSiwcLogin(
  vendor: AiVendor,
  method: AiVendorMethod,
  onConnected: (c: AiConnection) => void,
): { button: ReactNode; hint: ReactNode; begin: () => void } {
  const [phase, setPhase] = useState<Phase>('idle')
  const [flow, setFlow] = useState<AiSiwcFlow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)
  const alive = useRef(true)

  const stopPolling = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      stopPolling()
    }
  }, [])

  const poll = (id: string) => {
    timer.current = window.setTimeout(async () => {
      let f: AiSiwcFlow
      try {
        f = await fetchSiwcFlow(id)
      } catch {
        if (alive.current) poll(id) // 读不到（后端忙 / 网络抖）：接着等，流程自己有 10 分钟时限
        return
      }
      if (!alive.current) return
      setFlow(f)
      if (f.state === 'waiting' || f.state === 'exchanging') {
        poll(id)
        return
      }
      setPhase('done')
      if (f.state === 'signed_in') {
        const r = await refreshAndFind(vendor, method)
        if (!alive.current) return
        if (r.conn) onConnected(r.conn)
        else setError(cx('siwc.failed'))
        return
      }
      setError(endedText(f))
    }, POLL_MS)
  }

  const begin = async () => {
    stopPolling()
    setError(null)
    setPhase('starting')
    try {
      const r = await startSiwcLogin()
      if (!alive.current) return
      setFlow(r.flow)
      setPhase('waiting')
      window.open(r.flow.authorize_url, '_blank', 'noreferrer')
      poll(r.flow_id)
    } catch (e) {
      if (!alive.current) return
      setPhase('idle')
      setError(backendErrorText(e))
    }
  }

  const cancel = () => {
    stopPolling()
    if (flow) void cancelSiwcFlow(flow.flow_id).catch(() => undefined)
    setPhase('idle')
    setFlow(null)
  }

  const waiting = phase === 'starting' || phase === 'waiting'
  const button = waiting ? (
    <Button variant="ghost" size="sm" data-connect-action="siwc-cancel" onClick={cancel}>
      {cx('siwc.cancel')}
    </Button>
  ) : (
    <Button variant="secondary" size="sm" data-connect-action="siwc" data-connect-login={phase} onClick={() => void begin()}>
      {cx('siwc.continue')}
    </Button>
  )

  const hint =
    phase === 'waiting' && flow ? (
      <p data-connect-login-hint className="type-caption">
        {cx('siwc.browserHint')}{' '}
        <a href={flow.authorize_url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
          {cx('siwc.openAgain')}
        </a>
      </p>
    ) : error ? (
      <p role="alert" data-connect-login-hint="failed" className="text-xs text-danger">
        {error}
      </p>
    ) : null

  return { button, hint, begin: () => void begin() }
}
