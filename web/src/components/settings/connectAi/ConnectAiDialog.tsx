import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AiConnection } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { CompatibleEndpoint } from './CompatibleEndpoint'
import { cx } from './copy'
import { connectionOf, listedVendors } from './vendors'
import { VendorRow } from './VendorRow'

/** 卡上默认露几家（用户决策：5 家，其余在「更多 AI…」里） */
export const CONNECT_AI_VISIBLE = 5

/**
 * 「连接一个 AI」：居中小弹窗（不是设置页）。按系统语言排好的厂商列表，默认露前 5 家，「更多 AI…」展开全部
 * 并在末尾给「其他兼容接口…」（手动填写地址）。点 Key 类就地展开 Key 表单；点订阅类直接发起登录；
 * 连好（capabilities 已刷新）后自动关闭并调 `onConnected`。「稍后」= 关闭，什么都不改。
 */
export function ConnectAiDialog({
  open,
  reason = 'general',
  onClose,
  onConnected,
}: {
  open: boolean
  reason?: 'general' | 'fix'
  onClose: () => void
  onConnected?: (c?: AiConnection) => void
}) {
  useTranslation('dialogs')
  const caps = useAiStore((s) => s.caps)
  const [all, setAll] = useState(false)
  const [manual, setManual] = useState(false)
  const vendors = listedVendors(caps)
  const shown = all ? vendors : vendors.slice(0, CONNECT_AI_VISIBLE)

  const done = (c?: AiConnection) => {
    onConnected?.(c)
    onClose()
  }

  return (
    <>
      <Dialog
        open={open && !manual}
        onOpenChange={(v) => !v && onClose()}
        title={reason === 'fix' ? cx('titleFix') : cx('title')}
        size="sm"
        footer={{
          secondary: (
            <Button variant="secondary" size="lg" data-connect-later onClick={onClose}>
              {cx('later')}
            </Button>
          ),
        }}
      >
        <div data-connect-ai-dialog={reason} className="flex flex-col gap-1">
          <ul className="-mx-4 flex flex-col">
            {shown.map((v) => (
              <VendorRow key={v.id} vendor={v} connection={connectionOf(caps, v)} onConnected={done} />
            ))}
          </ul>
          {!all && vendors.length > CONNECT_AI_VISIBLE && (
            <div>
              <Button variant="ghost" size="sm" data-connect-more onClick={() => setAll(true)}>
                {cx('more')}
              </Button>
            </div>
          )}
          {(all || vendors.length <= CONNECT_AI_VISIBLE) && (
            <div>
              <Button variant="ghost" size="sm" data-connect-other onClick={() => setManual(true)}>
                {cx('other')}
              </Button>
            </div>
          )}
        </div>
      </Dialog>
      {open && manual && (
        <CompatibleEndpoint
          onClose={() => setManual(false)}
          onSaved={() => {
            setManual(false)
            done()
          }}
        />
      )}
    </>
  )
}
