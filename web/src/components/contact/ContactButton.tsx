import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { useContactStore } from '@/store/contactStore'
import { Mail } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { Button } from '../ui/Button'
import { Tip } from '../ui/Tooltip'

/**
 * 「联系作者」入口（ADR 0145）：编辑器顶栏右侧（`topbar`，撤销重做与导出之间，次级胶囊、不抢导出）与主页两个视图的右上角
 * （`home`，与左上角的应用菜单对称）。**开关只看 `contactStore.capability`**（引擎说开着才非空；`TAVOTTO_NO_CONTACT=1` 或
 * 没取到 = `null` = 完全不渲染）。窄于 900 只留图标，名字进可达名与气泡。有未读回信时右上角一个小圆点（`data-contact-unread`）。
 * 点击只是打开写信窗口，不联网。
 */
export function ContactButton({ placement, className }: { placement: 'topbar' | 'home'; className?: string }) {
  useTranslation('dialogs')
  const enabled = useContactStore((s) => s.capability !== null)
  const unread = useContactStore((s) => s.unread)
  if (!enabled) return null
  const label = translate('contact.buttonLabel', { ns: 'dialogs' })
  return (
    <Tip label={label}>
      <Button
        variant={placement === 'topbar' ? 'secondary' : 'ghost'}
        size="md"
        data-contact-button={placement}
        data-contact-unread={unread > 0 ? unread : undefined}
        aria-label={unread > 0 ? `${label} · ${translate('contact.unreadDot', { ns: 'dialogs' })}` : label}
        onClick={() => useContactStore.getState().setOpen(true, unread > 0 ? 'letters' : 'write')}
        className={cn('relative shrink-0', placement === 'home' && 'text-ink-2', className)}
      >
        <Mail size={ICON_SIZE.sm} aria-hidden />
        {/* 窄于 900：只留图标（名字在可达名与气泡里） */}
        <span className={cn(placement === 'topbar' && 'max-[899px]:sr-only')}>{label}</span>
        {unread > 0 && (
          <span
            aria-hidden
            data-contact-dot
            className="absolute right-1 top-1 size-1.5 rounded-full bg-accent"
          />
        )}
      </Button>
    </Tip>
  )
}
