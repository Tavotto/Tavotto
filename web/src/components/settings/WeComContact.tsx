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
export function WeComQrImage() {
  return (
    // 原图（sha256 钉死、不改）上下各自带一条 2 px 的细边线；用外层裁掉 1 个显示像素（约 3.7 个原图像素，
    // 只吃线与线内侧的留白），二维码本体与左右留白原样保留
    <div className="mt-1 h-[158px] w-[160px] overflow-hidden rounded-md bg-paper" data-wecom-qr-crop>
      <img src={qrUrl} alt={wc('qrAlt')} width={160} height={160} data-wecom-qr-img className="-mt-px block max-w-none" />
    </div>
  )
}

/**
 * 发送确认框完成态、没有回信通道时，副标题位置的后半句：「想直接沟通？加企业微信联系我们」（仅中文界面，英文不渲染）。
 * 只是文字链（系统浏览器打开）；占位问题不存在——它替换副标题那一行，不新增行（#797 的发送按钮不跳位）。
 */
export function WeComDirectLine() {
  const visible = useWeComContactVisible()
  if (!visible) return null
  return (
    <>
      {' · '}
      {wc('hintNoId')}
      <WeComLink />
    </>
  )
}
