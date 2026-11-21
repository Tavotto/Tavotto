import { useEffect, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { licenseHas, licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { useUiStore } from '@/store/uiStore'
import { Tip } from '../ui/Tooltip'

/**
 * 付费功能没解锁时的画法（ADR 0193 §八，照导出灰格式气泡 #1820）：置灰 + 悬停一句话 + 点一下去「关于与更新」里的授权行，
 * **不弹窗**。灰掉的控件不吃指针（里面的 `<button disabled>` 不派发事件，悬停与点击落到外面这层），所以气泡与点击都挂在外层。
 *
 * 只有授权入口出现（能力开着）时才画锁：入口不出现 = 没有可买的东西，控件原样。已解锁 = 原样。
 * **只能包新功能**——现有功能永久免费（ADR 0193 §一）；今天没有任何调用点，是给以后的付费功能留的写法。
 */
export function PremiumLock({
  feature,
  children,
  side = 'top',
}: {
  /** 功能键（闭集，见引擎 `license.FEATURES`） */
  feature: string
  children: ReactElement
  side?: 'top' | 'bottom' | 'left' | 'right'
}) {
  useTranslation('dialogs')
  const status = useLicenseStore((s) => s.status)
  const load = useLicenseStore((s) => s.load)
  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  if (!licenseVisible(status) || licenseHas(status, feature)) return children
  return (
    <Tip label={translate('settings.license.lock.tip', { ns: 'dialogs' })} side={side} tone="light">
      <span
        data-premium-lock={feature}
        role="button"
        tabIndex={0}
        aria-disabled="true"
        aria-label={translate('settings.license.lock.aria', { ns: 'dialogs' })}
        onClick={() => useUiStore.getState().setSettingsOpen(true, 'about')}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            useUiStore.getState().setSettingsOpen(true, 'about')
          }
        }}
        className={cn('inline-flex opacity-50 outline-none focus-visible:focus-ring')}
      >
        <span className="pointer-events-none inline-flex" inert>
          {children}
        </span>
      </span>
    </Tip>
  )
}
