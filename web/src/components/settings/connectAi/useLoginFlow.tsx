import { useState, type ReactNode } from 'react'
import type { AiConnection, AiVendor, AiVendorMethod } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { ag } from '../agentState'
import { useAgentLogin } from '../useAgentLogin'
import { cx } from './copy'
import { loginAgentOf } from './vendors'

/** 重读 capabilities，看这家厂商有没有 ready 的连接；读失败 = 没刷新成功（`ok:false`） */
export async function refreshAndFind(vendorId: string): Promise<{ ok: boolean; conn?: AiConnection }> {
  try {
    await useAiStore.getState().loadCaps(true)
  } catch {
    return { ok: false }
  }
  const conn = useAiStore.getState().caps?.connections?.find((x) => x.vendor === vendorId && x.state === 'ready')
  return { ok: true, conn }
}

/**
 * 订阅登录一行的两个部件：按钮（「登录」→「等待登录…」→「我已登录」）与它下面的一句提示。
 * 状态机是 `useAgentLogin`（官方登录命令、轮询 / 「我已登录」），这里只负责摆出来。
 * 登录结束后重读 capabilities，对应连接 ready 了就调 `onConnected`。
 */
export function useLoginFlow(
  vendor: AiVendor,
  method: AiVendorMethod,
  onConnected: (c: AiConnection) => void,
): { button: ReactNode; hint: ReactNode; begin: () => void } {
  const [loginError, setLoginError] = useState<string | null>(null)
  const login = useAgentLogin(
    { id: loginAgentOf(vendor, method) },
    async () => {
      const r = await refreshAndFind(vendor.id)
      if (r.conn) onConnected(r.conn)
      return r.ok
    },
    setLoginError,
  )
  const confirming = login.phase === 'terminal' || login.phase === 'manual' || login.phase === 'retry'

  const button = (
    <Button
      variant="secondary"
      size="sm"
      data-connect-action="login"
      data-connect-login={login.phase}
      loading={login.phase === 'waiting'}
      disabled={login.phase === 'waiting'}
      onClick={() => (confirming ? login.confirm() : login.begin())}
    >
      {login.phase === 'waiting' ? ag('login.waiting') : confirming ? ag('login.done') : cx('signIn')}
    </Button>
  )

  const hint =
    login.phase !== 'idle' ? (
      <p data-connect-login-hint className="type-caption" role={login.phase === 'retry' ? 'alert' : undefined}>
        {login.phase === 'waiting' && login.authUrl ? (
          <>
            {ag('login.browserHint')}{' '}
            <a href={login.authUrl} target="_blank" rel="noreferrer" className="underline underline-offset-2">
              {ag('login.openLink')}
            </a>
          </>
        ) : login.phase === 'retry' ? (
          ag('login.refreshFailed')
        ) : login.phase === 'terminal' ? (
          ag('login.terminalHint')
        ) : login.phase === 'manual' ? (
          ag('login.manualHint', { command: login.command })
        ) : null}
      </p>
    ) : loginError ? (
      <p role="alert" className="text-xs text-danger">
        {loginError}
      </p>
    ) : null

  return { button, hint, begin: login.begin }
}
