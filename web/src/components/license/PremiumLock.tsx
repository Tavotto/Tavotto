import { useEffect, useState, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { PRO_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { licenseHas, licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { useUiStore } from '@/store/uiStore'
import { Popover } from '../ui/Popover'
import { SUBTLE_LINK_CLASS } from '../ui/subtleLink'
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
  tip,
  align,
  alignOffset,
  arrow,
  soft,
  learn,
}: {
  /** 功能键（闭集，见引擎 `license.FEATURES`） */
  feature: string
  children: ReactElement
  side?: 'top' | 'bottom' | 'left' | 'right'
  /** 悬停那一句换成这个功能自己的（多行用换行；最后一行可以是「了解高级版 ›」）；不给就是通用那一句 */
  tip?: ReactNode
  align?: 'start' | 'center' | 'end'
  alignOffset?: number
  arrow?: boolean
  /** 锁着的控件也是要读的字（分段里的「批注」）：置灰只降一小档 */
  soft?: boolean
  /**
   * 给了 = 点一下**不跳设置**，只在原地弹 `tip` 那一句 + 这一行链接（「了解高级版 ›」，点它才去「关于与更新」）。
   * 悬停仍是 `tip` + 这一行。不给 = 原来的行为（点一下直接去授权行）。修改方案卡的批注入口用它（维护者 2026-11-28）
   */
  learn?: ReactNode
}) {
  useTranslation('dialogs')
  const status = useLicenseStore((s) => s.status)
  const load = useLicenseStore((s) => s.load)
  const [asked, setAsked] = useState(false)
  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  if (!licenseVisible(status) || licenseHas(status, feature)) return children
  const toSettings = () => {
    setAsked(false)
    useUiStore.getState().setSettingsOpen(true, 'about')
  }
  const press = learn ? () => setAsked(true) : toSettings
  const label = tip ?? translate('settings.license.lock.tip', { ns: 'dialogs', pro: PRO_NAME })
  return (
    <Tip
      label={
        learn ? (
          <>
            {label}
            <span className="mt-2 block text-ink">{learn}</span>
          </>
        ) : (
          label
        )
      }
      side={side}
      tone="light"
      align={align}
      alignOffset={alignOffset}
      arrow={arrow}
    >
      <span
        data-premium-lock={feature}
        role="button"
        tabIndex={0}
        aria-disabled="true"
        aria-label={translate('settings.license.lock.aria', { ns: 'dialogs', pro: PRO_NAME })}
        onClick={press}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            press()
          }
        }}
        className={cn('relative inline-flex outline-none focus-visible:focus-ring', soft ? 'opacity-80' : 'opacity-50')}
      >
        <span className="pointer-events-none inline-flex" inert>
          {children}
        </span>
        {/* 点了才弹的那一句：锚在控件本身（隐藏锚点 + 受控打开），不与悬停气泡抢同一个触发器 */}
        {learn && (
          <Popover
            open={asked}
            onOpenChange={setAsked}
            side={side}
            align="center"
            width={240}
            arrow
            trigger={<span aria-hidden className="pointer-events-none absolute inset-0" />}
          >
            {/* 浮层经 Portal 挂出去，但 React 事件仍冒泡回外层的锁：拦在这里，点链接不会又把它打开 */}
            <div className="flex flex-col gap-2 px-1 py-0.5 text-xs leading-relaxed text-ink" data-premium-lock-ask={feature} onClick={(e) => e.stopPropagation()}>
              <span>{label}</span>
              <button type="button" className={cn(SUBTLE_LINK_CLASS, 'self-start')} onClick={toSettings} data-premium-lock-learn>
                {learn}
              </button>
            </div>
          </Popover>
        )}
      </span>
    </Tip>
  )
}
