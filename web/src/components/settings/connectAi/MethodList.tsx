import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Sparkles } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { AiConnection, AiVendor, AiVendorMethod } from '@/lib/api'
import { cn } from '@/lib/utils'
import { StatusPill } from '../../ui/StatusPill'
import { OAuthLoginDialog } from '../OAuthLoginDialog'
import { ConnectionManage } from './ConnectionManage'
import { cx } from './copy'
import { connectionErrorText, connectionForMethod, methodHint, methodName, vendorName } from './vendors'
import { refreshAndFind, useLoginFlow } from './useLoginFlow'
import { VendorKeyForm } from './VendorKeyForm'

/** 选中某种方式之后摆在卡片下面的东西：Key 表单 / 登录按钮与提示 / 设备码小对话框 */
function MethodConnect({
  vendor,
  method,
  connection,
  onConnected,
  onCancel,
}: {
  vendor: AiVendor
  method: AiVendorMethod
  /** 这种方式已有的连接（error / needs_auth 的也算）：改 Key 要更新它，不是另建一条 */
  connection?: AiConnection
  onConnected: (c: AiConnection) => void
  onCancel: () => void
}) {
  const flow = useLoginFlow(vendor, method, onConnected)
  // 点订阅登录这张卡 = 直接发起登录，不再多点一次「登录」
  const { begin } = flow
  useEffect(() => {
    if (method.kind === 'login') begin()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  if (method.kind === 'key') return <VendorKeyForm vendor={vendor} method={method} connectionId={connection?.id} onConnected={onConnected} />
  if (method.kind === 'oauth') {
    return (
      <OAuthLoginDialog
        provider={vendor.logo}
        name={vendorName(vendor)}
        onClose={onCancel}
        onSignedIn={() => void refreshAndFind(vendor, method).then((r) => r.conn && onConnected(r.conn))}
      />
    )
  }
  return (
    <div className="flex flex-col gap-1">
      <div>{flow.button}</div>
      {flow.hint}
    </div>
  )
}

/**
 * 二级卡片：一家厂商有多种连接方式（API Key / 订阅登录 …）时，每种方式一张可点的选项卡
 * （图标 + 方式名 + 一句短说明）；不可用的置灰并给一句原因。
 * **状态落在每种方式上**：每张卡自己显示「已连接」或异常；点没连的卡，它下面展开该方式的表单 / 登录；
 * 点已连的 Key 类卡，展开管理（换 Key / 默认型号 / 断开）——所以一种连好了也不会堵住另一种的入口。
 */
export function MethodList({
  vendor,
  connections = [],
  onConnected,
}: {
  vendor: AiVendor
  connections?: AiConnection[]
  onConnected: (c: AiConnection) => void
}) {
  useTranslation('dialogs')
  const [picked, setPicked] = useState<string | null>(null)
  const methods = vendor.methods ?? []
  return (
    <div data-connect-methods={vendor.id} className="flex flex-col gap-2">
      {methods.map((m) => {
        const on = picked === m.id
        const Icon = m.kind === 'key' ? KeyRound : Sparkles
        const conn = connectionForMethod(connections, vendor, m)
        const ready = conn?.state === 'ready'
        // 已连的订阅类没有可管理的东西（只能在命令行里退出登录）：卡片只显示状态，不展开
        const clickable = m.available && !(ready && m.kind !== 'key')
        return (
          <div key={m.id} className="flex flex-col gap-2">
            <button
              type="button"
              data-connect-method={m.id}
              data-connect-method-kind={m.kind}
              data-connect-method-state={ready ? 'ready' : (conn?.state ?? 'none')}
              aria-pressed={on}
              disabled={!clickable}
              onClick={() => setPicked(on ? null : m.id)}
              className={cn(
                'focus-visible:focus-ring flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left outline-none transition-colors',
                on ? 'border-ink-3 bg-surface-hover' : 'border-border bg-surface hover:bg-surface-hover',
                !m.available && 'cursor-not-allowed opacity-40 hover:bg-surface',
                m.available && !clickable && 'cursor-default hover:bg-surface',
              )}
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-group text-ink-2">
                <Icon size={ICON_SIZE.md} aria-hidden />
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-base font-medium text-ink">{methodName(m)}</span>
                <span className="type-caption truncate">{m.available ? methodHint(vendor, m) : cx('needsInstall')}</span>
              </span>
              {conn && (
                <StatusPill
                  tone={ready ? 'ok' : conn.state === 'needs_auth' ? 'warn' : 'danger'}
                  dot
                  data-connect-state={conn.state}
                >
                  {ready ? cx('connected') : conn.state === 'needs_auth' ? cx('needsAuth') : connectionErrorText(conn)}
                </StatusPill>
              )}
            </button>
            {on &&
              (ready && conn ? (
                <ConnectionManage vendor={vendor} connection={conn} method={m} />
              ) : (
                <MethodConnect vendor={vendor} method={m} connection={conn} onConnected={onConnected} onCancel={() => setPicked(null)} />
              ))}
          </div>
        )
      })}
    </div>
  )
}
