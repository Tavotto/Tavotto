import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { currentLocale, t as translate } from '@/i18n'
import qrUrl from '@/assets/wecom-contact-qr.png'
import { WECOM_CONTACT_URL } from '@/lib/contact'
import { Details, Summary } from '../ui/Details'

const wc = (key: string) => translate(`settings.diagnostics.send.contact.${key}`, { ns: 'dialogs' })

/** 联系入口只在中文界面出现：判据是界面语言（`currentLocale()`），不看系统区域。 */
export function useWeComContactVisible(): boolean {
  useTranslation('dialogs')
  return currentLocale() === 'zh-CN'
}

/** 「加企业微信联系我们」文字链（系统浏览器打开，与其余外链同一写法）。 */
export function WeComLink() {
  return (
    <a
      href={WECOM_CONTACT_URL}
      target="_blank"
      rel="noreferrer"
      data-wecom-link
      className="underline underline-offset-2 hover:text-ink"
    >
      {wc('link')}
    </a>
  )
}

/** 默认折叠的二维码：展开才挂 img。 */
export function WeComQr() {
  const [open, setOpen] = useState(false)
  return (
    <Details data-wecom-qr className="text-sm" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <Summary className="h-6 text-ink-2 hover:text-ink">{wc('showQr')}</Summary>
      {open && (
        <img
          src={qrUrl}
          alt={wc('qrAlt')}
          width={160}
          height={160}
          data-wecom-qr-img
          className="mt-1 rounded-md bg-white"
        />
      )}
    </Details>
  )
}

/** 确认框里的一行：想直接沟通？加企业微信（失败时没有报告编号，换一句不提编号的）。 */
export function WeComContactNote({ withReportId }: { withReportId: boolean }) {
  const visible = useWeComContactVisible()
  if (!visible) return null
  return (
    <div className="flex flex-col gap-0.5" data-wecom-contact>
      <p className="type-caption">
        {wc(withReportId ? 'hint' : 'hintNoId')} <WeComLink />
      </p>
      <WeComQr />
    </div>
  )
}
