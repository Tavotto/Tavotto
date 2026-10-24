import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { BrandMark } from '@/components/ui/BrandMark'
import { PRODUCT_NAME } from '@/lib/brand'
import type { AiConnection, AiVendor } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { Button, IconButton } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { BrandLogo } from '../BrandLogo'
import { BrandLines } from './BrandLines'
import { CompatibleEndpoint } from './CompatibleEndpoint'
import { cx } from './copy'
import { MethodList } from './MethodList'
import { connectionOf, connectionsOf, listedVendors, vendorName } from './vendors'
import { VendorRow } from './VendorRow'

/** 卡上默认露几家（用户决策：5 家，其余在「更多 AI…」里） */
export const CONNECT_AI_VISIBLE = 5

/**
 * 「连接一个 AI」：宽矩形小弹窗（不是设置页）——左侧是会动的品牌竖线画（`BrandLines`，窄屏不显示），
 * 右侧是标题 + 一句说明 + 按系统语言排好的厂商列表（默认前 5 家 +「更多 AI…」，展开后末尾是「其他兼容接口…」）。
 * 整个弹窗高度固定，列表在右栏里自己滚。点 Key 类就地展开 Key 表单；点订阅类直接登录；一家厂商有多种
 * 连接方式（API Key / 订阅登录）时进二级卡片让用户自己选。连好（capabilities 已刷新）后自动关闭并调
 * `onConnected`。「稍后」= 关闭，什么都不改。
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
  const [step, setStep] = useState<AiVendor | null>(null)
  const vendors = listedVendors(caps)
  const shown = all ? vendors : vendors.slice(0, CONNECT_AI_VISIBLE)
  const title = reason === 'fix' ? cx('titleFix') : cx('title')

  const done = (c?: AiConnection) => {
    onConnected?.(c)
    onClose()
  }

  return (
    <>
      <Dialog
        open={open && !manual}
        onOpenChange={(v) => !v && onClose()}
        title={title}
        description={cx('subtitle')}
        chrome="split"
        width={800}
        height="480px"
        anchor="connect-ai"
      >
        <div data-connect-ai-dialog={reason} className="flex h-full min-h-0">
          <div
            data-connect-brand
            style={{ backgroundColor: 'var(--color-paper-ink)' }}
            className="relative hidden w-[40%] shrink-0 overflow-hidden text-paper min-[700px]:block"
          >
            <BrandLines className="absolute inset-0 size-full" />
            <div className="pointer-events-none absolute left-5 top-5 flex items-center gap-2">
              <BrandMark size={24} tone="mono" />
              <span className="text-base font-medium">{PRODUCT_NAME}</span>
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="shrink-0 px-6 pb-2 pt-5 pr-12">
              {step ? (
                <div data-connect-step={step.id} className="flex items-center gap-3">
                  <IconButton data-connect-back label={cx('back')} tip={false} onClick={() => setStep(null)}>
                    <ArrowLeft size={ICON_SIZE.md} />
                  </IconButton>
                  <BrandLogo logo={step.logo} size={32} />
                  <span className="truncate text-lg font-medium text-ink">{vendorName(step)}</span>
                </div>
              ) : (
                <>
                  <p aria-hidden className="type-title">
                    {title}
                  </p>
                  <p aria-hidden className="mt-1 text-base leading-[1.5] text-ink-2">
                    {cx('subtitle')}
                  </p>
                </>
              )}
            </div>
            <div data-connect-scroll className="min-h-0 flex-1 overflow-y-auto px-6 pb-2">
              {step ? (
                <div className="pt-2">
                  <MethodList vendor={step} connections={connectionsOf(caps, step)} onConnected={done} />
                </div>
              ) : (
                <>
                  <ul className="-mx-4 flex flex-col">
                    {shown.map((v) => (
                      <VendorRow
                        key={v.id}
                        vendor={v}
                        connection={connectionOf(caps, v)}
                        connections={connectionsOf(caps, v)}
                        onConnected={done}
                        onChooseMethods={setStep}
                      />
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
                </>
              )}
            </div>
            <div className="flex shrink-0 justify-end px-6 pb-4 pt-2">
              <Button variant="secondary" size="lg" data-connect-later onClick={onClose}>
                {cx('later')}
              </Button>
            </div>
          </div>
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
