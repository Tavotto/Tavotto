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
      {open && <WeComQrImage />}
    </Details>
  )
}

/** 二维码图（展开后才挂）。 */
function WeComQrImage() {
  return (
    <img
      src={qrUrl}
      alt={wc('qrAlt')}
      width={160}
      height={160}
      data-wecom-qr-img
      className="mt-1 rounded-md bg-white"
    />
  )
}

/**
 * 确认框里的一行：想直接沟通？加企业微信（失败时没有报告编号，换一句不提编号的）。
 * 链接与「显示二维码」同在一行（只占一行高）；二维码默认折叠，展开才挂 `<img>`。
 *
 * `active=false` 时**占位不显示**（`invisible` + `inert`，中文界面）：结果态才出现的这一行若是从无到有，
 * 对话框会变高、页脚「发送」按钮跟着跳位（#797 e2e 量的就是发送前后的按钮坐标）。英文界面一律不渲染。
 */
export function WeComContactNote({ withReportId, active }: { withReportId: boolean; active: boolean }) {
  const visible = useWeComContactVisible()
  const [qrOpen, setQrOpen] = useState(false)
  if (!visible) return null
  return (
    <div
      className={active ? undefined : 'invisible'}
      data-wecom-contact={active ? 'active' : 'reserved'}
      aria-hidden={active ? undefined : true}
      inert={!active}
    >
      <p className="type-caption">
        {wc(withReportId ? 'hint' : 'hintNoId')} <WeComLink />
        {' · '}
        <button
          type="button"
          aria-expanded={qrOpen}
          data-wecom-qr-toggle
          className="underline underline-offset-2 hover:text-ink"
          onClick={() => setQrOpen((v) => !v)}
        >
          {wc('showQr')}
        </button>
      </p>
      {active && qrOpen && <WeComQrImage />}
    </div>
  )
}
