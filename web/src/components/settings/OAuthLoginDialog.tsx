import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backendErrorText, cancelAiOauthFlow, fetchAiOauthFlow, startAiOauth, type AiOauthFlow } from '@/lib/api'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { ag } from './agentState'

/**
 * 账号登录（设备码 OAuth，ADR 0146）的小对话框：一句话 + 大号验证码 + 一个主按钮。
 *
 * 流程：挂载即向后端发起设备授权 → 用外链方式打开授权网址 → 按后端给的间隔轮询流程状态；
 * `signed_in` 时通知父级并关闭；`expired / denied / failed` 换成一句话 + 「再试一次」。
 * 卸载（关对话框 / 登录完成）一律停止轮询；还在等的流程尽力取消，不让后端白白轮询到过期。
 * 令牌全程只在后端：这里只有验证码与授权网址。
 */
export function OAuthLoginDialog({
  provider,
  region,
  name,
  onClose,
  onSignedIn,
}: {
  provider: string
  region?: string
  /** 对话框标题里的厂商名（含区域），由父级拼好 */
  name: string
  onClose: () => void
  onSignedIn: () => void
}) {
  useTranslation('dialogs')
  const [flow, setFlow] = useState<AiOauthFlow | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 用 ref 记「当前这一轮」：重试 / 卸载时让上一轮的在途请求结果作废
  const round = useRef(0)
  const timer = useRef<number | null>(null)
  const flowRef = useRef<AiOauthFlow | null>(null)
  const done = useRef(false)

  const stopPolling = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }

  const begin = useCallback(async () => {
    const mine = ++round.current
    stopPolling()
    setFlow(null)
    setError(null)
    flowRef.current = null
    let started: AiOauthFlow
    try {
      started = await startAiOauth(provider, region)
    } catch (e) {
      if (mine === round.current) setError(backendErrorText(e))
      return
    }
    if (mine !== round.current) return
    flowRef.current = started
    setFlow(started)
    window.open(started.verification_uri_complete, '_blank', 'noreferrer')

    const tick = async (f: AiOauthFlow) => {
      timer.current = null
      let next: AiOauthFlow | null = null
      try {
        next = await fetchAiOauthFlow(f.flow_id)
      } catch {
        /* 网络抖动：下一轮再问 */
      }
      if (mine !== round.current) return
      if (next) {
        flowRef.current = next
        setFlow(next)
        if (next.state === 'signed_in') {
          done.current = true
          onSignedIn()
          return
        }
        if (next.state !== 'waiting') return // 终态：停止轮询
      }
      timer.current = window.setTimeout(() => void tick(next ?? f), Math.max(1, (next ?? f).interval) * 1000)
    }
    timer.current = window.setTimeout(() => void tick(started), Math.max(1, started.interval) * 1000)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, region])

  useEffect(() => {
    void begin()
    return () => {
      round.current += 1 // 作废在途结果
      stopPolling()
      const f = flowRef.current
      if (f && f.state === 'waiting' && !done.current) void cancelAiOauthFlow(f.flow_id).catch(() => undefined)
    }
  }, [begin])

  const state = flow?.state
  const failedState = error ? 'failed' : state && state !== 'waiting' && state !== 'signed_in' ? state : null

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={ag('oauth.title', { name })}
      size="sm"
      footer={{
        secondary: (
          <Button variant="secondary" size="lg" data-oauth-cancel onClick={onClose}>
            {ag('oauth.cancel')}
          </Button>
        ),
        primary: failedState ? (
          <Button variant="primary" size="lg" data-oauth-retry onClick={() => void begin()}>
            {ag('oauth.again')}
          </Button>
        ) : (
          <Button
            variant="primary"
            size="lg"
            data-oauth-reopen
            disabled={!flow}
            onClick={() => {
              if (flow) window.open(flow.verification_uri_complete, '_blank', 'noreferrer')
            }}
          >
            {ag('oauth.reopen')}
          </Button>
        ),
      }}
    >
      <div data-oauth-dialog={provider} className="flex flex-col items-center gap-3 py-1 text-center">
        {failedState ? (
          <p role="alert" data-oauth-state={failedState} className="text-base text-ink-2">
            {error ?? ag(`oauth.state.${failedState}`)}
          </p>
        ) : (
          <>
            <p className="text-base text-ink-2">{ag('oauth.instruction')}</p>
            <p
              data-oauth-code
              aria-label={ag('oauth.codeAria')}
              className="select-all font-mono text-3xl font-medium tracking-wide text-ink"
            >
              {flow?.user_code ?? '········'}
            </p>
          </>
        )}
      </div>
    </Dialog>
  )
}
