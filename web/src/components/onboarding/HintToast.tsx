import { useTranslation } from 'react-i18next'
import { Lightbulb, X } from 'lucide-react'
import { t as translate } from '@/i18n'
import { useHintStore } from '@/lib/onboarding/hints'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'

/**
 * 一次性情境提示的落点：画布右下角一条小卡片，可关、到时自己走、不挡操作。
 * 与状态 toast（底部居中）分开占位——两者同时出现时不互相盖。
 * 文案在 `workspace:hints.<kind>`。
 */
export function HintToast() {
  const { t } = useTranslation('workspace')
  const current = useHintStore((s) => s.current)
  const token = useHintStore((s) => s.token)
  const dismiss = useHintStore((s) => s.dismiss)
  const { mounted, state } = usePresence(!!current, DURATION.exit)
  if (!mounted || !current) return null
  return (
    <div className="pointer-events-none absolute bottom-3 right-3 z-20 flex justify-end">
      <div
        key={token}
        role="status"
        data-state={state}
        data-onboarding-hint={current}
        className={cn(
          'pointer-events-auto flex max-w-[320px] items-start gap-2 rounded-md border border-border bg-surface px-3 py-2 text-xs text-ink-2 shadow-pop',
          'data-[state=open]:animate-rise-in data-[state=closed]:animate-rise-out',
        )}
      >
        <Lightbulb size={13} className="mt-px shrink-0 text-ink-3" aria-hidden />
        <span className="min-w-0 flex-1 leading-relaxed">{t(`hints.${current}`)}</span>
        <button
          type="button"
          onClick={dismiss}
          aria-label={translate('actions.close')}
          className="-mr-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-sm text-ink-3 outline-none hover:bg-ink/[.055] hover:text-ink focus-visible:focus-ring"
        >
          <X size={12} />
        </button>
      </div>
    </div>
  )
}
