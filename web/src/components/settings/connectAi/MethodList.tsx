import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Sparkles } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { AiConnection, AiVendor, AiVendorMethod } from '@/lib/api'
import { cn } from '@/lib/utils'
import { OAuthLoginDialog } from '../OAuthLoginDialog'
import { cx } from './copy'
import { methodHint, methodName, vendorName } from './vendors'
import { refreshAndFind, useLoginFlow } from './useLoginFlow'
import { VendorKeyForm } from './VendorKeyForm'

/** 选中某种方式之后摆在卡片下面的东西：Key 表单 / 登录按钮与提示 / 设备码小对话框 */
function MethodConnect({
  vendor,
  method,
  onConnected,
  onCancel,
}: {
  vendor: AiVendor
  method: AiVendorMethod
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
  if (method.kind === 'key') return <VendorKeyForm vendor={vendor} method={method} onConnected={onConnected} />
  if (method.kind === 'oauth') {
    return (
      <OAuthLoginDialog
        provider={vendor.logo}
        name={vendorName(vendor)}
        onClose={onCancel}
        onSignedIn={() => void refreshAndFind(vendor.id).then((r) => r.conn && onConnected(r.conn))}
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
 * （图标 + 方式名 + 一句短说明）；不可用的置灰并给一句原因。选中一张，它下面展开该方式的表单 / 登录。
 */
export function MethodList({ vendor, onConnected }: { vendor: AiVendor; onConnected: (c: AiConnection) => void }) {
  useTranslation('dialogs')
  const [picked, setPicked] = useState<string | null>(null)
  const methods = vendor.methods ?? []
  return (
    <div data-connect-methods={vendor.id} className="flex flex-col gap-2">
      {methods.map((m) => {
        const on = picked === m.id
        const Icon = m.kind === 'key' ? KeyRound : Sparkles
        return (
          <div key={m.id} className="flex flex-col gap-2">
            <button
              type="button"
              data-connect-method={m.id}
              data-connect-method-kind={m.kind}
              aria-pressed={on}
              disabled={!m.available}
              onClick={() => setPicked(on ? null : m.id)}
              className={cn(
                'focus-visible:focus-ring flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left outline-none transition-colors',
                on ? 'border-ink-3 bg-surface-hover' : 'border-border bg-surface hover:bg-surface-hover',
                !m.available && 'cursor-not-allowed opacity-40 hover:bg-surface',
              )}
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-group text-ink-2">
                <Icon size={ICON_SIZE.md} aria-hidden />
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-base font-medium text-ink">{methodName(m)}</span>
                <span className="type-caption truncate">{m.available ? methodHint(vendor, m) : cx('needsInstall')}</span>
              </span>
            </button>
            {on && <MethodConnect vendor={vendor} method={m} onConnected={onConnected} onCancel={() => setPicked(null)} />}
          </div>
        )
      })}
    </div>
  )
}
