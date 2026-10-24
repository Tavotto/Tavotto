import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { AiConnection, AiVendor } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { StatusPill } from '../../ui/StatusPill'
import { ag } from '../agentState'
import { BrandLogo } from '../BrandLogo'
import { OAuthLoginDialog } from '../OAuthLoginDialog'
import { useAgentLogin } from '../useAgentLogin'
import { cx } from './copy'
import { connectionErrorText, loginAgentOf, vendorName } from './vendors'
import { VendorKeyForm } from './VendorKeyForm'

/** 重读 capabilities，看这家厂商有没有 ready 的连接；读失败 = 没刷新成功（`false`） */
async function refreshAndFind(vendorId: string): Promise<{ ok: boolean; conn?: AiConnection }> {
  try {
    await useAiStore.getState().loadCaps(true)
  } catch {
    return { ok: false }
  }
  const conn = useAiStore.getState().caps?.connections?.find((x) => x.vendor === vendorId && x.state === 'ready')
  return { ok: true, conn }
}

/**
 * 「连接 AI」列表的一行（连接弹窗与设置页共用）：`[彩色 logo] 厂商名 ……… [状态 / 按钮]`。
 *
 * 右侧只有一个动作，按厂商的 `connect` 类型定：Key 类「连接」（就地展开 Key 表单）、订阅类「登录」
 * （沿用官方登录进度与「我已登录」）、账号登录类「登录」（设备码小对话框）。已连接显示「已连接」；
 * 设置页里已连接的 Key 类行可展开（`manage`：换 Key / 默认型号 / 断开），弹窗里不展开。
 * 连好之后重读 capabilities，再调 `onConnected`。
 */
export function VendorRow({
  vendor,
  connection,
  onConnected,
  manage,
  className,
}: {
  vendor: AiVendor
  connection?: AiConnection
  onConnected: (c: AiConnection) => void
  /** 设置页：已连接时点行展开的管理区 */
  manage?: (c: AiConnection) => ReactNode
  className?: string
}) {
  useTranslation('dialogs')
  const [open, setOpen] = useState(false)
  const [oauth, setOauth] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)

  // 登录结束 / 用户点「我已登录」：重读 capabilities，看对应订阅连接是不是 ready 了
  const login = useAgentLogin(
    { id: loginAgentOf(vendor) },
    async () => {
      const r = await refreshAndFind(vendor.id)
      if (r.conn) onConnected(r.conn)
      return r.ok
    },
    setLoginError,
  )

  const ready = connection?.state === 'ready'
  const needsAuth = connection?.state === 'needs_auth'
  const canManage = ready && !!manage && vendor.connect === 'key'
  const unavailable = vendor.connect === 'login' && !vendor.available

  const status =
    connection && !ready ? (
      <StatusPill tone={needsAuth ? 'warn' : 'danger'} dot data-connect-state={connection.state}>
        {needsAuth ? cx('needsAuth') : connectionErrorText(connection)}
      </StatusPill>
    ) : null
  const confirming = login.phase === 'terminal' || login.phase === 'manual' || login.phase === 'retry'

  let action: ReactNode
  if (ready) {
    action = (
      <StatusPill tone="ok" dot data-connect-state="ready">
        {cx('connected')}
      </StatusPill>
    )
  } else if (unavailable) {
    action = <span className="type-caption">{cx('needsInstall')}</span>
  } else if (vendor.connect === 'key') {
    action = (
      <Button
        variant="secondary"
        size="sm"
        data-connect-action="key"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {cx('connect')}
      </Button>
    )
  } else if (vendor.connect === 'oauth') {
    action = (
      <Button variant="secondary" size="sm" data-connect-action="oauth" onClick={() => setOauth(true)}>
        {cx('signIn')}
      </Button>
    )
  } else {
    action = (
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
  }

  const hint =
    vendor.connect === 'login' && login.phase !== 'idle' ? (
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

  const rowBody = (
    <>
      <BrandLogo logo={vendor.logo} size={24} />
      <span className="min-w-0 flex-1 truncate text-base font-medium text-ink">{vendorName(vendor)}</span>
    </>
  )

  return (
    <li data-connect-vendor={vendor.id} data-connect-kind={vendor.connect} className={cn('flex flex-col px-4 py-2', className)}>
      <div className="flex min-h-9 items-center gap-3">
        {canManage ? (
          <button
            type="button"
            data-connect-expand={vendor.id}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="focus-visible:focus-ring -mx-1 flex min-w-0 flex-1 items-center gap-3 rounded-md px-1 text-left outline-none"
          >
            {rowBody}
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-3">{rowBody}</div>
        )}
        <span className="flex shrink-0 items-center gap-2">
          {status}
          {action}
          {canManage && (
            <ChevronRight
              size={ICON_SIZE.xs}
              aria-hidden
              className={cn('text-ink-3 transition-transform', open && 'rotate-90')}
            />
          )}
        </span>
      </div>
      {hint && <div className="pl-11 pt-1">{hint}</div>}
      {open && !ready && vendor.connect === 'key' && (
        <div className="pl-11 pt-2">
          <VendorKeyForm vendor={vendor} connectionId={connection?.id} onConnected={onConnected} />
        </div>
      )}
      {open && canManage && connection && <div className="pl-11 pt-2">{manage!(connection)}</div>}
      {oauth && (
        <OAuthLoginDialog
          provider={vendor.logo}
          name={vendorName(vendor)}
          onClose={() => setOauth(false)}
          onSignedIn={() => {
            setOauth(false)
            void refreshAndFind(vendor.id).then((r) => r.conn && onConnected(r.conn))
          }}
        />
      )}
    </li>
  )
}
