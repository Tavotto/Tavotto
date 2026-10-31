import { useState, type ReactNode } from 'react'
import type { AiConnection, AiVendor, AiVendorMethod } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { ag } from '../agentState'
import { useAgentLogin } from '../useAgentLogin'
import { cx } from './copy'
import { connectionForMethod, connectionsOf, loginAgentOf } from './vendors'

/**
 * 重读 capabilities，看**所选方式**对应的连接 ready 了没有；读失败 = 没刷新成功（`ok:false`）。
 * 只认这一种方式的连接（`connectionForMethod`）：这家厂商另一种方式早就连好的（比如已有 API Key 连接），
 * 不算这次登录成功的证据。
 */
export async function refreshAndFind(
  vendor: AiVendor,
  method: AiVendorMethod,
): Promise<{ ok: boolean; conn?: AiConnection }> {
  try {
    await useAiStore.getState().loadCaps(true)
  } catch {
    return { ok: false }
  }
  const c = connectionForMethod(connectionsOf(useAiStore.getState().caps, vendor), vendor, method)
  return { ok: true, conn: c?.state === 'ready' ? c : undefined }
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
  /** 读到了状态、但所选连接仍没 ready（登录进程失败退出 / 没登完）：入口回到「登录」，可以再发起 */
  const [notDone, setNotDone] = useState(false)
  const login = useAgentLogin(
    { id: loginAgentOf(vendor, method) },
    async () => {
      const r = await refreshAndFind(vendor, method)
      if (r.conn) onConnected(r.conn)
      // 没能读到状态（ok:false）：返回 false → 重试刷新；读到了但这种方式还没 ready：
      // 不是刷新问题，回到初始「登录」按钮（再点 = 重新发起登录）并给失败那句
      setNotDone(r.ok && !r.conn)
      return r.ok
    },
    setLoginError,
  )
  const confirming = login.phase === 'terminal' || login.phase === 'manual' || login.phase === 'retry'

  const begin = () => {
    setNotDone(false)
    login.begin()
  }

  const button = (
    <Button
      variant="secondary"
      size="sm"
      data-connect-action="login"
      data-connect-login={login.phase}
      loading={login.phase === 'waiting'}
      disabled={login.phase === 'waiting'}
      onClick={() => (confirming ? login.confirm() : begin())}
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
    ) : notDone ? (
      <p role="alert" data-connect-login-hint="failed" className="text-xs text-danger">
        {ag('oauth.state.failed')}
      </p>
    ) : loginError ? (
      <p role="alert" className="text-xs text-danger">
        {loginError}
      </p>
    ) : null

  return { button, hint, begin }
}
