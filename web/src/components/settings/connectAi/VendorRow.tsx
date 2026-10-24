import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { AiConnection, AiVendor } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '../../ui/Button'
import { StatusPill } from '../../ui/StatusPill'
import { BrandLogo } from '../BrandLogo'
import { OAuthLoginDialog } from '../OAuthLoginDialog'
import { cx } from './copy'
import { MethodList } from './MethodList'
import { connectionErrorText, methodsOf, vendorName } from './vendors'
import { refreshAndFind, useLoginFlow } from './useLoginFlow'
import { VendorKeyForm } from './VendorKeyForm'

/**
 * 「连接 AI」列表的一行（连接弹窗与设置页共用）：`[logo] 厂商名 ……… [状态 / 按钮]`。
 *
 * 只有一种连接方式的厂商：右侧一个动作，按方式的类型定——Key 类「连接」（就地展开 Key 表单）、订阅类「登录」
 * （沿用官方登录进度与「我已登录」）、账号登录类「登录」（设备码小对话框）。
 * 有多种方式（API Key + 订阅登录等）的厂商：「连接」打开二级卡片让用户自己选——弹窗里由 `onChooseMethods`
 * 换成二级页，设置页（没传它）就在行下展开同一份卡片。已连接显示「已连接」；设置页里已连接的 Key 类行可展开
 * （`manage`：换 Key / 默认型号 / 断开）。连好之后重读 capabilities，再调 `onConnected`。
 */
export function VendorRow({
  vendor,
  connection,
  connections,
  onConnected,
  manage,
  onChooseMethods,
  className,
}: {
  vendor: AiVendor
  connection?: AiConnection
  /** 这家厂商的全部连接（多方式厂商按方式各自看状态）；没给就只有 `connection` 那一条 */
  connections?: AiConnection[]
  onConnected: (c: AiConnection) => void
  /** 设置页：已连接时点行展开的管理区 */
  manage?: (c: AiConnection) => ReactNode
  /** 弹窗：多种方式时进二级页 */
  onChooseMethods?: (v: AiVendor) => void
  className?: string
}) {
  useTranslation('dialogs')
  const [open, setOpen] = useState(false)
  const [oauth, setOauth] = useState(false)
  const methods = methodsOf(vendor)
  const multi = methods.length > 1
  const method = methods[0]
  const flow = useLoginFlow(vendor, method, onConnected)

  const all = connections ?? (connection ? [connection] : [])
  const anyReady = all.some((c) => c.state === 'ready')
  const ready = connection?.state === 'ready'
  const needsAuth = connection?.state === 'needs_auth'
  const canManage = !multi && ready && !!manage && connection?.route !== 'cli:codex' && connection?.route !== 'cli:claude' && !connection?.id.startsWith('sub:')
  const unavailable = !multi && !method.available

  const status =
    !multi && connection && !ready ? (
      <StatusPill tone={needsAuth ? 'warn' : 'danger'} dot data-connect-state={connection.state}>
        {needsAuth ? cx('needsAuth') : connectionErrorText(connection)}
      </StatusPill>
    ) : null

  let action: ReactNode
  if (multi) {
    // 多种方式：「已连接」只是说有一种连好了——入口仍在，点开二级卡片可以加另一种 / 管理已连的
    action = (
      <span className="flex items-center gap-2">
        {anyReady && (
          <StatusPill tone="ok" dot data-connect-state="ready">
            {cx('connected')}
          </StatusPill>
        )}
        <Button
          variant={anyReady ? 'ghost' : 'secondary'}
          size="sm"
          data-connect-action="methods"
          aria-expanded={onChooseMethods ? undefined : open}
          aria-label={anyReady ? cx('connect') : undefined}
          onClick={() => (onChooseMethods ? onChooseMethods(vendor) : setOpen((v) => !v))}
        >
          {anyReady ? <ChevronRight size={ICON_SIZE.xs} aria-hidden /> : cx('connect')}
        </Button>
      </span>
    )
  } else if (ready) {
    action = (
      <StatusPill tone="ok" dot data-connect-state="ready">
        {cx('connected')}
      </StatusPill>
    )
  } else if (unavailable) {
    action = <span className="type-caption">{cx('needsInstall')}</span>
  } else if (method.kind === 'key') {
    action = (
      <Button variant="secondary" size="sm" data-connect-action="key" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {cx('connect')}
      </Button>
    )
  } else if (method.kind === 'oauth') {
    action = (
      <Button variant="secondary" size="sm" data-connect-action="oauth" onClick={() => setOauth(true)}>
        {cx('signIn')}
      </Button>
    )
  } else {
    action = flow.button
  }

  const rowBody = (
    <>
      <BrandLogo logo={vendor.logo} size={24} />
      <span className="min-w-0 flex-1 truncate text-base font-medium text-ink">{vendorName(vendor)}</span>
    </>
  )

  return (
    <li
      data-connect-vendor={vendor.id}
      data-connect-kind={multi ? 'multi' : method.kind}
      className={cn('flex flex-col px-4 py-2', className)}
    >
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
            <ChevronRight size={ICON_SIZE.xs} aria-hidden className={cn('text-ink-3 transition-transform', open && 'rotate-90')} />
          )}
        </span>
      </div>
      {!multi && method.kind === 'login' && flow.hint && <div className="pl-9 pt-1">{flow.hint}</div>}
      {open && !ready && !multi && method.kind === 'key' && (
        <div className="pl-9 pt-2">
          <VendorKeyForm vendor={vendor} method={method} connectionId={connection?.id} onConnected={onConnected} />
        </div>
      )}
      {open && multi && !onChooseMethods && (
        <div className="pl-9 pt-2">
          <MethodList vendor={vendor} connections={all} onConnected={onConnected} />
        </div>
      )}
      {open && canManage && connection && <div className="pl-9 pt-2">{manage!(connection)}</div>}
      {oauth && (
        <OAuthLoginDialog
          provider={vendor.logo}
          name={vendorName(vendor)}
          onClose={() => setOauth(false)}
          onSignedIn={() => {
            setOauth(false)
            void refreshAndFind(vendor, method).then((r) => r.conn && onConnected(r.conn))
          }}
        />
      )}
    </li>
  )
}
