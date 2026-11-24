import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { useUiStore } from '@/store/uiStore'
import { ICON_SIZE } from '../ui/Icon'
import { Sparkles } from '../ui/icons'

const DAY_MS = 86_400_000
/** 到期前多少天起，顶栏胶囊开始说「剩 N 天」 */
export const PRO_CHIP_WARN_DAYS = 14

/** 胶囊上写什么：过期「已到期」、到期前两周「剩 N 天」；平时（未激活、已激活且不临近）不出现——免费用户的顶栏不放商业入口（agy 审核）。纯函数，测试直接断言 */
export function proChipText(
  state: string,
  exp: number | null,
  now: number = Date.now(),
): { kind: 'expired' | 'soon'; days?: number } | null {
  if (state === 'expired' || state === 'revoked') return { kind: 'expired' }
  if (state === 'active' && exp) {
    const days = Math.ceil((exp * 1000 - now) / DAY_MS)
    if (days <= PRO_CHIP_WARN_DAYS) return { kind: 'soon', days: Math.max(0, days) }
  }
  return null
}

/**
 * 顶栏的「高级版」胶囊（入口方案 B，维护者拍板前不挂进顶栏）：提醒只走胶囊，不推页面。
 * 灰色中性胶囊（不用黄 / 红底），点开高级版页。入口没开时不渲染。
 */
export function ProChip() {
  useTranslation('dialogs')
  const status = useLicenseStore((s) => s.status)
  if (!licenseVisible(status)) return null
  const t = proChipText(status.state, status.exp)
  if (!t) return null
  const label =
    t.kind === 'soon'
      ? translate('pro.chip.soon', { ns: 'dialogs', count: t.days })
      : translate('pro.chip.expired', { ns: 'dialogs' })
  return (
    <button
      type="button"
      data-topbar-pro
      aria-label={label}
      onClick={() => useUiStore.getState().setProOpen(true)}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium outline-none',
        'bg-surface-active text-ink-2 hover:bg-surface-hover focus-visible:focus-ring',
      )}
    >
      <Sparkles size={ICON_SIZE.sm} aria-hidden />
      <span>{label}</span>
    </button>
  )
}
